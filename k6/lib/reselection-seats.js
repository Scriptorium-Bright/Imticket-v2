import { validateProfile } from './seats.js';

export function desiredSeatCount(profile, random) {
  validateProfile(profile);
  const draw = random();
  return draw < profile.seatSelectionRatio[0] ? 1
    : draw < profile.seatSelectionRatio[0] + profile.seatSelectionRatio[1] ? 2 : 3;
}

export const seatSetKey = (seats) => seats.map((s) => s.id).sort((a, b) => a - b).join(',');

function drawSet(pool, count, random, previousKey) {
  if (pool.length < count) return null;
  const candidates = [...pool];
  for (let i = 0; i < count; i++) {
    const j = i + Math.floor(random() * (candidates.length - i));
    [candidates[i], candidates[j]] = [candidates[j], candidates[i]];
  }
  let selected = candidates.slice(0, count);
  if (seatSetKey(selected) === previousKey) {
    if (pool.length === count) return null;
    // Replace one seat instead of unbounded rejection sampling. Only this Journey remembers the previous set.
    selected = [...selected.slice(0, count - 1), candidates[count]];
  }
  return selected;
}

export function selectAvailableSeats(seatMap, profile, count, random, previousKey = null) {
  validateProfile(profile);
  if (seatMap.length !== profile.seatCount) throw new Error('PROFILE_SEAT_COUNT_MISMATCH');
  if (![1, 2, 3].includes(count)) throw new Error('INVALID_REQUESTED_SEAT_COUNT');
  const ordered = [...seatMap].sort((a, b) => a.id - b.id);
  const boundary = Math.floor(ordered.length * profile.hotSeatRatio);
  const available = ordered.filter((s) => s.status === 'AVAILABLE');
  if (available.length < count) return {outcome: 'SOLD_OUT', reason: 'insufficient-available-seats', availableCount: available.length, count};
  // The hot range uses ALL seat positions; it never moves as seats sell.
  const hot = ordered.slice(0, boundary).filter((s) => s.status === 'AVAILABLE');
  const cold = ordered.slice(boundary).filter((s) => s.status === 'AVAILABLE');
  const requestedRegion = random() < profile.hotTrafficRatio ? 'hot' : 'cold';
  for (const [region, pool] of [[requestedRegion, requestedRegion === 'hot' ? hot : cold],
    [requestedRegion === 'hot' ? 'cold' : 'hot', requestedRegion === 'hot' ? cold : hot], ['mixed', available]]) {
    const seats = drawSet(pool, count, random, previousKey);
    if (seats) return {outcome: 'SELECTED', seats, count, region, requestedRegion,
      fallback: region !== requestedRegion, availableCount: available.length};
  }
  // N AVAILABLE seats exist, but only the previously conflicting set is selectable.
  // Refresh the server map; do not claim SOLD_OUT or silently retry the identical set.
  return {outcome: 'REFRESH_REQUIRED', reason: 'same-conflicting-set-only', count, availableCount: available.length};
}
