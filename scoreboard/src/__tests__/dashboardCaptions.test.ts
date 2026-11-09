import { expect, it } from 'vitest';
import {
  CAPTION_COUNT, CAPTION_INTERVAL_MS, DETECTED_STEPS_CAPTIONS,
  MOST_HIT_LANE_CAPTIONS, sessionCaptionDecks,
} from '../dashboardCaptions';

it('provides exactly 50 unique nonempty captions per tile and a 15-second interval', () => {
  expect(CAPTION_COUNT).toBe(50);
  expect(CAPTION_INTERVAL_MS).toBe(15_000);
  for (const captions of [MOST_HIT_LANE_CAPTIONS, DETECTED_STEPS_CAPTIONS]) {
    expect(captions).toHaveLength(50);
    expect(new Set(captions).size).toBe(50);
    expect(captions.every(caption => caption.trim().length > 0)).toBe(true);
  }
});

it('keeps session decks stable while using a different order for a new game', () => {
  const first = sessionCaptionDecks('first-game');
  expect(sessionCaptionDecks('first-game')).toEqual(first);
  const next = sessionCaptionDecks('next-game');
  expect(next.mostHitLane).not.toEqual(first.mostHitLane);
  expect(next.detectedSteps).not.toEqual(first.detectedSteps);
  expect(MOST_HIT_LANE_CAPTIONS[0]).toBe('Your feet have a favorite address.');
  expect(DETECTED_STEPS_CAPTIONS[0]).toBe('Tiny arrows. Big commitment.');
});

it('does not repeat within a full cycle and keeps simultaneous player captions distinct', () => {
  const decks = sessionCaptionDecks('session');
  for (const deck of [decks.mostHitLane, decks.detectedSteps]) {
    const p1 = Array.from({ length: 50 }, (_, index) => deck[index]);
    const p2 = Array.from({ length: 50 }, (_, index) => deck[(index + 25) % 50]);
    expect(new Set(p1).size).toBe(50);
    expect(new Set(p2).size).toBe(50);
    expect(p1.every((caption, index) => caption !== p2[index])).toBe(true);
    expect(new Set(deck)).toEqual(new Set(
      deck === decks.mostHitLane ? MOST_HIT_LANE_CAPTIONS : DETECTED_STEPS_CAPTIONS,
    ));
    expect(deck[49]).not.toBe(deck[0]);
  }
});
