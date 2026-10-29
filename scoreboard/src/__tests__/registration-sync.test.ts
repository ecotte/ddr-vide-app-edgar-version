import { expect, it, vi } from 'vitest';
import { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../../rayfin/data/schema';
import { syncRegistrations } from '../../scripts/registrations';

it('closes the previous game and relays at most 25 rows across current and historical registration tables without PINs', async () => {
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
  vi.spyOn(users, 'execute').mockResolvedValue([]);
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
    registrationType: 'special' as const, createdAt: now, eventQueued: false };
  vi.spyOn(slots, 'execute').mockResolvedValue(Array.from({ length: 25 }, () => ({ ...row, id: crypto.randomUUID() })));
  const updateSlot = vi.spyOn(client.data.GamePlayerSession, 'update').mockResolvedValue(row);
  const player = { score: null, combo: null, hits: 0, observedScore: null, lastVideoTimeMs: null };
  const snapshot = { active: true, sessionId: current, revision: 1, players: { P1: player, P2: player } };
  const rows = await syncRegistrations(client, snapshot, [], [], [row.id], previous);
  expect(updateGame).toHaveBeenCalledWith({ id: previous }, { active: false, endedAt: expect.any(Date) });
  expect(updateSlot).toHaveBeenCalledWith({ id: row.id }, { eventQueued: true });
  expect(rows).toHaveLength(25);
  expect(rows[0]).toMatchObject({ player: 'P1', relaySource: 'slot', registrationType: 'special' });
  expect(selectUsers).toHaveBeenCalledWith(['id', 'gameSessionId', 'player', 'username', 'createdAt']);
  expect(selectSlots).toHaveBeenCalledWith(['id', 'gameSessionId', 'player', 'username', 'createdAt', 'registrationType']);
  expect(first).toHaveBeenCalledWith(25);
  await expect(syncRegistrations(client, snapshot, [], [], ['bad'])).rejects.toThrow('Invalid registration acknowledgment');
  vi.restoreAllMocks();
});
