import { describe, it, expect, vi, afterEach } from 'vitest';
import { arrowBalance, parseSnapshot, poll } from '../state';

const player = { score: null, combo: 3, hits: 0,
  hitsByDirection: { Left: 0, Down: 0, Up: 0, Right: 0 }, hitPace: 0, observedScore: 4, lastVideoTimeMs: 10 };
const snapshot = { active: true, sessionId: 'session', revision: 1, players: { P1: player, P2: player } };

describe('scoreboard state', () => {
  it('retains accepted and observed values separately', () => {
    expect(parseSnapshot(snapshot).players.P1).toEqual(player);
  });
  it('does not invent a directional breakdown for older snapshots or retain misses', () => {
    const legacy = { ...player, hitsByDirection: undefined, hits: 10, misses: 2 };
    const result = parseSnapshot({ ...snapshot, players: { P1: legacy, P2: legacy } });
    expect(result.players.P1.hitsByDirection).toBeNull();
    expect(result.players.P1.hits).toBe(10);
    expect(result.players.P1).not.toHaveProperty('misses');
  });
  it('rejects invalid directional counts', () => {
    const invalid = { ...player, hitsByDirection: { Left: -1, Down: 0, Up: 0, Right: 0 } };
    expect(() => parseSnapshot({ ...snapshot, players: { P1: invalid, P2: player } })).toThrow();
  });
  it('calculates balance from actual directional counts and handles no hits', () => {
    expect(arrowBalance(player)).toBeNull();
    expect(arrowBalance()).toBeNull();
    expect(arrowBalance({ ...player, hitsByDirection: null })).toBeNull();
    const shares = arrowBalance({
      ...player, hits: 10, hitsByDirection: { Left: 1, Down: 2, Up: 3, Right: 4 },
    });
    expect(shares).toEqual({ Left: 10, Down: 20, Up: 30, Right: 40 });
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
  it.each([null, {}, { ...snapshot, revision: -1 }, {
    ...snapshot, players: { P1: { ...player, score: '123' }, P2: player },
  }])('rejects invalid input', value => {
    expect(() => parseSnapshot(value)).toThrow('Invalid scoreboard data');
  });
});

afterEach(() => vi.useRealTimers());
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
