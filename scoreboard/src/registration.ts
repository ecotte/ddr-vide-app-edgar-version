import type { AppFunctionsSchema } from '../rayfin/functions/src/types';
import type { Client } from './client';
export { SPECIAL_NAMES, type SpecialName } from '../rayfin/functions/src/identity';

export type RegistrationRequest = AppFunctionsSchema['registerPlayer']['input'];
export type PlayerPass = AppFunctionsSchema['registerPlayer']['output'];

export function registrationStation(search: string): { player?: 'P1' | 'P2'; error: string } {
  const flags = new URLSearchParams(search).getAll('player');
  if (!flags.length) return { error: 'Choose a station: open this kiosk with ?player=1 or ?player=2.' };
  if (flags.length !== 1 || !['1', '2'].includes(flags[0])) {
    return { error: 'Invalid station URL. Use ?player=1 or ?player=2.' };
  }
  return { player: flags[0] === '1' ? 'P1' : 'P2', error: '' };
}

export function saveRegistration(client: Client, request: RegistrationRequest): Promise<PlayerPass> {
  return client.functions.registerPlayer.invoke(request, { timeoutMs: 120000 });
}
