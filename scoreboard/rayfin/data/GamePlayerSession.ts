import { entity, authenticated, uuid, text, date, boolean, set, int } from '@microsoft/rayfin-core';

@entity()
@authenticated(['read', 'create'])
@authenticated('update', {
  include: ['id', 'eventQueued'],
})
export class GamePlayerSession {
  @uuid() id!: string;
  @uuid() gameSessionId!: string;
  @set('P1', 'P2') player!: 'P1' | 'P2';
  @text({ max: 40, unique: true }) slotKey!: string;
  @text({ max: 80 }) username!: string;
  @text({ max: 120, unique: true }) usernameKey!: string;
  @text({ max: 6, optional: true }) pin?: string;
  @text({ max: 80, unique: true }) pinKey!: string;
  @set('player', 'special') registrationType!: 'player' | 'special';
  @boolean({ optional: true }) excludeFromLeaderboard?: boolean;
  @int({ optional: true, min: 0, max: 5 }) difficulty?: number;
  @date() createdAt!: Date;
  @boolean() eventQueued!: boolean;
}
