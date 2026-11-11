export const BOARD_ID = 'd00d0001-0000-4000-8000-000000000001';
export const DIRECTIONS = ['Up-Right', 'Up-Left', 'Down-Right', 'Down-Left', 'Center'] as const;
export type Direction = typeof DIRECTIONS[number];

export interface PlayerState {
  username?: string | null;
  score: number | null;
  runningScore?: number | null;
  combo: number | null;
  currentStreak?: number | null;
  bestStreak?: number | null;
  hits: number;
  unassignedHits?: number;
  unassignedMisses?: number;
  misses?: number | null;
  hitsByDirection?: Record<Direction, number> | null;
  missesByDirection?: Record<Direction, number> | null;
  hitPace?: number | null;
  averageResponseTimeMs?: number | null;
  responseSamples?: number;
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

function isDirectionCounts(value: unknown): value is Record<Direction, number> {
  return isRecord(value) && DIRECTIONS.every(direction => {
    const count = value[direction];
    return typeof count === 'number' && numberOrNull(count);
  });
}

function copyDirectionCounts(counts: Record<Direction, number>): Record<Direction, number> {
  return {
    'Up-Right': counts['Up-Right'], 'Up-Left': counts['Up-Left'],
    'Down-Right': counts['Down-Right'], 'Down-Left': counts['Down-Left'],
    Center: counts.Center,
  };
}

function isPlayer(value: unknown): value is PlayerState {
  if (!isRecord(value)) return false;
  const directions = value.hitsByDirection;
  return isRecord(value) && numberOrNull(value.score) && numberOrNull(value.combo)
    && (value.runningScore == null || numberOrNull(value.runningScore))
    && (value.username == null || (
      typeof value.username === 'string' && value.username.trim().length > 0 && value.username.length <= 80
    ))
    && numberOrNull(value.observedScore) && numberOrNull(value.lastVideoTimeMs)
    && typeof value.hits === 'number' && numberOrNull(value.hits)
    && ((value.currentStreak == null && value.bestStreak == null) || (
      typeof value.currentStreak === 'number' && numberOrNull(value.currentStreak)
      && typeof value.bestStreak === 'number' && numberOrNull(value.bestStreak)
      && value.currentStreak <= value.bestStreak && value.bestStreak <= value.hits
    ))
    && (value.unassignedHits === undefined || (
      typeof value.unassignedHits === 'number' && numberOrNull(value.unassignedHits)
      && value.unassignedHits <= value.hits
    ))
    && (value.unassignedMisses === undefined || (
      typeof value.unassignedMisses === 'number' && numberOrNull(value.unassignedMisses)
      && (value.unassignedMisses === 0 || (
        typeof value.misses === 'number' && value.unassignedMisses <= value.misses
      ))
    ))
    && (value.misses == null || numberOrNull(value.misses))
    && (value.responseSamples === undefined || (
      typeof value.responseSamples === 'number' && numberOrNull(value.responseSamples)
    ))
    && ((value.averageResponseTimeMs == null && (value.responseSamples ?? 0) === 0)
      || (typeof value.averageResponseTimeMs === 'number'
        && Number.isFinite(value.averageResponseTimeMs) && value.averageResponseTimeMs > 0
        && typeof value.responseSamples === 'number' && value.responseSamples > 0
        && value.responseSamples <= value.hits))
    && (value.hitPace == null || (
      typeof value.hitPace === 'number' && Number.isFinite(value.hitPace) && value.hitPace >= 0
    ))
    && (directions == null || isDirectionCounts(directions))
    && (value.missesByDirection == null || isDirectionCounts(value.missesByDirection));
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
    username: player.username ?? null,
    score: player.score, combo: player.combo, hits: player.hits,
    ...(player.bestStreak != null
      ? { currentStreak: player.currentStreak, bestStreak: player.bestStreak } : {}),
    ...(player.runningScore != null ? { runningScore: player.runningScore } : {}),
    unassignedHits: player.unassignedHits ?? 0,
    unassignedMisses: player.unassignedMisses ?? 0,
    misses: player.misses ?? null,
    hitPace: player.hitPace ?? null,
    averageResponseTimeMs: player.averageResponseTimeMs ?? null,
    responseSamples: player.responseSamples ?? 0,
    observedScore: player.observedScore, lastVideoTimeMs: player.lastVideoTimeMs,
    hitsByDirection: player.hitsByDirection ? copyDirectionCounts(player.hitsByDirection) : null,
    ...(player.missesByDirection != null
      ? { missesByDirection: copyDirectionCounts(player.missesByDirection) } : {}),
  };
}

export function arrowAccuracy(player?: PlayerState): Record<Direction, number | null> | null {
  const hits = player?.hitsByDirection;
  const misses = player?.missesByDirection;
  if (!hits || !misses || !DIRECTIONS.some(direction => hits[direction] + misses[direction] > 0)) return null;
  const rate = (direction: Direction) => {
    const total = hits[direction] + misses[direction];
    return total > 0 ? hits[direction] / total * 100 : null;
  };
  return {
    'Up-Right': rate('Up-Right'), 'Up-Left': rate('Up-Left'),
    'Down-Right': rate('Down-Right'), 'Down-Left': rate('Down-Left'),
    Center: rate('Center'),
  };
}

// Schedule after completion, so slow queries never create overlapping requests.
export function poll(task: () => Promise<void>, milliseconds: number | (() => number) = 1000): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout>;
  const run = async () => {
    try {
      await task();
    } finally {
      if (!stopped) timer = setTimeout(() => { void run(); },
        typeof milliseconds === 'function' ? milliseconds() : milliseconds);
    }
  };
  void run();
  return () => { stopped = true; clearTimeout(timer); };
}
