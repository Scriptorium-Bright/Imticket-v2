import { SharedArray } from 'k6/data';
import { validateProfile } from './seats.js';
import { validateTokenPool } from './tokens.js';
import { validateArrival } from './arrival.js';

function positiveInteger(value, name) {
  if (!/^[1-9]\d*$/.test(String(value)) || !Number.isSafeInteger(Number(value))) {
    throw new Error(`${name} must be a positive safe integer`);
  }
  return Number(value);
}

function duration(value, name) {
  if (!/^(?:\d+(?:\.\d+)?(?:ms|s|m|h))+$/.test(value) || !/[1-9]/.test(value)) {
    throw new Error(`${name} must be a positive k6 duration`);
  }
  return value;
}

export function smokeConfig() {
  const baseUrl = (__ENV.BASE_URL || '').replace(/\/+$/, '');
  if (!/^https?:\/\/[^\s/?#]+(?::\d+)?(?:\/[^?#\s]*)?$/.test(baseUrl)
      || baseUrl.includes('@') || baseUrl.includes('<')) {
    throw new Error('BASE_URL must be the real HTTP(S) application origin/base URL');
  }
  const performanceTimeId = positiveInteger(__ENV.PERFORMANCE_TIME_ID, 'PERFORMANCE_TIME_ID');
  const vus = positiveInteger(__ENV.SMOKE_VUS || '1', 'SMOKE_VUS');
  const seatCount = positiveInteger(__ENV.SMOKE_SEATS || '1', 'SMOKE_SEATS');
  if (vus > 2 || seatCount > 3) throw new Error('Smoke permits 1–2 VUs and 1–3 seats per Journey');
  if (__ENV.TEST_ACCESS_TOKEN && __ENV.TEST_TOKENS_FILE) {
    throw new Error('Use either TEST_ACCESS_TOKEN or TEST_TOKENS_FILE');
  }
  const tokenPool = new SharedArray('preissued-token-pool', () => {
    if (__ENV.TEST_TOKENS_FILE) {
      return validateTokenPool(JSON.parse(open(__ENV.TEST_TOKENS_FILE)));
    }
    if (!__ENV.TEST_ACCESS_TOKEN) {
      throw new Error('TEST_ACCESS_TOKEN or TEST_TOKENS_FILE required; login/register are excluded. '
        + 'Use scripts/issue-token.py separately with TEST_USERNAME/TEST_PASSWORD.');
    }
    return validateTokenPool([{accessToken: __ENV.TEST_ACCESS_TOKEN}]);
  });
  const profiles = JSON.parse(open(import.meta.resolve('../config/profiles.json')));
  const profileName = __ENV.WORKLOAD_PROFILE || 'smoke';
  const profile = profileName === 'smoke' ? null : validateProfile(profiles[profileName]);
  if (!__ENV.RUN_DIR || !__ENV.RUN_ID) throw new Error('Run via scripts/run-experiment.py to preserve results');
  const scenario = {executor: 'per-vu-iterations', vus, iterations: 1};
  // Smoke ends when these 1–2 journeys finish. Optional limits are not measurement stages.
  if (__ENV.SMOKE_MAX_DURATION) scenario.maxDuration = duration(__ENV.SMOKE_MAX_DURATION, 'SMOKE_MAX_DURATION');
  if (__ENV.SMOKE_GRACEFUL_STOP) scenario.gracefulStop = duration(__ENV.SMOKE_GRACEFUL_STOP, 'SMOKE_GRACEFUL_STOP');
  return {
    baseUrl, performanceTimeId, vus, seatCount, tokenPool, profileName, profile,
    requestTimeout: __ENV.HTTP_TIMEOUT ? duration(__ENV.HTTP_TIMEOUT, 'HTTP_TIMEOUT') : null,
    scenario, runDir: __ENV.RUN_DIR, runId: __ENV.RUN_ID, phase: __ENV.RUN_PHASE || 'smoke',
  };
}

export function pilotConfig() {
  const settings = JSON.parse(open(__ENV.PILOT_CONFIG_FILE));
  const baseUrl = (__ENV.BASE_URL || '').replace(/\/+$/, '');
  if (!/^https?:\/\/[^\s/?#]+(?::\d+)?(?:\/[^?#\s]*)?$/.test(baseUrl)
      || baseUrl.includes('@') || baseUrl.includes('<')) throw new Error('Real BASE_URL required');
  const phase = __ENV.RUN_PHASE;
  const performanceTimeId = positiveInteger(__ENV.PERFORMANCE_TIME_ID, 'PERFORMANCE_TIME_ID');
  if (__ENV.TEST_ACCESS_TOKEN || !__ENV.TEST_TOKENS_FILE) throw new Error('Pilot requires an explicit preissued token pool file');
  const tokenPool = new SharedArray('preissued-token-pool', () => validateTokenPool(JSON.parse(open(__ENV.TEST_TOKENS_FILE))));
  const expectedTokenCount = phase === 'warmup' && settings.warmup.tokenPoolSize
    ? settings.warmup.tokenPoolSize : settings.auth.tokenPoolSize;
  if (tokenPool.length !== expectedTokenCount) throw new Error('Pilot token pool size differs from config');
  const profiles = JSON.parse(open(import.meta.resolve('../config/profiles.json')));
  const profileName = settings.profile;
  const profile = validateProfile(profiles[profileName]);
  if (!['pilot', 'baseline', 'warmup', 'journey-smoke'].includes(phase) || !__ENV.RUN_DIR || !__ENV.RUN_ID) throw new Error('Use the experiment runner');
  const workloadModel = settings.workloadModel || 'single-attempt';
  if (!['single-attempt', 'reselection'].includes(workloadModel)) throw new Error('Unknown workloadModel');
  const workloadAxis = settings.workloadAxis || 'iteration-arrival-rate';
  if (!['iteration-arrival-rate', 'target-vus'].includes(workloadAxis)) throw new Error('Unknown workloadAxis');
  const targetVUs = workloadAxis === 'target-vus' ? settings.measurement.vus : null;
  const scenario = phase === 'journey-smoke' ? {...settings.smoke}
    : phase === 'warmup' ? validateArrival(settings.warmup.execution)
      : workloadAxis === 'target-vus' ? validateTargetVUs(settings.measurement, targetVUs)
        : validateArrival(settings.measurement);
  if (!Number.isSafeInteger(settings.randomSeed) || settings.randomSeed < 0 || settings.randomSeed > 0xFFFFFFFF) {
    throw new Error('Explicit uint32 randomSeed required');
  }
  return {kind: __ENV.RUN_KIND || 'pilot', workloadModel, workloadAxis, targetVUs,
    vus: targetVUs, reselection: settings.reselection,
    baseUrl, performanceTimeId, tokenPool, profileName, profile, phase, scenario,
    randomSeed: settings.randomSeed, requestTimeout: duration(settings.requestTimeout, 'requestTimeout'),
    runDir: __ENV.RUN_DIR, runId: __ENV.RUN_ID};
}

function validateTargetVUs(value, targetVUs) {
  const fields = ['executor', 'vus', 'iterations', 'maxDuration', 'gracefulStop'];
  if (!value || Object.keys(value).sort().join() !== fields.sort().join()
      || value.executor !== 'per-vu-iterations' || value.vus !== targetVUs || value.iterations !== 1
      || !Number.isSafeInteger(targetVUs) || targetVUs < 1) {
    throw new Error('VU Baseline requires per-vu-iterations with one iteration per target VU');
  }
  duration(value.maxDuration, 'VU_BASELINE_MAX_DURATION');
  if (!/^(?:\d+(?:\.\d+)?(?:ms|s|m|h))+$/.test(value.gracefulStop)) {
    throw new Error('VU_BASELINE_GRACEFUL_STOP must be a k6 duration');
  }
  return value;
}
