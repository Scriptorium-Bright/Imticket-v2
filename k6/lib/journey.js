import { check, group } from 'k6';
import exec from 'k6/execution';
import { ApiFailure, bookingApi } from './api.js';
import { metrics } from './metrics.js';

export function bookingJourney(config, accessToken, chooseSeats) {
  return group('booking-journey', () => {
    const started = Date.now();
    let outcome = 'system-failure';
    let reason = 'client-exception';
    let stage = 'seat-map';
    metrics.journeyStarted.add(1);
    // Initialize zero-valued counters so successful smoke reports contain explicit zeroes.
    metrics.businessFailures.add(0);
    metrics.systemFailures.add(0);
    metrics.reservationSuccess.add(0);
    metrics.paymentApplied.add(0);
    metrics.bookingCompleted.add(0);
    metrics.seatsConfirmed.add(0);
    const api = bookingApi(config, accessToken);
    const identity = `${config.runId}-${exec.vu.idInTest}-${exec.scenario.iterationInTest}`;
    try {
      const seatMap = api.seatMap();
      metrics.observedSeatCount.add(seatMap.length);
      const selection = chooseSeats(seatMap);
      metrics.seatChoices.add(1, {region: selection.region, seats: String(selection.count)});
      const body = {performanceTimeId: config.performanceTimeId, seatIds: selection.seats.map((s) => s.id)};
      const context = {...body, totalPrice: selection.seats.reduce((sum, s) => sum + s.price, 0)};
      stage = 'selection-summary';
      api.selectionSummary(body, context);
      stage = 'pre-reserve';
      const reservation = api.preReserve(body, context, `reserve-${identity}`);
      metrics.reservationSuccess.add(1);
      context.reservationId = reservation.id;
      context.reservationCode = reservation.reservationCode;
      stage = 'payment-prepare';
      const prepared = api.prepare(reservation.id, context, `payment-${identity}`);
      context.paymentOrderId = prepared.paymentOrderId;
      stage = 'payment-verify';
      api.verify(prepared.paymentOrderId, prepared.providerPaymentId, context);
      metrics.paymentApplied.add(1);
      stage = 'reservation-detail';
      api.detail(reservation.id, context);
      outcome = 'success';
      reason = 'success';
      metrics.bookingCompleted.add(1);
      metrics.seatsConfirmed.add(context.seatIds.length);
      metrics.bookingDuration.add(Date.now() - started);
    } catch (error) {
      if (error instanceof ApiFailure) {
        outcome = error.outcome;
        reason = error.reason;
        stage = error.api;
      } else if (String(error.message).startsWith('SMOKE_FIXTURE_UNAVAILABLE')) {
        reason = 'smoke-fixture-unavailable';
        stage = 'seat-selection';
      }
      const tags = {api: stage, reason};
      if (outcome === 'business-failure') {
        // Expected business outcomes remain in metrics/raw reason tags, without per-iteration logs.
        metrics.businessFailures.add(1, tags);
      } else {
        metrics.systemFailures.add(1, tags);
        // No response body, token, username, or raw URL in logs.
        console.error(`Journey failed: ${stage}, ${outcome}, ${reason}`);
      }
    } finally {
      metrics.journeyFinished.add(1, {outcome, reason});
      metrics.journeyDuration.add(Date.now() - started, {outcome});
      check(outcome, {'booking journey completed': (value) => value === 'success'}, {api: 'journey'});
    }
  });
}
