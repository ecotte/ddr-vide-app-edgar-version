import { entity, authenticated, uuid, text, date, boolean } from '@microsoft/rayfin-core';

@entity()
@authenticated('read')
@authenticated('update', {
  include: ['id', 'eventQueued'],
})
export class SpecialSession {
  @uuid() id!: string;
  @uuid() gameSessionId!: string;
  @text({ max: 2, optional: true }) player?: 'P1' | 'P2';
  @text({ max: 120, unique: true }) registrationKey!: string;
  @text({ max: 80 }) username!: string;
  @date() createdAt!: Date;
  @boolean() eventQueued!: boolean;
}
