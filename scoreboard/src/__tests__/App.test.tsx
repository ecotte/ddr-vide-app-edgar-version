import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../../rayfin/data/schema';
import type { AppFunctionsSchema } from '../../rayfin/functions/src/types';
import App from '../App';
import { initializeOperator, signIn } from '../client';

vi.mock('../client', () => ({
  initializeOperator: vi.fn(),
  signIn: vi.fn(),
}));

const player = { score: 1200, combo: 65, hits: 20, misses: 5,
  hitsByDirection: { 'Up-Right': 1, 'Up-Left': 2, 'Down-Right': 3, 'Down-Left': 4, Center: 10 },
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
  expect(screen.getByRole('meter', { name: 'Player 1 Up-Right share' })).toHaveAttribute('aria-valuenow', '5');
  expect(screen.getByRole('meter', { name: 'Player 1 Center share' })).toHaveAttribute('aria-valuenow', '50');
  expect(screen.getByLabelText('Player 1 arrow balance')).toHaveTextContent('not accuracy');
});

it('shows per-player response averages, sourced comparisons, cumulative percentage and step titles', async () => {
  const { client } = fakeClient();
  await act(async () => { render(<App client={client} />); });
  expect(screen.getByLabelText('Player 1 cumulative hit percentage')).toHaveTextContent('80.0%');
  expect(screen.getByLabelText('Player 1 cumulative hit percentage')).toHaveTextContent('20 hits / 25');
  expect(screen.getByLabelText('Player 1 time to answer')).toHaveTextContent('301 ms');
  expect(screen.getByLabelText('Player 1 time to answer')).toHaveTextContent('2 timed hits');
  expect(screen.getByLabelText('Player 2 time to answer')).toHaveTextContent('125 ms');
  const comparison = within(screen.getByLabelText('Player 2 animal comparison'));
  expect(comparison.getByText('Faster than the mouse reference')).toBeInTheDocument();
  expect(comparison.getByRole('link')).toHaveAttribute('href', 'https://pmc.ncbi.nlm.nih.gov/articles/PMC3809337/');
  expect(comparison.getByText(/not a like-for-like test/)).toBeInTheDocument();
  expect(screen.getByLabelText('Player 1 step title')).toHaveTextContent('Center-Step Superstar');
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
  for (const name of ['time to answer', 'animal comparison', 'cumulative hit percentage', 'step title', 'hit pace', 'arrow balance', 'hits by arrow']) {
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
});
