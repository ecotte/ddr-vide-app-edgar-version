import type { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../rayfin/data/schema.js';
import type { BoardSnapshot } from '../src/state.js';

export async function syncRegistrations(
  client: RayfinClient<AppSchema>, snapshot: BoardSnapshot, acknowledged: unknown, acknowledgedSpecial: unknown = [],
) {
  const validIds = (ids: unknown): ids is string[] => Array.isArray(ids)
    && ids.every(id => typeof id === 'string' && /^[a-f0-9-]{36}$/i.test(id));
  if (!validIds(acknowledged) || !validIds(acknowledgedSpecial)
      || acknowledged.length + acknowledgedSpecial.length > 25) {
    throw new Error('Invalid registration acknowledgment');
  }
  for (const id of acknowledged) {
    await client.data.UserSession.update({ id }, { eventQueued: true });
  }
  for (const id of acknowledgedSpecial) {
    await client.data.SpecialSession.update({ id }, { eventQueued: true });
  }
  const now = new Date();
  if (snapshot.sessionId) {
    if (!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(snapshot.sessionId)) {
      throw new Error('Invalid game session ID');
    }
    await client.data.GameSession.upsert({ id: snapshot.sessionId }, {
      id: snapshot.sessionId, startedAt: now, lastSeenAt: now, active: snapshot.active,
    }, { lastSeenAt: now, active: snapshot.active });
  }
  const [players, specials] = await Promise.all([
    client.data.UserSession.select(['id', 'gameSessionId', 'player', 'username', 'createdAt'])
      .where({ eventQueued: { eq: false } }).orderBy({ createdAt: 'asc' }).first(25).execute(),
    client.data.SpecialSession.select(['id', 'gameSessionId', 'player', 'username', 'createdAt'])
      .where({ eventQueued: { eq: false } }).orderBy({ createdAt: 'asc' }).first(25).execute(),
  ]);
  return [
    ...players.map(row => ({ ...row, registrationType: 'player' })),
    ...specials.map(row => ({ ...row, registrationType: 'special' })),
  ].sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()).slice(0, 25);
}
