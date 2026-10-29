import { entity, authenticated, uuid, text, date, boolean } from '@microsoft/rayfin-core';

@entity()
@authenticated(['read', 'create', 'update'], {
  policy: claims => claims.email.eq('yaschust@microsoft.com'),
})
export class UserSession {
  @uuid() id!: string;
  @uuid() gameSessionId!: string;
  @text({ max: 80, unique: true }) username!: string;
  @text({ max: 6, unique: true }) pin!: string;
  @date() createdAt!: Date;
  @boolean() eventQueued!: boolean;
}
