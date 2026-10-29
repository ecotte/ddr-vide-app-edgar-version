import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../../rayfin/data/schema';
import App from '../App';
import { initializeOperator, signIn } from '../client';

vi.mock('../client', () => ({
  initializeOperator: vi.fn(),
  signIn: vi.fn(),
}));

const player = { score: 1200, combo: 65, hits: 20,
  hitsByDirection: { 'Up-Right': 1, 'Up-Left': 2, 'Down-Right': 3, 'Down-Left': 4, Center: 10 },
  hitPace: 2.4, observedScore: 1300, lastVideoTimeMs: 1000 };
const snapshot = { active: true, sessionId: 'session', revision: 1, players: {
  P1: player, P2: { ...player, score: null, combo: null, observedScore: null },
} };

function fakeClient() {
  const client = new RayfinClient<AppSchema>({
    baseUrl: 'https://example.invalid/', publishableKey: 'pk-test', authStorage: false,
  });
  const entity = client.data.LiveBoard;
  const query = entity.select(['id', 'snapshot', 'publishedAt']);
  const execute = vi.spyOn(query, 'execute').mockResolvedValue([{
    id: 'board', snapshot: JSON.stringify(snapshot), publishedAt: new Date('2026-10-12T16:00:00Z'),
  }]);
  const select = vi.spyOn(entity, 'select').mockReturnValue(query);
  vi.spyOn(query, 'where').mockReturnValue(query);
  vi.spyOn(client.auth, 'onSessionChange').mockReturnValue(() => {});
  return { client, execute, select };
}

beforeEach(() => {
  window.location.hash = '';
  vi.useFakeTimers();
  vi.mocked(initializeOperator).mockResolvedValue(true);
  vi.mocked(signIn).mockReset();
});
afterEach(() => { cleanup(); window.location.hash = ''; vi.useRealTimers(); });

it('themes the organizer sign-in without querying data or changing the Microsoft sign-in flow', async () => {
  window.location.hash = '#register';
  vi.mocked(initializeOperator).mockResolvedValue(false);
  vi.mocked(signIn).mockRejectedValue(new Error('Please try signing in again.'));
  const { client, execute } = fakeClient();
  await act(async () => { render(<App client={client} />); });
  expect(screen.getByRole('heading', { name: /PUT YOUR/ })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Open the dance floor.' })).toBeInTheDocument();
  expect(screen.getByText('ORGANIZER SETUP')).toBeInTheDocument();
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
  expect(screen.getByRole('heading', { name: 'Live scoreboard' })).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /Player check-in/ })).not.toBeInTheDocument();
  expect(screen.getByText('1,200')).toBeInTheDocument();
  expect(screen.getByText('65')).toBeInTheDocument();
  expect(screen.getByText('Unconfirmed OCR: 1,300')).toBeInTheDocument();
  expect(screen.getByText('Waiting for players')).toBeInTheDocument();
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
