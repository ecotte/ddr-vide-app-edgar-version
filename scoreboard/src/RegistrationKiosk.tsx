import { useRef, useState } from 'react';
import type { Client } from './client';
import {
  SPECIAL_NAMES, registrationStation, saveRegistration, type SpecialName, type RegistrationRequest, type PlayerPass,
} from './registration';
import BoothShell from './BoothShell';

type Registration = { kind: 'player' | 'special'; data: PlayerPass };
type Corner = 'top' | 'bottom';

export default function RegistrationKiosk({ client }: { client: Client }) {
  const draft = useRef<RegistrationRequest | null>(null);
  const busy = useRef(false);
  const gesture = useRef<{ taps: Corner[]; startedAt: number }>({ taps: [], startedAt: 0 });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<Registration | null>(null);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState('');
  const [station] = useState(() => registrationStation(window.location.search));
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
    setSaved(null);
    setError('');
  }
  function tapCorner(corner: Corner) {
    if (busy.current) return;
    const now = Date.now();
    if (now - gesture.current.startedAt > 8000) gesture.current.taps = [];
    if (!gesture.current.taps.length) gesture.current.startedAt = now;
    gesture.current.taps = [...gesture.current.taps, corner].slice(-4);
    const sequence = gesture.current.taps.join(',');
    const name = sequence === 'top,top,bottom,bottom' ? SPECIAL_NAMES[0]
      : sequence === 'bottom,bottom,top,top' ? SPECIAL_NAMES[1] : undefined;
    if (name) {
      gesture.current = { taps: [], startedAt: 0 };
      void register(name);
    }
  }
  return <BoothShell playerPass={Boolean(saved)}>
    {!saved && <>
      <button type="button" className="booth-corner booth-corner-top" aria-label="Top-right corner"
        disabled={saving || Boolean(station.error)} onClick={() => tapCorner('top')} />
      <button type="button" className="booth-corner booth-corner-bottom" aria-label="Bottom-left corner"
        disabled={saving || Boolean(station.error)} onClick={() => tapCorner('bottom')} />
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
      <button className="booth-primary" disabled={saving || Boolean(station.error)} onClick={() => { void register(); }}>
        {saving ? 'Registering...' : draft.current ? 'Retry registration' : 'Register me'}
        <span aria-hidden="true">&#8594;</span>
      </button>
      {saving && <p role="status" className="booth-card-note">{progress}</p>}
      {(station.error || error) && <p role="alert" className="error">{station.error || error}</p>}
      {error && <button className="booth-secondary" onClick={next}>Next player / Start over</button>}
    </section>}
  </BoothShell>;
}
