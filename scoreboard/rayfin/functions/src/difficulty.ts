export const DIFFICULTIES = [0, 3, 4, 5] as const;
export type Difficulty = typeof DIFFICULTIES[number];

export function isDifficulty(value: unknown): value is Difficulty {
  return value === 0 || value === 3 || value === 4 || value === 5;
}

export function difficultyLabel(value: Difficulty | null): string {
  return value === 0 ? 'Easy' : value === 5 ? 'Hard' : value === null ? 'Normal (legacy)' : `Normal ${value}`;
}

export function difficultyMultiplier(value: Difficulty | null): number {
  return value === 5 ? 1.5 : value === 4 ? 1.25 : 1;
}

export function scoreForDifficulty(baseScore: number, difficulty: Difficulty | null): number {
  if (!Number.isSafeInteger(baseScore) || baseScore < 0 || (difficulty !== null && !isDifficulty(difficulty))) {
    throw new Error('Invalid base score or difficulty.');
  }
  const quarters = BigInt(difficultyMultiplier(difficulty) * 4);
  const adjusted = (BigInt(baseScore) * quarters + 2n) / 4n;
  if (adjusted > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new Error('Adjusted score exceeds the supported range.');
  }
  return Number(adjusted);
}
