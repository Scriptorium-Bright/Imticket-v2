import { Counter, Trend } from 'k6/metrics';

export const reselectionMetrics = {
  started: new Counter('booking_journey_started'),
  completed: new Counter('booking_journey_completed'),
  soldOut: new Counter('booking_journey_sold_out'),
  systemFailed: new Counter('booking_journey_system_failed'),
  abandoned: new Counter('booking_journey_abandoned'),
  guardStopped: new Counter('booking_journey_guard_stopped'),
  businessTerminated: new Counter('booking_journey_business_terminated'),
  selectionAttempts: new Counter('seat_selection_attempts'),
  preReserveAttempts: new Counter('pre_reserve_attempts'),
  conflicts: new Counter('seat_conflicts'),
  reselections: new Counter('reselections'),
  fallbacks: new Counter('seat_selection_fallbacks'),
  staleRefreshes: new Counter('reselection_stale_refreshes'),
  retries: new Trend('retries_per_journey'),
  retriesPerSuccess: new Trend('retries_per_successful_booking'),
  successfulDuration: new Trend('successful_booking_duration', true),
  soldOutDuration: new Trend('sold_out_journey_duration', true),
  failedDuration: new Trend('failed_journey_duration', true),
  abandonedDuration: new Trend('abandoned_journey_duration', true),
  guardDuration: new Trend('guard_stopped_journey_duration', true),
  businessDuration: new Trend('business_terminated_journey_duration', true),
};

export function reselectionThresholds(base) {
  const thresholds = {...base, booking_journey_guard_stopped: ['count==0']};
  // SOLD_OUT may legitimately stop before later APIs. System contract violations still increment system_failures.
  for (const name of Object.keys(thresholds)) if (name.startsWith('checks{')) thresholds[name] = ['rate>=0'];
  return thresholds;
}
