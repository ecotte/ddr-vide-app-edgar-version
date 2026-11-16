import { entity, authenticated, uuid, text, date } from '@microsoft/rayfin-core';

@entity()
@authenticated('read')
@authenticated(['create', 'update'])
export class LiveBoard {
  @uuid() id!: string;
  @text({ max: 4000 }) snapshot!: string;
  @date() publishedAt!: Date;
}
