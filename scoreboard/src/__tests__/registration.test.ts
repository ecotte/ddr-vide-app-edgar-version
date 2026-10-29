import { afterEach, expect, it, vi } from 'vitest';
import { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../../rayfin/data/schema';
import type { GamePlayerSession } from '../../rayfin/data/GamePlayerSession';
import type { AppFunctionsSchema } from '../../rayfin/functions/src/types';
import { registerPlayer } from '../../rayfin/functions/src/registration';
import { ADJECTIVES, NOUNS, newIdentity } from '../../rayfin/functions/src/identity';
import { registrationStation, saveRegistration } from '../registration';

const gameId = 'c7510905-589f-4d05-bd14-cd718e288a49';
function setup() {
  const client = new RayfinClient<AppSchema, AppFunctionsSchema>({
    baseUrl: 'https://example.invalid/', publishableKey: 'pk-test', authStorage: false,
  });
  const game = { id: gameId, startedAt: new Date(), lastSeenAt: new Date(), active: true };
  const games = client.data.GameSession.select(['id']);
  vi.spyOn(client.data.GameSession, 'select').mockReturnValue(games);
  vi.spyOn(games, 'orderBy').mockReturnValue(games);
  vi.spyOn(games, 'first').mockReturnValue(games);
  const gameExecute = vi.spyOn(games, 'execute').mockResolvedValue([game]);
  const slots = client.data.GamePlayerSession.select(['id']);
  vi.spyOn(client.data.GamePlayerSession, 'select').mockReturnValue(slots);
  const slotWhere = vi.spyOn(slots, 'where').mockReturnValue(slots);
  vi.spyOn(slots, 'first').mockReturnValue(slots);
  const slotExecute = vi.spyOn(slots, 'execute').mockResolvedValue([]);
  const users = client.data.UserSession.select(['id']);
  vi.spyOn(client.data.UserSession, 'select').mockReturnValue(users);
  vi.spyOn(users, 'where').mockReturnValue(users);
  vi.spyOn(users, 'first').mockReturnValue(users);
  const userExecute = vi.spyOn(users, 'execute').mockResolvedValue([]);
  const specials = client.data.SpecialSession.select(['id']);
  vi.spyOn(client.data.SpecialSession, 'select').mockReturnValue(specials);
  vi.spyOn(specials, 'where').mockReturnValue(specials);
  vi.spyOn(specials, 'first').mockReturnValue(specials);
  vi.spyOn(specials, 'execute').mockResolvedValue([]);
  const find = vi.spyOn(client.data.GamePlayerSession, 'findById').mockResolvedValue(null);
  const create = vi.spyOn(client.data.GamePlayerSession, 'create').mockImplementation(async row => ({
    ...row, id: row.id ?? crypto.randomUUID(), pin: row.pin ?? undefined,
  }));
  return { client, game, gameExecute, slotExecute, slotWhere, userExecute, find, create };
}
afterEach(() => vi.restoreAllMocks());

it.each([['?player=1', 'P1'], ['?player=2', 'P2']])('reads station %s', (search, player) => {
  expect(registrationStation(search)).toEqual({ player, error: '' });
});
it.each(['', '?player=', '?player=3', '?player=P1', '?player=1&player=2'])('blocks missing or invalid station %s', search => {
  expect(registrationStation(search).error).not.toBe('');
});
it('generates forty distinct words per list and capitalized names with four digits and independent six-digit PINs', () => {
  for (const words of [ADJECTIVES, NOUNS]) {
    expect(new Set(words).size).toBeGreaterThanOrEqual(40);
    expect(new Set(words).size).toBe(words.length);
  }
  const names = new RegExp(`^(${ADJECTIVES.map(word => word[0].toUpperCase() + word.slice(1)).join('|')})`
    + `(${NOUNS.map(word => word[0].toUpperCase() + word.slice(1)).join('|')})[1-9][0-9]{3}$`);
  for (let i = 0; i < 1000; i++) {
    expect(newIdentity().username).toMatch(names);
    expect(newIdentity().pin).toMatch(/^[1-9][0-9]{5}$/);
  }
});
it('invokes exactly one typed function and performs no browser data checks', async () => {
  const { client } = setup();
  const request = { requestId: crypto.randomUUID(), player: 'P1', specialName: '' };
  const result = { id: request.requestId, player: 'P1' as const, username: 'SillyEventhouse1234',
    gameSessionId: gameId, pin: '123456', registrationType: 'player' as const };
  const invoke = vi.spyOn(client.functions.registerPlayer, 'invoke').mockResolvedValue(result);
  expect(await saveRegistration(client, request)).toEqual(result);
  expect(invoke).toHaveBeenCalledExactlyOnceWith(request, { timeoutMs: 120000 });
  expect(client.data.GameSession.select).not.toHaveBeenCalled();
});
it('rejects invalid parameters before accessing the database', async () => {
  const { client, find } = setup();
  for (const args of [['bad', 'P1', ''], [crypto.randomUUID(), 'P3', ''], [crypto.randomUUID(), 'P1', 'OtherName']]) {
    await expect(registerPlayer(client.data, args[0], args[1], args[2])).rejects.toThrow('Invalid player registration');
  }
  expect(find).not.toHaveBeenCalled();
});
it('rejects inactive, absent, ended and stale game sessions without reserving a slot', async () => {
  const { client, game, gameExecute, create } = setup();
  for (const rows of [[], [{ ...game, active: false }], [{ ...game, endedAt: new Date() }], [{ ...game, lastSeenAt: new Date(0) }]]) {
    gameExecute.mockResolvedValueOnce(rows);
    await expect(registerPlayer(client.data, crypto.randomUUID(), 'P1', '')).rejects.toThrow('No live game session');
  }
  expect(create).not.toHaveBeenCalled();
});
it('reserves the correct station with server-generated credentials and returns only player-pass fields', async () => {
  const { client, create } = setup();
  const id = crypto.randomUUID();
  const pass = await registerPlayer(client.data, id, 'P2', '');
  expect(pass).toMatchObject({ id, player: 'P2', gameSessionId: gameId, registrationType: 'player' });
  expect(pass.pin).toMatch(/^[1-9][0-9]{5}$/);
  expect(pass).not.toHaveProperty('pinKey');
  expect(create.mock.calls[0][0].slotKey).toBe(`${gameId}:P2`);
});
it('replays the same saved request even after the game ends, without revealing another player request', async () => {
  const { client, find, gameExecute, create } = setup();
  const id = crypto.randomUUID();
  const saved = { id, gameSessionId: gameId, player: 'P1', username: 'SillyEventhouse1234',
    pin: '123456', registrationType: 'player', slotKey: `${gameId}:P1`, usernameKey: 'test',
    pinKey: 'test', createdAt: new Date(), eventQueued: false } satisfies GamePlayerSession;
  find.mockResolvedValue(saved);
  gameExecute.mockResolvedValue([]);
  expect((await registerPlayer(client.data, id, 'P1', '')).id).toBe(id);
  await expect(registerPlayer(client.data, id, 'P2', '')).rejects.toThrow('different player');
  expect(create).not.toHaveBeenCalled();
});
it('recovers a committed row after a lost create response', async () => {
  const { client, create, find } = setup();
  create.mockImplementation(async row => {
    find.mockResolvedValue({ ...row, id: row.id ?? crypto.randomUUID() });
    throw new Error('Lost response');
  });
  const id = crypto.randomUUID();
  expect((await registerPlayer(client.data, id, 'P1', '')).id).toBe(id);
});
it('regenerates server credentials when a historical identity is already reserved', async () => {
  const { client, userExecute, create } = setup();
  const old = { id: crypto.randomUUID(), gameSessionId: crypto.randomUUID(), player: 'P2' as const,
    username: 'SillyEventhouse1234', pin: '123456', createdAt: new Date(), eventQueued: true };
  userExecute.mockResolvedValueOnce([]).mockResolvedValueOnce([old]).mockResolvedValue([]);
  await registerPlayer(client.data, crypto.randomUUID(), 'P1', '');
  expect(create).toHaveBeenCalledTimes(1);
  expect(userExecute.mock.calls.length).toBeGreaterThan(3);
});
it('does not disclose an existing pass when its request ID is reused with a different special choice', async () => {
  const { client, find, create } = setup();
  const id = crypto.randomUUID();
  find.mockResolvedValue({
    id, gameSessionId: gameId, player: 'P1', username: 'YaelTheChampion', pin: undefined,
    registrationType: 'special', slotKey: `${gameId}:P1`, usernameKey: 'special:test',
    pinKey: 'special:test', createdAt: new Date(), eventQueued: false,
  });
  await expect(registerPlayer(client.data, id, 'P1', 'EdgarIsGoingDown')).rejects.toThrow('different player');
  expect(create).not.toHaveBeenCalled();
});
it('blocks occupied slots and ambiguous historical games', async () => {
  const { client, slotExecute, create, userExecute } = setup();
  const saved = { id: crypto.randomUUID(), gameSessionId: gameId, player: 'P1' as const,
    username: 'SillyEventhouse1234', pin: '123456', registrationType: 'player' as const,
    slotKey: `${gameId}:P1`, usernameKey: 'player:test', pinKey: 'pin:123456', createdAt: new Date(), eventQueued: false };
  slotExecute.mockResolvedValueOnce([saved]);
  await expect(registerPlayer(client.data, crypto.randomUUID(), 'P1', '')).rejects.toThrow('already registered');
  userExecute.mockResolvedValueOnce([{ ...saved, player: undefined }]);
  await expect(registerPlayer(client.data, crypto.randomUUID(), 'P1', '')).rejects.toThrow('before station limits');
  expect(create).not.toHaveBeenCalled();
});
it.each(['', 'YaelTheChampion'])('lets only one request win when a normal and %s registration race', async special => {
  const { client, slotExecute, slotWhere, find, create } = setup();
  let winner: GamePlayerSession | undefined;
  find.mockImplementation(async id => winner?.id === id ? winner : null);
  slotExecute.mockImplementation(async () => {
    const filter = slotWhere.mock.lastCall?.[0];
    return winner && JSON.stringify(filter).includes(winner.slotKey) ? [winner] : [];
  });
  create.mockImplementation(async row => {
    if (winner) throw new Error('SQL unique slot constraint');
    winner = { ...row, id: row.id ?? crypto.randomUUID() };
    return winner;
  });
  const results = await Promise.allSettled([
    registerPlayer(client.data, crypto.randomUUID(), 'P1', ''),
    registerPlayer(client.data, crypto.randomUUID(), 'P1', special),
  ]);
  expect(results.filter(result => result.status === 'fulfilled')).toHaveLength(1);
  expect(results.filter(result => result.status === 'rejected')).toHaveLength(1);
});
it.each(['YaelTheChampion', 'EdgarIsGoingDown'])('keeps %s in the same slot limit without generating a PIN', async name => {
  const { client, create } = setup();
  const pass = await registerPlayer(client.data, crypto.randomUUID(), 'P1', name);
  expect(pass).toMatchObject({ username: name, pin: null, registrationType: 'special' });
  expect(create.mock.calls[0][0]).not.toHaveProperty('pin');
});
it('surfaces unrelated database errors without successful-looking fallbacks', async () => {
  const { client, create } = setup();
  create.mockRejectedValue(new Error('Permission denied'));
  await expect(registerPlayer(client.data, crypto.randomUUID(), 'P1', '')).rejects.toThrow('Permission denied');
});
