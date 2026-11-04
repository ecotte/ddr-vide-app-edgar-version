import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../../rayfin/data/schema';
import type { AppFunctionsSchema } from '../../rayfin/functions/src/types';
import RegistrationKiosk from '../RegistrationKiosk';
import { saveRegistration, type PlayerPass } from '../registration';

vi.mock('../registration', async importOriginal => ({
  ...await importOriginal<typeof import('../registration')>(),
  saveRegistration: vi.fn(),
}));
const pass: PlayerPass = {
  id: 'user', gameSessionId: 'game', player: 'P1', username: 'SillyEventhouse1234',
  pin: '123456', registrationType: 'player',
  excludeFromLeaderboard: false,
};
beforeEach(() => {
  window.history.replaceState(null, '', '/?player=1#register');
  vi.mocked(saveRegistration).mockResolvedValue(pass);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });
function client() {
  return new RayfinClient<AppSchema, AppFunctionsSchema>({
    baseUrl: 'https://example.invalid/', publishableKey: 'pk-test', authStorage: false,
  });
}
it('shows only a saved pass, its game ID and camera prompt, then clears them for the next player', async () => {
  render(<RegistrationKiosk client={client()} />);
  expect(screen.queryByText(pass.pin!)).not.toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Register me' })); });
  expect(saveRegistration).toHaveBeenCalledTimes(1);
  expect(screen.getByText(pass.username)).toBeInTheDocument();
  expect(screen.getByText(pass.pin!)).toBeInTheDocument();
  expect(screen.getByText('game', { selector: 'code' })).toBeInTheDocument();
  expect(screen.getByText(/Photograph this screen now/)).toBeInTheDocument();
  expect(screen.queryByText(/registration database|Eventhouse delivery/)).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /scoreboard/i })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /Next player/ }));
  expect(screen.queryByText(pass.pin!)).not.toBeInTheDocument();
  expect(screen.queryByText(/Game session ID:/)).not.toBeInTheDocument();
});
it('retains the request ID after a failure so retry cannot create a duplicate', async () => {
  vi.mocked(saveRegistration).mockRejectedValueOnce(new Error('Connection interrupted'));
  render(<RegistrationKiosk client={client()} />);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Register me' })); });
  const request = vi.mocked(saveRegistration).mock.calls[0][1];
  expect(screen.getByRole('alert')).toHaveTextContent('Connection interrupted');
  expect(screen.queryByText(pass.pin!)).not.toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry registration' })); });
  expect(vi.mocked(saveRegistration).mock.calls[1][1]).toEqual(request);
  expect(screen.getByText(pass.username)).toBeInTheDocument();
});

it('offers a discreet easy-mode action below normal registration and retains its mode on retry', async () => {
  vi.mocked(saveRegistration).mockRejectedValueOnce(new Error('Connection interrupted'))
    .mockResolvedValueOnce({ ...pass, excludeFromLeaderboard: true });
  render(<RegistrationKiosk client={client()} />);
  const normal = screen.getByRole('button', { name: 'Register me' });
  const easy = screen.getByRole('button', { name: 'Easy mode (not ranked)' });
  expect(normal.nextElementSibling).toBe(easy);
  expect(easy).toHaveClass('booth-easy-mode');
  expect(easy).not.toHaveClass('booth-primary');
  await act(async () => { fireEvent.click(easy); });
  const request = vi.mocked(saveRegistration).mock.calls[0][1];
  expect(request).toMatchObject({ player: 'P1', specialName: '', excludeFromLeaderboard: true });
  expect(screen.queryByRole('button', { name: 'Easy mode (not ranked)' })).not.toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry registration' })); });
  expect(vi.mocked(saveRegistration).mock.calls[1][1]).toEqual(request);
  expect(screen.getByText(/Easy mode: shown on the live scoreboard/)).toBeInTheDocument();
  expect(screen.getByText(pass.pin!)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /Next player/ }));
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Register me' })); });
  expect(vi.mocked(saveRegistration).mock.calls[2][1]).toMatchObject({ excludeFromLeaderboard: false });
  expect(vi.mocked(saveRegistration).mock.calls[2][1].requestId).not.toBe(request.requestId);
});
it('shows progress and prevents concurrent clicks while its single server request is pending', async () => {
  let resolve!: (value: PlayerPass) => void;
  vi.mocked(saveRegistration).mockReturnValue(new Promise(done => { resolve = done; }));
  render(<RegistrationKiosk client={client()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Register me' }));
  expect(screen.getByRole('status')).toHaveTextContent('Saving your player pass');
  expect(screen.getByRole('button', { name: 'Registering...' })).toBeDisabled();
  expect(saveRegistration).toHaveBeenCalledTimes(1);
  await act(async () => { resolve(pass); });
});
it.each([
  ['YaelTheChampion', ['Top-right corner', 'Top-right corner', 'Bottom-left corner', 'Bottom-left corner']],
  ['EdgarIsGoingDown', ['Bottom-left corner', 'Bottom-left corner', 'Top-right corner', 'Top-right corner']],
  ['The Dancing Queen', ['Top-left corner', 'Top-right corner', 'Bottom-right corner', 'Bottom-left corner']],
])('sends one special registration request for %s without creating a PIN', async (name, corners) => {
  vi.mocked(saveRegistration).mockResolvedValue({ ...pass, username: name, pin: null, registrationType: 'special' });
  render(<RegistrationKiosk client={client()} />);
  for (const corner of corners.slice(0, 3)) fireEvent.click(screen.getByRole('button', { name: corner }));
  expect(saveRegistration).not.toHaveBeenCalled();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: corners[3] })); });
  expect(saveRegistration).toHaveBeenCalledTimes(1);
  expect(vi.mocked(saveRegistration).mock.calls[0][1]).toMatchObject({ player: 'P1', specialName: name });
  expect(screen.getByText(name)).toBeInTheDocument();
  expect(screen.queryByText('Your unique PIN')).not.toBeInTheDocument();
});
it.each(['wrong order', 'expired'])('does not activate The Dancing Queen for %s taps', reason => {
  const now = vi.spyOn(Date, 'now').mockReturnValue(10000);
  render(<RegistrationKiosk client={client()} />);
  const corners = reason === 'wrong order'
    ? ['Top-left corner', 'Bottom-right corner', 'Top-right corner', 'Bottom-left corner']
    : ['Top-left corner', 'Top-right corner', 'Bottom-right corner', 'Bottom-left corner'];
  try {
    for (const [index, corner] of corners.entries()) {
      if (reason === 'expired' && index === 3) now.mockReturnValue(18001);
      fireEvent.click(screen.getByRole('button', { name: corner }));
    }
    expect(saveRegistration).not.toHaveBeenCalled();
  } finally {
    now.mockRestore();
  }
});
it('launches a chicken after three top-left taps without registering, cleans up and can repeat', () => {
  render(<RegistrationKiosk client={client()} />);
  const corner = screen.getByRole('button', { name: 'Top-left corner' });
  const chickenName = 'A chicken doing the funky chicken dance';
  fireEvent.click(corner);
  fireEvent.click(corner);
  expect(screen.queryByRole('img', { name: chickenName })).not.toBeInTheDocument();
  fireEvent.click(corner);
  const chicken = screen.getByRole('img', { name: chickenName });
  expect(chicken.querySelectorAll('.booth-chicken-eye')).toHaveLength(2);
  fireEvent.animationEnd(chicken.querySelector('svg')!);
  expect(chicken).toBeInTheDocument();
  fireEvent.animationEnd(chicken);
  expect(screen.queryByRole('img', { name: chickenName })).not.toBeInTheDocument();
  for (let i = 0; i < 3; i++) fireEvent.click(corner);
  expect(screen.getByRole('img', { name: chickenName })).toBeInTheDocument();
  expect(saveRegistration).not.toHaveBeenCalled();
});
it('does not launch a chicken for expired top-left taps', () => {
  const now = vi.spyOn(Date, 'now').mockReturnValue(10000);
  render(<RegistrationKiosk client={client()} />);
  try {
    const corner = screen.getByRole('button', { name: 'Top-left corner' });
    fireEvent.click(corner);
    fireEvent.click(corner);
    now.mockReturnValue(18001);
    fireEvent.click(corner);
    expect(screen.queryByRole('img', { name: /funky chicken dance/ })).not.toBeInTheDocument();
    expect(saveRegistration).not.toHaveBeenCalled();
  } finally {
    now.mockRestore();
  }
});
it.each([['1', 'P1'], ['2', 'P2']])('sends station %s in its single request', async (flag, player) => {
  window.history.replaceState(null, '', `/?player=${flag}#register`);
  render(<RegistrationKiosk client={client()} />);
  expect(screen.getByText(`Station ${flag}`)).toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Register me' })); });
  expect(vi.mocked(saveRegistration).mock.calls[0][1]).toMatchObject({ player, specialName: '' });
});
it.each(['/', '/?player=3#register'])('blocks missing or invalid station URLs %s', url => {
  window.history.replaceState(null, '', url);
  render(<RegistrationKiosk client={client()} />);
  expect(screen.getByRole('alert')).toHaveTextContent(/station/i);
  expect(screen.getByRole('button', { name: 'Register me' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Easy mode (not ranked)' })).toBeDisabled();
  expect(saveRegistration).not.toHaveBeenCalled();
});
it('does not activate on interleaved or expired corner taps', () => {
  const now = vi.spyOn(Date, 'now').mockReturnValue(10000);
  render(<RegistrationKiosk client={client()} />);
  for (const corner of ['Top-right corner', 'Bottom-left corner', 'Top-right corner', 'Bottom-left corner']) {
    fireEvent.click(screen.getByRole('button', { name: corner }));
  }
  now.mockReturnValue(20000);
  fireEvent.click(screen.getByRole('button', { name: 'Top-right corner' }));
  fireEvent.click(screen.getByRole('button', { name: 'Top-right corner' }));
  now.mockReturnValue(29000);
  fireEvent.click(screen.getByRole('button', { name: 'Bottom-left corner' }));
  fireEvent.click(screen.getByRole('button', { name: 'Bottom-left corner' }));
  expect(saveRegistration).not.toHaveBeenCalled();
  now.mockRestore();
});
