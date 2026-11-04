import type { RayfinContext } from '@microsoft/fabric-user-data-functions';
import type { AppSchema } from '../../data/schema.js';
import type { GamePlayerSession } from '../../data/GamePlayerSession.js';
import type { GameSession } from '../../data/GameSession.js';
import { newIdentity, SPECIAL_NAMES } from './identity.js';

export type RegistrationData = ReturnType<RayfinContext<AppSchema>['getDataClient']>;
export class RegistrationError extends Error {}
export interface PlayerPass {
  id: string;
  gameSessionId: string;
  player: 'P1' | 'P2';
  username: string;
  pin: string | null;
  registrationType: 'player' | 'special';
  excludeFromLeaderboard: boolean;
}

function playerPass(row: GamePlayerSession): PlayerPass {
  return {
    id: row.id, gameSessionId: row.gameSessionId, player: row.player, username: row.username,
    pin: row.registrationType === 'special' ? null : row.pin ?? null,
    registrationType: row.registrationType,
    excludeFromLeaderboard: row.excludeFromLeaderboard ?? false,
  };
}

async function verifyLiveGame(data: RegistrationData, game: GameSession | undefined, requestedAt: number) {
  const unavailable = () => new RegistrationError('No live game session. Ask the organizer to enable Share live scoreboard in the DDR app.');
  if (!game?.active || game.endedAt != null) throw unavailable();
  const seen = new Date(game.lastSeenAt).getTime();
  if (!Number.isFinite(seen)) throw unavailable();
  if (requestedAt - seen <= 15000 && seen <= Date.now() + 5000) return game;

  // Publisher and Functions clocks can differ. An advancing heartbeat proves
  // liveness without extending the stale timeout or accepting an ended game.
  const deadline = Date.now() + 8000;
  for (let attempt = 0; attempt < 16 && Date.now() < deadline; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 500));
    const [current] = await data.GameSession.select(['id', 'startedAt', 'lastSeenAt', 'active', 'endedAt'])
      .orderBy({ startedAt: 'desc' }).first(1).execute();
    if (!current?.active || current.endedAt != null) throw unavailable();
    if (current.id !== game.id) {
      throw new RegistrationError('The game session changed. Please retry registration.');
    }
    const latest = new Date(current.lastSeenAt).getTime();
    if (!Number.isFinite(latest) || latest < seen) throw unavailable();
    if (latest > seen) return current;
  }
  throw new RegistrationError('The desktop is not updating this game. Check Share live scoreboard and its connection status, then retry.');
}

async function sessionMode(data: RegistrationData, gameId: string): Promise<boolean | null> {
  const [locked] = await data.GameSessionMode.select(['id', 'excludeFromLeaderboard'])
    .where({ id: { eq: gameId } }).first(1).execute();
  const [players, historical, specials] = await Promise.all([
    data.GamePlayerSession.select(['id', 'excludeFromLeaderboard'])
      .where({ gameSessionId: { eq: gameId } }).first(3).execute(),
    data.UserSession.select(['id']).where({ gameSessionId: { eq: gameId } }).first(3).execute(),
    data.SpecialSession.select(['id']).where({ gameSessionId: { eq: gameId } }).first(3).execute(),
  ]);
  const modes = new Set([
    ...players.map(row => row.excludeFromLeaderboard ?? false),
    ...historical.map(() => false), ...specials.map(() => false),
    ...(locked ? [locked.excludeFromLeaderboard] : []),
  ]);
  if (modes.size > 1) {
    throw new RegistrationError('This game has conflicting player modes. Ask the organizer to start a new session.');
  }
  return locked?.excludeFromLeaderboard ?? (modes.size ? modes.has(true) : null);
}

async function lockSessionMode(data: RegistrationData, gameId: string, requested: boolean): Promise<boolean> {
  const existing = await sessionMode(data, gameId);
  const selected = existing ?? requested;
  try {
    // The game UUID is the primary key: concurrent kiosks cannot create two mode locks.
    const created = await data.GameSessionMode.create({ id: gameId, excludeFromLeaderboard: selected });
    if (created.id !== gameId) throw new Error('Session mode write returned an unexpected ID.');
    return selected;
  } catch (error) {
    const [locked] = await data.GameSessionMode.select(['id', 'excludeFromLeaderboard'])
      .where({ id: { eq: gameId } }).first(1).execute();
    if (!locked) throw error;
    if (existing !== null && locked.excludeFromLeaderboard !== existing) {
      throw new RegistrationError('This game has conflicting player modes. Ask the organizer to start a new session.');
    }
    return locked.excludeFromLeaderboard;
  }
}

export async function getRegistrationMode(data: RegistrationData): Promise<{
  gameSessionId: string | null; excludeFromLeaderboard: boolean | null;
}> {
  const [game] = await data.GameSession.select(['id', 'startedAt', 'active', 'endedAt'])
    .orderBy({ startedAt: 'desc' }).first(1).execute();
  if (!game?.active || game.endedAt != null) return { gameSessionId: null, excludeFromLeaderboard: null };
  return { gameSessionId: game.id, excludeFromLeaderboard: await sessionMode(data, game.id) };
}

export async function registerPlayer(
  data: RegistrationData, requestId: string, player: string, specialName: string,
  excludeFromLeaderboard = false,
): Promise<PlayerPass> {
  if (!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(requestId)
      || (player !== 'P1' && player !== 'P2') || typeof excludeFromLeaderboard !== 'boolean'
      || (specialName !== '' && !SPECIAL_NAMES.some(name => name === specialName))) {
    throw new RegistrationError('Invalid player registration request.');
  }
  const started = Date.now();
  const replay = (row: GamePlayerSession) => {
    if (row.player !== player || (specialName
      ? row.registrationType !== 'special' || row.username !== specialName
      : row.registrationType !== 'player')) {
      throw new RegistrationError('This registration request belongs to a different player. Choose Start over.');
    }
    return playerPass(row);
  };
  const findSaved = async () => {
    const [row] = await data.GamePlayerSession
      .select(['id', 'gameSessionId', 'player', 'username', 'pin', 'registrationType', 'excludeFromLeaderboard'])
      .where({ id: { eq: requestId } }).first(1).execute();
    return row;
  };
  const [existing, games] = await Promise.all([
    findSaved(),
    data.GameSession.select(['id', 'startedAt', 'lastSeenAt', 'active', 'endedAt'])
      .orderBy({ startedAt: 'desc' }).first(1).execute(),
  ]);
  if (existing) return replay(existing);
  const game = games[0];
  if (!game?.active || game.endedAt != null || !Number.isFinite(new Date(game.lastSeenAt).getTime())) {
    throw new RegistrationError('No live game session. Ask the organizer to enable Share live scoreboard in the DDR app.');
  }
  const slotKey = `${game.id}:${player}`;
  const full = () => new RegistrationError(`Station ${player === 'P1' ? '1' : '2'} is already registered for this game. Ask the organizer to start a new session.`);
  const occupied = async () => (await data.GamePlayerSession.select(['id'])
    .where({ slotKey: { eq: slotKey } }).first(1).execute()).length > 0;
  const firstIdentity = specialName ? { username: specialName, pin: undefined } : newIdentity();
  const historicalIdentityTaken = async (username: string, pin: string | undefined) => {
    if (!pin) return false;
    const [oldPin, oldName] = await Promise.all([
      data.UserSession.select(['id']).where({ pin: { eq: pin } }).first(1).execute(),
      data.UserSession.select(['id']).where({ username: { eq: username } }).first(1).execute(),
    ]);
    return oldPin.length + oldName.length > 0;
  };
  const [legacyPlayers, legacySpecials, slotOccupied, firstIdentityTaken] = await Promise.all([
    data.UserSession.select(['id', 'player']).where({ gameSessionId: { eq: game.id } }).first(3).execute(),
    data.SpecialSession.select(['id', 'player']).where({ gameSessionId: { eq: game.id } }).first(3).execute(),
    occupied(),
    historicalIdentityTaken(firstIdentity.username, firstIdentity.pin),
    verifyLiveGame(data, game, started),
  ]);
  const legacy = [...legacyPlayers, ...legacySpecials];
  if (legacy.some(row => !row.player) || legacy.length > 2) {
    throw new RegistrationError('This game has registrations from before station limits. Ask the organizer to start a new session.');
  }
  if (legacy.length === 2 || legacy.some(row => row.player === player) || slotOccupied) throw full();
  const sessionExcluded = await lockSessionMode(data, game.id, excludeFromLeaderboard);

  for (let attempt = 0; attempt < 10; attempt++) {
    const identity = attempt === 0 ? firstIdentity : specialName ? { username: specialName, pin: undefined } : newIdentity();
    const pin = identity.pin;
    const draft: GamePlayerSession = {
      id: requestId, gameSessionId: game.id, player, slotKey, username: identity.username,
      usernameKey: specialName ? `special:${game.id}:${player}:${specialName}` : `player:${identity.username}`,
      pinKey: specialName ? `special:${requestId}` : `pin:${pin}`,
      ...(pin ? { pin } : {}),
      registrationType: specialName ? 'special' : 'player', createdAt: new Date(), eventQueued: false,
      excludeFromLeaderboard: sessionExcluded,
    };
    const identityTaken = async () => {
      const [pins, names, oldPins, oldNames] = await Promise.all([
        data.GamePlayerSession.select(['id']).where({ pinKey: { eq: draft.pinKey } }).first(1).execute(),
        data.GamePlayerSession.select(['id']).where({ usernameKey: { eq: draft.usernameKey } }).first(1).execute(),
        data.UserSession.select(['id']).where({ pin: { eq: pin ?? '' } }).first(1).execute(),
        data.UserSession.select(['id']).where({ username: { eq: draft.username } }).first(1).execute(),
      ]);
      return pins.length + names.length + oldPins.length + oldNames.length > 0;
    };
    // Historical identities are read-only; current identities are reserved by SQL unique constraints.
    if (attempt === 0 ? firstIdentityTaken : await historicalIdentityTaken(identity.username, pin)) continue;
    try {
      const created = await data.GamePlayerSession.create(draft);
      if (created.id !== requestId) throw new Error('Registration write returned an unexpected ID.');
      return playerPass(draft);
    } catch (error) {
      const saved = await findSaved();
      if (saved) return replay(saved);
      if (await occupied()) throw full();
      if (specialName || !await identityTaken()) throw error;
    }
  }
  throw new RegistrationError('Could not reserve a unique name and PIN. Please retry registration.');
}
