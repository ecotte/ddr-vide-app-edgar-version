import type { ReactNode } from 'react';

export default function BoothShell({ children, variant = 'registration' }: {
  children: ReactNode;
  variant?: 'registration' | 'scoreboard';
}) {
  const scoreboard = variant === 'scoreboard';
  return <main className={`booth${scoreboard ? ' booth-scoreboard' : ''}`}>
    <div className="booth-tracks booth-tracks-blue" aria-hidden="true">
      <i /><i /><i /><i /><i />
    </div>
    <div className="booth-tracks booth-tracks-pink" aria-hidden="true">
      <i /><i /><i /><i /><i />
    </div>
    <div className="booth-content">
      <header className="booth-header">
        <span className="booth-brand">Microsoft <strong>Fabric</strong></span>
        <span className="booth-label"><span aria-hidden="true" /> {scoreboard ? 'DDR LIVE SCOREBOARD' : 'DDR PLAYER CHECK-IN'}</span>
      </header>
      {scoreboard ? children : <div className="booth-layout">
        <section className="booth-intro" aria-label="Welcome to the booth">
          <p className="booth-kicker">FEEL THE BEAT. CATCH THE DATA.</p>
          <h1>PUT YOUR<br />DATA <span>IN<br className="booth-title-break" /> MOTION.</span></h1>
          <p className="booth-subtitle">Dance at the speed of data with<br />Microsoft Fabric Real-Time Intelligence.</p>
          <div className="booth-arrow-tiles" aria-hidden="true">
            <span>&#8598;</span><span>&#8599;</span><span>&#8600;</span>
          </div>
          <ol className="booth-steps" aria-label="How to join">
            <li><span>01</span>Get your player name</li>
            <li><span>02</span>Snap your player pass</li>
            <li><span>03</span>Step up. Make your move.</li>
          </ol>
        </section>
        <div className="booth-panel">{children}</div>
      </div>}
      <footer className="booth-footer">
        <span>YOUR MOVES. YOUR DATA. REAL TIME.</span>
      </footer>
    </div>
  </main>;
}
