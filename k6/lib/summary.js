function value(number) {
  return Number.isFinite(number) ? number.toFixed(3) : 'N/A';
}

export function summaryText(data) {
  const lines = ['Imticket-v2 k6 summary', 'Counter rates: counts / k6 testRunDuration; HTTP latency unit: ms',
    'Rate metrics: fraction of non-zero samples (0-1)', ''];
  for (const name of Object.keys(data.metrics).sort()) {
    const metric = data.metrics[name];
    const displayName = name === 'http_offered' || name.startsWith('http_offered{')
      ? name.replace('http_offered', 'http_attempted') : name;
    const httpFailureRate = name === 'http_req_failed' || name.startsWith('http_req_failed{');
    // k6 Rate passes count non-zero samples. For http_req_failed these
    // are failed requests; zero samples are expected-response requests.
    const entries = httpFailureRate
      ? [['rate', metric.values.rate], ['failed_requests', metric.values.passes], ['expected_requests', metric.values.fails]]
      : Object.entries(metric.values);
    const values = entries.map(([key, number]) => `${key}=${value(number)}`).join(' ');
    const failures = Object.entries(metric.thresholds || {}).filter(([, result]) => !result.ok).map(([key]) => key);
    lines.push(`${displayName}: ${values}${failures.length ? ` FAILED(${failures.join(', ')})` : ''}`);
  }
  return lines.join('\n') + '\n';
}

export function saveSummary(data, config) {
  const isTargetVuMeasurement = config.workloadAxis === 'target-vus' && config.phase === 'baseline';
  const effectiveVUs = config.scenario.executor === 'per-vu-iterations'
    ? config.scenario.vus : config.scenario.preAllocatedVUs;
  data.experiment = {
    kind: config.kind || 'smoke', phase: config.phase, runId: config.runId, profile: config.profileName,
    performanceTimeId: config.performanceTimeId, authMode: 'preissued-token-pool',
    configuredVUs: effectiveVUs, targetVUs: isTargetVuMeasurement ? config.targetVUs : null,
    tokenPoolSize: config.tokenPool.length,
    tokenSelection: isTargetVuMeasurement
      ? 'vu-id-to-unique-member' : 'iteration-round-robin', smokeSeatsPerJourney: config.seatCount,
    execution: config.scenario, requestTimeout: config.requestTimeout,
    workloadModel: config.workloadModel || 'single-attempt', workloadAxis: config.workloadAxis || 'contract-smoke',
  };
  if (config.kind && config.kind !== 'smoke') {
    if (config.scenario.executor === 'ramping-arrival-rate') {
      data.experiment.preAllocatedVUs = config.scenario.preAllocatedVUs;
      data.experiment.maxVUs = config.scenario.maxVUs;
    }
    data.experiment.randomSeed = config.randomSeed;
  }
  const plain = summaryText(data);
  return {
    [`${config.runDir}/k6-summary.json`]: JSON.stringify(data, null, 2) + '\n',
    [`${config.runDir}/k6-summary.txt`]: plain,
    stdout: plain,
  };
}
