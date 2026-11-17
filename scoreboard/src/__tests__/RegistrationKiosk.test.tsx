import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RayfinClient } from '@microsoft/rayfin-client';
import type { AppSchema } from '../../rayfin/data/schema';
import type { AppFunctionsSchema } from '../../rayfin/functions/src/types';
import RegistrationKiosk from '../RegistrationKiosk';
import { getKioskStatus, saveRegistration, type KioskStatus, type PlayerPass } from '../registration';

vi.mock('../registration', async importOriginal => ({
  ...await importOriginal<typeof import('../registration')>(),
  saveRegistration: vi.fn(),
  getKioskStatus: vi.fn(),
}));
const pass: PlayerPass = {
  id: 'user', gameSessionId: 'game', player: 'P1', username: 'SillyEventhouse1234',
  pin: '123456', registrationType: 'player', excludeFromLeaderboard: false, difficulty: 3,
};
const clients: RayfinClient<AppSchema, AppFunctionsSchema>[] = [];
beforeEach(() => {
  vi.useFakeTimers();
  window.history.replaceState(null, '', '/?player=1#register');
  vi.mocked(saveRegistration).mockResolvedValue(pass);
  vi.mocked(getKioskStatus).mockResolvedValue({ gameSessionId: 'game', registered: false });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach(client => client.auth.destroy());
  vi.restoreAllMocks();
  vi.resetAllMocks();
  vi.useRealTimers();
});
function client() {
  const value = new RayfinClient<AppSchema, AppFunctionsSchema>({
    baseUrl: 'https://example.invalid/', publishableKey: 'pk-test', authStorage: false,
  });
  clients.push(value);
  return value;
}
async function open() {
  const value = client();
  const view = await act(async () => render(<RegistrationKiosk client={value} />));
  return { ...view, client: value };
}
async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}
async function register() {
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Register me' })); });
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

it('describes the ranking and score rules underneath each accessible difficulty choice', async () => {
  await open();
  for (const [label, description] of [
    ['Easy', 'Not ranked'], ['Normal 3', 'Standard score'], ['Normal 4', 'Score multiplied by 1.25'],
  ]) {
    const radio = screen.getByRole('radio', { name: label });
    expect(radio).toHaveAccessibleDescription(description);
    const caption = screen.getByText(description, { selector: 'small' });
    expect(radio.closest('label')).toContainElement(caption);
    expect(caption.previousElementSibling).toHaveTextContent(label);
  }
});

it('shows the saved pass for exactly eight seconds, then keeps the name and difficulty in GAME IN PROGRESS', async () => {
  await open();
  expect(screen.queryByText(pass.pin!)).not.toBeInTheDocument();
  await register();
  expect(saveRegistration).toHaveBeenCalledTimes(1);
  expect(screen.getByText(pass.username)).toBeInTheDocument();
  expect(screen.getByText(pass.pin!)).toBeInTheDocument();
  expect(screen.getByText('game', { selector: 'code' })).toBeInTheDocument();
  expect(screen.getByText(/Photograph this screen now/)).toBeInTheDocument();
  expect(screen.getByText(/hides automatically after 8 seconds/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Next player/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /scoreboard/i })).not.toBeInTheDocument();
  await advance(5000);
  expect(screen.getByText(pass.pin!)).toBeInTheDocument();
  await advance(2999);
  expect(screen.getByText(pass.pin!)).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'GAME IN PROGRESS' })).not.toBeInTheDocument();
  await advance(1);
  expect(screen.getByRole('heading', { name: 'GAME IN PROGRESS' })).toBeInTheDocument();
  expect(screen.getByText(pass.username)).toBeInTheDocument();
  expect(screen.queryByText(pass.pin!)).not.toBeInTheDocument();
  expect(screen.queryByText(/Game session ID:/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Register|Start over|Next player/ })).not.toBeInTheDocument();
  await advance(10000);
  expect(screen.getByRole('heading', { name: 'GAME IN PROGRESS' })).toBeInTheDocument();
});

it('reopens automatically only after a new available game and resets the next difficulty and request', async () => {
  await open();
  expect(screen.queryByText(/Last player:/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('radio', { name: 'Easy' }));
  vi.mocked(saveRegistration).mockResolvedValueOnce({ ...pass, difficulty: 0, excludeFromLeaderboard: true });
  await register();
  const previousRequest = vi.mocked(saveRegistration).mock.calls[0][1];
  await advance(8000);
  vi.mocked(getKioskStatus).mockResolvedValue({ gameSessionId: null, registered: false });
  await advance(1000);
  expect(screen.getByRole('heading', { name: 'GAME IN PROGRESS' })).toBeInTheDocument();
  vi.mocked(getKioskStatus).mockResolvedValue({ gameSessionId: 'new-game', registered: false });
  await advance(1000);
  expect(screen.getByRole('radio', { name: 'Normal 3' })).toBeChecked();
  expect(screen.getByRole('button', { name: 'Register me' })).toBeEnabled();
  expect(screen.getByText(/Last player:/)).toHaveTextContent(`Last player: ${pass.username}`);
  vi.mocked(saveRegistration).mockResolvedValue({ ...pass, gameSessionId: 'new-game', username: 'NextPlayer1234' });
  await register();
  const next = vi.mocked(saveRegistration).mock.calls[1][1];
  expect(next.requestId).not.toBe(previousRequest.requestId);
  expect(next.difficulty).toBe(3);
  await advance(8000);
  expect(screen.getByText('NextPlayer1234')).toBeInTheDocument();
  expect(screen.queryByText(pass.username)).not.toBeInTheDocument();
  vi.mocked(getKioskStatus).mockResolvedValue({ gameSessionId: 'third-game', registered: false });
  await advance(1000);
  expect(screen.getByText(/Last player:/)).toHaveTextContent('Last player: NextPlayer1234');
  vi.mocked(getKioskStatus).mockResolvedValue({ gameSessionId: 'fourth-game', registered: false });
  await advance(1000);
  expect(screen.getByText(/Last player:/)).toHaveTextContent('Last player: NextPlayer1234');
});

it.each([['1', 'P1'], ['2', 'P2']] as const)(
  'restores the name for occupied station %s after refresh without redisplaying a player pass', async (station, player) => {
  window.history.replaceState(null, '', `/?player=${station}#register`);
  vi.mocked(getKioskStatus).mockResolvedValue({
    gameSessionId: 'game', registered: true, username: pass.username, difficulty: player === 'P1' ? 0 : 4,
  });
  await open();
  expect(getKioskStatus).toHaveBeenCalledWith(expect.anything(), player);
  expect(screen.getByText(`Difficulty: ${player === 'P1' ? 'Easy' : 'Normal 4'}`)).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'GAME IN PROGRESS' })).toBeInTheDocument();
  expect(screen.getByText(pass.username)).toBeInTheDocument();
  expect(screen.queryByText(pass.pin!)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Register me' })).not.toBeInTheDocument();
  expect(saveRegistration).not.toHaveBeenCalled();
  vi.mocked(getKioskStatus).mockResolvedValue({
    gameSessionId: 'new-game', registered: true, username: 'OtherPlayer1234', difficulty: null,
  });
  await advance(1000);
  expect(screen.getByRole('heading', { name: 'GAME IN PROGRESS' })).toBeInTheDocument();
  expect(screen.getByText('OtherPlayer1234')).toBeInTheDocument();
  expect(screen.getByText('Difficulty: Normal (legacy)')).toBeInTheDocument();
  expect(screen.queryByText(`Difficulty: ${player === 'P1' ? 'Easy' : 'Normal 4'}`)).not.toBeInTheDocument();
  expect(screen.queryByText(pass.username)).not.toBeInTheDocument();
  vi.mocked(getKioskStatus).mockResolvedValue({ gameSessionId: 'next-game', registered: false });
  await advance(1000);
  expect(screen.getByRole('button', { name: 'Register me' })).toBeEnabled();
  expect(screen.getByText(/Last player:/)).toHaveTextContent('Last player: OtherPlayer1234');
  expect(screen.queryByText(/Difficulty:/)).not.toBeInTheDocument();
});

it('waits for the initial station lookup and for a game instead of showing an available slot by default', async () => {
  const status = deferred<KioskStatus>();
  vi.mocked(getKioskStatus).mockReturnValue(status.promise);
  render(<RegistrationKiosk client={client()} />);
  expect(screen.getByRole('heading', { name: 'Checking this station...' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Register me' })).not.toBeInTheDocument();
  await act(async () => { status.resolve({ gameSessionId: null, registered: false }); });
  expect(screen.getByRole('heading', { name: 'Waiting for a game' })).toBeInTheDocument();
});

it('fails closed on a station lookup error and recovers only when a lookup succeeds', async () => {
  vi.mocked(getKioskStatus).mockRejectedValueOnce(new Error('Station lookup unavailable'));
  await open();
  expect(screen.getByRole('alert')).toHaveTextContent('Station lookup unavailable');
  expect(screen.queryByRole('button', { name: 'Register me' })).not.toBeInTheDocument();
  await advance(1000);
  expect(screen.getByRole('button', { name: 'Register me' })).toBeEnabled();
  vi.mocked(getKioskStatus).mockRejectedValueOnce(new Error('Connection interrupted'));
  await advance(1000);
  expect(screen.getByRole('button', { name: 'Register me' })).toBeDisabled();
  await advance(1000);
  expect(screen.getByRole('button', { name: 'Register me' })).toBeEnabled();
});

it('keeps an occupied station locked through connection failures and recovers on a confirmed new game', async () => {
  vi.mocked(getKioskStatus).mockResolvedValueOnce({ gameSessionId: 'game', registered: true, username: pass.username })
    .mockRejectedValueOnce(new Error('Connection interrupted'));
  await open();
  await advance(1000);
  expect(screen.getByRole('heading', { name: 'GAME IN PROGRESS' })).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('Connection interrupted');
  expect(screen.getByText(pass.username)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Register me' })).not.toBeInTheDocument();
  vi.mocked(getKioskStatus).mockResolvedValue({ gameSessionId: 'new-game', registered: false });
  await advance(1000);
  expect(screen.getByRole('button', { name: 'Register me' })).toBeEnabled();
});

it('does not apply an old status response that started before registration succeeded', async () => {
  await open();
  const status = deferred<KioskStatus>();
  vi.mocked(getKioskStatus).mockReturnValueOnce(status.promise);
  await advance(1000);
  await register();
  await act(async () => { status.resolve({ gameSessionId: 'old-game', registered: false }); });
  expect(screen.getByText(pass.pin!)).toBeInTheDocument();
  await advance(8000);
  expect(screen.getByRole('heading', { name: 'GAME IN PROGRESS' })).toBeInTheDocument();
});

it('ignores a registration response from an old game after the session is reset', async () => {
  const pending = deferred<PlayerPass>();
  vi.mocked(saveRegistration).mockReturnValueOnce(pending.promise);
  await open();
  await register();
  vi.mocked(getKioskStatus).mockResolvedValue({ gameSessionId: 'new-game', registered: false });
  await advance(1000);
  expect(screen.getByRole('button', { name: 'Register me' })).toBeEnabled();
  await act(async () => { pending.resolve(pass); });
  expect(screen.queryByText(pass.username)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Register me' })).toBeEnabled();
});

it('clears an old pass on reset and cancels its timer so it cannot hide the next player early', async () => {
  await open();
  await register();
  vi.mocked(getKioskStatus).mockResolvedValue({ gameSessionId: 'new-game', registered: false });
  await advance(1000);
  expect(screen.queryByText(pass.pin!)).not.toBeInTheDocument();
  vi.mocked(saveRegistration).mockResolvedValue({ ...pass, gameSessionId: 'new-game', pin: '654321' });
  await register();
  await advance(7000);
  expect(screen.getByText('654321')).toBeInTheDocument();
  await advance(1000);
  expect(screen.getByRole('heading', { name: 'GAME IN PROGRESS' })).toBeInTheDocument();
});

it('preserves a pass saved in the new game before the status poll observes that reset', async () => {
  await open();
  vi.mocked(saveRegistration).mockResolvedValue({ ...pass, gameSessionId: 'new-game' });
  await register();
  vi.mocked(getKioskStatus).mockResolvedValue({ gameSessionId: 'new-game', registered: true });
  await advance(1000);
  expect(screen.getByText(pass.pin!)).toBeInTheDocument();
  await advance(6999);
  expect(screen.getByText(pass.pin!)).toBeInTheDocument();
  await advance(1);
  expect(screen.getByRole('heading', { name: 'GAME IN PROGRESS' })).toBeInTheDocument();
});

it('cleans up polling and the eight-second timer on unmount', async () => {
  const view = await open();
  await register();
  const calls = vi.mocked(getKioskStatus).mock.calls.length;
  view.unmount();
  await advance(10000);
  expect(getKioskStatus).toHaveBeenCalledTimes(calls);
});

it('hides credentials after eight seconds even while a status request is stalled', async () => {
  await open();
  await register();
  const pending = deferred<KioskStatus>();
  vi.mocked(getKioskStatus).mockReturnValueOnce(pending.promise);
  await advance(8000);
  expect(screen.queryByText(pass.pin!)).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'GAME IN PROGRESS' })).toBeInTheDocument();
  await act(async () => { pending.resolve({ gameSessionId: 'game', registered: true }); });
});

it('retains the request ID after a failure so retry cannot create a duplicate', async () => {
  vi.mocked(saveRegistration).mockRejectedValueOnce(new Error('Connection interrupted'));
  await open();
  await register();
  const request = vi.mocked(saveRegistration).mock.calls[0][1];
  expect(screen.getByRole('alert')).toHaveTextContent('Connection interrupted');
  expect(screen.queryByText(pass.pin!)).not.toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry registration' })); });
  expect(vi.mocked(saveRegistration).mock.calls[1][1]).toEqual(request);
  expect(screen.getByText(pass.username)).toBeInTheDocument();
});

it('permits an idempotent retry to recover a lost player pass even after status reports the slot occupied', async () => {
  vi.mocked(saveRegistration).mockRejectedValueOnce(new Error('Lost registration response'));
  await open();
  await register();
  const request = vi.mocked(saveRegistration).mock.calls[0][1];
  vi.mocked(getKioskStatus).mockResolvedValue({ gameSessionId: 'game', registered: true });
  await advance(1000);
  expect(screen.getByRole('button', { name: 'Retry registration' })).toBeEnabled();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry registration' })); });
  expect(vi.mocked(saveRegistration).mock.calls[1][1]).toEqual(request);
  expect(screen.getByText(pass.pin!)).toBeInTheDocument();
});

it('Start over cannot reopen an occupied station after a failed registration', async () => {
  vi.mocked(saveRegistration).mockRejectedValueOnce(new Error('Station is already registered'));
  await open();
  await register();
  vi.mocked(getKioskStatus).mockResolvedValue({ gameSessionId: 'game', registered: true });
  await advance(1000);
  fireEvent.click(screen.getByRole('button', { name: 'Start over' }));
  expect(screen.getByRole('heading', { name: 'GAME IN PROGRESS' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Register me' })).not.toBeInTheDocument();
});

it('retains Easy on retry, and Start over clears only an uncommitted draft', async () => {
  vi.mocked(saveRegistration).mockRejectedValueOnce(new Error('Connection interrupted'))
    .mockRejectedValueOnce(new Error('Still interrupted'));
  await open();
  fireEvent.click(screen.getByRole('radio', { name: 'Easy' }));
  await register();
  const request = vi.mocked(saveRegistration).mock.calls[0][1];
  expect(request).toMatchObject({ player: 'P1', specialName: '', difficulty: 0 });
  expect(screen.getByRole('radio', { name: 'Normal 4' })).toBeDisabled();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry registration' })); });
  expect(vi.mocked(saveRegistration).mock.calls[1][1]).toEqual(request);
  fireEvent.click(screen.getByRole('button', { name: 'Start over' }));
  expect(screen.getByRole('radio', { name: 'Normal 3' })).toBeChecked();
  await register();
  expect(vi.mocked(saveRegistration).mock.calls[2][1].requestId).not.toBe(request.requestId);
});

it.each([[0, 'Easy'], [3, 'Normal 3'], [4, 'Normal 4']] as const)(
  'registers independent numeric difficulty %s and shows %s on the saved pass',
  async (difficulty, label) => {
    vi.mocked(saveRegistration).mockResolvedValue({ ...pass, difficulty, excludeFromLeaderboard: difficulty === 0 });
    await open();
    expect(screen.getByRole('radio', { name: 'Normal 3' })).toBeChecked();
    fireEvent.click(screen.getByRole('radio', { name: label }));
    await register();
    expect(vi.mocked(saveRegistration).mock.calls[0][1].difficulty).toBe(difficulty);
    expect(screen.getByText(`Difficulty: ${label}`)).toBeInTheDocument();
    expect(screen.getByText(difficulty === 0 ? /Easy mode: shown/ : /This player is eligible/)).toBeInTheDocument();
    await advance(8000);
    expect(screen.getByRole('heading', { name: 'GAME IN PROGRESS' })).toBeInTheDocument();
    expect(screen.getByText(`Difficulty: ${label}`)).toBeInTheDocument();
    expect(screen.queryByText(pass.pin!)).not.toBeInTheDocument();
  },
);

it('shows progress and prevents concurrent clicks while its registration request is pending', async () => {
  const pending = deferred<PlayerPass>();
  vi.mocked(saveRegistration).mockReturnValue(pending.promise);
  await open();
  fireEvent.click(screen.getByRole('button', { name: 'Register me' }));
  expect(screen.getByRole('status')).toHaveTextContent('Saving your player pass');
  expect(screen.getByRole('button', { name: 'Registering...' })).toBeDisabled();
  expect(saveRegistration).toHaveBeenCalledTimes(1);
  await act(async () => { pending.resolve(pass); });
});

it.each([
  ['YaelTheChampion', ['Top-right corner', 'Top-right corner', 'Bottom-left corner', 'Bottom-left corner']],
  ['EdgarIsGoingDown', ['Bottom-left corner', 'Bottom-left corner', 'Top-right corner', 'Top-right corner']],
  ['The Dancing Queen', ['Top-left corner', 'Top-right corner', 'Bottom-right corner', 'Bottom-left corner']],
  ['Bowser', ['Bottom-right corner', 'Bottom-right corner', 'Bottom-right corner']],
])('uses the same eight-second lifecycle for special player %s without creating a PIN', async (name, corners) => {
  vi.mocked(saveRegistration).mockResolvedValue({ ...pass, username: name, pin: null, registrationType: 'special' });
  await open();
  for (const corner of corners.slice(0, -1)) fireEvent.click(screen.getByRole('button', { name: corner }));
  expect(saveRegistration).not.toHaveBeenCalled();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: corners[corners.length - 1] })); });
  expect(saveRegistration).toHaveBeenCalledTimes(1);
  expect(vi.mocked(saveRegistration).mock.calls[0][1]).toMatchObject({ player: 'P1', specialName: name });
  expect(screen.getByText(name)).toBeInTheDocument();
  expect(screen.queryByText('Your unique PIN')).not.toBeInTheDocument();
  await advance(8000);
  expect(screen.getByRole('heading', { name: 'GAME IN PROGRESS' })).toBeInTheDocument();
  expect(screen.getByText(name)).toBeInTheDocument();
});

it.each(['interleaved', 'expired'])('does not register Bowser for %s bottom-right taps', async reason => {
  const now = vi.spyOn(Date, 'now').mockReturnValue(10000);
  await open();
  const corner = screen.getByRole('button', { name: 'Bottom-right corner' });
  fireEvent.click(corner);
  fireEvent.click(corner);
  if (reason === 'expired') now.mockReturnValue(18001);
  else fireEvent.click(screen.getByRole('button', { name: 'Top-right corner' }));
  fireEvent.click(corner);
  expect(saveRegistration).not.toHaveBeenCalled();
});

it('registers Bowser once for station 2 with the selected difficulty despite extra taps', async () => {
  window.history.replaceState(null, '', '/?player=2#register');
  const pending = deferred<PlayerPass>();
  vi.mocked(saveRegistration).mockReturnValue(pending.promise);
  await open();
  fireEvent.click(screen.getByRole('radio', { name: 'Normal 4' }));
  const corner = screen.getByRole('button', { name: 'Bottom-right corner' });
  for (let i = 0; i < 6; i++) fireEvent.click(corner);
  expect(saveRegistration).toHaveBeenCalledTimes(1);
  expect(vi.mocked(saveRegistration).mock.calls[0][1]).toMatchObject({
    player: 'P2', specialName: 'Bowser', difficulty: 4,
  });
  await act(async () => {
    pending.resolve({ ...pass, player: 'P2', username: 'Bowser', pin: null, registrationType: 'special', difficulty: 4 });
  });
  expect(screen.getByText('Bowser')).toBeInTheDocument();
});

it.each(['wrong order', 'expired'])('does not activate The Dancing Queen for %s taps', async reason => {
  const now = vi.spyOn(Date, 'now').mockReturnValue(10000);
  await open();
  const corners = reason === 'wrong order'
    ? ['Top-left corner', 'Bottom-right corner', 'Top-right corner', 'Bottom-left corner']
    : ['Top-left corner', 'Top-right corner', 'Bottom-right corner', 'Bottom-left corner'];
  for (const [index, corner] of corners.entries()) {
    if (reason === 'expired' && index === 3) now.mockReturnValue(18001);
    fireEvent.click(screen.getByRole('button', { name: corner }));
  }
  expect(saveRegistration).not.toHaveBeenCalled();
});

it('launches and cleans up a chicken after three top-left taps without registering', async () => {
  await open();
  const corner = screen.getByRole('button', { name: 'Top-left corner' });
  const name = 'A chicken doing the funky chicken dance';
  fireEvent.click(corner);
  fireEvent.click(corner);
  expect(screen.queryByRole('img', { name })).not.toBeInTheDocument();
  fireEvent.click(corner);
  const chicken = screen.getByRole('img', { name });
  expect(chicken.querySelectorAll('.booth-chicken-eye')).toHaveLength(2);
  fireEvent.animationEnd(chicken.querySelector('svg')!);
  expect(chicken).toBeInTheDocument();
  fireEvent.animationEnd(chicken);
  expect(screen.queryByRole('img', { name })).not.toBeInTheDocument();
  for (let i = 0; i < 3; i++) fireEvent.click(corner);
  expect(screen.getByRole('img', { name })).toBeInTheDocument();
  expect(saveRegistration).not.toHaveBeenCalled();
});

it('does not launch a chicken for expired top-left taps', async () => {
  const now = vi.spyOn(Date, 'now').mockReturnValue(10000);
  await open();
  const corner = screen.getByRole('button', { name: 'Top-left corner' });
  fireEvent.click(corner);
  fireEvent.click(corner);
  now.mockReturnValue(18001);
  fireEvent.click(corner);
  expect(screen.queryByRole('img', { name: /funky chicken dance/ })).not.toBeInTheDocument();
  expect(saveRegistration).not.toHaveBeenCalled();
});

it.each([['1', 'P1'], ['2', 'P2']])('polls and registers only its own station %s', async (flag, player) => {
  window.history.replaceState(null, '', `/?player=${flag}#register`);
  const view = await open();
  expect(screen.getByText(`Station ${flag}`)).toBeInTheDocument();
  expect(getKioskStatus).toHaveBeenCalledWith(view.client, player);
  await register();
  expect(vi.mocked(saveRegistration).mock.calls[0][1]).toMatchObject({ player, specialName: '' });
});

it.each(['/', '/?player=3#register'])('blocks missing or invalid station URLs %s without polling', async url => {
  window.history.replaceState(null, '', url);
  await open();
  expect(screen.getByRole('alert')).toHaveTextContent(/station/i);
  expect(screen.getByRole('button', { name: 'Register me' })).toBeDisabled();
  for (const radio of screen.getAllByRole('radio')) expect(radio).toBeDisabled();
  expect(saveRegistration).not.toHaveBeenCalled();
  expect(getKioskStatus).not.toHaveBeenCalled();
});

it('does not activate special registration on interleaved or expired corner taps', async () => {
  const now = vi.spyOn(Date, 'now').mockReturnValue(10000);
  await open();
  for (const corner of ['Top-right corner', 'Bottom-left corner', 'Top-right corner', 'Bottom-left corner']) {
    fireEvent.click(screen.getByRole('button', { name: corner }));
  }
  now.mockReturnValue(20000);
  fireEvent.click(screen.getByRole('button', { name: 'Top-right corner' }));
  fireEvent.click(screen.getByRole('button', { name: 'Top-right corner' }));
  now.mockReturnValue(28001);
  fireEvent.click(screen.getByRole('button', { name: 'Bottom-left corner' }));
  fireEvent.click(screen.getByRole('button', { name: 'Bottom-left corner' }));
  expect(saveRegistration).not.toHaveBeenCalled();
});
