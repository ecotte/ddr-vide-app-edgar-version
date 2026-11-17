/**
 * Function schema types for RayfinClient.
 *
 * AUTO-GENERATED — do not edit manually.
 * Re-generated automatically when function source files change.
 *
 * If this file is not updating automatically, run:
 *   rayfin dev functions apply
 *
 * The schema is a closed object type: only the function names listed
 * below are accepted by RayfinClient.functions.<name>.invoke(...).
 * Adding, renaming, or changing the signature of a udf.func() call
 * regenerates this file and surfaces type errors at every consumer.
 *
 * IMPORTANT: This file must NOT import any Node.js packages — it is
 * resolved by the frontend app's TypeScript compiler.
 */

export type AppFunctionsSchema = {
  registerPlayer: {
    input: { requestId: string; player: string; specialName: string };
    output: { id: string; gameSessionId: string; player: 'P1' | 'P2'; username: string; pin: null | string; registrationType: 'player' | 'special'; excludeFromLeaderboard: boolean; difficulty: null | 0 | 3 | 4 };
  };
  registerEasyPlayer: {
    input: { requestId: string; player: string; specialName: string };
    output: { id: string; gameSessionId: string; player: 'P1' | 'P2'; username: string; pin: null | string; registrationType: 'player' | 'special'; excludeFromLeaderboard: boolean; difficulty: null | 0 | 3 | 4 };
  };
  registerPlayerWithDifficulty: {
    input: { requestId: string; player: string; specialName: string; difficulty: number };
    output: { id: string; gameSessionId: string; player: 'P1' | 'P2'; username: string; pin: null | string; registrationType: 'player' | 'special'; excludeFromLeaderboard: boolean; difficulty: null | 0 | 3 | 4 };
  };
  getRegistrationMode: {
    input: Record<string, never>;
    output: { gameSessionId: null | string; excludeFromLeaderboard: null | false | true };
  };
};
