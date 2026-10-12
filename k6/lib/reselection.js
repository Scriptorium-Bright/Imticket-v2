// Pure behavior engine, shared by k6 and unit tests. No VU-shared inventory state.
import { desiredSeatCount, selectAvailableSeats, seatSetKey } from './reselection-seats.js';
import { durationSeconds } from './arrival.js';

export function thinkTimeSeconds(policy, random) {
  if (!policy.enabled) return 0;
  if (policy.distribution === 'constant') return durationSeconds(policy.duration, true);
  if (policy.distribution === 'uniform') {
    const min = durationSeconds(policy.min, true), max = durationSeconds(policy.max, true);
    return min + random() * (max - min);
  }
  throw new Error('INVALID_THINK_TIME_POLICY');
}

export function runReselection({api, profile, policy, random, identity, performanceTimeId,
  now = Date.now, sleep = () => {}, emit = () => {}, observe = null}) {
  const started = now(), count = desiredSeatCount(profile, random);
  const guardMs = durationSeconds(policy.safetyGuard.maxJourneyDuration) * 1000;
  let stage = 'seat-map', previousKey = null, retries = 0, attempts = 0, refresh = false;
  const trace = (state, details = {}) => {
    if (observe) observe(state, {api: stage, retries, attempts, requestedSeatCount: count, refresh, ...details});
  };
  const result = (outcome, reason) => {
    const value = {outcome, reason, stage, retries, requestedSeatCount: count, duration: now() - started};
    trace('engine.result', {outcome, reason});
    return value;
  };
  const checkpoint = (location) => {
    trace('engine.checkpoint', {checkpoint: location});
    if (now() - started >= guardMs) throw {termination: 'SAFETY_GUARD', reason: 'journey-time-guard'};
    const abandon = policy.abandonment;
    if (abandon.enabled && abandon.maxDuration !== null && now() - started >= durationSeconds(abandon.maxDuration) * 1000) {
      throw {termination: 'USER_ABANDONED', reason: 'user-duration-limit'};
    }
  };
  const call = (name, fn) => {
    stage = name; checkpoint('before-api');
    trace('engine.api-call');
    const value = fn();
    trace('engine.api-returned');
    return value;
  };
  try {
    while (true) {
      checkpoint('loop-entry');
      if (refresh) {
        if (policy.abandonment.enabled && policy.abandonment.maxReselections !== null && retries >= policy.abandonment.maxReselections) {
          return result('USER_ABANDONED', 'user-reselection-limit');
        }
        const pause = thinkTimeSeconds(policy.thinkTime, random);
        if (pause) {
          const limits = [(guardMs - (now() - started)) / 1000];
          if (policy.abandonment.enabled && policy.abandonment.maxDuration !== null) {
            limits.push(durationSeconds(policy.abandonment.maxDuration) - (now() - started) / 1000);
          }
          sleep(Math.max(0, Math.min(pause, ...limits)));
        }
        checkpoint('before-reselection');
        retries++;
        emit('reselection');
      }
      const map = call('seat-map', () => api.seatMap());
      emit('seat_map', {count: map.length});
      emit('selection_attempt');
      trace('engine.select-seats');
      const selection = selectAvailableSeats(map, profile, count, random, previousKey);
      trace('engine.selected-seats');
      if (selection.outcome === 'SOLD_OUT') return result('SOLD_OUT', selection.reason);
      if (selection.outcome === 'REFRESH_REQUIRED') {
        emit('stale_refresh');
        refresh = true;
        continue;
      }
      emit('selection', selection);
      const body = {performanceTimeId, seatIds: selection.seats.map((s) => s.id)};
      const context = {...body, totalPrice: selection.seats.reduce((sum, s) => sum + s.price, 0)};
      call('selection-summary', () => api.selectionSummary(body, context));
      let reservation;
      try {
        reservation = call('pre-reserve', () => {
          attempts++;
          emit('pre_reserve_attempt');
          return api.preReserve(body, context, `reserve-${identity}-a${attempts}`);
        });
      } catch (error) {
        if (error.api === 'pre-reserve' && error.outcome === 'business-failure'
            && error.reason === 'seat-unavailable' && error.errorCode === 'SEAT_UNAVAILABLE') {
          emit('conflict');
          previousKey = seatSetKey(selection.seats);
          refresh = true;
          trace('engine.conflict-retry-pending');
          continue;
        }
        throw error;
      }
      emit('reserved');
      context.reservationId = reservation.id;
      context.reservationCode = reservation.reservationCode;
      const prepared = call('payment-prepare', () => api.prepare(reservation.id, context, `payment-${identity}-a${attempts}`));
      context.paymentOrderId = prepared.paymentOrderId;
      call('payment-verify', () => api.verify(prepared.paymentOrderId, prepared.providerPaymentId, context));
      emit('payment_applied');
      call('reservation-detail', () => api.detail(reservation.id, context));
      emit('booking_completed', {count});
      return result('BOOKING_COMPLETED', 'success');
    }
  } catch (error) {
    if (error.termination) return result(error.termination, error.reason);
    if (error.api) stage = error.api;
    // A real known payment-expiry business response is terminal, not a seat conflict or a server error.
    if (error.api === 'payment-verify' && error.outcome === 'business-failure' && error.reason === 'expired-refund-pending') {
      return result('BUSINESS_TERMINATED', error.reason);
    }
    return result('SYSTEM_FAILURE', error.outcome === 'business-failure' ? 'unexpected-nonretryable-response' : error.reason || 'client-exception');
  }
}
