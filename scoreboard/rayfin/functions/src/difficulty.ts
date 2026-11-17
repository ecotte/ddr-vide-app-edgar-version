export const DIFFICULTIES = [0, 3, 4] as const;
export type Difficulty = typeof DIFFICULTIES[number];

export function isDifficulty(value: unknown): value is Difficulty {
  return value === 0 || value === 3 || value === 4;
}

export function difficultyLabel(value: Difficulty | null): string {
  return value === 0 ? 'Easy' : value === null ? 'Normal (legacy)' : `Normal ${value}`;
}

export function scoreForDifficulty(baseScore: number, difficulty: Difficulty | null): number {
  if (!Number.isSafeInteger(baseScore) || baseScore < 0 || (difficulty !== null && !isDifficulty(difficulty))) {
    throw new Error('Invalid base score or difficulty.');
  }
  const adjusted = difficulty === 4 ? (BigInt(baseScore) * 5n + 2n) / 4n : BigInt(baseScore);
  if (adjusted > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new Error('Adjusted score exceeds the supported range.');
  }
  return Number(adjusted);
}
