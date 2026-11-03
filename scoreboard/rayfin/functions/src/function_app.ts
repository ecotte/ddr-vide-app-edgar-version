import {
  UserDataFunctions, UserThrownError, UserDataFunctionInternalError, type RayfinContext,
} from '@microsoft/fabric-user-data-functions';
import type { AppSchema } from '../../data/schema.js';
import { registerPlayer, RegistrationError, type PlayerPass } from './registration.js';

const udf = new UserDataFunctions();

udf.func('registerPlayer', async (
  requestId: string, player: string, specialName: string, ctx: RayfinContext<AppSchema>,
): Promise<PlayerPass> => {
  const started = Date.now();
  try {
    const result = await registerPlayer(ctx.getDataClient(), requestId, player, specialName);
    console.log(JSON.stringify({ operation: 'registerPlayer', durationMs: Date.now() - started, status: 'saved' }));
    return result;
  } catch (error) {
    console.error(JSON.stringify({ operation: 'registerPlayer', durationMs: Date.now() - started, status: 'failed' }));
    if (error instanceof RegistrationError) throw new UserThrownError(error.message);
    throw new UserDataFunctionInternalError('Registration service unavailable. Retry the same registration to recover your player pass.');
  }
}, []);
