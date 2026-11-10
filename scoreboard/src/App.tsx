import { useEffect, useMemo, useState } from 'react';
import { initializeOperator, signIn, type Client } from './client';
import { BOARD_ID, DIRECTIONS, arrowAccuracy, parseSnapshot, poll, type BoardSnapshot, type PlayerState } from './state';
import RegistrationKiosk from './RegistrationKiosk';
import BoothShell from './BoothShell';
import { animalComparison, cumulativeHitPercentage, mostHitLanes, stepTitle, totalDetectedSteps } from './dashboardMetrics';
import { CAPTION_COUNT, CAPTION_INTERVAL_MS, sessionCaptionDecks } from './dashboardCaptions';

function message(error: unknown) {
  return error instanceof Error ? error.message : 'The scoreboard request failed.';
}

function Player({ name, state, waiting, sessionId, live }: {
  name: string; state?: PlayerState; waiting: boolean; sessionId: string; live: boolean;
}) {
  const [captionStep, setCaptionStep] = useState(0);
  const captions = useMemo(() => sessionCaptionDecks(sessionId), [sessionId]);
  const captionIndex = (captionStep + (name === 'Player 2' ? CAPTION_COUNT / 2 : 0)) % CAPTION_COUNT;
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => {
      setCaptionStep(step => (step + 1) % CAPTION_COUNT);
    }, CAPTION_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [live]);
  const accuracy = waiting ? null : arrowAccuracy(state);
  const player = waiting ? undefined : state;
  const percentage = player ? cumulativeHitPercentage(player) : null;
  const title = player ? stepTitle(player) : null;
  const favorite = player ? mostHitLanes(player) : null;
  const steps = player ? totalDetectedSteps(player) : null;
  const response = player?.averageResponseTimeMs;
  const comparison = response == null ? null : animalComparison(response);
  const directions = player?.hitsByDirection;
  const score = player?.runningScore ?? player?.score;
  const hasRunningScore = player?.runningScore != null;
  return <section className={`player ${name === 'Player 2' ? 'pink' : ''}`}>
    <div className="player-heading">
    <h2><span className="player-identity">{name}
      {player?.username && <span className="player-name">{player.username}</span>}
    </span><span className="player-corner" aria-hidden="true">
      <svg viewBox="0 0 32 32" width="32" height="32" fill="none" stroke="currentColor"
        strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="18" cy="5" r="2.5" fill="currentColor" stroke="none" />
        <path d="m17 11-4 9 7 3 4 6M13 20l-5 8M17 11l-7 3-5-4M17 11l6 3 4-6" />
      </svg>
    </span></h2>
    {!player && <p className="observation">Waiting for players</p>}
    </div>
    <div className="player-score">
      <div className="score-label">{score == null || hasRunningScore ? 'RUNNING SCORE' : 'ACCEPTED SCORE'}</div>
      <div className="score" aria-label={`${name} score`}>{score == null
        ? <span className="waiting-data">Waiting for data</span> : score.toLocaleString()}</div>
    </div>
    {player?.combo != null && <div className="combo"><span>COMBO</span><strong>{player.combo}</strong></div>}
    <div className="total-hits"><span>TOTAL HITS</span><strong>{player
      ? player.hits.toLocaleString() : <span className="waiting-data">Waiting for data</span>}</strong></div>
    <div className="counts" aria-label={`${name} hits by arrow`}>
      {DIRECTIONS.map(direction => <div key={direction}>
        <span className="arrow-icon" aria-hidden="true">{{
          'Up-Right': '\u2197', 'Up-Left': '\u2196', 'Down-Right': '\u2198', 'Down-Left': '\u2199',
          Center: '\u25cf',
        }[direction]}</span>
        <span>{direction.toUpperCase()}</span>
        <strong aria-label={`${direction} hits`}>
          {directions ? directions[direction].toLocaleString() : <span className="waiting-data">Waiting for data</span>}
        </strong>
      </div>)}
    </div>
    <div className="player-metrics">
      <section className="metric-card metric-most-hit" aria-label={`${name} most-hit lane`}>
        <h3>MOST-HIT LANE</h3>
        <strong className="metric-title">{favorite?.directions.join(' + ') ?? 'Waiting for data'}</strong>
        {favorite && <p>{favorite.hits.toLocaleString()} {favorite.hits === 1 ? 'hit' : 'hits'}
          {favorite.directions.length > 1 ? ' each. ' : '. '}
          <span className="metric-caption">{captions.mostHitLane[captionIndex]}</span></p>}
      </section>
      <section className="metric-card metric-steps" aria-label={`${name} total detected steps`}>
        <h3>TOTAL DETECTED STEPS</h3>
        <strong className={steps == null ? 'metric-title' : 'metric-value'}>{steps?.toLocaleString() ?? 'Waiting for data'}</strong>
        {steps != null && <p className={steps > 0 ? 'metric-caption' : undefined}>
          {steps > 0 ? captions.detectedSteps[captionIndex] : 'Waiting for data'}
        </p>}
      </section>
      <section className="metric-card metric-percentage" aria-label={`${name} cumulative hit percentage`}>
        <h3>CUMULATIVE HIT PERCENTAGE</h3>
        <strong className={percentage == null ? 'metric-title' : 'metric-value'}>
          {percentage == null ? 'Waiting for data' : `${percentage.toFixed(1)}%`}
        </strong>
        {player && steps != null && percentage != null
          && <p>{player.hits.toLocaleString()} hits / {steps.toLocaleString()} detected outcomes.</p>}
      </section>
      {response != null && <section className="metric-card metric-response" aria-label={`${name} time to answer`}>
        <h3>TIME TO ANSWER</h3>
        <strong className="metric-value">{response.toFixed(0)} <span>ms</span></strong>
        <p>Average across {player?.responseSamples?.toLocaleString()} timed hits this game.</p>
        <p>Detected arrow appearance to matching lane hit, using video time.</p>
      </section>}
      {comparison && <section className="metric-card metric-comparison" aria-label={`${name} animal comparison`}>
        <h3>ANIMAL REACTION COMPARISON</h3>
        <strong className="metric-title">{comparison.label}</strong>
        <p>{comparison.benchmark.milliseconds} ms reference; {comparison.differenceMs === 0
          ? 'your average matches it.'
          : `your average is ${comparison.differenceMs.toFixed(1)} ms ${comparison.faster ? 'below' : 'above'} it.`}</p>
        <p>{comparison.benchmark.condition}</p>
        <p><a href={comparison.benchmark.url} target="_blank" rel="noreferrer">{comparison.benchmark.citation}</a></p>
        <p>Different tasks and senses, not a like-for-like test or an animal speed ranking.</p>
      </section>}
      <section className="metric-card metric-step-title" aria-label={`${name} step title`}>
        <h3>STEP TITLE</h3>
        {title && <span className="step-title-symbol" aria-hidden="true">{title.symbol}</span>}
        <strong className="metric-title">{title?.title ?? 'Waiting for data'}</strong>
        {title && <p>{title.detail}</p>}
      </section>
    </div>
    <section className="hit-pace" aria-label={`${name} hit pace`}>
      <h3>HIT PACE</h3>
      {player?.hitPace == null ? <p>Waiting for data</p> : <>
        <div><strong>{player.hitPace.toFixed(1)}</strong>
          <span> detected hits / sec</span></div>
        <p>Last 5 seconds of video; holds while paused.</p>
      </>}
    </section>
    <section className="arrow-accuracy" aria-label={`${name} arrow accuracy`}>
      <h3>ARROW ACCURACY</h3>
      {DIRECTIONS.map(direction => <div className="accuracy-row" key={direction}>
        <span>{direction}</span>
        <div className="accuracy-track" {...(accuracy?.[direction] != null ? {
          role: 'meter', 'aria-label': `${name} ${direction} accuracy`,
          'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': accuracy[direction],
          'aria-valuetext': `${accuracy[direction].toFixed(1)}% accuracy`,
        } : { 'aria-hidden': true })}>
          <div style={{ width: `${accuracy?.[direction] ?? 0}%` }} />
        </div>
        <strong>{accuracy?.[direction] != null ? `${accuracy[direction].toFixed(1)}%`
          : player?.hitsByDirection && player.missesByDirection ? 'No attempts' : 'Waiting'}</strong>
      </div>)}
    </section>
    <div className="player-notes">
    {!waiting && !hasRunningScore && state?.observedScore != null && state.observedScore !== state.score
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
  if (!ready) return <BoothShell variant={registration ? 'registration' : 'scoreboard'}>
    <section className="booth-card booth-auth-panel">
      <h2>{registration ? 'Player check-in' : 'DDR Scoreboard'}</h2>
      <p role="status">Checking Microsoft sign-in...</p>
    </section>
  </BoothShell>;
  if (!authenticated) {
    const content = <>
    {!registration && <><p className="booth-card-kicker">EVERY MOVE TELLS A STORY</p>
      <span className="booth-card-icon" aria-hidden="true">&#8599;</span></>}
      <p className="booth-overline">{registration ? 'ORGANIZER SETUP' : 'LIVE SCOREBOARD'}</p>
    <h2>{registration ? 'Open the dance floor.' : 'DDR Scoreboard'}</h2>
    <p>{registration ? 'Organizer: sign in once to open this station. Players do not need Microsoft accounts.'
      : 'Sign in with an authorized Microsoft account to view the live scores.'}</p>
    <button className="booth-primary" onClick={() => {
      setError('');
      void signIn(client).then(session => setAuthenticated(session.isAuthenticated))
        .catch(e => setError(message(e)));
    }}>Sign in with Microsoft</button>
    {error && <p role="alert" className="error">{error}</p>}
    </>;
    return <BoothShell variant={registration ? 'registration' : 'scoreboard'}>
      <section className="booth-card booth-auth-panel">{content}</section>
    </BoothShell>;
  }
  return registration ? <RegistrationKiosk client={client} /> : <Scoreboard client={client} />;
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
  const live = Boolean(snapshot?.active && !stale && !error);
  return <BoothShell variant="scoreboard">
    <header className="booth-scoreboard-heading"><div>
      <p className="booth-kicker">PUT YOUR DATA IN MOTION</p>
      <h1>Live <span>scoreboard</span></h1>
      <p className="booth-subtitle">Feel the beat. See every move in real time.</p>
    </div>
      <span className={`badge ${stale || error ? 'offline' : ''}`}>
        {error ? 'Connection error' : stale ? 'Desktop publisher disconnected' : snapshot?.active ? 'Live feed' : 'Waiting for players'}
      </span></header>
    {error && <p role="alert" className="error">{error} Last received values are retained.</p>}
    {stale && <p role="status" className="error">No desktop update for 15 seconds. These are last received values, not live scores.</p>}
    <div className="players">
      <Player key={`${snapshot?.sessionId}:P1`} name="Player 1" state={snapshot?.players.P1}
        waiting={!snapshot?.active} sessionId={snapshot?.sessionId ?? ''} live={live} />
      <Player key={`${snapshot?.sessionId}:P2`} name="Player 2" state={snapshot?.players.P2}
        waiting={!snapshot?.active} sessionId={snapshot?.sessionId ?? ''} live={live} />
    </div>
    <div className="booth-feed-notes"><span>Refresh: 1 second after each completed request. OCR and network latency still apply.</span>
      <span>{published ? 'Connected to DDR Scoreboard on Fabric' : 'No scoreboard state published yet'}</span></div>
  </BoothShell>;
}
