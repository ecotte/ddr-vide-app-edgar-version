export const CAPTION_INTERVAL_MS = 15_000;
export const CAPTION_COUNT = 50;

export const MOST_HIT_LANE_CAPTIONS = [
  'Your feet have a favorite address.',
  'Some arrows get the VIP treatment.',
  'The fan club has chosen its seats.',
  'A very specific kind of loyalty.',
  'Apparently, this is the scenic route.',
  'The arrows are comparing popularity.',
  'Your comfort zone brought dancing shoes.',
  'A tiny campaign for arrow supremacy.',
  'The local favorite has entered the chat.',
  'Your feet are voting with their feet.',
  'Emotional support, delivered by arrows.',
  'The spotlight has excellent taste.',
  'A fan favorite with floor-level access.',
  'Your shoes seem to know the neighborhood.',
  'The other arrows have requested a meeting.',
  'Preferred parking for dancing feet.',
  'A small but enthusiastic following.',
  'The dance floor has a regular customer.',
  'This part of the floor knows your order.',
  'Arrows with main-character energy.',
  'The popularity contest has choreography.',
  'Your feet keep renewing the subscription.',
  'A standing appointment with the beat.',
  'The floor plan has a plot twist.',
  'A familiar route with excellent music.',
  'Your shoes have bookmarked the location.',
  'The arrow fan mail is piling up.',
  'A very niche loyalty program.',
  'The neighborhood dance committee approves.',
  'Your feet may be building a local brand.',
  'Comfort food, but for choreography.',
  'The dance floor has saved your usual spot.',
  'A repeat customer with good rhythm.',
  'The arrows are negotiating celebrity status.',
  'Your feet have a preferred destination.',
  'A tiny landmark on the dance-floor map.',
  'The floor has noticed the attention.',
  'Your shoes have a favorite side quest.',
  'A chart-topping choice, quite literally.',
  'The beat knows where to send the invitations.',
  'A frequent-flyer program for stationary travel.',
  'Your feet have established a home base.',
  'The arrow council has a front-runner.',
  'A little favoritism, a lot of footwork.',
  'The floor is considering reserved seating.',
  'Your shoes have found their people.',
  'A love letter written in directional input.',
  'The popularity poll is accepting footnotes.',
  'The favorite route comes with a soundtrack.',
  'Your feet are making a very clear recommendation.',
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
    mostHitLane: shuffledCaptions(MOST_HIT_LANE_CAPTIONS, `${sessionId}:lane`),
    detectedSteps: shuffledCaptions(DETECTED_STEPS_CAPTIONS, `${sessionId}:steps`),
  };
}
