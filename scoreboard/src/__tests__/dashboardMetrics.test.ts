import { expect, it } from 'vitest';
import { animalComparison, cumulativeHitPercentage, stepTitle } from '../dashboardMetrics';
import { DIRECTIONS, type PlayerState } from '../state';

const player: PlayerState = {
  score: null, combo: null, hits: 0, misses: 0, observedScore: null, lastVideoTimeMs: null,
  hitsByDirection: { 'Up-Right': 0, 'Up-Left': 0, 'Down-Right': 0, 'Down-Left': 0, Center: 0 },
};

it.each([
  [0, 0, null], [3, 1, 75], [0, 2, 0], [5, 0, 100], [3, undefined, null],
])('calculates cumulative hit percentage for %s hits and %s misses', (hits, misses, percentage) => {
  expect(cumulativeHitPercentage({ ...player, hits, misses })).toBe(percentage);
});

it.each([
  ['Up-Left', 'Left-Step Legend'], ['Up-Right', 'Right-Step Royalty'],
  ['Down-Left', 'Forward-Step Phenom'], ['Down-Right', 'Back-Step Boss'],
  ['Center', 'Center-Step Superstar'],
] as const)('awards %s the title %s only when it leads', (direction, title) => {
  expect(stepTitle({
    ...player, hits: 2, hitsByDirection: { ...player.hitsByDirection!, [direction]: 2 },
  })?.title).toBe(title);
});

it('does not award titles for missing, zero, or partially tied counts', () => {
  expect(stepTitle({ ...player, hitsByDirection: null })).toBeNull();
  expect(stepTitle(player)).toBeNull();
  expect(stepTitle({
    ...player, hits: 2, hitsByDirection: { ...player.hitsByDirection!, 'Up-Left': 1, 'Up-Right': 1 },
  })).toBeNull();
});

it('awards All-Step Ace only for a positive tie across all five lanes', () => {
  expect(stepTitle({
    ...player, hits: 10, hitsByDirection: {
      'Up-Left': 2, 'Up-Right': 2, 'Down-Left': 2, 'Down-Right': 2, Center: 2,
    },
  })?.title).toBe('All-Step Ace');
  expect(DIRECTIONS).toHaveLength(5);
});

it.each([
  [0, 'goldfish', 'Faster'], [10.63, 'goldfish', 'Faster'],
  [10.64, 'goldfish', 'Matching'], [249.99, 'mouse', 'Faster'],
  [250, 'mouse', 'Matching'], [250.01, 'mouse', 'Chasing'],
])('compares %s ms to a sourced reference without overstating threshold equality', (time, animal, label) => {
  const result = animalComparison(time);
  expect(result.benchmark.animal).toBe(animal);
  expect(result.label).toContain(label);
  expect(result.benchmark.url).toMatch(/^https:\/\/pmc\.ncbi\.nlm\.nih\.gov\/articles\/PMC\d+\/$/);
  expect(result.benchmark.condition).toBeTruthy();
  expect(result.differenceMs).toBeGreaterThanOrEqual(0);
});
