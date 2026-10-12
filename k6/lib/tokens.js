// Pool cardinality is supplied by the experimenter, independent of VUs/iterations.
export function validateTokenPool(entries) {
  if (!Array.isArray(entries) || entries.length === 0) throw new Error('Token pool must be nonempty');
  for (const entry of entries) {
    if (!entry || typeof entry.accessToken !== 'string' || !entry.accessToken
        || entry.accessToken.includes('<') || /\s/.test(entry.accessToken)) {
      throw new Error('Token pool requires real preissued accessToken values');
    }
    if (entry.memberId !== undefined && (!Number.isSafeInteger(entry.memberId) || entry.memberId <= 0)) {
      throw new Error('Token pool memberId must be a positive safe integer');
    }
  }
  if (new Set(entries.map((entry) => entry.accessToken)).size !== entries.length) {
    throw new Error('Duplicate JWT entries in token pool would change selection weights');
  }
  return entries.map((entry) => ({accessToken: entry.accessToken, memberId: entry.memberId}));
}

export function selectToken(pool, iterationIndex) {
  if (!pool.length || !Number.isSafeInteger(iterationIndex) || iterationIndex < 0) {
    throw new Error('Token selection requires a nonempty pool and a valid iteration index');
  }
  // Round-robin over the global scenario iteration number, never over VU ID.
  return pool[iterationIndex % pool.length].accessToken;
}

export function selectTokenForVU(pool, vuIdInTest, targetVUs) {
  if (!pool.length || !Number.isSafeInteger(vuIdInTest) || vuIdInTest < 1
      || !Number.isSafeInteger(targetVUs) || targetVUs < 1 || pool.length !== targetVUs
      || vuIdInTest > targetVUs) {
    throw new Error('Target VU token mapping requires one pool entry per configured VU');
  }
  return pool[vuIdInTest - 1].accessToken;
}
