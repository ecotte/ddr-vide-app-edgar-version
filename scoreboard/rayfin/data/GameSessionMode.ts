import { entity, authenticated, uuid, boolean } from '@microsoft/rayfin-core';

@entity()
@authenticated(['read', 'create'], {
  policy: claims => claims.email.eq('yaschust@microsoft.com'),
})
export class GameSessionMode {
  @uuid() id!: string;
  @boolean() excludeFromLeaderboard!: boolean;
}
