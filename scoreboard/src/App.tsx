import { useEffect, useState } from 'react';
import { initializeOperator, signIn, type Client } from './client';
import { BOARD_ID, DIRECTIONS, arrowBalance, parseSnapshot, poll, type BoardSnapshot, type PlayerState } from './state';
import Registration from './Registration';

function message(error: unknown) {
  return error instanceof Error ? error.message : 'The scoreboard request failed.';
}

function Player({ name, state, waiting }: { name: string; state?: PlayerState; waiting: boolean }) {
  const balance = waiting ? null : arrowBalance(state);
  return <section className={`player ${name === 'Player 2' ? 'pink' : ''}`}>
    <h2>{name}</h2>
    <div className="score-label">ACCEPTED SCORE</div>
    <div className="score">{waiting || state?.score == null ? 'Waiting for players' : state.score.toLocaleString()}</div>
    <div className="combo"><span>COMBO</span><strong>{waiting || state?.combo == null ? '--' : state.combo}</strong></div>
    <div className="total-hits"><span>TOTAL HITS</span><strong>{waiting ? '--' : state?.hits ?? 0}</strong></div>
    <div className="counts" aria-label={`${name} hits by arrow`}>
      {DIRECTIONS.map(direction => <div key={direction}>
        <span className="arrow-icon" aria-hidden="true">{{
          'Up-Right': '\u2197', 'Up-Left': '\u2196', 'Down-Right': '\u2198', 'Down-Left': '\u2199',
          Center: '\u25cf',
        }[direction]}</span>
        <span>{direction.toUpperCase()}</span>
        <strong aria-label={`${direction} hits`}>
          {waiting || !state?.hitsByDirection ? '--' : state.hitsByDirection[direction].toLocaleString()}
        </strong>
      </div>)}
    </div>
    <section className="hit-pace" aria-label={`${name} hit pace`}>
      <h3>HIT PACE</h3>
      <div><strong>{waiting || state?.hitPace == null ? '--' : state.hitPace.toFixed(1)}</strong>
        <span> detected hits / sec</span></div>
      <p>Last 5 seconds of video; holds while paused.</p>
    </section>
    <section className="arrow-balance" aria-label={`${name} arrow balance`}>
      <h3>ARROW BALANCE</h3>
      {DIRECTIONS.map(direction => <div className="balance-row" key={direction}>
        <span>{direction}</span>
        <div role="meter" aria-label={`${name} ${direction} share`}
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={balance?.[direction] ?? 0}
          aria-valuetext={balance ? `${balance[direction].toFixed(1)}% of detected hits` : 'No hit balance available'}>
          <div style={{ width: `${balance?.[direction] ?? 0}%` }} />
        </div>
        <strong>{balance ? `${balance[direction].toFixed(1)}%` : '--'}</strong>
      </div>)}
      <p>{balance ? 'Share of detected hits this session, not accuracy.' : 'Waiting for directional hits.'}</p>
    </section>
    <div className="player-notes">
    {!waiting && state && !state.hitsByDirection && <p className="observation">
      Arrow breakdown unavailable. Restart the updated DDR app and start a new session.
    </p>}
    {!waiting && state && state.hitPace == null && <p className="observation">
      Hit pace unavailable. Restart the updated DDR app.
    </p>}
    {!waiting && state?.observedScore != null && state.observedScore !== state.score
      && <p className="observation">Unconfirmed OCR: {state.observedScore.toLocaleString()}</p>}
    </div>
  </section>;
}

export default function App({ client }: { client: Client }) {
  const [registration, setRegistration] = useState(window.location.hash === '#register');
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const changed = () => setRegistration(window.location.hash === '#register');
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
  useEffect(() => {
    let disposed = false;
    void initializeOperator(client).then(value => {
      if (!disposed) setAuthenticated(value);
    }).catch(e => { if (!disposed) setError(message(e)); })
      .finally(() => { if (!disposed) setReady(true); });
    const unsubscribe = client.auth.onSessionChange(session => {
      if (!disposed) setAuthenticated(session?.isAuthenticated ?? false);
    });
    return () => { disposed = true; unsubscribe(); };
  }, [client]);
  if (!ready) return <main><h1>{registration ? 'Player check-in' : 'DDR Scoreboard'}</h1><p>Checking Microsoft sign-in...</p></main>;
  if (!authenticated) return <main><h1>{registration ? 'Player check-in' : 'DDR Scoreboard'}</h1>
    <p>{registration ? 'Organizer: sign in once on this shared device. Players do not need Microsoft accounts. Sign in again if the session expires.'
      : 'Sign in with an authorized Microsoft account to view the live scores.'}</p>
    <button onClick={() => {
      setError('');
      void signIn(client).then(session => setAuthenticated(session.isAuthenticated))
        .catch(e => setError(message(e)));
    }}>Sign in with Microsoft</button>
    {error && <p role="alert" className="error">{error}</p>}
  </main>;
  return registration ? <Registration client={client} /> : <Scoreboard client={client} />;
}

function Scoreboard({ client }: { client: Client }) {
  const [snapshot, setSnapshot] = useState<BoardSnapshot | null>(null);
  const [error, setError] = useState('');
  const [published, setPublished] = useState('');
  const [lastChange, setLastChange] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    let disposed = false;
    let previousStamp = '';
    const stop = poll(async () => {
      try {
        const rows = await client.data.LiveBoard.select(['id', 'snapshot', 'publishedAt'])
          .where({ id: { eq: BOARD_ID } }).execute();
        const row = rows[0];
        if (disposed) return;
        const data = row ? parseSnapshot(JSON.parse(row.snapshot)) : null;
        const stamp = row ? String(row.publishedAt) : '';
        setSnapshot(data);
        setPublished(stamp);
        if (stamp !== previousStamp) { setLastChange(Date.now()); previousStamp = stamp; }
        setError('');
      } catch (e) {
        if (!disposed) setError(`Unable to refresh: ${message(e)}`);
      }
    });
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => { disposed = true; stop(); clearInterval(clock); };
  }, [client]);
  const stale = Boolean(snapshot?.active && now - lastChange > 15000);
  return <main>
    <header><div><p className="eyebrow">DANCE DANCE REVOLUTION</p><h1>Live scoreboard</h1></div>
      <span className={`badge ${stale || error ? 'offline' : ''}`}>
        {error ? 'Connection error' : stale ? 'Desktop publisher disconnected' : snapshot?.active ? 'Live feed' : 'Waiting for players'}
      </span></header>
    {error && <p role="alert" className="error">{error} Last received values are retained.</p>}
    {stale && <p role="status" className="error">No desktop update for 15 seconds. These are last received values, not live scores.</p>}
    <div className="players">
      <Player name="Player 1" state={snapshot?.players.P1} waiting={!snapshot?.active} />
      <Player name="Player 2" state={snapshot?.players.P2} waiting={!snapshot?.active} />
    </div>
    <footer><a href="#register">Player check-in kiosk</a><span>Refresh: 1 second after each completed request. OCR and network latency still apply.</span>
      <span>{published ? 'Connected to DDR Scoreboard on Fabric' : 'No scoreboard state published yet'}</span></footer>
  </main>;
}
