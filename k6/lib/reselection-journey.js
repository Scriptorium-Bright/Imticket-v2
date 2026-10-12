import { check, group, sleep } from 'k6';
import exec from 'k6/execution';
import { bookingApi } from './api.js';
import { metrics } from './metrics.js';
import { reselectionMetrics as r } from './reselection-metrics.js';
import { runReselection } from './reselection.js';
import { harnessTrace } from './harness-trace.js';

export function reselectionJourney(config, accessToken, random, apiOverride = null) {
  return group('booking-journey', () => {
    if (harnessTrace) harnessTrace('journey.initial-metrics');
    metrics.journeyStarted.add(1);
    for (const name of ['businessFailures', 'systemFailures', 'reservationSuccess', 'paymentApplied', 'bookingCompleted', 'seatsConfirmed']) metrics[name].add(0);
    for (const metric of [r.completed, r.soldOut, r.systemFailed, r.abandoned, r.guardStopped, r.businessTerminated,
      r.selectionAttempts, r.preReserveAttempts, r.conflicts, r.reselections, r.fallbacks, r.staleRefreshes]) metric.add(0);
    r.started.add(1);
    const result = runReselection({api: apiOverride || bookingApi(config, accessToken, harnessTrace), profile: config.profile,
      policy: config.reselection, random, identity: `${config.runId}-${exec.scenario.iterationInTest}`,
      performanceTimeId: config.performanceTimeId, sleep, observe: harnessTrace,
      emit(event, value) {
        if (harnessTrace) harnessTrace(`engine.emit.${event}`);
        if (event === 'seat_map') metrics.observedSeatCount.add(value.count);
        else if (event === 'selection_attempt') r.selectionAttempts.add(1);
        else if (event === 'pre_reserve_attempt') r.preReserveAttempts.add(1);
        else if (event === 'conflict') r.conflicts.add(1, {api: 'pre-reserve', reason: 'seat-unavailable'});
        else if (event === 'reselection') r.reselections.add(1);
        else if (event === 'stale_refresh') r.staleRefreshes.add(1);
        else if (event === 'selection') {
          metrics.seatChoices.add(1, {region: value.region, requested_region: value.requestedRegion, seats: String(value.count)});
          if (value.fallback) r.fallbacks.add(1, {requested_region: value.requestedRegion, selected_region: value.region});
        } else if (event === 'reserved') metrics.reservationSuccess.add(1);
        else if (event === 'payment_applied') metrics.paymentApplied.add(1);
        else if (event === 'booking_completed') {
          metrics.bookingCompleted.add(1);
          metrics.seatsConfirmed.add(value.count);
        }
      }});
    const {outcome, reason, stage, duration} = result;
    const tags = {api: stage, reason};
    const endings = {
      BOOKING_COMPLETED: [r.completed, r.successfulDuration, 'success'],
      SOLD_OUT: [r.soldOut, r.soldOutDuration, 'sold-out'],
      SYSTEM_FAILURE: [r.systemFailed, r.failedDuration, 'system-failure'],
      USER_ABANDONED: [r.abandoned, r.abandonedDuration, 'user-abandoned'],
      SAFETY_GUARD: [r.guardStopped, r.guardDuration, 'safety-guard'],
      BUSINESS_TERMINATED: [r.businessTerminated, r.businessDuration, 'business-terminated'],
    };
    const [counter, trend, label] = endings[outcome];
    if (harnessTrace) harnessTrace('finalize.terminal-counter', {outcome, reason, api: stage});
    counter.add(1, tags);
    if (harnessTrace) harnessTrace('finalize.terminal-duration');
    trend.add(duration);
    if (harnessTrace) harnessTrace('finalize.retries');
    r.retries.add(result.retries, {outcome: label});
    if (harnessTrace) harnessTrace('finalize.outcome-metrics-and-log');
    if (outcome === 'BOOKING_COMPLETED') {
      r.retriesPerSuccess.add(result.retries);
      metrics.bookingDuration.add(duration);
    } else if (outcome === 'SYSTEM_FAILURE') {
      metrics.systemFailures.add(1, tags);
      console.error(`Journey failed: ${stage}, system-failure, ${reason}`);
    } else if (outcome === 'BUSINESS_TERMINATED') metrics.businessFailures.add(1, tags);
    else if (outcome === 'SAFETY_GUARD') console.error(`Journey safety guard reached: ${stage}, ${reason}`);
    if (harnessTrace) harnessTrace('finalize.journey-finished');
    metrics.journeyFinished.add(1, {outcome: label, reason});
    if (harnessTrace) harnessTrace('finalize.journey-duration');
    metrics.journeyDuration.add(duration, {outcome: label});
    if (harnessTrace) harnessTrace('finalize.check');
    check(outcome, {'booking journey reached a normal terminal outcome': (v) => !['SYSTEM_FAILURE', 'SAFETY_GUARD'].includes(v)}, {api: 'journey'});
    if (harnessTrace) harnessTrace('finalize.return');
    return result;
  });
}
