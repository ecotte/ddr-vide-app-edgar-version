import { entity, authenticated, uuid, date, boolean } from '@microsoft/rayfin-core';

@entity()
@authenticated(['read', 'create', 'update'], {
  policy: claims => claims.email.eq('yaschust@microsoft.com'),
})
export class GameSession {
  @uuid() id!: string;
  @date() startedAt!: Date;
  @date() lastSeenAt!: Date;
  @boolean() active!: boolean;
}
