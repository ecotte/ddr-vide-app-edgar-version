import { entity, authenticated, uuid, text, date } from '@microsoft/rayfin-core';

@entity()
@authenticated('read')
@authenticated(['create', 'update'], {
  policy: claims => claims.email.eq('yaschust@microsoft.com'),
})
export class LiveBoard {
  @uuid() id!: string;
  @text({ max: 4000 }) snapshot!: string;
  @date() publishedAt!: Date;
}
