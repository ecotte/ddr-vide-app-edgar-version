import { DIRECTIONS, type Direction, type PlayerState } from './state';

const STEP_TITLES: Record<Direction, { title: string; symbol: string }> = {
  'Up-Left': { title: 'Up-Left Legend', symbol: '\u2196' },
  'Up-Right': { title: 'Up-Right Royalty', symbol: '\u2197' },
  'Down-Left': { title: 'Down-Left Dynamo', symbol: '\u2199' },
  'Down-Right': { title: 'Down-Right Champion', symbol: '\u2198' },
  Center: { title: 'Center-Step Superstar', symbol: '\u25a3' },
};

export function stepTitle(player: PlayerState) {
  const mostHit = mostHitLanes(player);
  const warmingUp = {
    title: 'Warming up', symbol: '\u266a',
    detail: !player.hitsByDirection
      ? 'Waiting for per-lane hit data.'
      : 'Waiting for a recognized hit with a direction.',
  };
  if (!mostHit) return warmingUp;
  const leaders = mostHit.directions;
  const hitCount = `${mostHit.hits.toLocaleString()} ${mostHit.hits === 1 ? 'hit' : 'hits'}`;
  if (leaders.length === DIRECTIONS.length) {
    return { title: 'All-Step Ace', symbol: '\u2196 \u2197 \u25a3 \u2199 \u2198',
      detail: `All five directions tied at ${hitCount} each.` };
  }
  if (leaders.length !== 1) {
    return {
      title: 'Joint leaders',
      symbol: leaders.map(direction => STEP_TITLES[direction].symbol).join(' '),
      detail: `Most hits: ${leaders.join(', ')} (${hitCount} each).`,
    };
  }
  const direction = leaders[0];
  return {
    ...STEP_TITLES[direction],
    detail: `Most hits: ${direction} (${hitCount}).`,
  };
}

export function mostHitLanes(player: PlayerState) {
  const counts = player.hitsByDirection;
  if (!counts) return null;
  const hits = Math.max(...DIRECTIONS.map(direction => counts[direction]));
  if (hits === 0) return null;
  return { directions: DIRECTIONS.filter(direction => counts[direction] === hits), hits };
}

export function totalDetectedSteps(player: PlayerState): number | null {
  if (player.misses == null) return null;
  return player.hits + player.misses;
}

export function cumulativeHitPercentage(player: PlayerState): number | null {
  const outcomes = totalDetectedSteps(player);
  return outcomes == null || outcomes === 0 ? null : player.hits / outcomes * 100;
}

export const ANIMAL_BENCHMARKS = [
  {
    animal: 'goldfish',
    milliseconds: 10.64,
    condition: 'Mean auditory C-start latency, fish 0-30 cm from the speaker: 10.64 ms.',
    citation: 'Martorell & Medan (2022), Scientific Reports',
    url: 'https://pmc.ncbi.nlm.nih.gov/articles/PMC8776867/',
  },
  {
    animal: 'mouse',
    milliseconds: 250,
    condition: 'Visual looming-escape responses reported as low as approximately 250 ms.',
    citation: 'Yilmaz & Meister (2013), Current Biology',
    url: 'https://pmc.ncbi.nlm.nih.gov/articles/PMC3809337/',
  },
] as const;

export function animalComparison(milliseconds: number) {
  const benchmark = ANIMAL_BENCHMARKS.find(value => milliseconds <= value.milliseconds)
    ?? ANIMAL_BENCHMARKS[ANIMAL_BENCHMARKS.length - 1];
  const faster = milliseconds < benchmark.milliseconds;
  return {
    benchmark,
    label: faster ? `Faster than the ${benchmark.animal} reference`
      : milliseconds === benchmark.milliseconds ? `Matching the ${benchmark.animal} reference`
      : `Chasing the ${benchmark.animal} reference`,
    differenceMs: Math.abs(milliseconds - benchmark.milliseconds),
    faster,
  };
}
