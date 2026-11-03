import { entity, authenticated, uuid, text, date, boolean, set } from '@microsoft/rayfin-core';

@entity()
@authenticated(['read', 'create'], {
  policy: claims => claims.email.eq('yaschust@microsoft.com'),
})
@authenticated('update', {
  policy: claims => claims.email.eq('yaschust@microsoft.com'),
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
  @date() createdAt!: Date;
  @boolean() eventQueued!: boolean;
}
