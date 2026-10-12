import { Counter, Gauge, Trend } from 'k6/metrics';
import { APIS } from './contracts.js';

export const metrics = {
  httpAttempted: new Counter('http_attempted'),
  httpCompleted: new Counter('http_completed'),
  httpSuccess: new Counter('http_contract_success'),
  apiBusinessFailures: new Counter('api_business_failures'),
  apiSystemFailures: new Counter('api_system_failures'),
  journeyStarted: new Counter('journey_started'),
  firstRequestStarted: new Counter('first_request_started'),
  journeyFinished: new Counter('journey_finished'),
  reservationSuccess: new Counter('reservation_success'),
  paymentApplied: new Counter('payment_applied'),
  bookingCompleted: new Counter('booking_completed'),
  seatsConfirmed: new Counter('seats_confirmed'),
  businessFailures: new Counter('business_failures'),
  systemFailures: new Counter('system_failures'),
  journeyDuration: new Trend('journey_duration', true),
  bookingDuration: new Trend('booking_duration', true),
  seatChoices: new Counter('seat_choices'),
  observedSeatCount: new Gauge('observed_seat_count'),
};

export function smokeThresholds(users) {
  const thresholds = {
    checks: ['rate==1'],
    booking_completed: [`count==${users}`],
    business_failures: ['count==0'],
    system_failures: ['count==0'],
    dropped_iterations: ['count==0'],
  };
  // Materialize per-API submetrics in handleSummary; these are not latency SLOs.
  for (const api of APIS) {
    thresholds[`http_req_duration{api:${api}}`] = ['p(99)>=0'];
    thresholds[`http_attempted{api:${api}}`] = ['count>=0'];
    thresholds[`http_completed{api:${api}}`] = ['count>=0'];
    thresholds[`api_business_failures{api:${api}}`] = ['count>=0'];
    thresholds[`api_system_failures{api:${api}}`] = ['count>=0'];
  }
  return thresholds;
}
