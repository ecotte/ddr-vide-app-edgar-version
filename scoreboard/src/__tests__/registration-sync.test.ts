import { expect, it, vi } from 'vitest';
import { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../../rayfin/data/schema';
import { syncRegistrations, withPlayerNames } from '../../scripts/registrations';
import { scoreForDifficulty, type Difficulty } from '../../rayfin/functions/src/difficulty';

it('closes the previous game and selects PINs for normal registration relay with a shared 25-row limit', async () => {
  const client = new RayfinClient<AppSchema>({
    baseUrl: 'https://example.invalid/', publishableKey: 'pk-test', authStorage: false,
  });

  const current = crypto.randomUUID(), previous = crypto.randomUUID();
  const now = new Date();
  const game = { id: current, startedAt: now, lastSeenAt: now, active: true };
  const updateGame = vi.spyOn(client.data.GameSession, 'update').mockResolvedValue(game);
  vi.spyOn(client.data.GameSession, 'upsert').mockResolvedValue(game);
  const users = client.data.UserSession.select(['id']);
  const selectUsers = vi.spyOn(client.data.UserSession, 'select').mockReturnValue(users);
  vi.spyOn(users, 'where').mockReturnValue(users);
  vi.spyOn(users, 'orderBy').mockReturnValue(users);
  vi.spyOn(users, 'first').mockReturnValue(users);
  const executeUsers = vi.spyOn(users, 'execute').mockResolvedValue([]);
  const specials = client.data.SpecialSession.select(['id']);
  vi.spyOn(client.data.SpecialSession, 'select').mockReturnValue(specials);
  vi.spyOn(specials, 'where').mockReturnValue(specials);
  vi.spyOn(specials, 'orderBy').mockReturnValue(specials);
  vi.spyOn(specials, 'first').mockReturnValue(specials);
  vi.spyOn(specials, 'execute').mockResolvedValue([]);
  const slots = client.data.GamePlayerSession.select(['id']);
  const selectSlots = vi.spyOn(client.data.GamePlayerSession, 'select').mockReturnValue(slots);
  vi.spyOn(slots, 'where').mockReturnValue(slots);
  vi.spyOn(slots, 'orderBy').mockReturnValue(slots);
  const first = vi.spyOn(slots, 'first').mockReturnValue(slots);
  const row = { id: crypto.randomUUID(), gameSessionId: current, player: 'P1' as const,
    slotKey: `${current}:P1`, username: 'YaelTheChampion', usernameKey: 'special:test', pinKey: 'special:test',
    registrationType: 'special' as const, createdAt: now, eventQueued: false, excludeFromLeaderboard: true, difficulty: 0 };
  const executeSlots = vi.spyOn(slots, 'execute').mockResolvedValue(
    Array.from({ length: 25 }, () => ({ ...row, id: crypto.randomUUID() })),
  );
  const updateSlot = vi.spyOn(client.data.GamePlayerSession, 'update').mockResolvedValue(row);
  const player = { score: null, combo: null, hits: 0, observedScore: null, lastVideoTimeMs: null };
  const snapshot = { active: true, sessionId: current, revision: 1, players: { P1: player, P2: player } };
  const rows = await syncRegistrations(client, snapshot, [], [], [row.id], previous);
  expect(updateGame).toHaveBeenCalledWith({ id: previous }, { active: false, endedAt: expect.any(Date) });
  expect(updateSlot).toHaveBeenCalledWith({ id: row.id }, { eventQueued: true });
  expect(rows).toHaveLength(25);
  expect(rows[0]).toMatchObject({
    player: 'P1', relaySource: 'slot', registrationType: 'special', excludeFromLeaderboard: true, difficulty: 0,
  });
  expect(selectUsers).toHaveBeenCalledWith(['id', 'gameSessionId', 'player', 'username', 'pin', 'createdAt']);
  expect(selectSlots).toHaveBeenCalledWith([
    'id', 'gameSessionId', 'player', 'username', 'pin', 'createdAt', 'registrationType', 'excludeFromLeaderboard', 'difficulty',
  ]);
  expect(first).toHaveBeenCalledWith(25);
  executeUsers.mockResolvedValue([{
    id: crypto.randomUUID(), gameSessionId: current, player: 'P1',
    username: 'HappyLakehouse1234', pin: '001234', createdAt: now, eventQueued: false,
  }]);
  executeSlots.mockResolvedValue([{
    ...row, registrationType: 'player', username: 'HappyLakehouse5678', pin: '005678', difficulty: 4, excludeFromLeaderboard: false,
  }]);
  const normalRows = await syncRegistrations(client, snapshot, [], [], [], current);
  expect(normalRows).toHaveLength(2);
  expect(normalRows[0]).toMatchObject({ registrationType: 'player', pin: '001234' });
  expect(normalRows[1]).toMatchObject({ registrationType: 'player', pin: '005678', relaySource: 'slot', difficulty: 4 });
  await expect(syncRegistrations(client, snapshot, [], [], ['bad'])).rejects.toThrow('Invalid registration acknowledgment');
  vi.restoreAllMocks();
});

it('publishes only current-game check-in names, including already-relayed and special players, without PINs', async () => {
  const client = new RayfinClient<AppSchema>({
    baseUrl: 'https://example.invalid/', publishableKey: 'pk-test', authStorage: false,
  });
  const sessionId = crypto.randomUUID();
  const nextId = crypto.randomUUID();
  const query = client.data.GamePlayerSession.select(['player', 'username', 'difficulty']);
  const select = vi.spyOn(client.data.GamePlayerSession, 'select').mockReturnValue(query);
  const where = vi.spyOn(query, 'where').mockReturnValue(query);
  const first = vi.spyOn(query, 'first').mockReturnValue(query);
  const row = {
    id: crypto.randomUUID(), gameSessionId: sessionId, slotKey: `${sessionId}:P1`,
    usernameKey: 'special:test', pinKey: 'special:test', registrationType: 'special' as const,
    createdAt: new Date(), eventQueued: true,
  };
  const execute = vi.spyOn(query, 'execute').mockResolvedValue([
    { ...row, player: 'P2', username: 'HappyLakehouse5678', pin: '005678' },
    { ...row, player: 'P1', username: 'YaelTheChampion' },
  ]);
  const player = { score: 1200, combo: 65, hits: 20, observedScore: null, lastVideoTimeMs: 1000 };
  const snapshot = { active: true, sessionId, revision: 1, players: { P1: player, P2: player } };
  const named = await withPlayerNames(client, snapshot);
  expect(select).toHaveBeenCalledWith(['player', 'username', 'difficulty']);
  expect(where).toHaveBeenCalledWith({ gameSessionId: { eq: sessionId } });
  expect(first).toHaveBeenCalledWith(2);
  expect(named.players.P1).toEqual({ ...player, username: 'YaelTheChampion', difficulty: null });
  expect(named.players.P2).toEqual({ ...player, username: 'HappyLakehouse5678', difficulty: null });
  expect(snapshot.players.P1).not.toHaveProperty('username');
  execute.mockResolvedValue([{ ...row, player: 'P2', username: 'HappyLakehouse5678' }]);
  const singlePlayer = await withPlayerNames(client, named);
  expect(singlePlayer.players.P1.username).toBeNull();
  expect(singlePlayer.players.P2.username).toBe('HappyLakehouse5678');
  execute.mockResolvedValue([]);
  const next = await withPlayerNames(client, { ...named, sessionId: nextId });
  expect(where).toHaveBeenLastCalledWith({ gameSessionId: { eq: nextId } });
  expect(next.players.P1.username).toBeNull();
  expect(next.players.P2.username).toBeNull();
  await withPlayerNames(client, { ...snapshot, sessionId: '' });
  expect(execute).toHaveBeenCalledTimes(3);
  execute.mockRejectedValue(new Error('Name lookup failed'));
  await expect(withPlayerNames(client, snapshot)).rejects.toThrow('Name lookup failed');
  vi.restoreAllMocks();
});

it.each([
  [0, 0], [5, 6], [10, 13], [15, 19], [40, 50], [100, 125],
  [7205759403792793, 9007199254740991],
])('rounds the entire Normal 4 base score %s to %s without intermediate rounding', (base, expected) => {
  expect(scoreForDifficulty(base, 4)).toBe(expected);
});

it.each([0, 3, null] satisfies (Difficulty | null)[])('keeps difficulty %s scores unmultiplied', difficulty => {
  expect(scoreForDifficulty(105, difficulty)).toBe(105);
});

it.each([-1, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1])('rejects invalid base score %s', base => {
  expect(() => scoreForDifficulty(base, 4)).toThrow('Invalid base score');
});

it('rejects an adjusted score beyond safe integer range rather than silently rounding inaccurately', () => {
  expect(() => scoreForDifficulty(7205759403792794, 4)).toThrow('exceeds');
});

it('applies each current-game registration to its full base total, never compounds, and clears on a new game', async () => {
  const client = new RayfinClient<AppSchema>({
    baseUrl: 'https://example.invalid/', publishableKey: 'pk-test', authStorage: false,
  });
  const id = crypto.randomUUID();
  const row = {
    id: crypto.randomUUID(), gameSessionId: id, player: 'P1' as const, username: 'HappyLakehouse5678',
    slotKey: `${id}:P1`, usernameKey: 'player:test', pinKey: 'pin:test', pin: '123456',
    registrationType: 'player' as const, createdAt: new Date(), eventQueued: true,
    difficulty: 4, excludeFromLeaderboard: false,
  };
  const query = client.data.GamePlayerSession.select(['player', 'username', 'difficulty']);
  vi.spyOn(client.data.GamePlayerSession, 'select').mockReturnValue(query);
  const where = vi.spyOn(query, 'where').mockReturnValue(query);
  vi.spyOn(query, 'first').mockReturnValue(query);
  const execute = vi.spyOn(query, 'execute').mockResolvedValue([]);
  const player = { score: 9999, runningScore: 105, combo: 6, hits: 10, observedScore: null, lastVideoTimeMs: 1000 };
  const input = { active: true, sessionId: id, revision: 1, players: { P1: player, P2: player } };
  try {
    const before = await withPlayerNames(client, input);
    expect(before.players.P1.runningScore).toBe(105);
    execute.mockResolvedValue([row, { ...row, player: 'P2', difficulty: 3 }]);
    const registered = await withPlayerNames(client, input);
    expect(registered.players.P1).toMatchObject({ runningScore: 131, baseRunningScore: 105, difficulty: 4, hits: 10, combo: 6, score: 9999 });
    expect(registered.players.P2).toMatchObject({ runningScore: 105, baseRunningScore: 105, difficulty: 3 });
    expect(input.players.P1.runningScore).toBe(105);
    expect(await withPlayerNames(client, registered)).toEqual(registered);
    execute.mockResolvedValue([{ ...row, difficulty: 0, excludeFromLeaderboard: true }]);
    expect((await withPlayerNames(client, input)).players.P1.runningScore).toBe(105);
    execute.mockResolvedValue([]);
    const next = await withPlayerNames(client, {
      ...input, sessionId: crypto.randomUUID(),
      players: { P1: { ...player, runningScore: 0 }, P2: { ...player, runningScore: 0 } },
    });
    expect(where).toHaveBeenLastCalledWith({ gameSessionId: { eq: next.sessionId } });
    expect(next.players.P1).toMatchObject({ runningScore: 0, baseRunningScore: 0, difficulty: null, username: null });
    execute.mockResolvedValue([{ ...row, difficulty: 2 }]);
    await expect(withPlayerNames(client, input)).rejects.toThrow('Invalid registered player difficulty');
  } finally {
    client.auth.destroy();
    vi.restoreAllMocks();
  }
});
