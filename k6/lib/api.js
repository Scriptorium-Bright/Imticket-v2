import http from 'k6/http';
import { check } from 'k6';
import { classifyResponse, transportFailure } from './contracts.js';
import { metrics } from './metrics.js';

export class ApiFailure extends Error {
  constructor(api, result, status) {
    super(`${api}: ${result.outcome} (${result.reason}, status=${status || 0})`);
    this.api = api;
    this.outcome = result.outcome;
    this.reason = result.reason;
    this.errorCode = result.errorCode;
  }
}

export function request(config, accessToken, api, method, path, body, context = {}, key = null, observe = null, firstRequest = false) {
  const trace = (state, details = {}) => { if (observe) observe(state, {api, ...details}); };
  trace('http.initial-metrics');
  const headers = {Authorization: `Bearer ${accessToken}`};
  if (body !== null) headers['Content-Type'] = 'application/json';
  if (key) headers['Idempotency-Key'] = key;
  const params = {
    headers,
    tags: {api, name: `${method} ${api}`},
    redirects: 0,
    // http_req_failed is an HTTP status metric; SEAT_UNAVAILABLE is classified below.
    responseCallback: http.expectedStatuses(200),
  };
  if (config.requestTimeout) params.timeout = config.requestTimeout;
  metrics.httpAttempted.add(1, {api});
  metrics.httpCompleted.add(0, {api});
  metrics.httpSuccess.add(0, {api});
  metrics.apiBusinessFailures.add(0, {api});
  metrics.apiSystemFailures.add(0, {api});
  let response;
  try {
    const payload = body === null ? null : JSON.stringify(body);
    trace('http.dispatch');
    // One timestamped client marker per Journey, immediately before the first HTTP call.
    // This is client dispatch intent, not a socket-write or server-receipt timestamp.
    if (firstRequest) metrics.firstRequestStarted.add(1, {api});
    response = http.request(method, config.baseUrl + path, payload, params);
    trace('http.returned', {status: response.status, error_code: response.error_code || 0});
  } catch (_) {
    trace('http.exception');
    metrics.apiSystemFailures.add(1, {api, reason: 'request-exception', error_code: 'none'});
    check(false, {[`${api} contract`]: (ok) => ok}, {api});
    throw new ApiFailure(api, {outcome: 'system-failure', reason: 'request-exception'}, 0);
  }
  // Received HTTP response, including 4xx/5xx, excluding transport errors/status=0.
  trace('http.response-metrics');
  if (response.status > 0 && !transportFailure(response)) metrics.httpCompleted.add(1, {api});
  trace('http.classify-response');
  const result = classifyResponse(api, response, context);
  trace('http.classified-response', {outcome: result.outcome, reason: result.reason});
  const tags = {
    api, reason: result.reason, code: result.errorCode || 'none',
    status: String(response.status), error_code: String(response.error_code || 'none'),
  };
  // Expected business outcomes pass the API contract check; the smoke journey must still complete.
  check(result, {[`${api} contract`]: (r) => r.outcome !== 'system-failure'}, {api});
  if (result.outcome === 'success') {
    metrics.httpSuccess.add(1, {api});
    return result.data;
  }
  if (result.outcome === 'business-failure') metrics.apiBusinessFailures.add(1, tags);
  else metrics.apiSystemFailures.add(1, tags);
  throw new ApiFailure(api, result, response.status);
}

export function bookingApi(config, accessToken, observe = null) {
  let firstSeatMap = true;
  return {
    seatMap: () => {
      const first = firstSeatMap;
      firstSeatMap = false;
      return request(config, accessToken, 'seat-map', 'GET', `/api/seats/${config.performanceTimeId}`, null, {}, null, observe, first);
    },
    selectionSummary: (body, context) => request(config, accessToken, 'selection-summary', 'POST', '/api/seats/selection-summary', body, context, null, observe),
    preReserve: (body, context, key) => request(config, accessToken, 'pre-reserve', 'POST', '/api/reservations/pre-reserve', body, context, key, observe),
    prepare: (reservationId, context, key) => request(config, accessToken, 'payment-prepare', 'POST', '/api/payments/prepare', {reservationId}, context, key, observe),
    verify: (paymentOrderId, providerPaymentId, context) => request(config, accessToken, 'payment-verify', 'POST', `/api/payments/${paymentOrderId}/verify`, {providerPaymentId}, context, null, observe),
    detail: (reservationId, context) => request(config, accessToken, 'reservation-detail', 'GET', `/api/reservations/${reservationId}`, null, context, null, observe),
  };
}
