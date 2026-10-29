import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../../rayfin/data/schema';
import RegistrationKiosk from '../RegistrationKiosk';
import { latestGame, newIdentity, saveRegistration, saveSpecialRegistration } from '../registration';

vi.mock('../registration', async importOriginal => ({
  ...await importOriginal<typeof import('../registration')>(),
  latestGame: vi.fn(),
  saveRegistration: vi.fn(),
  newIdentity: vi.fn(),
  saveSpecialRegistration: vi.fn(),
  SPECIAL_NAMES: ['YaelTheChampion', 'EdgarIsGoingDown'],
}));
beforeEach(() => {
  window.history.replaceState(null, '', '/');
  vi.mocked(newIdentity).mockReturnValue({ username: 'SillyEventhouse1234', pin: '123456' });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });
function client() {
  return new RayfinClient<AppSchema>({
    baseUrl: 'https://example.invalid/', publishableKey: 'pk-test', authStorage: false,
  });
}
it('shows credentials and photo instructions only after saving, and clears them for the next player', async () => {
  vi.mocked(latestGame).mockResolvedValue({
    id: 'game', active: true, startedAt: new Date(), lastSeenAt: new Date(),
  });
  vi.mocked(saveRegistration).mockImplementation(async (_client, draft) => draft);
  render(<RegistrationKiosk client={client()} />);
  expect(screen.getByRole('heading', { name: /PUT YOUR/ })).toBeInTheDocument();
  expect(screen.getByRole('list', { name: 'How to join' })).toBeInTheDocument();
  expect(screen.queryByText('123456')).not.toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Register me' })); });
  expect(screen.getByText('123456')).toBeInTheDocument();
  expect(screen.getByText('SillyEventhouse1234')).toBeInTheDocument();
  expect(screen.getByText(/Photograph this screen now/)).toBeInTheDocument();
  expect(screen.getByLabelText('Your player identity')).toBeInTheDocument();
  expect(screen.queryByText(/registration database|Eventhouse delivery|Game session:/)).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /scoreboard/i })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /Next player/ }));
  expect(screen.queryByText('123456')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Register me' })).toBeInTheDocument();
});
it('shows a visible error without presenting an unsaved identity', async () => {
  vi.mocked(latestGame).mockRejectedValue(new Error('No live game session'));
  render(<RegistrationKiosk client={client()} />);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Register me' })); });
  expect(screen.getByRole('alert')).toHaveTextContent('No live game session');
  expect(screen.queryByText('123456')).not.toBeInTheDocument();
  expect(saveRegistration).not.toHaveBeenCalled();
});

it.each([
  ['YaelTheChampion', ['Top-right corner', 'Top-right corner', 'Bottom-left corner', 'Bottom-left corner']],
  ['EdgarIsGoingDown', ['Bottom-left corner', 'Bottom-left corner', 'Top-right corner', 'Top-right corner']],
])('registers %s immediately after its exact corner sequence, without generating a PIN', async (name, corners) => {
  vi.mocked(latestGame).mockResolvedValue({
    id: 'game', active: true, startedAt: new Date(), lastSeenAt: new Date(),
  });
  vi.mocked(saveSpecialRegistration).mockImplementation(async (_client, draft) => draft);
  render(<RegistrationKiosk client={client()} />);
  for (const corner of corners.slice(0, 3)) {
    fireEvent.click(screen.getByRole('button', { name: corner }));
  }
  expect(saveSpecialRegistration).not.toHaveBeenCalled();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: corners[3] })); });
  expect(screen.getByText(name)).toBeInTheDocument();
  expect(screen.getByText('Special entry. No PIN needed.')).toBeInTheDocument();
  expect(screen.queryByText('Your unique PIN')).not.toBeInTheDocument();
  expect(newIdentity).not.toHaveBeenCalled();
  expect(saveRegistration).not.toHaveBeenCalled();
  const draft = vi.mocked(saveSpecialRegistration).mock.calls[0][1];
  expect(draft).not.toHaveProperty('pin');
  expect(draft.registrationKey).toBe(`game:${name}:unassigned`);
});

it('does not activate on interleaved or expired corner taps', () => {
  const now = vi.spyOn(Date, 'now').mockReturnValue(10000);
  render(<RegistrationKiosk client={client()} />);
  for (const corner of ['Top-right corner', 'Bottom-left corner', 'Top-right corner', 'Bottom-left corner']) {
    fireEvent.click(screen.getByRole('button', { name: corner }));
  }
  expect(latestGame).not.toHaveBeenCalled();
  now.mockReturnValue(20000);
  fireEvent.click(screen.getByRole('button', { name: 'Top-right corner' }));
  fireEvent.click(screen.getByRole('button', { name: 'Top-right corner' }));
  now.mockReturnValue(29000);
  fireEvent.click(screen.getByRole('button', { name: 'Bottom-left corner' }));
  fireEvent.click(screen.getByRole('button', { name: 'Bottom-left corner' }));
  expect(latestGame).not.toHaveBeenCalled();
  now.mockRestore();
});

it.each([['1', 'P1'], ['2', 'P2']])('persists player %s and shows it on the saved player pass', async (flag, player) => {
  window.history.replaceState(null, '', `/?player=${flag}#register`);
  vi.mocked(latestGame).mockResolvedValue({
    id: 'game', active: true, startedAt: new Date(), lastSeenAt: new Date(),
  });
  vi.mocked(saveRegistration).mockImplementation(async (_client, draft) => draft);
  render(<RegistrationKiosk client={client()} />);
  expect(screen.getByText(`Player ${flag} station`)).toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Register me' })); });
  expect(vi.mocked(saveRegistration).mock.calls[0][1].player).toBe(player);
  expect(screen.getByText(`Player ${flag} station`)).toBeInTheDocument();
});

it('blocks invalid station URLs without querying or saving a registration', () => {
  window.history.replaceState(null, '', '/?player=3#register');
  render(<RegistrationKiosk client={client()} />);
  expect(screen.getByRole('alert')).toHaveTextContent('Invalid station URL');
  expect(screen.getByRole('button', { name: 'Register me' })).toBeDisabled();
  expect(latestGame).not.toHaveBeenCalled();
});

it('includes the station on special entries without introducing a PIN', async () => {
  window.history.replaceState(null, '', '/?player=2#register');
  vi.mocked(latestGame).mockResolvedValue({
    id: 'game', active: true, startedAt: new Date(), lastSeenAt: new Date(),
  });
  vi.mocked(saveSpecialRegistration).mockImplementation(async (_client, draft) => draft);
  render(<RegistrationKiosk client={client()} />);
  await act(async () => {
    for (const corner of ['Bottom-left corner', 'Bottom-left corner', 'Top-right corner', 'Top-right corner']) {
      fireEvent.click(screen.getByRole('button', { name: corner }));
    }
  });
  expect(vi.mocked(saveSpecialRegistration).mock.calls[0][1]).toMatchObject({
    player: 'P2', username: 'EdgarIsGoingDown', registrationKey: 'game:EdgarIsGoingDown:P2',
  });
  expect(screen.queryByText('Your unique PIN')).not.toBeInTheDocument();
});
