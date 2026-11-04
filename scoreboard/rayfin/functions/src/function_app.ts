import {
  UserDataFunctions, UserThrownError, UserDataFunctionInternalError, type RayfinContext,
} from '@microsoft/fabric-user-data-functions';
import type { AppSchema } from '../../data/schema.js';
import { getRegistrationMode, registerPlayer, RegistrationError, type PlayerPass } from './registration.js';

const udf = new UserDataFunctions();

async function savePlayer(
  requestId: string, player: string, specialName: string, ctx: RayfinContext<AppSchema>, excludeFromLeaderboard: boolean,
): Promise<PlayerPass> {
  const started = Date.now();
  try {
    const result = await registerPlayer(ctx.getDataClient(), requestId, player, specialName, excludeFromLeaderboard);
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
): Promise<PlayerPass> => savePlayer(requestId, player, specialName, ctx, false), []);

udf.func('registerEasyPlayer', async (
  requestId: string, player: string, specialName: string, ctx: RayfinContext<AppSchema>,
): Promise<PlayerPass> => savePlayer(requestId, player, specialName, ctx, true), []);

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
