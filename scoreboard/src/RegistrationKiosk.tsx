import { useRef, useState } from 'react';
import type { UserSession } from '../rayfin/data/UserSession';
import type { SpecialSession } from '../rayfin/data/SpecialSession';
import type { Client } from './client';
import { SPECIAL_NAMES, latestGame, newIdentity, registrationStation, saveSpecialRegistration, saveRegistration, type SpecialName } from './registration';
import BoothShell from './BoothShell';

type Registration = { kind: 'player'; data: UserSession } | { kind: 'special'; data: SpecialSession };
type Corner = 'top' | 'bottom';

export default function RegistrationKiosk({ client }: { client: Client }) {
  const draft = useRef<Registration | null>(null);
  const busy = useRef(false);
  const gesture = useRef<{ taps: Corner[]; startedAt: number }>({ taps: [], startedAt: 0 });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<Registration | null>(null);
  const [error, setError] = useState('');
  const [station] = useState(() => registrationStation(window.location.search));
  async function register(specialName?: SpecialName) {
    if (station.error) { setError(station.error); return; }
    if (busy.current) return;
    busy.current = true;
    setSaving(true);
    setError('');
    try {
      if (specialName && draft.current
          && (draft.current.kind !== 'special' || draft.current.data.username !== specialName)) {
        throw new Error('Retry the current registration, or choose Start over before changing players.');
      }
      if (!draft.current) {
        const game = await latestGame(client);
        const common = {
          id: crypto.randomUUID(), gameSessionId: game.id,
          createdAt: new Date(), eventQueued: false,
          ...(station.player ? { player: station.player } : {}),
        };
        draft.current = specialName
          ? { kind: 'special', data: { ...common, username: specialName, registrationKey: `${game.id}:${specialName}:${station.player ?? 'unassigned'}` } }
          : { kind: 'player', data: { ...common, ...newIdentity() } };
      }
      if (draft.current.kind === 'special') {
        const data = await saveSpecialRegistration(client, draft.current.data);
        setSaved({ kind: 'special', data });
      } else {
        const data = await saveRegistration(client, draft.current.data);
        setSaved({ kind: 'player', data });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Registration failed. Ask the organizer for help.');
    } finally {
      busy.current = false;
      setSaving(false);
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
  return <BoothShell>
    {!saved && <>
      <button type="button" className="booth-corner booth-corner-top" aria-label="Top-right corner"
        disabled={saving || Boolean(station.error)} onClick={() => tapCorner('top')} />
      <button type="button" className="booth-corner booth-corner-bottom" aria-label="Bottom-left corner"
        disabled={saving || Boolean(station.error)} onClick={() => tapCorner('bottom')} />
    </>}
    {saved ? <section className="booth-card booth-player-pass" aria-label="Your player identity">
      <p className="booth-card-kicker"><span aria-hidden="true">&#10003;</span> PLAYER PASS / CHECKED IN</p>
      <h2>You are registered!</h2>
      {saved.data.player && <p className="booth-station">Player {saved.data.player === 'P1' ? '1' : '2'} station</p>}
      <div className="booth-credentials">
        <p>Your player name</p><strong className="booth-identity">{saved.data.username}</strong>
        {saved.kind === 'player'
          ? <><p>Your unique PIN</p><strong className="booth-pin">{saved.data.pin}</strong></>
          : <p className="booth-champion-note">Special entry. No PIN needed.</p>}
      </div>
      <div className="booth-photo-prompt">
        <span aria-hidden="true">&#9635;</span>
        <div><strong>Photograph this screen now.</strong>
          <p>{saved.kind === 'player' ? 'Your name and PIN will be used to validate who you are.'
            : 'Your special player name is registered for this game session.'}</p></div>
      </div>
      <button className="booth-primary" onClick={next}>I have my photo - Next player <span aria-hidden="true">&#8594;</span></button>
      <p className="booth-privacy">{saved.kind === 'player'
        ? 'Keep your photo and PIN private. They are your player proof, not a Microsoft account login.'
        : 'This is your booth player identity, not a Microsoft account login.'}</p>
    </section> : <section className="booth-card" aria-labelledby="check-in-title">
      <p className="booth-card-kicker">YOUR NEXT MOVE STARTS HERE</p>
      <span className="booth-card-icon" aria-hidden="true">&#8599;</span>
      <p className="booth-overline">PLAYER CHECK-IN</p>
      <h2 id="check-in-title">Ready to play?</h2>
      {station.player && <p className="booth-station">Player {station.player === 'P1' ? '1' : '2'} station</p>}
      <p>Get a fun Fabric player name and a unique six-digit PIN. No personal details needed.</p>
      <button className="booth-primary" disabled={saving || Boolean(station.error)} onClick={() => { void register(); }}>
        {saving ? 'Registering...' : draft.current ? 'Retry registration' : 'Register me'}
        <span aria-hidden="true">&#8594;</span>
      </button>
      <p className="booth-card-note">One check-in. Your own identity. All the moves.</p>
      {(station.error || error) && <p role="alert" className="error">{station.error || error}</p>}
      {error && <button className="booth-secondary" onClick={next}>Next player / Start over</button>}
    </section>}
  </BoothShell>;
}
