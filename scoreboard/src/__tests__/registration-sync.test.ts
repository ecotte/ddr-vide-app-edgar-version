import { expect, it, vi } from 'vitest';
import { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../../rayfin/data/schema';
import { syncRegistrations, withPlayerNames } from '../../scripts/registrations';

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
    registrationType: 'special' as const, createdAt: now, eventQueued: false, excludeFromLeaderboard: true };
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
    player: 'P1', relaySource: 'slot', registrationType: 'special', excludeFromLeaderboard: true,
  });
  expect(selectUsers).toHaveBeenCalledWith(['id', 'gameSessionId', 'player', 'username', 'pin', 'createdAt']);
  expect(selectSlots).toHaveBeenCalledWith([
    'id', 'gameSessionId', 'player', 'username', 'pin', 'createdAt', 'registrationType', 'excludeFromLeaderboard',
  ]);
  expect(first).toHaveBeenCalledWith(25);
  executeUsers.mockResolvedValue([{
    id: crypto.randomUUID(), gameSessionId: current, player: 'P1',
    username: 'HappyLakehouse1234', pin: '001234', createdAt: now, eventQueued: false,
  }]);
  executeSlots.mockResolvedValue([{
    ...row, registrationType: 'player', username: 'HappyLakehouse5678', pin: '005678',
  }]);
  const normalRows = await syncRegistrations(client, snapshot, [], [], [], current);
  expect(normalRows).toHaveLength(2);
  expect(normalRows[0]).toMatchObject({ registrationType: 'player', pin: '001234' });
  expect(normalRows[1]).toMatchObject({ registrationType: 'player', pin: '005678', relaySource: 'slot' });
  await expect(syncRegistrations(client, snapshot, [], [], ['bad'])).rejects.toThrow('Invalid registration acknowledgment');
  vi.restoreAllMocks();
});

it('publishes only current-game check-in names, including already-relayed and special players, without PINs', async () => {
  const client = new RayfinClient<AppSchema>({
    baseUrl: 'https://example.invalid/', publishableKey: 'pk-test', authStorage: false,
  });
  const sessionId = crypto.randomUUID();
  const nextId = crypto.randomUUID();
  const query = client.data.GamePlayerSession.select(['player', 'username']);
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
  expect(select).toHaveBeenCalledWith(['player', 'username']);
  expect(where).toHaveBeenCalledWith({ gameSessionId: { eq: sessionId } });
  expect(first).toHaveBeenCalledWith(2);
  expect(named.players.P1).toEqual({ ...player, username: 'YaelTheChampion' });
  expect(named.players.P2).toEqual({ ...player, username: 'HappyLakehouse5678' });
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
