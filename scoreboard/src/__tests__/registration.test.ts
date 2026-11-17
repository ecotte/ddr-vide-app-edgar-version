import { afterEach, expect, it, vi } from 'vitest';
import { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../../rayfin/data/schema';
import type { GamePlayerSession } from '../../rayfin/data/GamePlayerSession';
import type { AppFunctionsSchema } from '../../rayfin/functions/src/types';
import { getKioskStatus, getRegistrationMode, registerPlayer } from '../../rayfin/functions/src/registration';
import { ADJECTIVES, NOUNS, newIdentity } from '../../rayfin/functions/src/identity';
import { registrationStation, saveRegistration } from '../registration';
import { DIFFICULTIES } from '../../rayfin/functions/src/difficulty';

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
  const specialExecute = vi.spyOn(specials, 'execute').mockResolvedValue([]);
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
    modes, createMode, modeExecute, specialExecute };
}
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

it.each([['?player=1', 'P1'], ['?player=2', 'P2']])('reads station %s', (search, player) => {
  expect(registrationStation(search)).toEqual({ player, error: '' });
});
it.each(['', '?player=', '?player=3', '?player=P1', '?player=1&player=2'])('blocks missing or invalid station %s', search => {
  expect(registrationStation(search).error).not.toBe('');
});

it.each(['P1', 'P2'] as const)('returns game identity, occupancy, player name and difficulty without PINs for station %s', async player => {
  const { client, slotExecute, slotWhere, create } = setup();
  expect(await getKioskStatus(client.data, player)).toEqual({ gameSessionId: gameId, registered: false });
  expect(slotWhere).toHaveBeenLastCalledWith({ slotKey: { eq: `${gameId}:${player}` } });
  const row = {
    id: crypto.randomUUID(), gameSessionId: gameId, player,
    slotKey: `${gameId}:${player}`, username: 'PrivatePlayer1234', pin: '123456',
    usernameKey: 'private', pinKey: 'private', registrationType: 'player' as const,
    createdAt: new Date(), eventQueued: true,
  };
  for (const [difficulty, excludeFromLeaderboard, expected] of [
    [0, true, 0], [3, false, 3], [4, false, 4], [undefined, true, 0], [undefined, false, null],
  ] as const) {
    slotExecute.mockResolvedValue([{ ...row, difficulty, excludeFromLeaderboard }]);
    expect(await getKioskStatus(client.data, player)).toEqual({
      gameSessionId: gameId, registered: true, username: 'PrivatePlayer1234', difficulty: expected,
    });
  }
  expect(client.data.GamePlayerSession.select).toHaveBeenCalledWith(['id', 'username', 'difficulty', 'excludeFromLeaderboard']);
  slotExecute.mockResolvedValue([{ ...row, difficulty: 4, excludeFromLeaderboard: true }]);
  await expect(getKioskStatus(client.data, player)).rejects.toThrow('invalid difficulty');
  expect(create).not.toHaveBeenCalled();
});

it.each(['', 'P3', '1'])('rejects invalid status station %s before reading data', async player => {
  const { client, gameExecute } = setup();
  await expect(getKioskStatus(client.data, player)).rejects.toThrow('Invalid kiosk station');
  expect(gameExecute).not.toHaveBeenCalled();
});

it('does not reopen check-in from an absent, inactive or ended newest game', async () => {
  const { client, game, gameExecute, slotExecute } = setup();
  for (const rows of [[], [{ ...game, active: false }], [{ ...game, endedAt: new Date() }]]) {
    gameExecute.mockResolvedValueOnce(rows);
    expect(await getKioskStatus(client.data, 'P1')).toEqual({ gameSessionId: null, registered: false });
  }
  expect(slotExecute).not.toHaveBeenCalled();
});

it('keeps occupancy independent and checks the new game rather than an old station row', async () => {
  const { client, game, gameExecute, slotExecute, slotWhere } = setup();
  const nextId = crypto.randomUUID();
  const occupied = {
    id: crypto.randomUUID(), gameSessionId: gameId, player: 'P1' as const,
    slotKey: `${gameId}:P1`, username: 'PlayerName1234', usernameKey: 'test',
    pinKey: 'test', registrationType: 'special' as const, createdAt: new Date(), eventQueued: true,
  };
  slotExecute.mockImplementation(async () => JSON.stringify(slotWhere.mock.lastCall?.[0]).includes(`${gameId}:P1`) ? [occupied] : []);
  expect((await getKioskStatus(client.data, 'P1')).registered).toBe(true);
  expect((await getKioskStatus(client.data, 'P2')).registered).toBe(false);
  gameExecute.mockResolvedValue([{ ...game, id: nextId }]);
  expect(await getKioskStatus(client.data, 'P1')).toEqual({ gameSessionId: nextId, registered: false });
});

it.each(['normal', 'special'])('returns the %s historical station name without returning PINs', async kind => {
  const { client, userExecute, specialExecute } = setup();
  const common = { id: crypto.randomUUID(), gameSessionId: gameId, player: 'P2' as const,
    username: 'SillyEventhouse1234', createdAt: new Date(), eventQueued: true };
  if (kind === 'normal') userExecute.mockResolvedValue([{ ...common, pin: '123456' }]);
  else specialExecute.mockResolvedValue([{ ...common, registrationKey: 'special' }]);
  expect(await getKioskStatus(client.data, 'P2')).toEqual({
    gameSessionId: gameId, registered: true, username: common.username, difficulty: null,
  });
  expect(await getKioskStatus(client.data, 'P1')).toEqual({ gameSessionId: gameId, registered: false });
});

it('surfaces status lookup failures and ambiguous legacy games rather than guessing that a station is free', async () => {
  const { client, slotExecute, userExecute } = setup();
  slotExecute.mockRejectedValueOnce(new Error('Status database unavailable'));
  await expect(getKioskStatus(client.data, 'P1')).rejects.toThrow('Status database unavailable');
  userExecute.mockResolvedValue([{
    id: crypto.randomUUID(), gameSessionId: gameId, username: 'SillyEventhouse1234',
    pin: '123456', createdAt: new Date(), eventQueued: true,
  }]);
  await expect(getKioskStatus(client.data, 'P1')).rejects.toThrow('before station limits');
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
  const request = { requestId: crypto.randomUUID(), player: 'P1', specialName: '', difficulty: 4 };
  const result = { id: request.requestId, player: 'P1' as const, username: 'SillyEventhouse1234',
    gameSessionId: gameId, pin: '123456', registrationType: 'player' as const, excludeFromLeaderboard: false, difficulty: 4 as const };
  const invoke = vi.spyOn(client.functions.registerPlayerWithDifficulty, 'invoke').mockResolvedValue(result);
  expect(await saveRegistration(client, request)).toEqual(result);
  expect(invoke).toHaveBeenCalledExactlyOnceWith(request, { timeoutMs: 120000 });
  expect(client.data.GameSession.select).not.toHaveBeenCalled();
});

it('sends Easy as numeric zero through the same independent-difficulty function', async () => {
  const { client } = setup();
  const request = { requestId: crypto.randomUUID(), player: 'P2', specialName: '', difficulty: 0 };
  const result = { id: request.requestId, player: 'P2' as const, username: 'SillyEventhouse1234',
    gameSessionId: gameId, pin: '123456', registrationType: 'player' as const, excludeFromLeaderboard: true, difficulty: 0 as const };
  const easy = vi.spyOn(client.functions.registerPlayerWithDifficulty, 'invoke').mockResolvedValue(result);
  const normal = vi.spyOn(client.functions.registerPlayer, 'invoke');
  expect(await saveRegistration(client, request)).toEqual(result);
  expect(easy).toHaveBeenCalledExactlyOnceWith({
    requestId: request.requestId, player: 'P2', specialName: '', difficulty: 0,
  }, { timeoutMs: 120000 });
  expect(normal).not.toHaveBeenCalled();
});

it.each(DIFFICULTIES)('persists independent difficulty %s without altering station or credentials', async difficulty => {
  const { client, create, find, gameExecute } = setup();
  const id = crypto.randomUUID();
  const pass = await registerPlayer(client.data, id, 'P1', '', difficulty);
  expect(pass.excludeFromLeaderboard).toBe(difficulty === 0);
  expect(pass.difficulty).toBe(difficulty);
  expect(pass.pin).toMatch(/^[1-9][0-9]{5}$/);
  const draft = create.mock.calls[0][0];
  expect(draft).toMatchObject({ slotKey: `${gameId}:P1`, difficulty, excludeFromLeaderboard: difficulty === 0, eventQueued: false });
  find.mockResolvedValue({ ...draft, id });
  gameExecute.mockResolvedValue([]);
  expect(await registerPlayer(client.data, id, 'P1', '', difficulty)).toEqual(pass);
  await expect(registerPlayer(client.data, id, 'P1', '', difficulty === 4 ? 3 : 4)).rejects.toThrow('different difficulty');
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
it.each(['YaelTheChampion', 'EdgarIsGoingDown', 'The Dancing Queen', 'Bowser'])('keeps %s in the same slot limit without generating a PIN', async name => {
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

it.each(DIFFICULTIES.flatMap(p1 => DIFFICULTIES.map(p2 => [p1, p2] as const)))(
  'keeps P1 difficulty %s independent of P2 difficulty %s, even for simultaneous special registrations',
  async (p1, p2) => {
    const { client, create, createMode, modeExecute, modes } = setup();
    modes.set(gameId, { id: gameId, excludeFromLeaderboard: true });
    const passes = await Promise.all([
      registerPlayer(client.data, crypto.randomUUID(), 'P1', '', p1),
      registerPlayer(client.data, crypto.randomUUID(), 'P2', 'The Dancing Queen', p2),
    ]);
    expect(passes.map(pass => pass.difficulty)).toEqual([p1, p2]);
    expect(passes.map(pass => pass.excludeFromLeaderboard)).toEqual([p1 === 0, p2 === 0]);
    expect(Object.fromEntries(create.mock.calls.map(([row]) => [row.player, row.difficulty]))).toEqual({ P1: p1, P2: p2 });
    expect(passes[1].pin).toBeNull();
    expect(createMode).not.toHaveBeenCalled();
    expect(modeExecute).not.toHaveBeenCalled();
  },
);

it('does not let a failed player write lock the other player to easy', async () => {
  const { client, create, createMode } = setup();
  create.mockRejectedValueOnce(new Error('Player write unavailable'));
  await expect(registerPlayer(client.data, crypto.randomUUID(), 'P1', '', 0)).rejects.toThrow('Player write unavailable');
  expect((await registerPlayer(client.data, crypto.randomUUID(), 'P2', '', 4)).difficulty).toBe(4);
  expect(createMode).not.toHaveBeenCalled();
});

it.each([-1, 1, 2, 5, 3.5, NaN, Infinity])('rejects unsupported numeric difficulty %s before database access', async difficulty => {
  const { client, find, create } = setup();
  await expect(registerPlayer(client.data, crypto.randomUUID(), 'P1', '', difficulty)).rejects.toThrow('Invalid player registration');
  expect(find).not.toHaveBeenCalled();
  expect(create).not.toHaveBeenCalled();
});

it.each(['0', '3', '4', true, false, {}, []])('does not coerce invalid difficulty %s into a supported choice', async difficulty => {
  const { client, create } = setup();
  await expect(Reflect.apply(registerPlayer, undefined, [
    client.data, crypto.randomUUID(), 'P1', '', difficulty,
  ])).rejects.toThrow('Invalid player registration');
  expect(create).not.toHaveBeenCalled();
});

it('preserves legacy Normal as unknown rather than inventing a numeric difficulty', async () => {
  const { client, create } = setup();
  const pass = await registerPlayer(client.data, crypto.randomUUID(), 'P1', '');
  expect(pass.difficulty).toBeNull();
  expect(pass.excludeFromLeaderboard).toBe(false);
  expect(create.mock.calls[0][0]).not.toHaveProperty('difficulty');
});

it('a same-station race with different difficulties still reserves exactly one slot', async () => {
  const { client, create, find, slotExecute, slotWhere } = setup();
  let winner: GamePlayerSession | undefined;
  find.mockImplementation(async id => winner?.id === id ? winner : null);
  slotExecute.mockImplementation(async () => {
    const filter = slotWhere.mock.lastCall?.[0];
    return winner && JSON.stringify(filter).includes(winner.slotKey) ? [winner] : [];
  });
  create.mockImplementation(async row => {
    if (winner) throw new Error('SQL slotKey unique constraint');
    winner = { ...row, id: row.id ?? crypto.randomUUID() };
    return winner;
  });
  const results = await Promise.allSettled([
    registerPlayer(client.data, crypto.randomUUID(), 'P1', '', 0),
    registerPlayer(client.data, crypto.randomUUID(), 'P1', '', 4),
  ]);
  expect(results.filter(result => result.status === 'fulfilled')).toHaveLength(1);
  expect(results.filter(result => result.status === 'rejected')).toHaveLength(1);
  const saved = results.find(result => result.status === 'fulfilled');
  if (saved?.status !== 'fulfilled') throw new Error('Expected one saved registration');
  expect(saved.value.difficulty).toBe(winner?.difficulty);
});

it('retains the saved difficulty when recovering a lost response and rejects retrying a different choice', async () => {
  const { client, create, find } = setup();
  create.mockImplementation(async row => {
    find.mockResolvedValue({ ...row, id: row.id ?? crypto.randomUUID() });
    throw new Error('Lost response');
  });
  const id = crypto.randomUUID();
  expect((await registerPlayer(client.data, id, 'P1', '', 4)).difficulty).toBe(4);
  await expect(registerPlayer(client.data, id, 'P1', '', 3)).rejects.toThrow('different difficulty');
  expect(create).toHaveBeenCalledTimes(1);
});

it.each([false, true])('recovers legacy registration mode %s without inventing a normal difficulty', async easy => {
  const { client, find } = setup();
  const id = crypto.randomUUID();
  find.mockResolvedValue({
    id, gameSessionId: gameId, player: 'P1', username: 'SillyEventhouse1234',
    pin: '123456', registrationType: 'player', slotKey: `${gameId}:P1`, usernameKey: 'test',
    pinKey: 'test', createdAt: new Date(), eventQueued: false, excludeFromLeaderboard: easy,
  });
  const pass = await registerPlayer(client.data, id, 'P1', '');
  expect(pass.difficulty).toBe(easy ? 0 : null);
});

it('keeps the cached-kiosk lookup compatible but never advertises a shared mode', async () => {
  const { client, modes, gameExecute } = setup();
  expect(await getRegistrationMode(client.data)).toEqual({ gameSessionId: gameId, excludeFromLeaderboard: null });
  modes.set(gameId, { id: gameId, excludeFromLeaderboard: true });
  expect(await getRegistrationMode(client.data)).toEqual({ gameSessionId: gameId, excludeFromLeaderboard: null });
  gameExecute.mockResolvedValue([]);
  expect(await getRegistrationMode(client.data)).toEqual({ gameSessionId: null, excludeFromLeaderboard: null });
});
