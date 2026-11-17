import type { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../rayfin/data/schema.js';
import type { BoardSnapshot, PlayerState } from '../src/state.js';
import { isDifficulty, scoreForDifficulty } from '../rayfin/functions/src/difficulty.js';

export async function withPlayerNames(
  client: RayfinClient<AppSchema>, snapshot: BoardSnapshot,
): Promise<BoardSnapshot> {
  const basePlayer = (player: PlayerState): PlayerState => {
    const base = player.baseRunningScore ?? player.runningScore;
    return {
      ...player, username: null, difficulty: null,
      ...(base != null ? { baseRunningScore: base, runningScore: base } : {}),
    };
  };
  const players: BoardSnapshot['players'] = {
    P1: basePlayer(snapshot.players.P1),
    P2: basePlayer(snapshot.players.P2),
  };
  if (snapshot.sessionId) {
    const rows = await client.data.GamePlayerSession.select(['player', 'username', 'difficulty'])
      .where({ gameSessionId: { eq: snapshot.sessionId } }).first(2).execute();
    for (const row of rows) {
      const difficulty = row.difficulty ?? null;
      if (difficulty !== null && !isDifficulty(difficulty)) throw new Error('Invalid registered player difficulty.');
      const player = players[row.player];
      player.username = row.username;
      player.difficulty = difficulty;
      if (player.baseRunningScore != null) {
        player.runningScore = scoreForDifficulty(player.baseRunningScore, difficulty);
      }
    }
  }
  return { ...snapshot, players };
}

export async function syncRegistrations(
  client: RayfinClient<AppSchema>, snapshot: BoardSnapshot, acknowledged: unknown, acknowledgedSpecial: unknown = [],
  acknowledgedSlots: unknown = [], previousSessionId?: string,
) {
  const validIds = (ids: unknown): ids is string[] => Array.isArray(ids)
    && ids.every(id => typeof id === 'string' && /^[a-f0-9-]{36}$/i.test(id));
  if (!validIds(acknowledged) || !validIds(acknowledgedSpecial) || !validIds(acknowledgedSlots)
      || acknowledged.length + acknowledgedSpecial.length + acknowledgedSlots.length > 25) {
    throw new Error('Invalid registration acknowledgment');
  }
  for (const id of acknowledged) {
    await client.data.UserSession.update({ id }, { eventQueued: true });
  }
  for (const id of acknowledgedSpecial) {
    await client.data.SpecialSession.update({ id }, { eventQueued: true });
  }
  for (const id of acknowledgedSlots) {
    await client.data.GamePlayerSession.update({ id }, { eventQueued: true });
  }
  const now = new Date();
  if (snapshot.sessionId) {
    if (!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(snapshot.sessionId)) {
      throw new Error('Invalid game session ID');
    }
    if (previousSessionId && previousSessionId !== snapshot.sessionId) {
      await client.data.GameSession.update({ id: previousSessionId }, { active: false, endedAt: now });
    }
    await client.data.GameSession.upsert({ id: snapshot.sessionId }, {
      id: snapshot.sessionId, startedAt: now, lastSeenAt: now, active: snapshot.active,
    }, { lastSeenAt: now, active: snapshot.active });
  }
  const [players, specials, slots] = await Promise.all([
    client.data.UserSession.select(['id', 'gameSessionId', 'player', 'username', 'pin', 'createdAt'])
      .where({ eventQueued: { eq: false } }).orderBy({ createdAt: 'asc' }).first(25).execute(),
    client.data.SpecialSession.select(['id', 'gameSessionId', 'player', 'username', 'createdAt'])
      .where({ eventQueued: { eq: false } }).orderBy({ createdAt: 'asc' }).first(25).execute(),
    client.data.GamePlayerSession.select([
      'id', 'gameSessionId', 'player', 'username', 'pin', 'createdAt', 'registrationType', 'excludeFromLeaderboard', 'difficulty',
    ])
      .where({ eventQueued: { eq: false } }).orderBy({ createdAt: 'asc' }).first(25).execute(),
  ]);
  return [
    ...players.map(row => ({ ...row, registrationType: 'player' })),
    ...specials.map(row => ({ ...row, registrationType: 'special' })),
    ...slots.map(row => ({ ...row, relaySource: 'slot' })),
  ].sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()).slice(0, 25);
}
