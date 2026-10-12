import { Counter, Gauge } from 'k6/metrics';
import { APIS } from './contracts.js';

export const pilotMetrics = {
  iterationStarted: new Counter('iteration_started'),
  scenarioStart: new Gauge('scenario_start_epoch_ms'),
  workloadEnd: new Gauge('workload_end_epoch_ms'),
};

export function pilotThresholds(phase) {
  // Baseline observes server errors instead of treating zero errors as a performance acceptance SLO.
  const thresholds = {system_failures: [phase === 'baseline' ? 'count>=0' : 'count==0'], dropped_iterations: ['count==0']};
  for (const api of APIS) {
    // Materialize submetrics only. Business outcomes can fail the Journey check.
    thresholds[`checks{api:${api}}`] = [phase === 'baseline' ? 'rate>=0' : 'rate==1'];
    thresholds[`http_req_duration{api:${api}}`] = ['p(99)>=0'];
    for (const metric of ['http_attempted', 'http_completed', 'api_business_failures', 'api_system_failures']) {
      thresholds[`${metric}{api:${api}}`] = ['count>=0'];
    }
  }
  thresholds[`iteration_duration{scenario:${phase}}`] = ['p(99)>=0'];
  return thresholds;
}
