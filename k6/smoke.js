import exec from 'k6/execution';
import { smokeConfig } from './lib/config.js';
import { bookingJourney } from './lib/journey.js';
import { selectSmokeSeats } from './lib/seats.js';
import { smokeThresholds } from './lib/metrics.js';
import { saveSummary } from './lib/summary.js';
import { selectToken } from './lib/tokens.js';

const config = smokeConfig();

export const options = {
  scenarios: {smoke: config.scenario},
  thresholds: smokeThresholds(config.vus),
  summaryTrendStats: ['count', 'avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
  summaryTimeUnit: 'ms',
  // Use the API name for dynamic paths; exclude URLs, VU IDs, and iteration IDs.
  systemTags: ['status', 'method', 'name', 'scenario', 'expected_response', 'error_code', 'group', 'check'],
  tags: {run_id: config.runId, profile: config.profileName, phase: config.phase},
};

export default function () {
  const index = exec.vu.idInTest - 1;
  bookingJourney(config, selectToken(config.tokenPool, exec.scenario.iterationInTest),
    (map) => selectSmokeSeats(map, index, config.seatCount));
}

export function handleSummary(data) {
  return saveSummary(data, config);
}
