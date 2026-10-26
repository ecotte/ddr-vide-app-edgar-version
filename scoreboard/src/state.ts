export const BOARD_ID = 'd00d0001-0000-4000-8000-000000000001';
export const DIRECTIONS = ['Up-Right', 'Up-Left', 'Down-Right', 'Down-Left', 'Center'] as const;
export type Direction = typeof DIRECTIONS[number];

export interface PlayerState {
  score: number | null;
  combo: number | null;
  hits: number;
  hitsByDirection?: Record<Direction, number> | null;
  hitPace?: number | null;
  observedScore: number | null;
  lastVideoTimeMs: number | null;
}

export interface BoardSnapshot {
  active: boolean;
  sessionId: string;
  revision: number;
  players: { P1: PlayerState; P2: PlayerState };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function numberOrNull(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0);
}

function isPlayer(value: unknown): value is PlayerState {
  if (!isRecord(value)) return false;
  const directions = value.hitsByDirection;
  return isRecord(value) && numberOrNull(value.score) && numberOrNull(value.combo)
    && numberOrNull(value.observedScore) && numberOrNull(value.lastVideoTimeMs)
    && typeof value.hits === 'number' && numberOrNull(value.hits)
    && (value.hitPace == null || (
      typeof value.hitPace === 'number' && Number.isFinite(value.hitPace) && value.hitPace >= 0
    ))
    && (directions == null || (
      isRecord(directions) && DIRECTIONS.every(direction => {
        const count = directions[direction];
        return typeof count === 'number' && numberOrNull(count);
      })
    ));
}

export function parseSnapshot(value: unknown): BoardSnapshot {
  if (!isRecord(value) || typeof value.active !== 'boolean'
      || typeof value.sessionId !== 'string' || value.sessionId.length > 64
      || typeof value.revision !== 'number' || !numberOrNull(value.revision)
      || !isRecord(value.players) || !isPlayer(value.players.P1) || !isPlayer(value.players.P2)) {
    throw new Error('Invalid scoreboard data received.');
  }
  return {
    active: value.active, sessionId: value.sessionId, revision: value.revision,
    players: { P1: cleanPlayer(value.players.P1), P2: cleanPlayer(value.players.P2) },
  };
}

function cleanPlayer(player: PlayerState): PlayerState {
  return {
    score: player.score, combo: player.combo, hits: player.hits,
    hitPace: player.hitPace ?? null,
    observedScore: player.observedScore, lastVideoTimeMs: player.lastVideoTimeMs,
    hitsByDirection: player.hitsByDirection ? {
      'Up-Right': player.hitsByDirection['Up-Right'], 'Up-Left': player.hitsByDirection['Up-Left'],
      'Down-Right': player.hitsByDirection['Down-Right'], 'Down-Left': player.hitsByDirection['Down-Left'],
      Center: player.hitsByDirection.Center,
    } : null,
  };
}

export function arrowBalance(player?: PlayerState): Record<Direction, number> | null {
  const hits = player?.hitsByDirection;
  if (!hits) return null;
  const total = DIRECTIONS.reduce((sum, direction) => sum + hits[direction], 0);
  if (total === 0) return null;
  return {
    'Up-Right': hits['Up-Right'] / total * 100, 'Up-Left': hits['Up-Left'] / total * 100,
    'Down-Right': hits['Down-Right'] / total * 100, 'Down-Left': hits['Down-Left'] / total * 100,
    Center: hits.Center / total * 100,
  };
}

// Schedule after completion, so slow queries never create overlapping requests.
export function poll(task: () => Promise<void>, milliseconds = 1000): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout>;
  const run = async () => {
    try {
      await task();
    } finally {
      if (!stopped) timer = setTimeout(() => { void run(); }, milliseconds);
    }
  };
  void run();
  return () => { stopped = true; clearTimeout(timer); };
}
