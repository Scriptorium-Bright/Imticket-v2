export function validateProfile(profile) {
  if (!profile || !Number.isInteger(profile.seatCount) || profile.seatCount < 3
      || !Number.isInteger(profile.referenceAttemptors) || profile.referenceAttemptors < 1
      || !Number.isInteger(profile.peakJourneyArrivalRate) || profile.peakJourneyArrivalRate < 1
      || !(profile.hotSeatRatio > 0 && profile.hotSeatRatio < 1)
      || !(profile.hotTrafficRatio >= 0 && profile.hotTrafficRatio <= 1)
      || !Array.isArray(profile.seatSelectionRatio) || profile.seatSelectionRatio.length !== 3
      || profile.seatSelectionRatio.some((p) => !Number.isFinite(p) || p < 0 || p > 1)
      || Math.abs(profile.seatSelectionRatio.reduce((a, b) => a + b, 0) - 1) > 1e-9) {
    throw new Error('Invalid workload profile');
  }
  return profile;
}

export function seededRandom(seed) {
  let state = seed >>> 0;
  return () => {
    state += 0x6D2B79F5;
    let value = Math.imul(state ^ (state >>> 15), 1 | state);
    value ^= value + Math.imul(value ^ (value >>> 7), 61 | value);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

export function selectSmokeSeats(seatMap, userIndex, count) {
  // Reserve disjoint ID-ordered slots across smoke users; never replace a taken slot.
  const ordered = [...seatMap].sort((a, b) => a.id - b.id);
  const chosen = ordered.slice(userIndex * count, (userIndex + 1) * count);
  if (chosen.length !== count || chosen.some((s) => s.status !== 'AVAILABLE')) {
    throw new Error('SMOKE_FIXTURE_UNAVAILABLE: smoke slots require fresh AVAILABLE seats');
  }
  return {seats: chosen, region: 'smoke-disjoint', count};
}

// Reusable for the later pilot/workload stage. No executor or duration is chosen here.
export function selectWorkloadSeats(seatMap, profile, random) {
  validateProfile(profile);
  if (seatMap.length !== profile.seatCount) throw new Error('PROFILE_SEAT_COUNT_MISMATCH');
  const ordered = [...seatMap].sort((a, b) => a.id - b.id);
  const hotCount = Math.floor(ordered.length * profile.hotSeatRatio);
  const draw = random();
  const ratios = profile.seatSelectionRatio;
  const count = draw < ratios[0] ? 1 : draw < ratios[0] + ratios[1] ? 2 : 3;
  const hot = random() < profile.hotTrafficRatio;
  const pool = hot ? ordered.slice(0, hotCount) : ordered.slice(hotCount);
  if (pool.length < count) throw new Error('SEAT_REGION_TOO_SMALL');
  // No AVAILABLE filter: independent users may select LOCKED/RESERVED or identical IDs.
  for (let i = 0; i < count; i++) {
    const index = i + Math.floor(random() * (pool.length - i));
    [pool[i], pool[index]] = [pool[index], pool[i]];
  }
  return {seats: pool.slice(0, count), region: hot ? 'hot' : 'cold', count};
}
