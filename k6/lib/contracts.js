// Contract sources: develop Controllers/DTOs and ReservationPaymentFlowIntegrationTest.
export const APIS = [
  'seat-map', 'selection-summary', 'pre-reserve',
  'payment-prepare', 'payment-verify', 'reservation-detail',
];

const id = (value) => Number.isSafeInteger(value) && value > 0;
const money = (value) => Number.isSafeInteger(value) && value >= 0;
const text = (value) => typeof value === 'string' && value.length > 0;
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const seat = (value) => object(value) && id(value.id) && text(value.section)
  && id(value.rowNumber) && id(value.seatNumber)
  && ['VIP', 'R', 'S', 'A'].includes(value.grade) && money(value.price)
  && ['AVAILABLE', 'LOCKED', 'RESERVED'].includes(value.status);
const seats = (value) => Array.isArray(value) && value.length > 0
  && value.every(seat) && new Set(value.map((s) => s.id)).size === value.length;
const reservation = (value) => object(value) && id(value.id)
  && text(value.reservationCode) && money(value.totalPrice);
const sameSeats = (actual, expected) => actual.length === expected.length
  && actual.every((s) => expected.includes(s.id));

export function validData(api, data, context = {}) {
  switch (api) {
    case 'seat-map': return seats(data);
    case 'selection-summary':
      return object(data) && data.performanceTimeId === context.performanceTimeId
        && seats(data.seats) && sameSeats(data.seats, context.seatIds)
        && data.totalPrice === context.totalPrice
        && data.seats.reduce((sum, s) => sum + s.price, 0) === data.totalPrice;
    case 'pre-reserve':
      return reservation(data) && data.status === 'PENDING_PAYMENT'
        && data.totalPrice === context.totalPrice && text(data.expiredAt);
    case 'payment-prepare':
      return object(data) && id(data.paymentOrderId) && text(data.merchantOrderId)
        && data.amount === context.totalPrice && data.currency === 'KRW'
        && text(data.provider) && text(data.providerPaymentId);
    case 'payment-verify':
      return object(data) && data.paymentOrderId === context.paymentOrderId
        && data.reservationId === context.reservationId && text(data.providerTransactionId)
        && ((data.paymentStatus === 'APPLIED' && data.reservationStatus === 'CONFIRMED')
          || (data.paymentStatus === 'REFUND_PENDING' && data.reservationStatus === 'EXPIRED'));
    case 'reservation-detail':
      return reservation(data) && data.id === context.reservationId
        && data.reservationCode === context.reservationCode && data.status === 'CONFIRMED'
        && data.expiredAt === null
        && data.totalPrice === context.totalPrice && seats(data.seats)
        && sameSeats(data.seats, context.seatIds) && data.seats.every((s) => s.status === 'RESERVED');
    default: return false;
  }
}

export function transportFailure(response) {
  const code = response && response.error_code;
  return !response || response.status === 0 || (code >= 1000 && code < 1400) || code >= 1600;
}

export function classifyResponse(api, response, context = {}) {
  const system = (reason, errorCode = null) => ({outcome: 'system-failure', reason, errorCode});
  if (response && response.status >= 500) return system('http-5xx');
  if (transportFailure(response)) {
    // k6 1050 is request timeout; retain the native code on metric tags as well.
    return system(response && response.error_code === 1050 ? 'timeout' : 'network-error');
  }
  let body;
  try { body = JSON.parse(response.body); } catch (_) { return system('invalid-json'); }
  if (!object(body)) return system('invalid-envelope');
  if (api === 'pre-reserve' && response.status === 409 && body.success === false
      && body.data === null && body.errorCode === 'SEAT_UNAVAILABLE' && text(body.message)) {
    return {outcome: 'business-failure', reason: 'seat-unavailable', errorCode: body.errorCode};
  }
  if (response.status !== 200 || body.success !== true || body.errorCode !== null
      || body.message !== null) return system('unexpected-response', body.errorCode || null);
  if (!validData(api, body.data, context)) return system('invalid-data');
  if (api === 'payment-prepare' && body.data.provider !== 'FAKE') {
    // PortOne needs external PAID approval; this server-only journey cannot supply it.
    return system('unsupported-payment-provider');
  }
  if (api === 'payment-verify' && body.data.reservationStatus === 'EXPIRED') {
    // A real 200 business outcome in PaymentCompletionService, not a server error code.
    return {outcome: 'business-failure', reason: 'expired-refund-pending', errorCode: null};
  }
  return {outcome: 'success', reason: 'success', errorCode: null, data: body.data};
}
