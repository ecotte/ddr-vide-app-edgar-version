import { useRef, useState } from 'react';
import type { Client } from './client';
import {
  SPECIAL_NAMES, DIFFICULTIES, difficultyLabel, registrationStation, saveRegistration,
  type Difficulty, type SpecialName, type RegistrationRequest, type PlayerPass,
} from './registration';
import BoothShell from './BoothShell';

type Registration = { kind: 'player' | 'special'; data: PlayerPass };
type Corner = 'top-left' | 'top-right' | 'bottom-right' | 'bottom-left';

const DIFFICULTY_DESCRIPTIONS: Record<Difficulty, string> = {
  0: 'Easy users are not ranked',
  3: 'Standard score',
  4: 'Score multiplied by 1.25',
};

export default function RegistrationKiosk({ client }: { client: Client }) {
  const draft = useRef<RegistrationRequest | null>(null);
  const busy = useRef(false);
  const gesture = useRef<{ taps: Corner[]; startedAt: number }>({ taps: [], startedAt: 0 });
  const [saving, setSaving] = useState(false);
  const [chickenDancing, setChickenDancing] = useState(false);
  const [saved, setSaved] = useState<Registration | null>(null);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState('');
  const [station] = useState(() => registrationStation(window.location.search));
  const [difficulty, setDifficulty] = useState<Difficulty>(3);
  async function register(specialName?: SpecialName) {
    if (station.error || !station.player) { setError(station.error); return; }
    if (busy.current) return;
    busy.current = true;
    setSaving(true);
    setProgress('Saving your player pass. Please keep this page open.');
    setError('');
    try {
      if (specialName && draft.current
          && draft.current.specialName !== specialName) {
        throw new Error('Retry the current registration, or choose Start over before changing players.');
      }
      if (!draft.current) {
        draft.current = {
          requestId: crypto.randomUUID(), player: station.player, specialName: specialName ?? '',
          difficulty,
        };
      }
      const data = await saveRegistration(client, draft.current);
      setSaved({ kind: data.registrationType, data });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Registration failed. Ask the organizer for help.');
    } finally {
      busy.current = false;
      setSaving(false);
      setProgress('');
    }
  }
  function next() {
    draft.current = null;
    gesture.current = { taps: [], startedAt: 0 };
    setChickenDancing(false);
    setSaved(null);
    setError('');
    setDifficulty(3);
  }
  function tapCorner(corner: Corner) {
    if (busy.current) return;
    const now = Date.now();
    if (now - gesture.current.startedAt > 8000) gesture.current.taps = [];
    if (!gesture.current.taps.length) gesture.current.startedAt = now;
    gesture.current.taps = [...gesture.current.taps, corner].slice(-4);
    if (gesture.current.taps.slice(-3).join(',') === 'top-left,top-left,top-left') {
      gesture.current = { taps: [], startedAt: 0 };
      setChickenDancing(true);
      return;
    }
    const sequence = gesture.current.taps.join(',');
    const name = sequence === 'top-right,top-right,bottom-left,bottom-left' ? SPECIAL_NAMES[0]
      : sequence === 'bottom-left,bottom-left,top-right,top-right' ? SPECIAL_NAMES[1]
      : sequence === 'top-left,top-right,bottom-right,bottom-left' ? SPECIAL_NAMES[2] : undefined;
    if (name) {
      gesture.current = { taps: [], startedAt: 0 };
      void register(name);
    }
  }
  return <BoothShell playerPass={Boolean(saved)}>
    {chickenDancing && <div className="booth-funky-chicken" role="img"
      aria-label="A chicken doing the funky chicken dance"
      onAnimationEnd={event => {
        if (event.target === event.currentTarget) setChickenDancing(false);
      }}>
      <svg className="booth-chicken-dancer" viewBox="0 0 160 180" aria-hidden="true" focusable="false">
        <g fill="none" stroke="#f6a735" strokeWidth="8" strokeLinecap="round" strokeLinejoin="round">
          <path className="booth-chicken-left-leg" d="M66 126 58 154 42 160 M58 154 67 162" />
          <path className="booth-chicken-right-leg" d="M96 126 104 154 120 160 M104 154 95 162" />
        </g>
        <ellipse cx="80" cy="104" rx="43" ry="38" fill="#fff3cd" stroke="#633c35" strokeWidth="3" />
        <path className="booth-chicken-left-wing" d="M55 92 Q8 62 23 104 Q32 128 57 112"
          fill="#f8d77e" stroke="#633c35" strokeWidth="3" />
        <path className="booth-chicken-right-wing" d="M103 92 Q150 62 137 104 Q128 128 103 112"
          fill="#f8d77e" stroke="#633c35" strokeWidth="3" />
        <path d="M66 40 Q48 13 65 16 Q71 1 81 20 Q101 6 96 32 L90 44" fill="#ef5963" stroke="#633c35" strokeWidth="3" />
        <circle cx="80" cy="58" r="29" fill="#fff3cd" stroke="#633c35" strokeWidth="3" />
        <path d="M74 74 Q65 94 79 90 Q93 94 86 74" fill="#ef5963" stroke="#633c35" strokeWidth="2" />
        <g className="booth-chicken-eye">
          <ellipse cx="68" cy="52" rx="8" ry="10" fill="#fff" stroke="#633c35" strokeWidth="2" />
          <circle cx="70" cy="53" r="4" fill="#302338" />
          <circle cx="71" cy="51" r="1.5" fill="#fff" />
        </g>
        <g className="booth-chicken-eye">
          <ellipse cx="92" cy="52" rx="8" ry="10" fill="#fff" stroke="#633c35" strokeWidth="2" />
          <circle cx="90" cy="53" r="4" fill="#302338" />
          <circle cx="91" cy="51" r="1.5" fill="#fff" />
        </g>
        <path d="m69 66 11-5 11 5-11 13Z" fill="#f6a735" stroke="#633c35" strokeWidth="2" strokeLinejoin="round" />
        <path d="M70 66h20" fill="none" stroke="#633c35" strokeWidth="2" />
      </svg>
    </div>}
    {!saved && <>
      <button type="button" className="booth-corner booth-corner-top-left" aria-label="Top-left corner"
        disabled={saving || Boolean(station.error)} onClick={() => tapCorner('top-left')} />
      <button type="button" className="booth-corner booth-corner-top-right" aria-label="Top-right corner"
        disabled={saving || Boolean(station.error)} onClick={() => tapCorner('top-right')} />
      <button type="button" className="booth-corner booth-corner-bottom-right" aria-label="Bottom-right corner"
        disabled={saving || Boolean(station.error)} onClick={() => tapCorner('bottom-right')} />
      <button type="button" className="booth-corner booth-corner-bottom-left" aria-label="Bottom-left corner"
        disabled={saving || Boolean(station.error)} onClick={() => tapCorner('bottom-left')} />
    </>}
    {saved ? <section className="booth-card booth-player-pass" aria-label="Your player identity">
      <p className="booth-card-kicker"><span aria-hidden="true">&#10003;</span> PLAYER PASS / CHECKED IN</p>
      <h2>You are registered!</h2>
      <div className="booth-credentials">
        <p>Your player name</p><strong className="booth-identity">{saved.data.username}</strong>
        {saved.kind === 'player'
          ? <><p>Your unique PIN</p><strong className="booth-pin">{saved.data.pin}</strong></>
          : <p className="booth-champion-note">Special entry. No PIN needed.</p>}
      </div>
      <p className="booth-game-session">Game session ID: <code>{saved.data.gameSessionId}</code></p>
      <p className="booth-card-note">Difficulty: {difficultyLabel(saved.data.difficulty)}</p>
      {saved.data.difficulty === 4 && <p className="booth-card-note">
        Your full game score receives a &#215;1.25 multiplier, rounded to the nearest whole point.
      </p>}
      {saved.data.excludeFromLeaderboard && <p className="booth-card-note">
        Easy mode: shown on the live scoreboard, excluded from leaderboard rankings.
      </p>}
      {!saved.data.excludeFromLeaderboard && <p className="booth-card-note">
        This player is eligible for leaderboard rankings.
      </p>}
      <div className="booth-photo-prompt">
        <svg className="booth-camera-icon" width="32" height="32" viewBox="0 0 24 24"
          fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" aria-hidden="true" focusable="false">
          <path d="M8 6 10 3h4l2 3h4a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2Z" />
          <circle cx="12" cy="13" r="4" />
        </svg>
        <div><strong>Photograph this screen now.</strong>
          <p>{saved.kind === 'player' ? 'Your name and PIN will be used to validate who you are.'
            : 'Your special player name is registered for this game session.'}</p></div>
      </div>
      <button className="booth-primary" onClick={next}>I have my photo - Next player <span aria-hidden="true">&#8594;</span></button>
      <p className="booth-privacy">{saved.kind === 'player'
        ? 'Keep your photo and PIN private.'
        : 'Your special player pass is ready.'}</p>
    </section> : <section className="booth-card" aria-labelledby="check-in-title">
      <p className="booth-overline">PLAYER CHECK-IN</p>
      <h2 id="check-in-title">Ready to play?</h2>
      <p>Get your Fabric player name and PIN, then snap your pass.</p>
      <fieldset className="booth-difficulty" disabled={saving || Boolean(station.error) || Boolean(draft.current)}>
        <legend>Choose your difficulty</legend>
        <div className="booth-difficulty-options">
          {DIFFICULTIES.map(value => <label key={value}>
            <input type="radio" name="difficulty" value={value} checked={difficulty === value}
              aria-labelledby={`difficulty-${value}-label`}
              aria-describedby={`difficulty-${value}-description`}
              onChange={() => setDifficulty(value)} />
            <span>
              <strong id={`difficulty-${value}-label`}>{difficultyLabel(value)}</strong>
              <small id={`difficulty-${value}-description`}>{DIFFICULTY_DESCRIPTIONS[value]}</small>
            </span>
          </label>)}
        </div>
      </fieldset>
      <p className="booth-card-note">Each player chooses independently. Normal 4 rounds the full game total.</p>
      <button className="booth-primary" disabled={saving || Boolean(station.error)} onClick={() => { void register(); }}>
        {saving ? 'Registering...' : draft.current ? 'Retry registration' : 'Register me'}
        <span aria-hidden="true">&#8594;</span>
      </button>
      {draft.current && !saving && <p className="booth-card-note">
        Retrying {difficultyLabel(difficulty)}. Choose Start over to change difficulty.
      </p>}
      {saving && <p role="status" className="booth-card-note">{progress}</p>}
      {(station.error || error) && <p role="alert" className="error">{station.error || error}</p>}
      {error && <button className="booth-secondary" onClick={next}>Next player / Start over</button>}
    </section>}
  </BoothShell>;
}
