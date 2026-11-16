import { entity, authenticated, uuid, boolean } from '@microsoft/rayfin-core';

@entity()
@authenticated(['read', 'create'])
export class GameSessionMode {
  @uuid() id!: string;
  @boolean() excludeFromLeaderboard!: boolean;
}
