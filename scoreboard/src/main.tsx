import { createRoot } from 'react-dom/client';
import App from './App';
import { createClient } from './client';
import './main.css';

const root = createRoot(document.getElementById('root')!);
createClient().then(client => root.render(<App client={client} />)).catch(error => {
  root.render(<main><h1>DDR Scoreboard</h1><p role="alert">
    {error instanceof Error ? error.message : 'Unable to initialize the scoreboard.'}
  </p></main>);
});
