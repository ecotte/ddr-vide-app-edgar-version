import { createInterface } from 'node:readline';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ensureAuthenticated } from '@microsoft/rayfin-cli/auth';
import { RayfinClient } from '@microsoft/rayfin-client';
import { signInWithEntraToken } from '@microsoft/rayfin-auth-provider-fabric';
import type { AppSchema } from '../rayfin/data/schema.js';
import { BOARD_ID, parseSnapshot } from '../src/state.js';
import { syncRegistrations } from './registrations.js';

const prefix = '__DDR_SCOREBOARD__';
const report = (status: string, revision?: number, registrations?: unknown) => {
  process.stdout.write(`${prefix}${JSON.stringify({ status, revision, registrations })}\n`);
};

async function main() {
  const root = resolve(fileURLToPath(new URL('.', import.meta.url)), '..', '..');
  process.loadEnvFile(resolve(root, 'rayfin', '.env'));
  const baseUrl = process.env.RAYFIN_PUBLIC_API_URL;
  const publishableKey = process.env.RAYFIN_PUBLIC_PUBLISHABLE_KEY;
  const tenantId = process.env.RAYFIN_PUBLIC_TENANT_ID;
  if (!baseUrl || !publishableKey || !tenantId) throw new Error('Missing deployment configuration');
  const endpoint = new URL(baseUrl);
  if (endpoint.protocol !== 'https:' || !endpoint.hostname.endsWith('.pbidedicated.windows.net')
      || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) {
    throw new Error('Invalid Rayfin endpoint');
  }
  const client = new RayfinClient<AppSchema>({ baseUrl, publishableKey, authStorage: false });
  const input = createInterface({ input: process.stdin, terminal: false });
  let closing = false;
  // Closing the desktop closes stdin, including when a request is still in flight.
  input.on('close', () => {
    client.auth.destroy();
    if (!closing) process.exit(0);
  });
  try {
    const credentials = await ensureAuthenticated(
      ['https://analysis.windows.net/powerbi/api/Item.Execute.All'],
      { tenantId, encryptionFallbackEnabled: false },
    );
    const session = await signInWithEntraToken(client.auth, { entraToken: credentials.token });
    if (!session.isAuthenticated) throw new Error('Sign-in failed');
    report('ready');
    let lastRevision = -1;
    for await (const line of input) {
      try {
        const request = JSON.parse(line);
        const snapshot = parseSnapshot(request);
        if (snapshot.revision < lastRevision) {
          report('invalid');
          continue;
        }
        const body = { snapshot: JSON.stringify(snapshot), publishedAt: new Date() };
        await client.data.LiveBoard.upsert(
          { id: BOARD_ID }, { id: BOARD_ID, ...body }, body,
        );
        const registrations = await syncRegistrations(client, snapshot, request.acknowledgedRegistrations ?? []);
        lastRevision = snapshot.revision;
        report('published', snapshot.revision, registrations);
      } catch {
        // Never forward SDK responses, tokens or stack traces over the status pipe.
        report('failed');
      }
    }
  } finally {
    closing = true;
    input.close();
    client.auth.destroy();
  }
}

main().catch(() => {
  report('auth-failed');
  process.exitCode = 1;
});
