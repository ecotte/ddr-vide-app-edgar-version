import { describe, it, expect, vi, afterEach } from 'vitest';
import { arrowAccuracy, parseSnapshot, poll } from '../state';

const player = { username: null, score: null, combo: 3, hits: 0, misses: 0, unassignedHits: 0, unassignedMisses: 0,
  hitsByDirection: { 'Up-Right': 0, 'Up-Left': 0, 'Down-Right': 0, 'Down-Left': 0, Center: 0 },
  hitPace: 0, averageResponseTimeMs: null, responseSamples: 0, observedScore: 4, lastVideoTimeMs: 10 };
const snapshot = { active: true, sessionId: 'session', revision: 1, players: { P1: player, P2: player } };

describe('scoreboard state', () => {
  it('preserves independent current and best streaks without fabricating legacy values', () => {
    const parsed = parseSnapshot({
      ...snapshot, players: {
        P1: { ...player, hits: 8, bestStreak: 5, currentStreak: 2 },
        P2: { ...player, bestStreak: 0, currentStreak: 0 },
      },
    });
    expect(parsed.players.P1.bestStreak).toBe(5);
    expect(parsed.players.P1.currentStreak).toBe(2);
    expect(parsed.players.P2.bestStreak).toBe(0);
    expect(parseSnapshot(snapshot).players.P1.bestStreak).toBeUndefined();
    expect(parseSnapshot(snapshot).players.P1.currentStreak).toBeUndefined();
  });
  it.each([
    { bestStreak: -1, currentStreak: 0 },
    { bestStreak: 2, currentStreak: 3 },
    { bestStreak: 9, currentStreak: 0 },
    { bestStreak: 1.5, currentStreak: 1 },
    { bestStreak: '2', currentStreak: 1 },
    { bestStreak: 2, currentStreak: true },
    { bestStreak: 2 },
    { currentStreak: 1 },
    { bestStreak: Infinity, currentStreak: 0 },
    { bestStreak: NaN, currentStreak: 0 },
  ])('rejects invalid streak data %j', streak => {
    expect(() => parseSnapshot({
      ...snapshot, players: { P1: { ...player, hits: 8, ...streak }, P2: player },
    })).toThrow('Invalid scoreboard data');
  });
  it('preserves independent lane misses without inventing them for older snapshots', () => {
    const misses = { 'Up-Right': 1, 'Up-Left': 2, 'Down-Right': 3, 'Down-Left': 4, Center: 5 };
    const parsed = parseSnapshot({
      ...snapshot, players: { P1: { ...player, missesByDirection: misses }, P2: player },
    });
    expect(parsed.players.P1.missesByDirection).toEqual(misses);
    expect(parsed.players.P1.missesByDirection).not.toBe(misses);
    expect(parsed.players.P2.missesByDirection).toBeUndefined();
  });
  it.each([-1, 0.5, '1', true, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1])(
    'rejects invalid per-lane miss count %s', count => {
      expect(() => parseSnapshot({
        ...snapshot, players: {
          P1: { ...player, missesByDirection: { ...player.hitsByDirection, Center: count } },
          P2: player,
        },
      })).toThrow('Invalid scoreboard data');
    },
  );
  it.each([[], {}, { 'Up-Right': 0, 'Up-Left': 0, 'Down-Right': 0, 'Down-Left': 0 }])(
    'rejects incomplete per-lane miss counts %j', missesByDirection => {
      expect(() => parseSnapshot({
        ...snapshot, players: { P1: { ...player, missesByDirection }, P2: player },
      })).toThrow('Invalid scoreboard data');
    },
  );
  it('preserves running scores through parsing and leaves older snapshots distinguishable', () => {
    for (const runningScore of [0, 10, 80, 130]) {
      expect(parseSnapshot({
        ...snapshot, players: { P1: { ...player, runningScore }, P2: player },
      }).players.P1.runningScore).toBe(runningScore);
    }
    expect(parseSnapshot(snapshot).players.P1.runningScore).toBeUndefined();
  });
  it.each([[4, 13], [5, 15]])('retains difficulty %s and base score without applying the multiplier again', (difficulty, score) => {
    const parsed = parseSnapshot({
      ...snapshot, players: { P1: { ...player, runningScore: score, baseRunningScore: 10, difficulty }, P2: player },
    });
    expect(parsed.players.P1).toMatchObject({ runningScore: score, baseRunningScore: 10, difficulty });
    expect(parseSnapshot(parsed)).toEqual(parsed);
    expect(parsed.players.P2.difficulty).toBeUndefined();
    expect(parsed.players.P2.baseRunningScore).toBeUndefined();
  });
  it.each([
    { difficulty: 1 }, { difficulty: 2 }, { difficulty: 6 }, { difficulty: '4' }, { difficulty: '5' }, { difficulty: true },
    { baseRunningScore: -1 }, { baseRunningScore: 1.5 }, { baseRunningScore: '5' },
    { baseRunningScore: 10, runningScore: null },
  ])('rejects invalid difficulty or base score %j', invalid => {
    expect(() => parseSnapshot({
      ...snapshot, players: { P1: { ...player, runningScore: 13, ...invalid }, P2: player },
    })).toThrow('Invalid scoreboard data');
  });
  it.each([-1, 1.5, '40', true, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1])(
    'rejects invalid running score %s', runningScore => {
      expect(() => parseSnapshot({
        ...snapshot, players: { P1: { ...player, runningScore }, P2: player },
      })).toThrow('Invalid scoreboard data');
    },
  );
  it('preserves unknown-direction outcomes while older snapshots default to zero', () => {
    const updated = { ...player, hits: 3, misses: 2, unassignedHits: 2, unassignedMisses: 1 };
    expect(parseSnapshot({ ...snapshot, players: { P1: updated, P2: player } }).players.P1).toEqual(updated);
    const legacy = { ...player, unassignedHits: undefined, unassignedMisses: undefined };
    const parsed = parseSnapshot({ ...snapshot, players: { P1: legacy, P2: legacy } });
    expect(parsed.players.P1.unassignedHits).toBe(0);
    expect(parsed.players.P1.unassignedMisses).toBe(0);
  });
  it.each([
    { unassignedHits: -1 }, { unassignedHits: 1 }, { unassignedHits: null },
    { unassignedMisses: 1 }, { unassignedMisses: '1' },
  ])('rejects invalid unknown-direction counts %j', invalid => {
    expect(() => parseSnapshot({
      ...snapshot, players: { P1: { ...player, ...invalid }, P2: player },
    })).toThrow();
  });
  it('preserves check-in names and accepts snapshots from older publishers', () => {
    expect(parseSnapshot({
      ...snapshot, players: { P1: { ...player, username: 'SillyEventhouse1234' }, P2: player },
    }).players.P1.username).toBe('SillyEventhouse1234');
    expect(parseSnapshot({
      ...snapshot, players: { P1: { ...player, username: undefined }, P2: player },
    }).players.P1.username).toBeNull();
  });
  it.each([123, '', '   ', 'x'.repeat(81)])('rejects invalid player names %s', username => {
    expect(() => parseSnapshot({
      ...snapshot, players: { P1: { ...player, username }, P2: player },
    })).toThrow('Invalid scoreboard data');
  });
  it('retains accepted and observed values separately', () => {
    expect(parseSnapshot(snapshot).players.P1).toEqual(player);
  });
  it('does not invent a directional breakdown for older snapshots', () => {
    const legacy = { ...player, hitsByDirection: undefined, hits: 10, misses: 2 };
    const result = parseSnapshot({ ...snapshot, players: { P1: legacy, P2: legacy } });
    expect(result.players.P1.hitsByDirection).toBeNull();
    expect(result.players.P1.hits).toBe(10);
    expect(result.players.P1.misses).toBe(2);
  });
  it('rejects invalid directional counts', () => {
    const invalid = { ...player, hitsByDirection: { ...player.hitsByDirection, Center: -1 } };
    expect(() => parseSnapshot({ ...snapshot, players: { P1: invalid, P2: player } })).toThrow();
  });
  it('rejects four-direction snapshots instead of inventing a five-direction mapping', () => {
    const legacy = { ...player, hitsByDirection: { Left: 1, Down: 2, Up: 3, Right: 4 } };
    expect(() => parseSnapshot({ ...snapshot, players: { P1: legacy, P2: player } })).toThrow();
  });
  it('requires the center count in five-direction snapshots', () => {
    const incomplete = { ...player, hitsByDirection: {
      'Up-Right': 0, 'Up-Left': 0, 'Down-Right': 0, 'Down-Left': 0,
    } };
    expect(() => parseSnapshot({ ...snapshot, players: { P1: incomplete, P2: player } })).toThrow();
  });
  it('calculates per-lane accuracy instead of hit distribution and handles unplayed lanes', () => {
    expect(arrowAccuracy(player)).toBeNull();
    expect(arrowAccuracy()).toBeNull();
    expect(arrowAccuracy({ ...player, hitsByDirection: null })).toBeNull();
    expect(arrowAccuracy({ ...player, missesByDirection: undefined })).toBeNull();
    const rates = arrowAccuracy({
      ...player, hits: 20, misses: 9, unassignedHits: 4, unassignedMisses: 3,
      hitsByDirection: { 'Up-Right': 1, 'Up-Left': 2, 'Down-Right': 3, 'Down-Left': 4, Center: 10 },
      missesByDirection: { 'Up-Right': 1, 'Up-Left': 2, 'Down-Right': 1, 'Down-Left': 0, Center: 0 },
    });
    expect(rates).toEqual({ 'Up-Right': 50, 'Up-Left': 50, 'Down-Right': 75, 'Down-Left': 100, Center: 100 });
    expect(arrowAccuracy({
      ...player, misses: 2,
      missesByDirection: { ...player.hitsByDirection, Center: 2 },
    })).toEqual({ 'Up-Right': null, 'Up-Left': null, 'Down-Right': null, 'Down-Left': null, Center: 0 });
  });
  it('keeps a missing pace unavailable for old snapshots', () => {
    const legacy = { ...player, hitPace: undefined };
    expect(parseSnapshot({ ...snapshot, players: { P1: legacy, P2: player } }).players.P1.hitPace).toBeNull();
  });
  it.each([-1, NaN, Infinity, '2'])('rejects invalid pace %s', hitPace => {
    expect(() => parseSnapshot({
      ...snapshot, players: { P1: { ...player, hitPace }, P2: player },
    })).toThrow();
  });
  it('keeps new metrics unavailable in legacy snapshots instead of inventing data', () => {
    const legacy = { ...player, misses: undefined, averageResponseTimeMs: undefined, responseSamples: undefined };
    const result = parseSnapshot({ ...snapshot, players: { P1: legacy, P2: player } }).players.P1;
    expect(result.misses).toBeNull();
    expect(result.averageResponseTimeMs).toBeNull();
    expect(result.responseSamples).toBe(0);
  });
  it.each([-1, NaN, Infinity, '2', true])('rejects invalid misses %s', misses => {
    expect(() => parseSnapshot({ ...snapshot, players: { P1: { ...player, misses }, P2: player } })).toThrow();
  });
  it.each([
    { averageResponseTimeMs: -1, responseSamples: 1 },
    { averageResponseTimeMs: 0, responseSamples: 1 },
    { averageResponseTimeMs: NaN, responseSamples: 1 },
    { averageResponseTimeMs: Infinity, responseSamples: 1 },
    { averageResponseTimeMs: '123', responseSamples: 1 },
    { averageResponseTimeMs: 123, responseSamples: 0 },
    { averageResponseTimeMs: 123, responseSamples: 3 },
    { averageResponseTimeMs: 123, responseSamples: 1.5 },
    { averageResponseTimeMs: 123 },
    { averageResponseTimeMs: null, responseSamples: 1 },
  ])('rejects inconsistent response measurements %j', timing => {
    expect(() => parseSnapshot({
      ...snapshot, players: { P1: { ...player, hits: 2, ...timing }, P2: player },
    })).toThrow();
  });
  it('preserves positive fractional response averages', () => {
    for (const averageResponseTimeMs of [1, 123.5]) {
      expect(parseSnapshot({
        ...snapshot, players: { P1: { ...player, hits: 2, averageResponseTimeMs, responseSamples: 2 }, P2: player },
      }).players.P1.averageResponseTimeMs).toBe(averageResponseTimeMs);
    }
  });
  it.each([null, {}, { ...snapshot, revision: -1 }, {
    ...snapshot, players: { P1: { ...player, score: '123' }, P2: player },
  }])('rejects invalid input', value => {
    expect(() => parseSnapshot(value)).toThrow('Invalid scoreboard data');
  });
});

afterEach(() => vi.useRealTimers());
it('supports immediate sequential refresh and response-dependent retry delay', async () => {
  vi.useFakeTimers();
  let finish!: () => void;
  let delay = 0;
  const task = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
  const stop = poll(task, () => delay);
  await vi.advanceTimersByTimeAsync(3000);
  expect(task).toHaveBeenCalledTimes(1);
  finish();
  await vi.advanceTimersByTimeAsync(1);
  expect(task).toHaveBeenCalledTimes(2);
  delay = 1000;
  finish();
  await vi.advanceTimersByTimeAsync(999);
  expect(task).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(1);
  expect(task).toHaveBeenCalledTimes(3);
  stop();
  finish();
  await vi.advanceTimersByTimeAsync(5000);
  expect(task).toHaveBeenCalledTimes(3);
});

it('polls after completion, never overlaps and stops cleanly', async () => {
  vi.useFakeTimers();
  let finish!: () => void;
  const task = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
  const stop = poll(task);
  await vi.advanceTimersByTimeAsync(5000);
  expect(task).toHaveBeenCalledTimes(1);
  finish();
  await vi.advanceTimersByTimeAsync(999);
  expect(task).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(task).toHaveBeenCalledTimes(2);
  stop();
  finish();
  await vi.advanceTimersByTimeAsync(5000);
  expect(task).toHaveBeenCalledTimes(2);
});
