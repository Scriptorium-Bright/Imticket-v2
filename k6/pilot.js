import exec from 'k6/execution';
import { pilotConfig } from './lib/config.js';
import { bookingJourney } from './lib/journey.js';
import { reselectionJourney } from './lib/reselection-journey.js';
import { reselectionThresholds } from './lib/reselection-metrics.js';
import { seededRandom, selectWorkloadSeats } from './lib/seats.js';
import { iterationSeed } from './lib/arrival.js';
import { pilotMetrics, pilotThresholds } from './lib/pilot-metrics.js';
import { saveSummary } from './lib/summary.js';
import { selectToken, selectTokenForVU } from './lib/tokens.js';
import { harnessTrace } from './lib/harness-trace.js';

const config = pilotConfig();

export const options = {
  scenarios: {[config.phase]: config.scenario},
  thresholds: config.workloadModel === 'reselection' ? reselectionThresholds(pilotThresholds(config.phase)) : pilotThresholds(config.phase),
  summaryTrendStats: ['count', 'avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
  summaryTimeUnit: 'ms',
  systemTags: ['status', 'method', 'name', 'scenario', 'expected_response', 'error_code', 'error', 'group', 'check'],
  tags: {run_id: config.runId, profile: config.profileName, phase: config.phase, workload_model: config.workloadModel},
};

export default function () {
  if (harnessTrace) harnessTrace('iteration.start');
  pilotMetrics.iterationStarted.add(1);
  if (exec.scenario.iterationInTest === 0) pilotMetrics.scenarioStart.add(exec.scenario.startTime);
  const random = seededRandom(iterationSeed(config.randomSeed, exec.scenario.iterationInTest));
  const token = config.workloadAxis === 'target-vus' && config.phase === 'baseline'
    ? selectTokenForVU(config.tokenPool, exec.vu.idInTest, config.targetVUs)
    : selectToken(config.tokenPool, exec.scenario.iterationInTest);
  if (config.workloadModel === 'reselection') reselectionJourney(config, token, random);
  else bookingJourney(config, token, (map) => selectWorkloadSeats(map, config.profile, random));
  if (harnessTrace) harnessTrace('iteration.return');
}

export function teardown() {
  // No HTTP, login, fixture preparation or collection in the k6 process.
  pilotMetrics.workloadEnd.add(Date.now());
}

export function handleSummary(data) {
  return saveSummary(data, config);
}
