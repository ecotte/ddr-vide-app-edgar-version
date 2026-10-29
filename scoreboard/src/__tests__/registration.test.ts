import { afterEach, expect, it, vi } from 'vitest';
import { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../../rayfin/data/schema';
import { ADJECTIVES, NOUNS, latestGame, newIdentity, registrationStation, saveRegistration, saveSpecialRegistration } from '../registration';
import { syncRegistrations } from '../../scripts/registrations';

const gameId = 'c7510905-589f-4d05-bd14-cd718e288a49';
const id = '045bc1ea-322c-4ae7-80e1-06e725893e8e';
function setup() {
  const client = new RayfinClient<AppSchema>({
    baseUrl: 'https://example.invalid/', publishableKey: 'pk-test', authStorage: false,
  });
  const game = { id: gameId, startedAt: new Date(), lastSeenAt: new Date(), active: true };
  const query = client.data.GameSession.select(['id']);
  vi.spyOn(client.data.GameSession, 'select').mockReturnValue(query);
  vi.spyOn(query, 'orderBy').mockReturnValue(query);
  vi.spyOn(query, 'first').mockReturnValue(query);
  const execute = vi.spyOn(query, 'execute').mockResolvedValue([game]);
  const draft = { id, gameSessionId: gameId, username: 'SillyEventhouse1234',
    pin: '123456', createdAt: new Date(), eventQueued: false };
  return { client, execute, game, draft };
}
afterEach(() => vi.restoreAllMocks());

it.each([
  ['', undefined],
  ['?player=1', 'P1'],
  ['?player=2', 'P2'],
  ['?player=2&source=booth', 'P2'],
])('reads the optional station flag %s', (search, player) => {
  expect(registrationStation(search)).toEqual({ player, error: '' });
});

it.each(['?player=3', '?player=', '?player=P1', '?player=1&player=2'])('rejects invalid station flag %s', search => {
  expect(registrationStation(search).error).toContain('Invalid station URL');
  expect(registrationStation(search).player).toBeUndefined();
});

it('provides at least 40 distinct alphabetic words in each username list', () => {
  for (const words of [ADJECTIVES, NOUNS]) {
    expect(new Set(words).size).toBeGreaterThanOrEqual(40);
    expect(new Set(words).size).toBe(words.length);
    for (const word of words) expect(word).toMatch(/^[a-z]+$/);
  }
});

it('capitalizes both username parts before four digits and generates a six-digit PIN', () => {
  const adjectives = ADJECTIVES.map(adjective => adjective[0].toUpperCase() + adjective.slice(1));
  const nouns = NOUNS.map(noun => noun[0].toUpperCase() + noun.slice(1));
  const pattern = new RegExp(`^(${adjectives.join('|')})(${nouns.join('|')})[1-9][0-9]{3}$`);
  for (let index = 0; index < 1000; index++) {
    const identity = newIdentity();
    expect(identity.username).toMatch(pattern);
    expect(identity.pin).toMatch(/^[1-9][0-9]{5}$/);
  }
});

it('rejects missing, inactive and stale game sessions', async () => {
  const { client, execute, game } = setup();
  for (const rows of [[], [{ ...game, active: false }], [{ ...game, lastSeenAt: new Date(Date.now() - 16000) }]]) {
    execute.mockResolvedValueOnce(rows);
    await expect(latestGame(client)).rejects.toThrow('No live game session');
  }
});

it('saves the selected game session and does not create duplicates on retry', async () => {
  const { client, draft } = setup();
  const find = vi.spyOn(client.data.UserSession, 'findById').mockResolvedValue(null);
  const create = vi.spyOn(client.data.UserSession, 'create').mockResolvedValue(draft);
  expect(await saveRegistration(client, draft)).toEqual(draft);
  expect(create).toHaveBeenCalledWith(draft);
  find.mockResolvedValue(draft);
  expect(await saveRegistration(client, draft)).toEqual(draft);
  expect(create).toHaveBeenCalledTimes(1);
});

it('recovers a committed registration after a lost response', async () => {
  const { client, draft } = setup();
  vi.spyOn(client.data.UserSession, 'findById').mockResolvedValueOnce(null).mockResolvedValueOnce(draft);
  vi.spyOn(client.data.UserSession, 'create').mockRejectedValue(new Error('Lost response'));
  expect(await saveRegistration(client, draft)).toEqual(draft);
});

it('does not save against a game that changed', async () => {
  const { client, draft } = setup();
  vi.spyOn(client.data.UserSession, 'findById').mockResolvedValue(null);
  draft.gameSessionId = crypto.randomUUID();
  const create = vi.spyOn(client.data.UserSession, 'create');
  await expect(saveRegistration(client, draft)).rejects.toThrow('game session changed');
  expect(create).not.toHaveBeenCalled();
});

it('retries confirmed name/PIN collisions but surfaces other failures', async () => {
  const { client, draft } = setup();
  vi.spyOn(client.data.UserSession, 'findById').mockResolvedValue(null);
  const query = client.data.UserSession.select(['id']);
  vi.spyOn(client.data.UserSession, 'select').mockReturnValue(query);
  vi.spyOn(query, 'where').mockReturnValue(query);
  vi.spyOn(query, 'first').mockReturnValue(query);
  const execute = vi.spyOn(query, 'execute').mockResolvedValue([draft]);
  const create = vi.spyOn(client.data.UserSession, 'create')
    .mockRejectedValueOnce(new Error('Unique constraint')).mockImplementation(async row => ({ ...draft, ...row }));
  const previous = draft.username;
  expect((await saveRegistration(client, draft)).id).toBe(draft.id);
  expect(draft.username).not.toBe(previous);
  expect(create).toHaveBeenCalledTimes(2);
  execute.mockResolvedValue([]);
  create.mockRejectedValue(new Error('Permission denied'));
  await expect(saveRegistration(client, { ...draft, id: crypto.randomUUID() })).rejects.toThrow('Permission denied');
});

it('relays bounded pending registrations without selecting PINs and acknowledges only supplied IDs', async () => {
  const { client, draft, game } = setup();
  const upsert = vi.spyOn(client.data.GameSession, 'upsert').mockResolvedValue(game);
  const update = vi.spyOn(client.data.UserSession, 'update').mockResolvedValue(draft);
  const query = client.data.UserSession.select(['id']);
  const select = vi.spyOn(client.data.UserSession, 'select').mockReturnValue(query);
  vi.spyOn(query, 'where').mockReturnValue(query);
  vi.spyOn(query, 'orderBy').mockReturnValue(query);
  const first = vi.spyOn(query, 'first').mockReturnValue(query);
  const players = Array.from({ length: 25 }, (_, index) => ({
    ...draft, id: crypto.randomUUID(), createdAt: new Date(Date.now() + index * 1000),
  }));
  vi.spyOn(query, 'execute').mockResolvedValue(players);
  const specialQuery = client.data.SpecialSession.select(['id']);
  vi.spyOn(client.data.SpecialSession, 'select').mockReturnValue(specialQuery);
  vi.spyOn(specialQuery, 'where').mockReturnValue(specialQuery);
  vi.spyOn(specialQuery, 'orderBy').mockReturnValue(specialQuery);
  vi.spyOn(specialQuery, 'first').mockReturnValue(specialQuery);
  const special = { id: crypto.randomUUID(), gameSessionId: gameId, registrationKey: `${gameId}:YaelTheChampion:unassigned`,
    username: 'YaelTheChampion', createdAt: new Date(0), eventQueued: false };
  vi.spyOn(specialQuery, 'execute').mockResolvedValue([special]);
  const specialUpdate = vi.spyOn(client.data.SpecialSession, 'update').mockResolvedValue(special);
  const player = { score: null, combo: null, hits: 0, observedScore: null, lastVideoTimeMs: null };
  const snapshot = { active: true, sessionId: gameId, revision: 1, players: { P1: player, P2: player } };
  const rows = await syncRegistrations(client, snapshot, [id], [special.id]);
  expect(rows).toHaveLength(25);
  expect(rows[0]).toMatchObject({ username: 'YaelTheChampion', registrationType: 'special' });
  expect(specialUpdate).toHaveBeenCalledWith({ id: special.id }, { eventQueued: true });
  expect(update).toHaveBeenCalledWith({ id }, { eventQueued: true });
  expect(select).toHaveBeenCalledWith(['id', 'gameSessionId', 'player', 'username', 'createdAt']);
  expect(first).toHaveBeenCalledWith(25);
  expect(upsert.mock.calls[0][2]).not.toHaveProperty('startedAt');
  await expect(syncRegistrations(client, snapshot, ['bad'])).rejects.toThrow('Invalid registration acknowledgment');
});

it('stores and reuses a special no-PIN registration only within its game and name', async () => {
  const { client } = setup();
  const draft = { id, gameSessionId: gameId, registrationKey: `${gameId}:YaelTheChampion:unassigned`,
    username: 'YaelTheChampion', createdAt: new Date(), eventQueued: false };
  const query = client.data.SpecialSession.select(['id']);
  vi.spyOn(client.data.SpecialSession, 'select').mockReturnValue(query);
  const where = vi.spyOn(query, 'where').mockReturnValue(query);
  vi.spyOn(query, 'first').mockReturnValue(query);
  const execute = vi.spyOn(query, 'execute').mockResolvedValueOnce([]).mockResolvedValueOnce([draft]);
  const create = vi.spyOn(client.data.SpecialSession, 'create').mockResolvedValue(draft);
  expect(await saveSpecialRegistration(client, draft)).toEqual(draft);
  expect(await saveSpecialRegistration(client, draft)).toEqual(draft);
  expect(create).toHaveBeenCalledTimes(1);
  expect(create.mock.calls[0][0]).not.toHaveProperty('pin');
  expect(where).toHaveBeenCalledWith({ registrationKey: { eq: draft.registrationKey } });
  execute.mockResolvedValue([]);
  await expect(saveSpecialRegistration(client, { ...draft, username: 'NotAllowed' })).rejects.toThrow('Invalid special');
});
