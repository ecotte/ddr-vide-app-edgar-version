import type { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../rayfin/data/schema.js';
import type { BoardSnapshot } from '../src/state.js';

export async function syncRegistrations(
  client: RayfinClient<AppSchema>, snapshot: BoardSnapshot, acknowledged: unknown,
) {
  if (!Array.isArray(acknowledged) || acknowledged.length > 25
      || !acknowledged.every(id => typeof id === 'string' && /^[a-f0-9-]{36}$/i.test(id))) {
    throw new Error('Invalid registration acknowledgment');
  }
  for (const id of acknowledged) {
    await client.data.UserSession.update({ id }, { eventQueued: true });
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
  return client.data.UserSession.select(['id', 'gameSessionId', 'username', 'createdAt'])
    .where({ eventQueued: { eq: false } }).orderBy({ createdAt: 'asc' }).first(25).execute();
}
