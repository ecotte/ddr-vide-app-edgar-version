import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../../rayfin/data/schema';
import type { AppFunctionsSchema } from '../../rayfin/functions/src/types';
import App from '../App';
import { initializeOperator, signIn } from '../client';
import { DETECTED_STEPS_CAPTIONS, MOST_HIT_LANE_CAPTIONS, sessionCaptionDecks } from '../dashboardCaptions';

vi.mock('../client', () => ({
  initializeOperator: vi.fn(),
  signIn: vi.fn(),
}));

const player = { score: 1200, combo: 65, hits: 20, misses: 5,
  hitsByDirection: { 'Up-Right': 1, 'Up-Left': 2, 'Down-Right': 3, 'Down-Left': 4, Center: 10 },
  missesByDirection: { 'Up-Right': 1, 'Up-Left': 1, 'Down-Right': 1, 'Down-Left': 2, Center: 0 },
  hitPace: 2.4, averageResponseTimeMs: 300.5, responseSamples: 2, observedScore: 1300, lastVideoTimeMs: 1000 };
const snapshot = { active: true, sessionId: 'session', revision: 1, players: {
  P1: player, P2: { ...player, score: null, combo: null, observedScore: null, averageResponseTimeMs: 125 },
} };

function fakeClient(value: unknown = snapshot) {
  const client = new RayfinClient<AppSchema, AppFunctionsSchema>({
    baseUrl: 'https://example.invalid/', publishableKey: 'pk-test', authStorage: false,
  });
  const entity = client.data.LiveBoard;
  const query = entity.select(['id', 'snapshot', 'publishedAt']);
  const execute = vi.spyOn(query, 'execute').mockResolvedValue([{
    id: 'board', snapshot: JSON.stringify(value), publishedAt: new Date('2026-10-12T16:00:00Z'),
  }]);
  const select = vi.spyOn(entity, 'select').mockReturnValue(query);
  vi.spyOn(query, 'where').mockReturnValue(query);
  vi.spyOn(client.auth, 'onSessionChange').mockReturnValue(() => {});
  return { client, execute, select };
}

beforeEach(() => {
  window.history.replaceState(null, '', '/');
  window.location.hash = '';
  vi.useFakeTimers();
  vi.mocked(initializeOperator).mockResolvedValue(true);
  vi.mocked(signIn).mockReset();
});
afterEach(() => { cleanup(); window.location.hash = ''; vi.useRealTimers(); });

it('themes the organizer sign-in without querying data or changing the Microsoft sign-in flow', async () => {
  window.history.replaceState(null, '', '/?player=2#register');
  vi.mocked(initializeOperator).mockResolvedValue(false);
  vi.mocked(signIn).mockRejectedValue(new Error('Please try signing in again.'));
  const { client, execute } = fakeClient();
  await act(async () => { render(<App client={client} />); });
  expect(screen.getByRole('heading', { name: /PUT YOUR/ })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Open the dance floor.' })).toBeInTheDocument();
  expect(screen.getByText('ORGANIZER SETUP')).toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'Microsoft Fabric' })).toHaveAttribute('src', expect.stringContaining('fabric-logo-white.png'));
  expect(screen.getByText('Station 2').closest('header')).toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Sign in with Microsoft' })); });
  expect(signIn).toHaveBeenCalledWith(client);
  expect(screen.getByRole('alert')).toHaveTextContent('Please try signing in again.');
  expect(execute).not.toHaveBeenCalled();
});

it('does not query data before sign-in', async () => {
  vi.mocked(initializeOperator).mockResolvedValue(false);
  const { client, execute } = fakeClient();
  await act(async () => { render(<App client={client} />); });
  expect(screen.getByRole('button', { name: 'Sign in with Microsoft' })).toBeInTheDocument();
  expect(execute).not.toHaveBeenCalled();
});

it('selects required fields and separates unconfirmed and accepted values', async () => {
  const { client, select } = fakeClient();
  await act(async () => { render(<App client={client} />); });
  expect(select).toHaveBeenCalledWith(['id', 'snapshot', 'publishedAt']);
  expect(screen.getByText('DDR LIVE SCOREBOARD')).toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'Microsoft Fabric' })).toHaveAttribute('width', '2296');
  expect(screen.getByRole('heading', { name: 'Live scoreboard' })).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /Player check-in/ })).not.toBeInTheDocument();
  expect(screen.getByText('1,200')).toBeInTheDocument();
  expect(screen.getByText('65')).toBeInTheDocument();
  expect(screen.getByText('Unconfirmed OCR: 1,300')).toBeInTheDocument();
  expect(screen.queryByText('Waiting for players')).not.toBeInTheDocument();
  expect(screen.queryByText('MISSES')).not.toBeInTheDocument();
  expect(screen.getAllByLabelText('Up-Right hits')[0]).toHaveTextContent('1');
  expect(screen.getAllByLabelText('Up-Left hits')[0]).toHaveTextContent('2');
  expect(screen.getAllByLabelText('Down-Right hits')[0]).toHaveTextContent('3');
  expect(screen.getAllByLabelText('Down-Left hits')[0]).toHaveTextContent('4');
  expect(screen.getAllByLabelText('Center hits')[0]).toHaveTextContent('10');
  expect(screen.queryByLabelText('Up hits')).not.toBeInTheDocument();
  expect(screen.getAllByRole('meter')).toHaveLength(10);
  expect(screen.getByLabelText('Player 1 hit pace')).toHaveTextContent('2.4');
  expect(screen.getByRole('meter', { name: 'Player 1 Up-Right accuracy' })).toHaveAttribute('aria-valuenow', '50');
  expect(screen.getByRole('meter', { name: 'Player 1 Center accuracy' })).toHaveAttribute('aria-valuenow', '100');
  expect(screen.getByLabelText('Player 1 arrow accuracy')).toHaveTextContent('ARROW ACCURACY');
  expect(screen.queryByText('ARROW BALANCE')).not.toBeInTheDocument();
});

it('distinguishes zero lane accuracy from unplayed lanes and hides unavailable legacy lane metrics', async () => {
  const zero = { 'Up-Right': 0, 'Up-Left': 0, 'Down-Right': 0, 'Down-Left': 0, Center: 0 };
  const p1 = {
    ...player, hits: 0, misses: 2, hitsByDirection: zero,
    missesByDirection: { ...zero, Center: 2 }, averageResponseTimeMs: null, responseSamples: 0,
  };
  const { client } = fakeClient({
    ...snapshot, players: { P1: p1, P2: { ...player, missesByDirection: undefined } },
  });
  await act(async () => { render(<App client={client} />); });
  const accuracy = screen.getByLabelText('Player 1 arrow accuracy');
  expect(within(accuracy).getAllByText('No attempts')).toHaveLength(4);
  expect(within(accuracy).getByRole('meter', { name: 'Player 1 Center accuracy' })).toHaveAttribute('aria-valuenow', '0');
  expect(within(accuracy).getByText('0.0%')).toBeInTheDocument();
  expect(within(accuracy).queryByRole('meter', { name: 'Player 1 Up-Right accuracy' })).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Player 2 arrow accuracy')).not.toBeInTheDocument();
});

it('keeps known zero hit pace visible after hits even when no recent hits remain', async () => {
  const { client } = fakeClient({
    ...snapshot, players: { P1: { ...player, hitPace: 0 }, P2: { ...player, hitPace: null } },
  });
  await act(async () => { render(<App client={client} />); });
  expect(screen.getByLabelText('Player 1 hit pace')).toHaveTextContent('0.0 detected hits / sec');
  expect(screen.queryByLabelText('Player 2 hit pace')).not.toBeInTheDocument();
});

it('uses judgment running scores as the main score including zero and suppresses unrelated OCR', async () => {
  const { client, execute } = fakeClient({
    ...snapshot, players: { P1: { ...player, runningScore: 130 }, P2: { ...player, runningScore: 0 } },
  });
  await act(async () => { render(<App client={client} />); });
  expect(screen.getAllByText('RUNNING SCORE')).toHaveLength(2);
  expect(screen.getByLabelText('Player 1 score')).toHaveTextContent('130');
  expect(screen.getByLabelText('Player 2 score')).toHaveTextContent('0');
  expect(screen.queryByText('ACCEPTED SCORE')).not.toBeInTheDocument();
  expect(screen.queryByText('1,200')).not.toBeInTheDocument();
  expect(screen.queryByText(/Unconfirmed OCR/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Good \+10 \/ Great \+20 \/ Perfect \+40 \/ Miss 0/)).not.toBeInTheDocument();
  execute.mockResolvedValue([{
    id: 'board', publishedAt: new Date(),
    snapshot: JSON.stringify({
      ...snapshot, sessionId: 'new-game', revision: 2,
      players: { P1: { ...player, runningScore: 0 }, P2: { ...player, runningScore: 0 } },
    }),
  }]);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByLabelText('Player 1 score')).toHaveTextContent('0');
});

it('shows per-player response averages, sourced comparisons, cumulative percentage and step titles', async () => {
  const { client } = fakeClient();
  await act(async () => { render(<App client={client} />); });
  expect(screen.getByLabelText('Player 1 cumulative hit percentage')).toHaveTextContent('80.0%');
  expect(screen.getByLabelText('Player 1 cumulative hit percentage')).toHaveTextContent('20 hits / 25');
  expect(screen.queryByText('Hits / (hits + misses) this game. Undetected notes are not counted.')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Player 1 time to answer')).toHaveTextContent('301 ms');
  expect(screen.getByLabelText('Player 1 time to answer')).toHaveTextContent('2 timed hits');
  expect(screen.getByLabelText('Player 2 time to answer')).toHaveTextContent('125 ms');
  const comparison = within(screen.getByLabelText('Player 2 animal comparison'));
  expect(comparison.getByText('Faster than the mouse reference')).toBeInTheDocument();
  expect(comparison.getByRole('link')).toHaveAttribute('href', 'https://pmc.ncbi.nlm.nih.gov/articles/PMC3809337/');
  expect(comparison.getByText(/not a like-for-like test/)).toBeInTheDocument();
  expect(screen.getByLabelText('Player 1 step title')).toHaveTextContent('Center-Step Superstar');
});

it('shows playful most-hit and detected-step tiles per player and clears them for a new game', async () => {
  const p2 = {
    ...player, hits: 3, misses: 1, averageResponseTimeMs: null, responseSamples: 0,
    hitsByDirection: { 'Up-Right': 1, 'Up-Left': 0, 'Down-Right': 0, 'Down-Left': 0, Center: 1 },
  };
  const { client, execute } = fakeClient({ ...snapshot, players: { P1: player, P2: p2 } });
  await act(async () => { render(<App client={client} />); });
  const p1Lane = screen.getByLabelText('Player 1 most-hit lane');
  expect(p1Lane).toHaveTextContent('Center');
  expect(p1Lane).toHaveTextContent('10 hits.');
  expect(MOST_HIT_LANE_CAPTIONS).toContain(p1Lane.querySelector('.metric-caption')?.textContent);
  expect(screen.getByLabelText('Player 1 total detected steps')).toHaveTextContent('25');
  expect(DETECTED_STEPS_CAPTIONS).toContain(
    screen.getByLabelText('Player 1 total detected steps').querySelector('.metric-caption')?.textContent,
  );
  const p2Lane = screen.getByLabelText('Player 2 most-hit lane');
  expect(p2Lane).toHaveTextContent('Up-Right + Center');
  expect(p2Lane).toHaveTextContent('1 hit each.');
  expect(MOST_HIT_LANE_CAPTIONS).toContain(p2Lane.querySelector('.metric-caption')?.textContent);
  expect(screen.getByLabelText('Player 2 total detected steps')).toHaveTextContent('4');
  expect(screen.queryByText('Hits + misses detected this game.')).not.toBeInTheDocument();
  const empty = {
    ...player, hits: 0, misses: 0, averageResponseTimeMs: null, responseSamples: 0,
    hitsByDirection: { 'Up-Right': 0, 'Up-Left': 0, 'Down-Right': 0, 'Down-Left': 0, Center: 0 },
  };
  execute.mockResolvedValue([{
    id: 'board', publishedAt: new Date(),
    snapshot: JSON.stringify({ ...snapshot, sessionId: 'new-game', revision: 2, players: { P1: empty, P2: empty } }),
  }]);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  for (const name of ['Player 1', 'Player 2']) {
    expect(screen.queryByLabelText(`${name} most-hit lane`)).not.toBeInTheDocument();
    const steps = screen.getByLabelText(`${name} total detected steps`);
    expect(steps.querySelector('.metric-value')).toHaveTextContent('0');
    expect(DETECTED_STEPS_CAPTIONS).toContain(steps.querySelector('.metric-caption')?.textContent);
  }
});

it('rotates both captions every 15 live seconds, not on polling or score changes, and resets for a new game', async () => {
  let current = snapshot;
  const { client, execute } = fakeClient();
  execute.mockImplementation(async () => [{
    id: 'board', snapshot: JSON.stringify(current), publishedAt: new Date(),
  }]);
  const view = await act(async () => render(<App client={client} />));
  const caption = (name: string, tile: string) =>
    screen.getByLabelText(`${name} ${tile}`).querySelector('.metric-caption')?.textContent;
  const decks = sessionCaptionDecks('session');
  expect(caption('Player 1', 'most-hit lane')).toBe(decks.mostHitLane[0]);
  expect(caption('Player 2', 'most-hit lane')).toBe(decks.mostHitLane[25]);
  expect(caption('Player 1', 'total detected steps')).toBe(decks.detectedSteps[0]);
  expect(caption('Player 2', 'total detected steps')).toBe(decks.detectedSteps[25]);
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  current = { ...snapshot, revision: 2, players: { ...snapshot.players, P1: { ...player, hits: 21 } } };
  await act(async () => { await vi.advanceTimersByTimeAsync(9999); });
  expect(caption('Player 1', 'most-hit lane')).toBe(decks.mostHitLane[0]);
  expect(caption('Player 1', 'total detected steps')).toBe(decks.detectedSteps[0]);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(caption('Player 1', 'most-hit lane')).toBe(decks.mostHitLane[1]);
  expect(caption('Player 2', 'most-hit lane')).toBe(decks.mostHitLane[26]);
  expect(caption('Player 1', 'total detected steps')).toBe(decks.detectedSteps[1]);
  current = { ...current, sessionId: 'next-game', revision: 3 };
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  const next = sessionCaptionDecks('next-game');
  expect(caption('Player 1', 'most-hit lane')).toBe(next.mostHitLane[0]);
  expect(caption('Player 2', 'total detected steps')).toBe(next.detectedSteps[25]);
  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
});

it.each(['inactive', 'error'])('pauses caption rotation while the feed has an %s state', async mode => {
  let current = snapshot;
  const { client, execute } = fakeClient();
  const healthy = async () => [{
    id: 'board', snapshot: JSON.stringify(current), publishedAt: new Date(),
  }];
  execute.mockImplementation(healthy);
  await act(async () => { render(<App client={client} />); });
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  if (mode === 'inactive') current = { ...snapshot, active: false };
  else execute.mockRejectedValue(new Error('Network unavailable'));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  current = snapshot;
  execute.mockImplementation(healthy);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  const caption = () => screen.getByLabelText('Player 1 most-hit lane').querySelector('.metric-caption')?.textContent;
  const deck = sessionCaptionDecks('session').mostHitLane;
  expect(caption()).toBe(deck[1]);
  await act(async () => { await vi.advanceTimersByTimeAsync(14999); });
  expect(caption()).toBe(deck[1]);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(caption()).toBe(deck[2]);
});

it('keeps stable alignment slots without inventing missing names, scores, combos or metrics', async () => {
  const { client } = fakeClient({
    ...snapshot, players: {
      P1: { ...player, username: 'BubblyDashboard4442', combo: null },
      P2: { ...player, score: null, username: null, averageResponseTimeMs: null, responseSamples: 0 },
    },
  });
  await act(async () => { render(<App client={client} />); });
  const panels = screen.getByRole('heading', { name: 'Player 1 BubblyDashboard4442' })
    .closest('.players')!;
  expect(panels.querySelectorAll('.player-heading')).toHaveLength(2);
  expect(panels.querySelectorAll('.player-score')).toHaveLength(1);
  expect(panels.querySelectorAll('.combo')).toHaveLength(1);
  for (const [label, className] of [
    ['most-hit lane', 'metric-most-hit'],
    ['total detected steps', 'metric-steps'],
    ['cumulative hit percentage', 'metric-percentage'],
    ['step title', 'metric-step-title'],
  ]) {
    for (const name of ['Player 1', 'Player 2']) {
      expect(screen.getByLabelText(`${name} ${label}`)).toHaveClass(className);
    }
  }
  expect(screen.getByLabelText('Player 1 time to answer')).toHaveClass('metric-response');
  expect(screen.queryByLabelText('Player 2 time to answer')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Player 2 score')).not.toBeInTheDocument();
  expect(screen.queryByText('--')).not.toBeInTheDocument();
});

it('matches titles and symbols to each player highest lane accuracy and updates after misses', async () => {
  const accurate = {
    ...player, hits: 10, misses: 2,
    hitsByDirection: { 'Up-Right': 4, 'Up-Left': 0, 'Down-Right': 0, 'Down-Left': 6, Center: 0 },
    missesByDirection: { 'Up-Right': 0, 'Up-Left': 0, 'Down-Right': 0, 'Down-Left': 2, Center: 0 },
  };
  const p2 = {
    ...accurate, hits: 7, misses: 3,
    hitsByDirection: { ...accurate.hitsByDirection, 'Up-Right': 0, Center: 1 },
    missesByDirection: { ...accurate.missesByDirection, Center: 1 },
  };
  const { client, execute } = fakeClient({ ...snapshot, players: { P1: accurate, P2: p2 } });
  await act(async () => { render(<App client={client} />); });
  const p1Title = screen.getByLabelText('Player 1 step title');
  expect(p1Title).toHaveTextContent('Up-Right Royalty');
  expect(screen.getByLabelText('Player 1 most-hit lane')).toHaveTextContent('Down-Left');
  expect(p1Title.querySelector('.step-title-symbol')).toHaveTextContent('\u2197');
  expect(p1Title).toHaveTextContent('Best hit rate: Up-Right (100.0%; 4 hits / 4 detected outcomes).');
  const p2Title = screen.getByLabelText('Player 2 step title');
  expect(p2Title).toHaveTextContent('Down-Left Dynamo');
  expect(p2Title.querySelector('.step-title-symbol')).toHaveTextContent('\u2199');
  expect(p2Title).toHaveTextContent('Best hit rate: Down-Left (75.0%; 6 hits / 8 detected outcomes).');
  execute.mockResolvedValue([{
    id: 'board', publishedAt: new Date(),
    snapshot: JSON.stringify({
      ...snapshot, revision: 2, players: {
        P1: { ...accurate, misses: 10, missesByDirection: { ...accurate.missesByDirection, 'Up-Right': 8 } },
        P2: p2,
      },
    }),
  }]);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByLabelText('Player 1 step title')).toHaveTextContent('Down-Left Dynamo');
});

it('omits accuracy titles for older snapshots with hits but no per-lane misses', async () => {
  const { missesByDirection: _misses, ...legacy } = player;
  const { client } = fakeClient({ ...snapshot, players: { P1: legacy, P2: legacy } });
  await act(async () => { render(<App client={client} />); });
  expect(screen.queryByLabelText('Player 1 step title')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Player 2 step title')).not.toBeInTheDocument();
});

it('omits unknown-direction explanations without changing displayed totals or lane counts', async () => {
  const { client } = fakeClient({
    ...snapshot, players: { P1: { ...player, unassignedHits: 3, unassignedMisses: 2 }, P2: player },
  });
  await act(async () => { render(<App client={client} />); });
  expect(screen.queryByText(/Direction unknown:/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Included in player totals, not arrow counts or response timing/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Player 1 unknown-direction outcomes')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Player 2 unknown-direction outcomes')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Player 1 cumulative hit percentage')).toHaveTextContent('20 hits / 25 detected outcomes.');
  expect(within(screen.getByLabelText('Player 1 hits by arrow')).getByLabelText('Center hits')).toHaveTextContent('10');
});

it('shows check-in names under the correct player labels with gender-neutral dancing icons', async () => {
  const { client, execute } = fakeClient({
    ...snapshot, players: {
      P1: { ...player, username: 'SillyEventhouse1234' },
      P2: { ...player, username: 'HappyLakehouse5678' },
    },
  });
  await act(async () => { render(<App client={client} />); });
  for (const [label, username] of [
    ['Player 1', 'SillyEventhouse1234'], ['Player 2', 'HappyLakehouse5678'],
  ]) {
    const heading = screen.getByRole('heading', { name: `${label} ${username}` });
    expect(heading.querySelector('.player-corner svg')).toHaveAttribute('viewBox', '0 0 32 32');
    expect(heading.querySelector('.player-corner')).toHaveAttribute('aria-hidden', 'true');
  }
  execute.mockResolvedValue([{
    id: 'board', snapshot: JSON.stringify({ ...snapshot, sessionId: 'new-game' }),
    publishedAt: new Date('2026-10-12T16:00:01Z'),
  }]);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.queryByText('SillyEventhouse1234')).not.toBeInTheDocument();
  expect(screen.queryByText('HappyLakehouse5678')).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Player 1' })).toBeInTheDocument();
});

it('does not show previous player names while the feed is inactive', async () => {
  const { client } = fakeClient({
    ...snapshot, active: false, players: { P1: { ...player, username: 'OldPlayer' }, P2: player },
  });
  await act(async () => { render(<App client={client} />); });
  expect(screen.queryByText('OldPlayer')).not.toBeInTheDocument();
});

it('hides missing metrics in legacy snapshots instead of leaving empty cards', async () => {
  const legacy = { score: null, combo: null, hits: 3, observedScore: null, lastVideoTimeMs: 100 };
  const { client } = fakeClient({ ...snapshot, players: { P1: legacy, P2: legacy } });
  await act(async () => { render(<App client={client} />); });
  for (const name of ['time to answer', 'animal comparison', 'cumulative hit percentage', 'step title', 'hit pace', 'arrow accuracy', 'hits by arrow', 'most-hit lane', 'total detected steps']) {
    expect(screen.queryByLabelText(`Player 1 ${name}`)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(`Player 2 ${name}`)).not.toBeInTheDocument();
  }
  expect(screen.queryByText('--')).not.toBeInTheDocument();
  expect(screen.queryByText('ACCEPTED SCORE')).not.toBeInTheDocument();
  expect(screen.queryByText('COMBO')).not.toBeInTheDocument();
});

it('shows zero-percent accuracy for real misses but no timing or title without hits', async () => {
  const empty = { ...player, hits: 0, misses: 2, hitsByDirection: {
    'Up-Right': 0, 'Up-Left': 0, 'Down-Right': 0, 'Down-Left': 0, Center: 0,
  }, averageResponseTimeMs: null, responseSamples: 0 };
  const { client } = fakeClient({ ...snapshot, players: { P1: empty, P2: empty } });
  await act(async () => { render(<App client={client} />); });
  expect(screen.getByLabelText('Player 1 cumulative hit percentage')).toHaveTextContent('0.0%');
  expect(screen.queryByLabelText('Player 1 time to answer')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Player 1 step title')).not.toBeInTheDocument();
});

it('hides all metric cards when the publisher is inactive, even with retained values', async () => {
  const { client } = fakeClient({ ...snapshot, active: false });
  await act(async () => { render(<App client={client} />); });
  expect(screen.queryByLabelText('Player 1 time to answer')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Player 1 cumulative hit percentage')).not.toBeInTheDocument();
  expect(screen.queryAllByRole('meter')).toHaveLength(0);
  expect(screen.queryByLabelText('Player 1 most-hit lane')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Player 2 most-hit lane')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Player 1 total detected steps')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Player 2 total detected steps')).not.toBeInTheDocument();
});

it('retains old values with an explicit warning after a query failure', async () => {
  const { client, execute } = fakeClient();
  await act(async () => { render(<App client={client} />); });
  execute.mockRejectedValueOnce(new Error('Network unavailable'));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByRole('alert')).toHaveTextContent('Network unavailable');
  expect(screen.getByText('1,200')).toBeInTheDocument();
});

it('shows disconnection for an unchanged publisher heartbeat', async () => {
  const { client } = fakeClient();
  await act(async () => { render(<App client={client} />); });
  await act(async () => { await vi.advanceTimersByTimeAsync(16000); });
  expect(screen.getByText('Desktop publisher disconnected')).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('last received values');
  const caption = screen.getByLabelText('Player 1 most-hit lane').querySelector('.metric-caption')?.textContent;
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(screen.getByLabelText('Player 1 most-hit lane').querySelector('.metric-caption')?.textContent).toBe(caption);
});
