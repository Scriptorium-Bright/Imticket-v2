// Opt-in diagnostic output. VU identity is metadata, never an indexed metric tag.
// Diagnostic runs are excluded from performance Baseline results.
import exec from 'k6/execution';
import { Gauge } from 'k6/metrics';

const enabled = __ENV.HARNESS_DIAGNOSTIC === '1';
const phase = enabled ? new Gauge('harness_phase') : null;
let sequence = 0, requestNumber = 0;
const fields = new Set(['api', 'outcome', 'reason', 'retries', 'attempts', 'requestedSeatCount',
  'checkpoint', 'refresh', 'status', 'error_code']);

export const harnessTrace = enabled ? function (state, details = {}) {
  const metadata = exec.vu.metrics.metadata;
  metadata.harness_vu = String(exec.vu.idInTest);
  metadata.harness_iteration = String(exec.vu.iterationInInstance);
  metadata.harness_state = state;
  metadata.harness_sequence = String(++sequence);
  metadata.harness_state_started_ms = String(Date.now());
  if (state === 'http.dispatch') metadata.harness_request = String(++requestNumber);
  for (const [key, value] of Object.entries(details)) {
    if (fields.has(key) && value !== undefined && value !== null) metadata[`harness_${key}`] = String(value);
  }
  // Entering a phase must be visible even if the operation never returns.
  // This marker adds output work: these runs are diagnostic, not Baseline.
  phase.add(Number(metadata.harness_state_started_ms));
} : null;
