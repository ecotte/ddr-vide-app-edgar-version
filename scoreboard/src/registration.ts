import type { UserSession } from '../rayfin/data/UserSession';
import type { SpecialSession } from '../rayfin/data/SpecialSession';
import type { Client } from './client';

export const SPECIAL_NAMES = ['YaelTheChampion', 'EdgarIsGoingDown'] as const;
export type SpecialName = typeof SPECIAL_NAMES[number];

export function registrationStation(search: string): { player?: 'P1' | 'P2'; error: string } {
  const flags = new URLSearchParams(search).getAll('player');
  if (!flags.length) return { error: '' };
  if (flags.length !== 1 || !['1', '2'].includes(flags[0])) {
    return { error: 'Invalid station URL. Use ?player=1 or ?player=2, or remove the player flag for an unassigned registration.' };
  }
  return { player: flags[0] === '1' ? 'P1' : 'P2', error: '' };
}

export const ADJECTIVES = [
  'silly', 'jolly', 'bouncy', 'cheery', 'zippy', 'groovy', 'sparkly', 'peppy',
  'wobbly', 'bubbly', 'giggly', 'fluffy', 'snazzy', 'jazzy', 'perky', 'quirky',
  'sunny', 'happy', 'merry', 'joyful', 'playful', 'sprightly', 'lively', 'jaunty',
  'plucky', 'spunky', 'sassy', 'nifty', 'dapper', 'chirpy', 'cuddly', 'fuzzy',
  'twinkly', 'zany', 'goofy', 'whimsical', 'cheerful', 'radiant', 'brilliant', 'dazzling',
];
export const NOUNS = [
  'eventhouse', 'lakehouse', 'warehouse', 'notebook', 'pipeline', 'onelake', 'dataflow', 'spark',
  'eventstream', 'activator', 'shortcut', 'mirroring', 'report', 'dashboard', 'workspace', 'capacity',
  'dataset', 'table', 'column', 'measure', 'dimension', 'partition', 'schema', 'catalog',
  'query', 'database', 'sqlendpoint', 'deltatable', 'datahub', 'semanticmodel', 'connector', 'gateway',
  'experiment', 'model', 'environment', 'schedule', 'trigger', 'parameter', 'function', 'deployment',
];

function randomInteger(limit: number) {
  const ceiling = Math.floor(0x100000000 / limit) * limit;
  const bytes = new Uint32Array(1);
  do { crypto.getRandomValues(bytes); } while (bytes[0] >= ceiling);
  return bytes[0] % limit;
}

export function newIdentity() {
  const adjective = ADJECTIVES[randomInteger(ADJECTIVES.length)];
  const noun = NOUNS[randomInteger(NOUNS.length)];
  return {
    username: adjective[0].toUpperCase() + adjective.slice(1)
      + noun[0].toUpperCase() + noun.slice(1) + (1000 + randomInteger(9000)),
    pin: String(100000 + randomInteger(900000)),
  };
}

export async function latestGame(client: Client) {
  const [game] = await client.data.GameSession.select(['id', 'startedAt', 'lastSeenAt', 'active'])
    .orderBy({ startedAt: 'desc' }).first(1).execute();
  const seen = game ? new Date(game.lastSeenAt).getTime() : NaN;
  if (!game?.active || !Number.isFinite(seen) || Date.now() - seen > 15000 || seen > Date.now() + 5000) {
    throw new Error('No live game session. Ask the organizer to enable Share live scoreboard in the DDR app.');
  }
  return game;
}

export async function saveRegistration(client: Client, draft: UserSession): Promise<UserSession> {
  const existing = await client.data.UserSession.findById(draft.id);
  if (existing) return existing;
  const game = await latestGame(client);
  if (game.id !== draft.gameSessionId) {
    throw new Error('The game session changed. Choose Next player to start a new registration.');
  }
  for (let attempt = 0; attempt < 10; attempt++) {
    try {
      return await client.data.UserSession.create(draft);
    } catch (error) {
      // A lost response must not create another player on retry.
      const saved = await client.data.UserSession.findById(draft.id);
      if (saved) return saved;
      const pins = await client.data.UserSession.select(['id']).where({ pin: { eq: draft.pin } }).first(1).execute();
      const names = await client.data.UserSession.select(['id']).where({ username: { eq: draft.username } }).first(1).execute();
      if (!pins.length && !names.length) throw error;
      Object.assign(draft, newIdentity());
    }
  }
  throw new Error('Could not reserve a unique name and PIN. Please retry registration.');
}

export async function saveSpecialRegistration(client: Client, draft: SpecialSession): Promise<SpecialSession> {
  if (!SPECIAL_NAMES.some(name => name === draft.username)
      || draft.registrationKey !== `${draft.gameSessionId}:${draft.username}:${draft.player ?? 'unassigned'}`) {
    throw new Error('Invalid special registration.');
  }
  const game = await latestGame(client);
  if (game.id !== draft.gameSessionId) {
    throw new Error('The game session changed. Choose Next player to start a new registration.');
  }
  const find = async () => {
    const [existing] = await client.data.SpecialSession.select(['id', 'gameSessionId', 'player', 'registrationKey', 'username', 'createdAt', 'eventQueued'])
      .where({ registrationKey: { eq: draft.registrationKey } }).first(1).execute();
    return existing;
  };
  const existing = await find();
  if (existing) return existing;
  try {
    return await client.data.SpecialSession.create(draft);
  } catch (error) {
    const saved = await find();
    if (saved) return saved;
    throw error;
  }
}
