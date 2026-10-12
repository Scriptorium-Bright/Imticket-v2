import assert from 'node:assert/strict';
import test from 'node:test';
import { desiredSeatCount, selectAvailableSeats, seatSetKey } from '../lib/reselection-seats.js';
import { runReselection, thinkTimeSeconds } from '../lib/reselection.js';
import { seededRandom, selectWorkloadSeats } from '../lib/seats.js';

const profile = {seatCount: 10, referenceAttemptors: 50, peakJourneyArrivalRate: 2,
  hotSeatRatio: .2, hotTrafficRatio: .35, seatSelectionRatio: [.6, .3, .1]};
const map = (available = Array.from({length: 10}, (_, i) => i + 1)) => Array.from({length: 10}, (_, i) => ({id: i + 1,
  status: available.includes(i + 1) ? 'AVAILABLE' : 'RESERVED', price: 10000}));
const policy = () => ({thinkTime: {enabled: false}, abandonment: {enabled: false, maxReselections: null, maxDuration: null},
  safetyGuard: {maxJourneyDuration: '60s'}});
const conflict = () => ({api: 'pre-reserve', outcome: 'business-failure', reason: 'seat-unavailable', errorCode: 'SEAT_UNAVAILABLE'});

function exercise({maps = [map()], mapDelay = 10, fail, verifyFailure, settings = policy(), chosenProfile = profile, random = () => .7, observe = null} = {}) {
  let clock = 0, reads = 0, reserves = 0;
  const calls = [], events = [], keys = [], counts = [], selections = [];
  const api = {
    seatMap() { clock += mapDelay; calls.push('map'); return maps[Math.min(reads++, maps.length - 1)]; },
    selectionSummary(body) { calls.push('summary'); counts.push(body.seatIds.length); selections.push(body.seatIds); },
    preReserve(body, context, key) { calls.push('reserve'); keys.push(key); if (fail) fail(++reserves); return {id: 1, reservationCode: 'code'}; },
    prepare(id, context, key) { calls.push('prepare'); keys.push(key); return {paymentOrderId: 2, providerPaymentId: 'fake'}; },
    verify() { calls.push('verify'); if (verifyFailure) throw verifyFailure; }, detail() { calls.push('detail'); },
  };
  const result = runReselection({api, profile: chosenProfile, policy: settings, random, identity: 'run-0', performanceTimeId: 9,
    now: () => clock, sleep: (s) => {clock += s * 1000;}, emit: (event, data) => events.push([event, data]), observe});
  return {result, calls, events, keys, counts, selections, reads};
}

test('first Reselection choice matches Single Attempt with an all-AVAILABLE fixture', () => {
  for (let seed = 1; seed < 100; seed++) {
    const p = {...profile, hotSeatRatio: .4};
    const random = seededRandom(seed), count = desiredSeatCount(p, random);
    const a = selectAvailableSeats(map(), p, count, random);
    const b = selectWorkloadSeats(map(), p, seededRandom(seed));
    assert.deepEqual(a.seats, b.seats);
    assert.equal(a.region, b.region);
  }
});

test('optional diagnostic observer preserves API order, fixed seat count and conflict-to-booking outcome', () => {
  const settings = {maps: [map(), map([1, 2, 3, 4])], fail: n => {if (n === 1) throw conflict();}};
  const events = [], original = exercise(settings);
  const traced = exercise({...settings, observe: (state, details) => events.push({state, ...details})});
  assert.deepEqual(traced, original);
  assert.equal(events.filter(e => e.state === 'engine.conflict-retry-pending').length, 1);
  const refresh = events.find(e => e.state === 'engine.checkpoint' && e.checkpoint === 'loop-entry' && e.refresh);
  assert.equal(refresh.api, 'pre-reserve'); // Previous API name, not an in-flight HTTP request.
  assert.equal(events.at(-1).outcome, 'BOOKING_COMPLETED');
  assert.equal(events.at(-1).requestedSeatCount, 2);
});

test('Single Attempt still selects occupied seats; Reselection uses server AVAILABLE seats', () => {
  const unavailable = map([]);
  assert.ok(selectWorkloadSeats(unavailable, profile, seededRandom(1)).seats.every(s => s.status === 'RESERVED'));
  assert.equal(selectAvailableSeats(unavailable, profile, 1, seededRandom(1)).outcome, 'SOLD_OUT');
});

test('conflict refreshes server map, preserves desired two seats and uses new idempotency keys', () => {
  const e = exercise({maps: [map(), map([1, 2, 3, 4])], fail: (n) => {if (n === 1) throw conflict();}});
  assert.equal(e.result.outcome, 'BOOKING_COMPLETED');
  assert.equal(e.result.retries, 1);
  assert.deepEqual(e.counts, [2, 2]);
  assert.deepEqual(e.calls, ['map', 'summary', 'reserve', 'map', 'summary', 'reserve', 'prepare', 'verify', 'detail']);
  assert.ok(e.selections[1].every(id => id <= 4));
  assert.equal(new Set(e.keys).size, e.keys.length);
  assert.equal(e.events.filter(([name]) => name === 'conflict').length, 1);
  assert.equal(e.events.filter(([name]) => name === 'reserved').length, 1);
});

test('region shortage falls back to other then whole map and never shrinks the requested N', () => {
  const p = {...profile, hotTrafficRatio: 1};
  const cold = selectAvailableSeats(map([3, 4, 5]), p, 3, () => 0);
  assert.equal(cold.region, 'cold'); assert.equal(cold.requestedRegion, 'hot'); assert.equal(cold.fallback, true);
  const mixed = selectAvailableSeats(map([1, 3, 4]), p, 3, () => 0);
  assert.equal(mixed.region, 'mixed'); assert.equal(mixed.seats.length, 3);
  assert.deepEqual(mixed.seats.map(s => s.id), [1, 3, 4]);
});

test('three-seat Journey with only two AVAILABLE seats is SOLD_OUT without pre-reserve', () => {
  const e = exercise({maps: [map([1, 2])], random: () => .99});
  assert.equal(e.result.outcome, 'SOLD_OUT'); assert.equal(e.result.requestedSeatCount, 3);
  assert.deepEqual(e.calls, ['map']);
});

test('a previous conflicting set is replaced, input map is immutable and hot boundary stays fixed', () => {
  const seats = map([2, 3, 4, 5]), original = JSON.stringify(seats);
  const p = {...profile, hotTrafficRatio: 1};
  const a = selectAvailableSeats(seats, p, 1, () => 0);
  assert.equal(a.region, 'hot'); assert.deepEqual(a.seats.map(s => s.id), [2]);
  const b = selectAvailableSeats(seats, p, 1, () => 0, seatSetKey(a.seats));
  assert.equal(b.region, 'cold'); assert.ok(b.fallback); assert.notEqual(seatSetKey(b.seats), seatSetKey(a.seats));
  assert.equal(JSON.stringify(seats), original);
});

test('only stale conflicting N-seat set refreshes until technical guard, not false SOLD_OUT', () => {
  const settings = policy(); settings.safetyGuard.maxJourneyDuration = '50ms';
  const e = exercise({maps: [map([3, 4])], fail: () => {throw conflict();}, settings});
  assert.equal(e.result.outcome, 'SAFETY_GUARD');
  assert.equal(e.calls.filter(s => s === 'reserve').length, 1);
  assert.ok(e.events.some(([name]) => name === 'stale_refresh'));
});

test('extended technical guard lets a conflict Journey finish after the old 60-second bound', () => {
  const settings = policy(); settings.safetyGuard.maxJourneyDuration = '10m';
  const e = exercise({maps: [map(), map([1, 2, 3, 4])], mapDelay: 31000,
    fail: n => {if (n === 1) throw conflict();}, settings});
  assert.equal(e.result.outcome, 'BOOKING_COMPLETED');
  assert.equal(e.result.duration, 62000);
  assert.equal(e.result.retries, 1);
  assert.deepEqual(e.counts, [2, 2]);
  assert.deepEqual(e.calls, ['map', 'summary', 'reserve', 'map', 'summary', 'reserve', 'prepare', 'verify', 'detail']);
});

test('5xx, timeout, network, contract and unexpected business errors are terminal System Failure', () => {
  for (const reason of ['http-5xx', 'timeout', 'network-error', 'invalid-data', 'unexpected-response']) {
    const e = exercise({fail: () => {throw {api: 'pre-reserve', outcome: 'system-failure', reason};}});
    assert.equal(e.result.outcome, 'SYSTEM_FAILURE'); assert.equal(e.result.reason, reason);
    assert.equal(e.reads, 1); assert.equal(e.result.retries, 0);
  }
  const e = exercise({fail: () => {throw {...conflict(), errorCode: 'UNEXPECTED'};}});
  assert.equal(e.result.outcome, 'SYSTEM_FAILURE');
});

test('no user retry limit is implied; optional explicit abandonment is separately recorded', () => {
  const e = exercise({fail: n => {if (n <= 4) throw conflict();}});
  assert.equal(e.result.outcome, 'BOOKING_COMPLETED'); assert.equal(e.result.retries, 4);
  const settings = policy(); settings.abandonment = {enabled: true, maxReselections: 0, maxDuration: null};
  const abandoned = exercise({settings, fail: () => {throw conflict();}});
  assert.equal(abandoned.result.outcome, 'USER_ABANDONED'); assert.equal(abandoned.reads, 1);
});

test('known payment expiry remains a separate terminal business outcome and never retries seats', () => {
  const e = exercise({verifyFailure: {api: 'payment-verify', outcome: 'business-failure', reason: 'expired-refund-pending'}});
  assert.equal(e.result.outcome, 'BUSINESS_TERMINATED'); assert.equal(e.result.retries, 0);
  assert.equal(e.reads, 1); assert.ok(!e.calls.includes('detail'));
  assert.ok(e.events.some(([name]) => name === 'reserved'));
  assert.ok(!e.events.some(([name]) => name === 'payment_applied'));
});

test('think time defaults off and is bounded by the technical guard when enabled', () => {
  assert.equal(thinkTimeSeconds({enabled: false}, () => {throw Error();}), 0);
  assert.equal(thinkTimeSeconds({enabled: true, distribution: 'uniform', min: '0s', max: '2s'}, () => .25), .5);
  const settings = policy(); settings.thinkTime = {enabled: true, distribution: 'constant', duration: '1h'};
  const e = exercise({settings, fail: () => {throw conflict();}});
  assert.equal(e.result.outcome, 'SAFETY_GUARD'); assert.equal(e.result.duration, 60000);
});

test('profile seat-count and requested-region distributions remain configured when eligible', () => {
  const random = seededRandom(20261011), totals = [0, 0, 0]; let hot = 0;
  for (let i = 0; i < 10000; i++) {
    const count = desiredSeatCount(profile, random); totals[count - 1]++;
    const choice = selectAvailableSeats(map(), profile, count, random);
    if (choice.requestedRegion === 'hot') hot++;
    assert.equal(choice.seats.length, count);
    if (!choice.fallback) assert.equal(choice.region, choice.requestedRegion);
  }
  assert.ok(Math.abs(hot / 10000 - .35) < .02);
  totals.forEach((n, i) => assert.ok(Math.abs(n / 10000 - profile.seatSelectionRatio[i]) < .02));
});
