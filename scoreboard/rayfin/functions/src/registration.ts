import type { RayfinContext } from '@microsoft/fabric-user-data-functions';
import type { AppSchema } from '../../data/schema.js';
import type { GamePlayerSession } from '../../data/GamePlayerSession.js';
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
}

function playerPass(row: GamePlayerSession): PlayerPass {
  return {
    id: row.id, gameSessionId: row.gameSessionId, player: row.player, username: row.username,
    pin: row.registrationType === 'special' ? null : row.pin ?? null,
    registrationType: row.registrationType,
  };
}

export async function registerPlayer(
  data: RegistrationData, requestId: string, player: string, specialName: string,
): Promise<PlayerPass> {
  if (!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(requestId)
      || (player !== 'P1' && player !== 'P2')
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
  const [existing, games] = await Promise.all([
    data.GamePlayerSession.findById(requestId),
    data.GameSession.select(['id', 'startedAt', 'lastSeenAt', 'active', 'endedAt'])
      .orderBy({ startedAt: 'desc' }).first(1).execute(),
  ]);
  if (existing) return replay(existing);
  const game = games[0];
  const seen = game ? new Date(game.lastSeenAt).getTime() : NaN;
  if (!game?.active || game.endedAt != null || !Number.isFinite(seen)
      || started - seen > 15000 || seen > Date.now() + 5000) {
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
  ]);
  const legacy = [...legacyPlayers, ...legacySpecials];
  if (legacy.some(row => !row.player) || legacy.length > 2) {
    throw new RegistrationError('This game has registrations from before station limits. Ask the organizer to start a new session.');
  }
  if (legacy.length === 2 || legacy.some(row => row.player === player) || slotOccupied) throw full();

  for (let attempt = 0; attempt < 10; attempt++) {
    const identity = attempt === 0 ? firstIdentity : specialName ? { username: specialName, pin: undefined } : newIdentity();
    const pin = identity.pin;
    const draft: GamePlayerSession = {
      id: requestId, gameSessionId: game.id, player, slotKey, username: identity.username,
      usernameKey: specialName ? `special:${game.id}:${player}:${specialName}` : `player:${identity.username}`,
      pinKey: specialName ? `special:${requestId}` : `pin:${pin}`,
      ...(pin ? { pin } : {}),
      registrationType: specialName ? 'special' : 'player', createdAt: new Date(), eventQueued: false,
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
      return playerPass(await data.GamePlayerSession.create(draft));
    } catch (error) {
      const saved = await data.GamePlayerSession.findById(requestId);
      if (saved) return replay(saved);
      if (await occupied()) throw full();
      if (specialName || !await identityTaken()) throw error;
    }
  }
  throw new RegistrationError('Could not reserve a unique name and PIN. Please retry registration.');
}
