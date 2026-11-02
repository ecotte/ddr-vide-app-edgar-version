import { DIRECTIONS, type Direction, type PlayerState } from './state';

const STEP_TITLES: Record<Direction, { title: string; symbol: string }> = {
  'Up-Left': { title: 'Left-Step Legend', symbol: '\u2190' },
  'Up-Right': { title: 'Right-Step Royalty', symbol: '\u2192' },
  'Down-Left': { title: 'Forward-Step Phenom', symbol: '\u2191' },
  'Down-Right': { title: 'Back-Step Boss', symbol: '\u2193' },
  Center: { title: 'Center-Step Superstar', symbol: '\u25a3' },
};

export function stepTitle(player: PlayerState) {
  const counts = player.hitsByDirection;
  if (!counts) return null;
  const maximum = Math.max(...DIRECTIONS.map(direction => counts[direction]));
  if (maximum === 0) return null;
  const leaders = DIRECTIONS.filter(direction => counts[direction] === maximum);
  if (leaders.length === DIRECTIONS.length) {
    return { title: 'All-Step Ace', symbol: '\u2190 \u2191 \u25a3 \u2193 \u2192',
      detail: 'All five directions tied for most hits.' };
  }
  if (leaders.length !== 1) return null;
  const direction = leaders[0];
  return { ...STEP_TITLES[direction], detail: `Most-hit lane: ${direction} (${maximum.toLocaleString()} hits).` };
}

export function cumulativeHitPercentage(player: PlayerState): number | null {
  if (player.misses == null) return null;
  const outcomes = player.hits + player.misses;
  return outcomes === 0 ? null : player.hits / outcomes * 100;
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
