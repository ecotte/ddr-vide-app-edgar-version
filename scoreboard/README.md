# DDR Scoreboard (Rayfin)

## Player registration kiosk

Open the hosted app with `#register` appended to its URL, or choose **Player
check-in kiosk** in the scoreboard footer. The organizer signs in on this shared
device using the authorized publisher account (`yaschust@microsoft.com`).
Players take turns; they do not sign in with their own Microsoft accounts.
Microsoft sessions can expire; the organizer must sign in again when prompted.
Keep this organizer-authenticated device supervised.
The organizer sign-in, player check-in, photo-ready player pass, and live
scoreboard share the booth's dark indigo, neon-track, and "Put your data in
motion" visual theme. The scoreboard keeps distinct cyan and pink player panels,
all existing metrics, and visible connection/staleness warnings.
Player-facing screens omit database, event-delivery, and internal session details.
Registration and the scoreboard are designed for separate screens, with no
navigation links between them. Open the kiosk at `#register` and the scoreboard
at the app's main URL or `#scoreboard`.

For station-specific kiosks, open `?player=1#register` or `?player=2#register`.
The kiosk displays its station and saves `P1` or `P2` on normal and special
registrations and the `user.register` event's `player` field. Omitting the flag
allows an unassigned registration; invalid or repeated player flags show an
error and block registration. Changing a kiosk station requires opening its
corresponding URL.
Static page assets are public so the kiosk can load without Fabric's separate
hosting sign-in gate. The app still requires Microsoft sign-in before querying
data; neither registration/PIN access nor scoreboard data is anonymous.

1. Enable **Share live scoreboard** in the DDR desktop and **Connect to Fabric**
   using the existing Eventstream destination.
2. Select **Register me** on the kiosk. The app reads the newest SQL
   `GameSession`, requires an active heartbeat within 15 seconds, and saves a
   `UserSession` linked by `gameSessionId`.
3. The generated name concatenates one of 40 positive playful adjectives, one
   of 40 Fabric-related nouns (items, features, and data concepts), and four
   digits, capitalizing both words, for example `SillyEventhouse1234`.
   Existing registered names are unchanged. The independent PIN has
   six digits. SQL unique constraints reserve both names and PINs across all
   registrations; confirmed collisions generate a fresh pair.
4. The player photographs the saved name/PIN screen and keeps it private for
   later identity validation. Choose **I have my photo - Next player** to clear
   the screen before handing over the device.

`GameSession.startedAt` records when this publisher first sees a game GUID, not
the Eventhouse ingestion timestamp. The publisher preserves that timestamp on
subsequent heartbeats. This path uses Rayfin SQL rather than querying Eventhouse;
it needs no new Eventhouse connector or anonymous data access.

The desktop retrieves up to 25 pending registrations per publish cycle and
durably enqueues a `user.register` event in the existing Fabric outbox. Only
after a successful local queue write does a subsequent request mark the SQL row
`eventQueued`. That flag means **queued locally**, not ingested by Eventhouse.
If Fabric is unconfigured or the disk write fails, SQL remains pending and the
desktop shows an error; sharing retries the relay. Offline sends use the existing
durable retry behavior. Leave both publishing controls enabled to deliver events.
This is at-least-once delivery: consumers must deduplicate by `event_id`.

The event uses the user-session UUID as `event_id`, the game GUID as `session_id`,
`event_type: "user.register"`, `source_mode: "registration"`, `sequence: 0`,
`player: "P1"` / `"P2"` (or null when unassigned), and null frame/video-time fields.
Payload contains `user_session_id`,
`game_session_id`, and `username`. Sequence is not a gameplay sequence; use
event ID for identity. **PINs stay in organizer-only SQL and the photo screen**;
they are not copied into telemetry. A PIN-checking/claim-redemption flow is not
included. The PIN is a workshop claim code, not strong account authentication.

Both new entities restrict read/create/update to the authorized publisher account;
ordinary scoreboard viewers cannot read registrations or PINs. Apply the new
schema and frontend together with `npx rayfin up` before using the kiosk, and
rebuild the desktop worker with `npm run build:publisher`.

### Booth easter eggs

On the signed-in player check-in screen, tap the actual screen corners within
eight seconds, in order:

- Top-right twice, then bottom-left twice: **YaelTheChampion**.
- Bottom-left twice, then top-right twice: **EdgarIsGoingDown**.

The fourth tap immediately registers the special name with **no PIN**. These
are easter eggs, not Microsoft sign-in or authorization bypasses. Each special
name can be registered once per game and station and reused in later games;
repeating its gesture within the same game and station returns the existing entry.
Normal player names and PINs retain their unique constraints.

No-PIN entries use organizer-only `SpecialSession` rows, with a unique
`registrationKey` combining game GUID, special name, and station. The desktop relays both
registration tables through the same durable outbox as `user.register`, with
`payload.registration_type` set to `player` or `special`. Both tables' pending
rows share the 25-per-cycle relay limit. Restart the DDR desktop after updating
its Python code and rebuilding its SDK worker to enable this relay.

## Workshop use: one desktop control

1. Launch the updated DDR desktop app.
2. In **Events**, enable **Share live scoreboard**. Complete Microsoft sign-in if prompted.
3. Leave **Record events** enabled and begin gameplay.
4. View the [hosted scoreboard](https://great-grass-4d2de2f7b4-westcentralus.webapp.msit.fabricapps.net).

No local website, Vite server, snapshot file, or broadcaster tab is required.
The desktop automatically starts and stops a private Node SDK worker. The worker
uses Rayfin's supported cached sign-in and direct Entra-token exchange; tokens
stay in the SDK's protected login cache or process memory, not command arguments
or scoreboard messages. The viewer remains Microsoft-sign-in-only because this
tenant blocks anonymous data access. Viewers need Fabric app permissions.

## Data flow

```text
DDR desktop calculations -> managed SDK worker -> Rayfin SQL -> viewer
DDR desktop events -> durable Eventstream publisher -> Eventhouse (unchanged)
```

Only one latest-state snapshot is sent at a time through private process pipes.
The Python worker reads the newest state after each completed request and waits
one second between attempts; failed requests do not create a replay backlog.
The hosted viewer independently waits one second after each completed query.
OCR and network time still add latency; this is not a sub-second guarantee.

The `LiveBoard` SQL table contains a single current-state row. It does not replace
the durable event history in Eventhouse. Publishing is off at app startup.
Disabling sharing sends an inactive, cleared state when the connection permits.
An interrupted connection or abrupt shutdown can leave old values in the cloud;
the viewer marks them disconnected after an unchanged heartbeat for 15 seconds.
Connection/publish errors appear in the desktop Events tab.

## Fixed event rules

- Only explicit session resets change the active session. Events cannot switch it.
- Event IDs are deduplicated for the entire active sharing session.
- Numeric fields use sequence order, not arrival time or maximum numeric value.
  Newer accepted downward corrections remain valid.
- Older score observations cannot replace newer accepted score information.
- Up-Right, Up-Left, Down-Right, Down-Left and Center hit counts are independent
  for each player. Both the desktop publisher and viewer must be updated;
  restart the publisher and start a new session to publish the five-direction
  state. Old four-direction snapshots are rejected explicitly, not remapped.
- Arrow balance is each direction's percentage of directional hits, not accuracy.
- Hit pace counts detected hits in `(current video time - 5000 ms, current video time] / 5`.
  Delivery time never produces a rate spike. Playback ages the window, pause holds
  it, and seeks clear it. New Session resets both recent pace and totals.
- Misses, synthetic samples, unknown-player events and unconfirmed observations
  never increase hit counts or pace. Miss events still go to Fabric history.
- The SDK worker rejects revisions older than its last acknowledged revision.

There is no distributed multiple-publisher mechanism: this app uses one
desktop-managed publisher. Stopping/restarting sharing cannot start a second
worker while the previous worker is still stopping.

## Setup / development

Requires Node.js 20.12+ and the existing Rayfin deployment configuration in
`rayfin/.env` (generated by the CLI and ignored by Git).

```powershell
npm install
npm run build:publisher
```

Rayfin sign-in can be prepared using `npx rayfin login`; normally enabling sharing
acquires the existing session or opens sign-in automatically. Do not enable
plaintext credential fallback.

Vite remains a **development/build tool only**, not part of the workshop data path.
For frontend development, use `npm run dev`. It cannot publish gameplay data.

```powershell
npm test
npm run lint
npm run build:fabric
npx rayfin up --workspace-id 83823ba5-af36-4336-a920-1454da98aa9d --item-name "DDR Scoreboard"
npx rayfin up status
```

The hosted data policy permits authenticated reads and restricts create/update
to `yaschust@microsoft.com`. No footage, reference images, or credentials are sent
in snapshots. The deployment uses the existing workshop Fabric capacity.

The scaffold's critical test-runner advisories were addressed by updating Vitest.
`npm audit` still reports moderate dependency-chain entries in Rayfin SQL tooling
and `sprintf-js` without a compatible upstream patch at setup time. Do not apply
`npm audit fix --force`, which proposes incompatible Rayfin downgrades.
