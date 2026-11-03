import type { ReactNode } from 'react';
import { registrationStation } from './registration';
import fabricLogo from './assets/fabric-logo-white.png';

export default function BoothShell({ children, variant = 'registration', playerPass = false }: {
  children: ReactNode;
  variant?: 'registration' | 'scoreboard';
  playerPass?: boolean;
}) {
  const scoreboard = variant === 'scoreboard';
  const station = scoreboard ? undefined : registrationStation(window.location.search).player;
  return <main className={`booth ${scoreboard ? 'booth-scoreboard' : 'booth-registration'}${playerPass ? ' booth-player-pass-screen' : ''}`}>
    <div className="booth-tracks booth-tracks-blue" aria-hidden="true">
      <i /><i /><i /><i /><i />
    </div>
    <div className="booth-tracks booth-tracks-pink" aria-hidden="true">
      <i /><i /><i /><i /><i />
    </div>
    <div className="booth-content">
      <header className="booth-header">
        <img className="booth-brand" src={fabricLogo} alt="Microsoft Fabric" width="2296" height="768" />
        {station
          ? <span className="booth-station">Station {station === 'P1' ? '1' : '2'}</span>
          : <span className="booth-label"><span aria-hidden="true" /> {scoreboard ? 'DDR LIVE SCOREBOARD' : 'DDR PLAYER CHECK-IN'}</span>}
      </header>
      {scoreboard ? children : <div className="booth-layout">
        {!playerPass && <section className="booth-intro" aria-label="Welcome to the booth">
          <h1>PUT YOUR DATA <span>IN MOTION.</span></h1>
        </section>}
        <div className="booth-panel">{children}</div>
      </div>}
      {!playerPass && <footer className="booth-footer">
        <span>YOUR MOVES. YOUR DATA. REAL TIME.</span>
      </footer>}
    </div>
  </main>;
}
