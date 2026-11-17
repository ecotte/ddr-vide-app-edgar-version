import type { AppFunctionsSchema } from '../rayfin/functions/src/types';
import type { Client } from './client';
export { SPECIAL_NAMES, type SpecialName } from '../rayfin/functions/src/identity';
export { DIFFICULTIES, difficultyLabel, type Difficulty } from '../rayfin/functions/src/difficulty';

export type RegistrationRequest = AppFunctionsSchema['registerPlayerWithDifficulty']['input'];
export type PlayerPass = AppFunctionsSchema['registerPlayerWithDifficulty']['output'];
export type KioskStatus = AppFunctionsSchema['getKioskStatus']['output'];

export function getKioskStatus(client: Client, player: 'P1' | 'P2'): Promise<KioskStatus> {
  return client.functions.getKioskStatus.invoke({ player });
}

export function registrationStation(search: string): { player?: 'P1' | 'P2'; error: string } {
  const flags = new URLSearchParams(search).getAll('player');
  if (!flags.length) return { error: 'Choose a station: open this kiosk with ?player=1 or ?player=2.' };
  if (flags.length !== 1 || !['1', '2'].includes(flags[0])) {
    return { error: 'Invalid station URL. Use ?player=1 or ?player=2.' };
  }
  return { player: flags[0] === '1' ? 'P1' : 'P2', error: '' };
}

export function saveRegistration(client: Client, request: RegistrationRequest): Promise<PlayerPass> {
  return client.functions.registerPlayerWithDifficulty.invoke(request, { timeoutMs: 120000 });
}
