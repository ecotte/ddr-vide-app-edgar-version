import { entity, authenticated, uuid, text, date, boolean } from '@microsoft/rayfin-core';

@entity()
@authenticated('read')
@authenticated('update', {
  include: ['id', 'eventQueued'],
})
export class UserSession {
  @uuid() id!: string;
  @uuid() gameSessionId!: string;
  @text({ max: 2, optional: true }) player?: 'P1' | 'P2';
  @text({ max: 80, unique: true }) username!: string;
  @text({ max: 6, unique: true }) pin!: string;
  @date() createdAt!: Date;
  @boolean() eventQueued!: boolean;
}
