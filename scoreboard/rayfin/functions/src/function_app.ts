import {
  UserDataFunctions, UserThrownError, UserDataFunctionInternalError, type RayfinContext,
} from '@microsoft/fabric-user-data-functions';
import type { AppSchema } from '../../data/schema.js';
import { getRegistrationMode, registerPlayer, RegistrationError, type PlayerPass } from './registration.js';
import { isDifficulty } from './difficulty.js';

const udf = new UserDataFunctions();

async function savePlayer(
  requestId: string, player: string, specialName: string, ctx: RayfinContext<AppSchema>, difficulty: number | null,
): Promise<PlayerPass> {
  const started = Date.now();
  try {
    const result = await registerPlayer(ctx.getDataClient(), requestId, player, specialName, difficulty);
    console.log(JSON.stringify({ operation: 'registerPlayer', durationMs: Date.now() - started, status: 'saved' }));
    return result;
  } catch (error) {
    console.error(JSON.stringify({ operation: 'registerPlayer', durationMs: Date.now() - started, status: 'failed' }));
    if (error instanceof RegistrationError) throw new UserThrownError(error.message);
    throw new UserDataFunctionInternalError('Registration service unavailable. Retry the same registration to recover your player pass.');
  }
}

udf.func('registerPlayer', async (
  requestId: string, player: string, specialName: string, ctx: RayfinContext<AppSchema>,
): Promise<PlayerPass> => savePlayer(requestId, player, specialName, ctx, null), []);

udf.func('registerEasyPlayer', async (
  requestId: string, player: string, specialName: string, ctx: RayfinContext<AppSchema>,
): Promise<PlayerPass> => savePlayer(requestId, player, specialName, ctx, 0), []);

udf.func('registerPlayerWithDifficulty', async (
  requestId: string, player: string, specialName: string, difficulty: number, ctx: RayfinContext<AppSchema>,
): Promise<PlayerPass> => {
  if (!isDifficulty(difficulty)) throw new UserThrownError('Choose Easy, Normal 3, or Normal 4.');
  return savePlayer(requestId, player, specialName, ctx, difficulty);
}, []);

udf.func('getRegistrationMode', async (ctx: RayfinContext<AppSchema>): Promise<{
  gameSessionId: string | null; excludeFromLeaderboard: boolean | null;
}> => {
  try {
    return await getRegistrationMode(ctx.getDataClient());
  } catch (error) {
    console.error(JSON.stringify({ operation: 'getRegistrationMode', status: 'failed' }));
    if (error instanceof RegistrationError) throw new UserThrownError(error.message);
    throw new UserDataFunctionInternalError('Unable to check the session mode. Ask the organizer to check the connection.');
  }
}, []);
