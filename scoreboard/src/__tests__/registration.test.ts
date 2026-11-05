import { afterEach, expect, it, vi } from 'vitest';
import { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../../rayfin/data/schema';
import type { GamePlayerSession } from '../../rayfin/data/GamePlayerSession';
import type { AppFunctionsSchema } from '../../rayfin/functions/src/types';
import { getRegistrationMode, registerPlayer } from '../../rayfin/functions/src/registration';
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
  const savedQuery = client.data.GamePlayerSession.select(['id', 'pin']);
  const find = vi.fn<(id: string) => Promise<GamePlayerSession | null>>().mockResolvedValue(null);
  const savedWhere = vi.spyOn(savedQuery, 'where').mockReturnValue(savedQuery);
  vi.spyOn(savedQuery, 'first').mockReturnValue(savedQuery);
  vi.spyOn(savedQuery, 'execute').mockImplementation(async () => {
    const filter = savedWhere.mock.lastCall?.[0].id;
    const id = typeof filter === 'object' && filter !== null && 'eq' in filter ? filter.eq : filter;
    if (typeof id !== 'string') throw new Error('Missing retry lookup ID');
    const row = await find(id);
    return row ? [row] : [];
  });
  vi.spyOn(client.data.GamePlayerSession, 'select').mockImplementation(fields => fields.includes('pin') ? savedQuery : slots);
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
  const create = vi.spyOn(client.data.GamePlayerSession, 'create').mockImplementation(async row => ({
    ...row, id: row.id ?? crypto.randomUUID(), pin: row.pin ?? undefined,
  }));
  const modes = new Map<string, { id: string; excludeFromLeaderboard: boolean }>();
  const modeQuery = client.data.GameSessionMode.select(['id', 'excludeFromLeaderboard']);
  vi.spyOn(client.data.GameSessionMode, 'select').mockReturnValue(modeQuery);
  const modeWhere = vi.spyOn(modeQuery, 'where').mockReturnValue(modeQuery);
  vi.spyOn(modeQuery, 'first').mockReturnValue(modeQuery);
  const modeExecute = vi.spyOn(modeQuery, 'execute').mockImplementation(async () => {
    const filter = modeWhere.mock.lastCall?.[0].id;
    const id = typeof filter === 'object' && filter !== null && 'eq' in filter ? filter.eq : filter;
    if (typeof id !== 'string') throw new Error('Missing session mode ID');
    const row = modes.get(id);
    return row ? [row] : [];
  });
  const createMode = vi.spyOn(client.data.GameSessionMode, 'create').mockImplementation(async row => {
    if (!row.id) throw new Error('Missing session mode ID');
    if (modes.has(row.id)) throw new Error('SQL primary key constraint');
    const saved = { id: row.id, excludeFromLeaderboard: row.excludeFromLeaderboard };
    modes.set(row.id, saved);
    return saved;
  });
  return { client, game, gameExecute, slotExecute, slotWhere, userExecute, find, create,
    modes, createMode, modeExecute };
}
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

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
    gameSessionId: gameId, pin: '123456', registrationType: 'player' as const, excludeFromLeaderboard: false };
  const invoke = vi.spyOn(client.functions.registerPlayer, 'invoke').mockResolvedValue(result);
  expect(await saveRegistration(client, request)).toEqual(result);
  expect(invoke).toHaveBeenCalledExactlyOnceWith(request, { timeoutMs: 120000 });
  expect(client.data.GameSession.select).not.toHaveBeenCalled();
});

it('uses the easy-mode server function without changing the normal function input contract', async () => {
  const { client } = setup();
  const request = { requestId: crypto.randomUUID(), player: 'P2', specialName: '', excludeFromLeaderboard: true };
  const result = { id: request.requestId, player: 'P2' as const, username: 'SillyEventhouse1234',
    gameSessionId: gameId, pin: '123456', registrationType: 'player' as const, excludeFromLeaderboard: true };
  const easy = vi.spyOn(client.functions.registerEasyPlayer, 'invoke').mockResolvedValue(result);
  const normal = vi.spyOn(client.functions.registerPlayer, 'invoke');
  expect(await saveRegistration(client, request)).toEqual(result);
  expect(easy).toHaveBeenCalledExactlyOnceWith({
    requestId: request.requestId, player: 'P2', specialName: '',
  }, { timeoutMs: 120000 });
  expect(normal).not.toHaveBeenCalled();
});

it.each([false, true])('persists leaderboard exclusion %s without altering station or credentials', async excluded => {
  const { client, create, find, gameExecute } = setup();
  const id = crypto.randomUUID();
  const pass = await registerPlayer(client.data, id, 'P1', '', excluded);
  expect(pass.excludeFromLeaderboard).toBe(excluded);
  expect(pass.pin).toMatch(/^[1-9][0-9]{5}$/);
  const draft = create.mock.calls[0][0];
  expect(draft).toMatchObject({ slotKey: `${gameId}:P1`, excludeFromLeaderboard: excluded, eventQueued: false });
  find.mockResolvedValue({ ...draft, id });
  gameExecute.mockResolvedValue([]);
  expect(await registerPlayer(client.data, id, 'P1', '', excluded)).toEqual(pass);
  expect(await registerPlayer(client.data, id, 'P1', '', !excluded)).toEqual(pass);
  expect(create).toHaveBeenCalledTimes(1);
});
it('rejects invalid parameters before accessing the database', async () => {
  const { client, find } = setup();
  for (const args of [['bad', 'P1', ''], [crypto.randomUUID(), 'P3', ''], [crypto.randomUUID(), 'P1', 'OtherName']]) {
    await expect(registerPlayer(client.data, args[0], args[1], args[2])).rejects.toThrow('Invalid player registration');
  }
  expect(find).not.toHaveBeenCalled();
});
it('rejects inactive, absent, ended and invalid-date game sessions without reserving a slot', async () => {
  const { client, game, gameExecute, create } = setup();
  for (const rows of [[], [{ ...game, active: false }], [{ ...game, endedAt: new Date() }], [{ ...game, lastSeenAt: new Date(NaN) }]]) {
    gameExecute.mockResolvedValueOnce(rows);
    await expect(registerPlayer(client.data, crypto.randomUUID(), 'P1', '')).rejects.toThrow('No live game session');
  }
  expect(create).not.toHaveBeenCalled();
});
it.each([-52000, 52000])('accepts a live publisher with %i ms clock offset only after its heartbeat advances', async offset => {
  vi.useFakeTimers();
  const { client, game, gameExecute, create } = setup();
  const seen = Date.now() + offset;
  gameExecute.mockResolvedValueOnce([{ ...game, lastSeenAt: new Date(seen) }])
    .mockResolvedValue([{ ...game, lastSeenAt: new Date(seen + 2000) }]);
  const pending = registerPlayer(client.data, crypto.randomUUID(), 'P1', '');
  await vi.advanceTimersByTimeAsync(499);
  expect(create).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  expect((await pending).gameSessionId).toBe(game.id);
});
it('rejects a stopped publisher even when its active flag remains set', async () => {
  vi.useFakeTimers();
  const { client, game, gameExecute, create } = setup();
  gameExecute.mockResolvedValue([{ ...game, lastSeenAt: new Date(Date.now() - 52000) }]);
  const pending = expect(registerPlayer(client.data, crypto.randomUUID(), 'P1', '')).rejects.toThrow('not updating');
  await vi.advanceTimersByTimeAsync(8000);
  await pending;
  expect(create).not.toHaveBeenCalled();
  expect(gameExecute).toHaveBeenCalledTimes(17);
});
it.each(['ended', 'changed'] as const)('rejects a game that is %s while verifying clock-skewed heartbeats', async state => {
  vi.useFakeTimers();
  const { client, game, gameExecute, create } = setup();
  const seen = Date.now() - 52000;
  gameExecute.mockResolvedValueOnce([{ ...game, lastSeenAt: new Date(seen) }])
    .mockResolvedValue([{ ...game, lastSeenAt: new Date(seen + 2000),
      ...(state === 'ended' ? { endedAt: new Date() } : { id: crypto.randomUUID() }) }]);
  const pending = expect(registerPlayer(client.data, crypto.randomUUID(), 'P1', '')).rejects
    .toThrow(state === 'ended' ? 'No live game' : 'game session changed');
  await vi.advanceTimersByTimeAsync(2000);
  await pending;
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
it('returns a complete player pass even when the SDK create response contains only the ID', async () => {
  const { client, create } = setup();
  create.mockResolvedValue(JSON.parse('{"id":"c7510905-589f-4d05-bd14-cd718e288a49"}'));
  const result = await registerPlayer(client.data, gameId, 'P1', '');
  expect(result).toMatchObject({ id: gameId, gameSessionId: gameId, player: 'P1', registrationType: 'player' });
  expect(result.username).toMatch(/^[A-Z][a-z]+[A-Z][a-z]+[0-9]{4}$/);
  expect(result.pin).toMatch(/^[1-9][0-9]{5}$/);
});
it('starts independent station checks while waiting for the clock-skew heartbeat', async () => {
  vi.useFakeTimers();
  const { client, game, gameExecute, slotExecute, userExecute, create } = setup();
  const seen = Date.now() - 52000;
  gameExecute.mockResolvedValueOnce([{ ...game, lastSeenAt: new Date(seen) }])
    .mockResolvedValue([{ ...game, lastSeenAt: new Date(seen + 1000) }]);
  const pending = registerPlayer(client.data, crypto.randomUUID(), 'P1', '');
  await vi.advanceTimersByTimeAsync(0);
  expect(slotExecute).toHaveBeenCalled();
  expect(userExecute).toHaveBeenCalled();
  expect(create).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(500);
  await pending;
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
it.each(['YaelTheChampion', 'EdgarIsGoingDown', 'The Dancing Queen'])('keeps %s in the same slot limit without generating a PIN', async name => {
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

it.each([false, true])('makes both players inherit the first session mode %s, including special entries', async firstMode => {
  const { client, create, modes } = setup();
  const first = await registerPlayer(client.data, crypto.randomUUID(), 'P1', '', firstMode);
  const second = await registerPlayer(client.data, crypto.randomUUID(), 'P2', 'The Dancing Queen', !firstMode);
  expect(first.excludeFromLeaderboard).toBe(firstMode);
  expect(second.excludeFromLeaderboard).toBe(firstMode);
  expect(create.mock.calls.map(([row]) => row.excludeFromLeaderboard)).toEqual([firstMode, firstMode]);
  expect(modes.get(gameId)?.excludeFromLeaderboard).toBe(firstMode);
  expect(second.pin).toBeNull();
});

it.each([false, true])('atomically picks one mode when opposing kiosks race (P1 requests %s)', async firstMode => {
  const { client, create, modes, createMode } = setup();
  const passes = await Promise.all([
    registerPlayer(client.data, crypto.randomUUID(), 'P1', '', firstMode),
    registerPlayer(client.data, crypto.randomUUID(), 'P2', '', !firstMode),
  ]);
  expect(createMode).toHaveBeenCalledTimes(2);
  expect(modes.size).toBe(1);
  const locked = modes.get(gameId)!.excludeFromLeaderboard;
  expect(passes.map(pass => pass.excludeFromLeaderboard)).toEqual([locked, locked]);
  expect(create.mock.calls.map(([row]) => row.excludeFromLeaderboard)).toEqual([locked, locked]);
});

it('keeps the mode after a lost lock response or failed player write and resets only for a new game', async () => {
  const { client, game, gameExecute, create, modes, createMode } = setup();
  createMode.mockImplementationOnce(async row => {
    modes.set(gameId, { id: gameId, excludeFromLeaderboard: row.excludeFromLeaderboard });
    throw new Error('Lost mode lock response');
  });
  create.mockRejectedValueOnce(new Error('Player write unavailable'));
  await expect(registerPlayer(client.data, crypto.randomUUID(), 'P1', '', true))
    .rejects.toThrow('Player write unavailable');
  const recovered = await registerPlayer(client.data, crypto.randomUUID(), 'P2', '', false);
  expect(recovered.excludeFromLeaderboard).toBe(true);
  const nextId = crypto.randomUUID();
  gameExecute.mockResolvedValue([{ ...game, id: nextId }]);
  expect((await registerPlayer(client.data, crypto.randomUUID(), 'P1', '', false)).excludeFromLeaderboard).toBe(false);
  expect(modes.get(gameId)?.excludeFromLeaderboard).toBe(true);
  expect(modes.get(nextId)?.excludeFromLeaderboard).toBe(false);
});

it('does not register anyone when the mode lock fails without an existing winner', async () => {
  const { client, createMode, create } = setup();
  createMode.mockRejectedValue(new Error('Mode database unavailable'));
  await expect(registerPlayer(client.data, crypto.randomUUID(), 'P1', '', true))
    .rejects.toThrow('Mode database unavailable');
  expect(create).not.toHaveBeenCalled();
});

it('does not fall back to a requested mode when reading the database lock fails', async () => {
  const { client, modeExecute, createMode, create } = setup();
  modeExecute.mockRejectedValue(new Error('Mode lookup unavailable'));
  await expect(registerPlayer(client.data, crypto.randomUUID(), 'P1', '', true))
    .rejects.toThrow('Mode lookup unavailable');
  expect(createMode).not.toHaveBeenCalled();
  expect(create).not.toHaveBeenCalled();
});

it.each([false, true])('uses an existing pre-lock player mode %s rather than the second player preference', async excluded => {
  const { client, slotExecute, slotWhere } = setup();
  const old = { id: crypto.randomUUID(), gameSessionId: gameId, player: 'P1' as const,
    username: 'SillyEventhouse1234', registrationType: 'player' as const,
    slotKey: `${gameId}:P1`, usernameKey: 'player:test', pinKey: 'pin:123456',
    createdAt: new Date(), eventQueued: true, excludeFromLeaderboard: excluded };
  slotExecute.mockImplementation(async () => slotWhere.mock.lastCall?.[0].gameSessionId ? [old] : []);
  expect((await registerPlayer(client.data, crypto.randomUUID(), 'P2', '', !excluded))
    .excludeFromLeaderboard).toBe(excluded);
});

it('rejects mixed pre-lock registrations rather than retagging already-relayed players', async () => {
  const { client, slotExecute, slotWhere, createMode, create } = setup();
  const old = { id: crypto.randomUUID(), gameSessionId: gameId, player: 'P1' as const,
    username: 'SillyEventhouse1234', registrationType: 'player' as const,
    slotKey: `${gameId}:P1`, usernameKey: 'player:test', pinKey: 'pin:123456',
    createdAt: new Date(), eventQueued: true };
  slotExecute.mockImplementation(async () => slotWhere.mock.lastCall?.[0].gameSessionId
    ? [{ ...old, excludeFromLeaderboard: true }, { ...old, excludeFromLeaderboard: false }] : []);
  await expect(registerPlayer(client.data, crypto.randomUUID(), 'P2', '', false))
    .rejects.toThrow('conflicting player modes');
  expect(createMode).not.toHaveBeenCalled();
  expect(create).not.toHaveBeenCalled();
});

it('reports unset, locked and inactive session modes without returning player data', async () => {
  const { client, modes, gameExecute } = setup();
  expect(await getRegistrationMode(client.data)).toEqual({ gameSessionId: gameId, excludeFromLeaderboard: null });
  modes.set(gameId, { id: gameId, excludeFromLeaderboard: true });
  expect(await getRegistrationMode(client.data)).toEqual({ gameSessionId: gameId, excludeFromLeaderboard: true });
  gameExecute.mockResolvedValue([]);
  expect(await getRegistrationMode(client.data)).toEqual({ gameSessionId: null, excludeFromLeaderboard: null });
});
