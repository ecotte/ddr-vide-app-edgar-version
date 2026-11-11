export const CAPTION_INTERVAL_MS = 15_000;
export const CAPTION_COUNT = 50;

export const BEST_STREAK_CAPTIONS = [
  'One good step deserves another.',
  'Your feet found a rhythm worth keeping.',
  'A little run with a big soundtrack.',
  'Consistency has entered the dance floor.',
  'The beat appreciates a repeat customer.',
  'Your shoes connected the dots.',
  'A string of good decisions, made by feet.',
  'The floor remembers this run.',
  'Momentum looks good on you.',
  'A highlight reel with no interruptions.',
  'Your rhythm kept its appointment.',
  'The arrows enjoyed that winning sequence.',
  'A chain reaction in dancing shoes.',
  'Your feet were on speaking terms.',
  'Small victories, neatly linked together.',
  'That run deserves a tiny celebration.',
  'The beat had your undivided attention.',
  'A personal best with floor-level origins.',
  'Your shoes made a compelling argument.',
  'Consistency, now with choreography.',
  'A smooth chapter in the step diary.',
  'The good steps brought their friends.',
  'Your rhythm built a little collection.',
  'A winning run without leaving the room.',
  'The floor gave that sequence a nod.',
  'Your feet stayed on the same page.',
  'A neat little necklace of hits.',
  'Good timing found some company.',
  'The beat likes a connected story.',
  'A run worth keeping on the scoreboard.',
  'Your shoes showed excellent follow-through.',
  'One hit at a time, a streak took shape.',
  'That sequence had a clear sense of direction.',
  'Your rhythm filed a strong progress report.',
  'The arrows saw teamwork down there.',
  'A tidy run of musical decisions.',
  'Your feet kept the conversation going.',
  'The soundtrack met a reliable partner.',
  'A little momentum went a long way.',
  'That run put the steady in steady steps.',
  'Your shoes built something worth counting.',
  'The beat had a good stretch of company.',
  'A sequence with a satisfying rhythm.',
  'Your feet made consistency look lively.',
  'Good steps travel in good company.',
  'A tiny winning chapter, saved for later.',
  'The floor witnessed some solid continuity.',
  'Your rhythm kept joining the dots.',
  'A streak is a team effort between steps.',
  'Your best run has a place of honor.',
] as const;

export const DETECTED_STEPS_CAPTIONS = [
  'Tiny arrows. Big commitment.',
  'The floor is keeping a very small diary.',
  'Somewhere, a spreadsheet is tapping along.',
  'Cardio, with a user interface.',
  'Your shoes have entered their data era.',
  'The beat has opened a support ticket.',
  'An unusually musical way to make numbers.',
  'The floor would like a word.',
  'Dance first. Explain the chart later.',
  'A footnote in motion.',
  'Your sneakers have joined the analytics team.',
  'The soundtrack comes with receipts.',
  'A meeting that could have been a dance.',
  'Your feet are generating interesting paperwork.',
  'The floor is collecting stories, not signatures.',
  'A very rhythmic data-entry method.',
  'The shoes are doing their own research.',
  'A small dashboard with big dance-floor opinions.',
  'The beat has excellent record-keeping.',
  'Choreography with a numerical side hustle.',
  'Your shoes may need a project manager.',
  'A spreadsheet wearing dancing shoes.',
  'The floor has become a willing collaborator.',
  'A musical experiment with foot-operated controls.',
  'Your sneakers have opinions about this song.',
  'The rhythm department is taking notes.',
  'An interpretive dance about data collection.',
  'The shoes are building a compelling case.',
  'A soundtrack for the activity log.',
  'Your feet are submitting their latest findings.',
  'The floor is enjoying its unusual office hours.',
  'A very physical approach to keyboard shortcuts.',
  'The beat has requested a progress update.',
  'Your shoes have adopted an agile methodology.',
  'Data points with a dance-floor origin story.',
  'The rhythm has its own accounting department.',
  'An excellent excuse to give the floor attention.',
  'Your sneakers are attending a musical workshop.',
  'The floor has been promoted to input device.',
  'A lively discussion between feet and arrows.',
  'Your shoes are writing the session notes.',
  'The dance floor is accepting performance art.',
  'A tiny research paper with a very good beat.',
  'The soundtrack is the unofficial team lead.',
  'Your feet have discovered interactive reporting.',
  'The floor is a surprisingly patient audience.',
  'A song-shaped excuse for a data adventure.',
  'Your sneakers are exploring the user experience.',
  'The rhythm team appreciates your participation.',
  'The floor is taking its new dashboard job seriously.',
] as const;

function shuffledCaptions(captions: readonly string[], key: string): readonly string[] {
  const deck = [...captions];
  let seed = 0x811c9dc5;
  for (let index = 0; index < key.length; index += 1) {
    seed = Math.imul(seed ^ key.charCodeAt(index), 0x01000193);
  }
  for (let index = deck.length - 1; index > 0; index -= 1) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const swap = seed % (index + 1);
    [deck[index], deck[swap]] = [deck[swap], deck[index]];
  }
  return deck;
}

export function sessionCaptionDecks(sessionId: string) {
  return {
    bestStreak: shuffledCaptions(BEST_STREAK_CAPTIONS, `${sessionId}:streak`),
    detectedSteps: shuffledCaptions(DETECTED_STEPS_CAPTIONS, `${sessionId}:steps`),
  };
}
