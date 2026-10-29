import { useRef, useState } from 'react';
import type { UserSession } from '../rayfin/data/UserSession';
import type { Client } from './client';
import { latestGame, newIdentity, saveRegistration } from './registration';

export default function Registration({ client }: { client: Client }) {
  const draft = useRef<UserSession | null>(null);
  const busy = useRef(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<UserSession | null>(null);
  const [error, setError] = useState('');
  async function register() {
    if (busy.current) return;
    busy.current = true;
    setSaving(true);
    setError('');
    try {
      if (!draft.current) {
        const game = await latestGame(client);
        draft.current = {
          id: crypto.randomUUID(), gameSessionId: game.id, ...newIdentity(),
          createdAt: new Date(), eventQueued: false,
        };
      }
      const result = await saveRegistration(client, draft.current);
      setSaved(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Registration failed. Ask the organizer for help.');
    } finally {
      busy.current = false;
      setSaving(false);
    }
  }
  function next() {
    draft.current = null;
    setSaved(null);
    setError('');
  }
  return <main className="registration">
    <p className="eyebrow">DANCE DANCE REVOLUTION</p>
    <h1>Player check-in</h1>
    {saved ? <section className="registration-card" aria-label="Your player identity">
      <h2>You are registered!</h2>
      <p>Your player name</p><strong className="identity">{saved.username}</strong>
      <p>Your unique PIN</p><strong className="pin">{saved.pin}</strong>
      <p className="photo-prompt">Photograph this screen now. Your name and PIN will be used to validate who you are.</p>
      <p>Keep your photo and PIN private. They are your player proof, not a Microsoft account login.</p>
      <p className="game-id">Game session: {saved.gameSessionId}</p>
      <p>Saved in the registration database. Eventhouse delivery runs separately through the DDR desktop.</p>
      <button onClick={next}>I have my photo - Next player</button>
    </section> : <section className="registration-card">
      <h2>Ready to play?</h2>
      <p>Get a fun Fabric player name and a unique six-digit PIN. No personal details needed.</p>
      <button disabled={saving} onClick={() => { void register(); }}>
        {saving ? 'Registering...' : draft.current ? 'Retry registration' : 'Register me'}
      </button>
      {error && <p role="alert" className="error">{error}</p>}
      {error && <button onClick={next}>Next player / Start over</button>}
    </section>}
    <footer><a href="#scoreboard">View scoreboard</a></footer>
  </main>;
}
