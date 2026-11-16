import { entity, authenticated, uuid, date, boolean } from '@microsoft/rayfin-core';

@entity()
@authenticated(['read', 'create', 'update'])
export class GameSession {
  @uuid() id!: string;
  @date() startedAt!: Date;
  @date() lastSeenAt!: Date;
  @date({ optional: true }) endedAt?: Date;
  @boolean() active!: boolean;
}
