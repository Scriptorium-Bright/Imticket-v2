import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { classifyResponse } from '../lib/contracts.js';
import { seededRandom, selectSmokeSeats, selectWorkloadSeats, validateProfile } from '../lib/seats.js';
import { selectToken, validateTokenPool } from '../lib/tokens.js';
import { summaryText } from '../lib/summary.js';

const response = (status, body, error_code = 0) => ({status, body: JSON.stringify(body), error_code});
const success = (data) => ({success: true, data, errorCode: null, message: null});
const failure = (errorCode) => ({success: false, data: null, errorCode, message: 'failure'});
const seat = (id, status = 'AVAILABLE') => ({id, section: 'A', rowNumber: 1, seatNumber: id, grade: 'VIP', price: 10000, status});

test('HTTP failure Rate counts use explicit labels without changing checks or raw summary', () => {
  const data = {metrics: {
    http_req_failed: {type: 'rate', values: {rate: 0.2, passes: 2, fails: 8}},
    'http_req_failed{api:seat-map}': {type: 'rate', values: {rate: 0, passes: 0, fails: 6}},
    checks: {type: 'rate', values: {rate: 0.75, passes: 3, fails: 1}},
  }};
  const original = JSON.stringify(data);
  const text = summaryText(data);
  assert.ok(text.includes('Rate metrics: fraction of non-zero samples (0-1)'));
  assert.ok(text.includes('http_req_failed: rate=0.200 failed_requests=2.000 expected_requests=8.000'));
  assert.ok(text.includes('http_req_failed{api:seat-map}: rate=0.000 failed_requests=0.000 expected_requests=6.000'));
  assert.ok(text.includes('checks: rate=0.750 passes=3.000 fails=1.000'));
  assert.equal(JSON.stringify(data), original);
});

test('historical HTTP attempt metric names render consistently without rewriting raw data', () => {
  const data = {metrics: {
    http_offered: {values: {count: 6, rate: 2}},
    'http_offered{api:seat-map}': {values: {count: 1, rate: 1 / 3}},
  }};
  const original = JSON.stringify(data);
  const text = summaryText(data);
  assert.ok(text.includes('http_attempted: count=6.000 rate=2.000'));
  assert.ok(text.includes('http_attempted{api:seat-map}: count=1.000 rate=0.333'));
  assert.ok(!text.includes('http_offered'));
  assert.equal(JSON.stringify(data), original);
});

test('SEAT_UNAVAILABLE is business, even when k6 attaches HTTP error_code 1409', () => {
  assert.equal(classifyResponse('pre-reserve', response(409, failure('SEAT_UNAVAILABLE'), 1409)).outcome, 'business-failure');
  assert.equal(classifyResponse('selection-summary', response(409, failure('SEAT_UNAVAILABLE'), 1409)).outcome, 'system-failure');
  assert.equal(classifyResponse('pre-reserve', response(500, failure('SEAT_UNAVAILABLE'), 1500)).reason, 'http-5xx');
  assert.equal(classifyResponse('pre-reserve', response(409, failure('IDEMPOTENCY_CONFLICT'), 1409)).outcome, 'system-failure');
});

test('transport, authentication, non-JSON and broken 200 contracts are system failures', () => {
  assert.equal(classifyResponse('seat-map', {status: 0, error_code: 1050}).reason, 'timeout');
  assert.equal(classifyResponse('seat-map', {status: 200, error_code: 1050}).reason, 'timeout');
  assert.equal(classifyResponse('seat-map', {status: 0, error_code: 1212}).reason, 'network-error');
  assert.equal(classifyResponse('seat-map', response(403, failure('LOGIN_FAILED'), 1403)).outcome, 'system-failure');
  assert.equal(classifyResponse('seat-map', {status: 200, body: '<html>error</html>'}).reason, 'invalid-json');
  assert.equal(classifyResponse('seat-map', response(200, success([{...seat(1), id: 2**53}]))).reason, 'invalid-data');
  assert.equal(classifyResponse('seat-map', response(200, success([seat(1)]))).outcome, 'success');
});

test('selection summary accepts occupied seats but checks seat identity and total', () => {
  const context = {performanceTimeId: 4, seatIds: [1], totalPrice: 10000};
  const data = {performanceTimeId: 4, seats: [seat(1, 'LOCKED')], totalPrice: 10000};
  assert.equal(classifyResponse('selection-summary', response(200, success(data)), context).outcome, 'success');
  assert.equal(classifyResponse('selection-summary', response(200, success({...data, totalPrice: 1})), context).outcome, 'system-failure');
});

test('confirmed reservation detail has expiredAt null, as Reservation.confirm implements', () => {
  const context = {reservationId: 4, reservationCode: 'code', totalPrice: 10000, seatIds: [1]};
  const data = {id: 4, reservationCode: 'code', totalPrice: 10000, status: 'CONFIRMED', expiredAt: null, seats: [seat(1, 'RESERVED')]};
  assert.equal(classifyResponse('reservation-detail', response(200, success(data)), context).outcome, 'success');
  assert.equal(classifyResponse('reservation-detail', response(200, success({...data, expiredAt: 'stale'})), context).outcome, 'system-failure');
});

test('iteration selects and reuses pool independently of VUs/member cardinality', () => {
  const pool = validateTokenPool([{memberId: 1, accessToken: 'token-1'}, {memberId: 2, accessToken: 'token-2'}]);
  assert.deepEqual(Array.from({length: 6}, (_, iteration) => selectToken(pool, iteration)), ['token-1', 'token-2', 'token-1', 'token-2', 'token-1', 'token-2']);
  const single = validateTokenPool([{accessToken: 'token-1'}]);
  assert.equal(selectToken(single, 1000), 'token-1');
  assert.throws(() => validateTokenPool([]), /nonempty/);
  assert.throws(() => validateTokenPool([{accessToken: 'same'}, {accessToken: 'same'}]), /Duplicate/);
});

test('known expired/refund outcome is business; PortOne cannot complete this server-only journey', () => {
  const context = {paymentOrderId: 10, reservationId: 5};
  const data = {...context, paymentStatus: 'REFUND_PENDING', reservationStatus: 'EXPIRED', providerTransactionId: 'tx'};
  const result = classifyResponse('payment-verify', response(200, success(data)), context);
  assert.equal(result.outcome, 'business-failure');
  assert.equal(result.errorCode, null);
  assert.equal(result.reason, 'expired-refund-pending');
  const prepared = {paymentOrderId: 10, merchantOrderId: 'order', amount: 10000, currency: 'KRW', provider: 'PORTONE', providerPaymentId: 'order'};
  assert.equal(classifyResponse('payment-prepare', response(200, success(prepared)), {totalPrice: 10000}).reason, 'unsupported-payment-provider');
});

test('smoke users select disjoint slots; stale slots fail rather than silently changing the fixture', () => {
  const map = Array.from({length: 6}, (_, i) => seat(i + 1));
  assert.deepEqual(selectSmokeSeats(map, 0, 3).seats.map((s) => s.id), [1, 2, 3]);
  assert.deepEqual(selectSmokeSeats(map, 1, 3).seats.map((s) => s.id), [4, 5, 6]);
  map[0].status = 'RESERVED';
  assert.throws(() => selectSmokeSeats(map, 0, 1), /SMOKE_FIXTURE_UNAVAILABLE/);
});

test('all profile probabilities validate and observed selection follows the configured distribution', () => {
  const profiles = JSON.parse(readFileSync(new URL('../config/profiles.json', import.meta.url)));
  assert.equal(Object.keys(profiles).length, 10);
  for (const profile of Object.values(profiles)) validateProfile(profile);
  const profile = profiles['idol-high'];
  const map = Array.from({length: profile.seatCount}, (_, i) => seat(i + 1, 'RESERVED'));
  const random = seededRandom(20261011);
  const totals = [0, 0, 0];
  let hot = 0;
  for (let iteration = 0; iteration < 10000; iteration++) {
    const chosen = selectWorkloadSeats(map, profile, random);
    totals[chosen.count - 1]++;
    assert.equal(new Set(chosen.seats.map((s) => s.id)).size, chosen.count);
    if (chosen.region === 'hot') {
      hot++;
      assert.ok(chosen.seats.every((s) => s.id <= 300));
    } else assert.ok(chosen.seats.every((s) => s.id > 300));
  }
  assert.ok(Math.abs(hot / 10000 - profile.hotTrafficRatio) < 0.015);
  totals.forEach((count, i) => assert.ok(Math.abs(count / 10000 - profile.seatSelectionRatio[i]) < 0.015));
  assert.throws(() => selectWorkloadSeats(map.slice(1), profile, random), /PROFILE_SEAT_COUNT_MISMATCH/);
});
