// Pure validation shared by k6 init and local Node tests. No implicit load defaults.
export function durationSeconds(value, allowZero = false) {
  if (typeof value !== 'string' || !/^(?:\d+(?:\.\d+)?(?:ms|s|m|h))+$/.test(value)) {
    throw new Error('Invalid arrival duration');
  }
  const units = {ms: 0.001, s: 1, m: 60, h: 3600};
  const total = [...value.matchAll(/(\d+(?:\.\d+)?)(ms|s|m|h)/g)]
    .reduce((sum, match) => sum + Number(match[1]) * units[match[2]], 0);
  if (!Number.isFinite(total) || total < 0 || (!allowZero && total === 0)) throw new Error('Invalid arrival duration');
  return total;
}

export function validateArrival(value) {
  const fields = ['executor', 'timeUnit', 'startRate', 'stages', 'preAllocatedVUs', 'maxVUs', 'gracefulStop'];
  if (!value || Object.keys(value).sort().join() !== fields.sort().join() || value.executor !== 'ramping-arrival-rate') {
    throw new Error('Explicit ramping-arrival-rate settings required');
  }
  durationSeconds(value.timeUnit);
  durationSeconds(value.gracefulStop, true);
  const integer = (n, min) => Number.isSafeInteger(n) && n >= min;
  if (!integer(value.startRate, 0) || !integer(value.preAllocatedVUs, 1)
      || !integer(value.maxVUs, value.preAllocatedVUs) || !Array.isArray(value.stages) || !value.stages.length) {
    throw new Error('Invalid arrival rate / VU allocation');
  }
  for (const stage of value.stages) {
    if (!stage || Object.keys(stage).sort().join() !== 'duration,target' || !integer(stage.target, 0)) {
      throw new Error('Invalid arrival stage');
    }
    durationSeconds(stage.duration);
  }
  if (Math.max(value.startRate, ...value.stages.map((s) => s.target)) === 0) throw new Error('No arrivals configured');
  return value;
}

export function iterationSeed(seed, iteration) {
  // Independent of VU allocation, run ID and fixture IDs.
  return (seed ^ Math.imul(iteration + 1, 0x9E3779B1)) >>> 0;
}
