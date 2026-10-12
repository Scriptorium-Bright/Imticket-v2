// One deterministic server-side race. Not a performance workload.
import { group } from 'k6';
import exec from 'k6/execution';
import { Counter } from 'k6/metrics';
import { pilotConfig } from './lib/config.js';
import { bookingApi } from './lib/api.js';
import { reselectionJourney } from './lib/reselection-journey.js';
import { reselectionThresholds } from './lib/reselection-metrics.js';
import { seededRandom } from './lib/seats.js';
import { pilotMetrics, pilotThresholds } from './lib/pilot-metrics.js';
import { saveSummary } from './lib/summary.js';

const base = pilotConfig();
const validation = JSON.parse(open(__ENV.PILOT_CONFIG_FILE)).validationScenario;
if (base.workloadModel !== 'reselection' || base.phase !== 'journey-smoke'
    || base.scenario.vus !== 1 || base.scenario.iterations !== 1 || base.tokenPool.length < 2
    || validation?.type !== 'controlled-seat-conflict') throw new Error('Use reselection.sh validation');
const config = {...base, kind: 'reselection-validation', profile: {...base.profile, ...validation.profileOverrides}};
const validationEvents = new Counter('validation_events');

export const options = {
  scenarios: {[config.phase]: config.scenario},
  thresholds: {...reselectionThresholds(pilotThresholds(config.phase)),
    booking_journey_started: ['count==1'], booking_journey_completed: ['count==1'],
    seat_conflicts: ['count==1'], reselections: ['count==1'], pre_reserve_attempts: ['count==2'],
    business_failures: ['count==0']},
  summaryTrendStats: ['count', 'avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
  summaryTimeUnit: 'ms',
  systemTags: ['status', 'method', 'name', 'scenario', 'expected_response', 'error_code', 'group', 'check'],
  tags: {run_id: config.runId, profile: config.profileName, phase: config.phase,
    workload_model: 'reselection', experiment_scope: 'controlled-validation'},
};

export default function () {
  pilotMetrics.iterationStarted.add(1);
  pilotMetrics.scenarioStart.add(exec.scenario.startTime);
  const subject = bookingApi(config, config.tokenPool[0].accessToken);
  const holder = bookingApi(config, config.tokenPool[1].accessToken);
  let injected = false, lastMap = [], target = null;
  // Raw points transport this tiny trace across k6 VU/summary contexts; no VU-shared inventory.
  const record = (event, data = {}) => validationEvents.add(1, {event, details: JSON.stringify(data)});
  const api = {...subject,
    seatMap() {
      lastMap = subject.seatMap();
      if (target === null) target = [...lastMap].sort((a, b) => a.id - b.id)[0].id;
      record('seat-map', {target_seat_id: target, target_status: lastMap.find((s) => s.id === target)?.status,
        available_seat_count: lastMap.filter((s) => s.status === 'AVAILABLE').length});
      return lastMap;
    },
    selectionSummary(body, context) {
      record('seat-selection', {seat_ids: body.seatIds,
        statuses: body.seatIds.map((id) => lastMap.find((s) => s.id === id)?.status)});
      return subject.selectionSummary(body, context);
    },
    preReserve(body, context, key) {
      if (!injected) {
        // Hold the selected seat AFTER the subject's real map/summary, BEFORE its real pre-reserve.
        const held = group('validation-conflict-holder', () => {
          holder.selectionSummary(body, context);
          return holder.preReserve(body, context, `reserve-${config.runId}-validation-holder`);
        });
        injected = true;
        record('holder-pre-reserved', {seat_ids: body.seatIds, reservation_id: held.id});
      }
      try {
        const reservation = subject.preReserve(body, context, key);
        record('subject-pre-reserved', {seat_ids: body.seatIds, reservation_id: reservation.id});
        return reservation;
      } catch (error) {
        record('pre-reserve-response', {seat_ids: body.seatIds, outcome: error.outcome,
          reason: error.reason, code: error.errorCode || null});
        throw error;
      }
    },
  };
  const result = reselectionJourney(config, config.tokenPool[0].accessToken, seededRandom(config.randomSeed), api);
  record('journey-ended', result);
}

export function teardown() { pilotMetrics.workloadEnd.add(Date.now()); }
export function handleSummary(data) {
  return saveSummary(data, config);
}
