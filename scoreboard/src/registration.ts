import type { UserSession } from '../rayfin/data/UserSession';
import type { Client } from './client';

export const ADJECTIVES = ['silly', 'jolly', 'bouncy', 'cheery', 'zippy', 'groovy', 'sparkly', 'peppy'];
export const NOUNS = ['eventhouse', 'lakehouse', 'warehouse', 'notebook', 'pipeline', 'onelake', 'dataflow', 'spark'];

function randomInteger(limit: number) {
  const ceiling = Math.floor(0x100000000 / limit) * limit;
  const bytes = new Uint32Array(1);
  do { crypto.getRandomValues(bytes); } while (bytes[0] >= ceiling);
  return bytes[0] % limit;
}

export function newIdentity() {
  return {
    username: ADJECTIVES[randomInteger(ADJECTIVES.length)]
      + NOUNS[randomInteger(NOUNS.length)] + (1000 + randomInteger(9000)),
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
