import { expect, it } from 'vitest';
import { animalComparison, cumulativeHitPercentage, mostHitLanes, stepTitle, totalDetectedSteps } from '../dashboardMetrics';
import { DIRECTIONS, type PlayerState } from '../state';

const player: PlayerState = {
  score: null, combo: null, hits: 0, misses: 0, observedScore: null, lastVideoTimeMs: null,
  hitsByDirection: { 'Up-Right': 0, 'Up-Left': 0, 'Down-Right': 0, 'Down-Left': 0, Center: 0 },
  missesByDirection: { 'Up-Right': 0, 'Up-Left': 0, 'Down-Right': 0, 'Down-Left': 0, Center: 0 },
};

it.each([
  [0, 0, 0], [3, 1, 4], [0, 2, 2], [5, 0, 5], [3, undefined, null], [3, null, null],
])('totals detected steps for %s hits and %s misses', (hits, misses, total) => {
  expect(totalDetectedSteps({ ...player, hits, misses })).toBe(total);
});

it('finds the most-hit lane by count without confusing it with lane accuracy or unassigned hits', () => {
  const state = {
    ...player, hits: 110, unassignedHits: 100,
    hitsByDirection: { ...player.hitsByDirection!, 'Down-Left': 6, 'Up-Right': 4 },
    missesByDirection: { ...player.missesByDirection!, 'Down-Left': 3 },
  };
  expect(mostHitLanes(state)).toEqual({ directions: ['Down-Left'], hits: 6 });
  expect(stepTitle(state)?.title).toBe('Down-Left Dynamo');
});

it('includes every tied most-hit lane and omits unavailable or zero counts', () => {
  expect(mostHitLanes({ ...player, hitsByDirection: null })).toBeNull();
  expect(mostHitLanes(player)).toBeNull();
  expect(mostHitLanes({
    ...player, hits: 4, hitsByDirection: { ...player.hitsByDirection!, Center: 2, 'Up-Left': 2 },
  })).toEqual({ directions: ['Up-Left', 'Center'], hits: 2 });
  const all = { 'Up-Right': 1, 'Up-Left': 1, 'Down-Right': 1, 'Down-Left': 1, Center: 1 };
  expect(mostHitLanes({ ...player, hits: 5, hitsByDirection: all })).toEqual({ directions: DIRECTIONS, hits: 1 });
});

it.each([
  [0, 0, null], [3, 1, 75], [0, 2, 0], [5, 0, 100], [3, undefined, null],
])('calculates cumulative hit percentage for %s hits and %s misses', (hits, misses, percentage) => {
  expect(cumulativeHitPercentage({ ...player, hits, misses })).toBe(percentage);
});

it.each([
  ['Up-Left', 'Up-Left Legend', '\u2196'], ['Up-Right', 'Up-Right Royalty', '\u2197'],
  ['Down-Left', 'Down-Left Dynamo', '\u2199'], ['Down-Right', 'Down-Right Champion', '\u2198'],
  ['Center', 'Center-Step Superstar', '\u25a3'],
] as const)('awards %s the title %s with its matching symbol %s', (direction, title, symbol) => {
  expect(stepTitle({
    ...player, hits: 2, hitsByDirection: { ...player.hitsByDirection!, [direction]: 2 },
  })).toEqual({ title, symbol, detail: `Most hits: ${direction} (2 hits).` });
});

it('keeps a warming-up tile instead of inventing titles for unavailable or zero hits', () => {
  for (const state of [
    { ...player, hitsByDirection: null },
    { ...player, missesByDirection: undefined },
    { ...player, missesByDirection: null },
    player,
  ]) {
    expect(stepTitle(state)).toMatchObject({ title: 'Warming up', symbol: '\u266a' });
  }
  expect(stepTitle({ ...player, hitsByDirection: undefined }).detail).toBe(
    'Waiting for per-lane hit data.',
  );
});

it.each([2, 3, 4])('keeps a Joint leaders tile when %s lanes tie for most hits', count => {
  const leaders = DIRECTIONS.slice(0, count);
  const hitsByDirection = { ...player.hitsByDirection! };
  for (const direction of leaders) hitsByDirection[direction] = 1;
  expect(stepTitle({ ...player, hits: count, hitsByDirection })).toMatchObject({
    title: 'Joint leaders',
    detail: `Most hits: ${leaders.join(', ')} (1 hit each).`,
  });
  expect(stepTitle({ ...player, hits: count, hitsByDirection }).symbol.split(' ')).toHaveLength(count);
});

it('awards All-Step Ace only for equal positive hit counts regardless of misses', () => {
  expect(stepTitle({
    ...player, hits: 10, hitsByDirection: {
      'Up-Left': 2, 'Up-Right': 2, 'Down-Left': 2, 'Down-Right': 2, Center: 2,
    }, missesByDirection: {
      'Up-Left': 1, 'Up-Right': 2, 'Down-Left': 3, 'Down-Right': 4, Center: 5,
    },
  })).toMatchObject({ title: 'All-Step Ace', detail: 'All five directions tied at 2 hits each.' });
  expect(DIRECTIONS).toHaveLength(5);
});

it('chooses hit volume over accuracy and excludes unassigned outcomes', () => {
  const result = stepTitle({
    ...player, hits: 110, misses: 100, unassignedHits: 100, unassignedMisses: 97,
    hitsByDirection: { ...player.hitsByDirection!, 'Down-Left': 6, 'Up-Right': 4 },
    missesByDirection: { ...player.missesByDirection!, 'Down-Left': 3 },
  });
  expect(result).toEqual({
    title: 'Down-Left Dynamo', symbol: '\u2199',
    detail: 'Most hits: Down-Left (6 hits).',
  });
});

it('awards Down-Left for the reported scoreboard counts even without per-lane misses', () => {
  expect(stepTitle({
    ...player, hits: 45, misses: 4, unassignedHits: 4,
    hitsByDirection: { 'Up-Right': 5, 'Up-Left': 10, 'Down-Right': 10, 'Down-Left': 11, Center: 5 },
    missesByDirection: undefined,
  })).toEqual({
    title: 'Down-Left Dynamo', symbol: '\u2199', detail: 'Most hits: Down-Left (11 hits).',
  });
});
it('does not reward zero-percent or unplayed lanes', () => {
  expect(stepTitle({
    ...player, misses: 2, missesByDirection: { ...player.missesByDirection!, Center: 2 },
  })).toMatchObject({ title: 'Warming up', detail: 'Waiting for a recognized hit with a direction.' });
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
