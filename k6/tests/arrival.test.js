import assert from 'node:assert/strict';
import test from 'node:test';
import {durationSeconds, validateArrival, iterationSeed} from '../lib/arrival.js';
import {seededRandom, selectWorkloadSeats} from '../lib/seats.js';

const scenario = () => ({executor: 'ramping-arrival-rate', startRate: 0, timeUnit: '2s',
  stages: [{duration: '1m30s', target: 2}, {duration: '30s', target: 0}], preAllocatedVUs: 3, maxVUs: 3, gracefulStop: '0s'});

test('arrival settings accept zero start/down-ramp/drain and preserve timeUnit', () => {
  assert.equal(validateArrival(scenario()).timeUnit, '2s');
  assert.equal(durationSeconds('1m30s'), 90);
  assert.equal(durationSeconds('500ms'), .5);
  for (const change of [{maxVUs: 2}, {startRate: -1}, {preAllocatedVUs: 0}, {timeUnit: '0s'}, {stages: []}, {stages: [{duration: '1s', target: null}]}]) {
    assert.throws(() => validateArrival({...scenario(), ...change}));
  }
});

test('iteration seed reproduces seat positions across fixture IDs and VU allocation', () => {
  const profile = {seatCount: 1000, referenceAttemptors: 500, peakJourneyArrivalRate: 20,
    hotSeatRatio: .2, hotTrafficRatio: .35, seatSelectionRatio: [.6, .3, .1]};
  const map = (offset) => Array.from({length: 1000}, (_, i) => ({id: offset + i, status: 'RESERVED'}));
  const choose = (offset, iteration) => selectWorkloadSeats(map(offset), profile, seededRandom(iterationSeed(20261011, iteration))).seats.map((s) => s.id - offset);
  assert.deepEqual(choose(1, 12), choose(10001, 12));
  assert.notDeepEqual(choose(1, 12), choose(1, 13));
});
