# DDR Video Event Extractor - Recorded video and HDMI capture

See the [architecture diagram and data-flow guide](docs/architecture.md) for the
desktop pipeline, both publishers, Rayfin hosting, Fabric items, authentication,
storage, and latency boundaries. - Milestone 1 (recorded video)

## Fabric dashboard: current-game insights

The second page of **DDR Live** includes separate P1/P2 dance-pad heatmaps,
a judgment showdown, five-second momentum bins, and current/best streaks with
miss markers. The existing tiles remain unchanged. These use the latest
non-test game observed in the last 24 hours, deduplicate `event_id`, and count
only `judgement` outcomes, never raw arrow activity. Session start markers determine
game order when available; games without a marker use their first observed gameplay
event. Older start markers prevent late events from reviving an announced old game.
All retained events for the selected game count, even if it began more than
24 hours ago. Registration and session-end events do not select a game.
A new game clears the visuals.
Heatmaps count each matched direction once and leave unobserved positions as
**No data**; judgments without a direction are excluded from the pad.
Judgment percentages and momentum count each recognized judgment once, including
multi-arrow judgments. BAD counts as a hit but neither extends nor breaks a
streak; other successful judgments extend it once, and Misses reset it. Missing timing
is excluded from momentum, and the final five-second bin can be incomplete.
These are recognized outcomes, not ground-truth accuracy: unrecognized misses
cannot count.

The custom visual source and shared query are in
[`dashboards/current-game.html`](dashboards/current-game.html) and
[`dashboards/current-game.kql`](dashboards/current-game.kql).
The HTML uses the dashboard's existing Visual Builder message protocol without
external chart libraries. Replace `__KIND__` with `heatmap`, `judgments`,
`momentum`, or `streaks`, and `__PLAYER__` with `P1`/`P2` for a pad.
Run `node --test .\dashboards\current-game.test.cjs` after the scoreboard's
existing npm dependencies are installed to test the rendered output.

## Desktop environment selection

The public DDR-dev connection IDs and hosting URLs are in
[`deployments/ddr-dev.json`](deployments/ddr-dev.json). The Rayfin publishable
key in that file is public, not a credential. Eventstream keys, tokens, local
environment files, and the active deployment registry must never be committed.

To select that environment on a desktop:

1. Stop **Share live scoreboard** and disconnect Fabric in the Events tab.
   Drain any pending events to their original destination first.
2. Sign into Azure CLI with access to the profile's destination tenant.
3. Run `.\.venv\Scripts\python.exe -m app.desktop_environment` to validate the
   profile without changing settings.
4. Run `.\.venv\Scripts\python.exe -m app.desktop_environment --apply --publishers-stopped`.
   This retrieves the destination's Eventstream connection directly from Fabric
   and saves it only in Windows Credential Manager. It updates ignored Rayfin
   and Vite environment files and retains the original deployment registry entry.
5. Reconnect Fabric and enable scoreboard sharing. Complete Microsoft sign-in
   for the destination tenant using its authorized organizer, then start a new game.

Use `--profile <path>` for another public profile with the same fields.
The switch refuses a nonempty outbox or a `DDR_EVENTSTREAM_CONNECTION_STRING`
launcher override. It does not restart the desktop, move queued events, copy
cloud data, change organizer permissions, or enable publishing automatically.
If the running desktop was launched with a connection-string override, remove
that override and restart it before reconnecting.
Cloud hosting origins are managed by Rayfin during deployment rather than
hardcoded to the original workspace in `rayfin.yml`.

Operator access follows Fabric app sharing rather than a personal email
allowlist. Any signed-in user with **Run and interact** access can publish
gameplay and operate check-in, including reading registration records.
Share app access only with trusted operators; anonymous access stays blocked.
This model does not provide a separate view-only app role.

## Rayfin live scoreboard

The Fabric sender immediately groups up to 100 already-queued events from the
same session into an SDK request, bounded by the service's message-size limit.
It never waits for a batch timer or for more events to arrive. A lone event sends
immediately; bursts avoid one network acknowledgment per event. Events remain
individual JSON messages in original queue order and are deleted locally only
after acknowledgment. Failed sends retain the whole unacknowledged group for retry.
This improves outbox throughput without changing Eventhouse ingestion policies
or the independent Rayfin scoreboard path.

An optional [Rayfin scoreboard](scoreboard/README.md) displays current P1/P2
scores, combos, and per-arrow hit counts without waiting for Eventhouse ingestion.
It requires Microsoft sign-in (anonymous data access is blocked by this tenant).
Enable **Events > Share live scoreboard**. The desktop manages its Rayfin SDK
connection automatically; there is no local server, snapshot file, or broadcaster
tab to run. Changed scoreboard state wakes the publisher immediately; changes
arriving during a request are sent as soon as it finishes, without a one-second
post-send delay. Idle publishing keeps a one-second heartbeat, and failed
requests retain a one-second retry delay. The viewer starts its next query as
soon as the prior successful query finishes, without overlapping requests.
Query failures retain a one-second retry delay. This increases read traffic;
recognition, correlation, network time and browser throttling still add latency.
Sharing is off at startup, excludes synthetic samples, and does not change
the existing durable Fabric publishing path. - Milestone 1 (recorded video)

The scoreboard's main **Running score** is calculated from recognized judgments:
Bad = 5, Good = 10, Great = 20, Perfect = 40, Miss = 0. Points are awarded per matched arrow
(two simultaneous Perfect arrows = 80); an unknown-direction judgment earns its
points once. Legacy ungraded hits earn no points. No OCR score box is needed.
Each player's total starts at zero and resets with New Session/game-end rotation.
Duplicate event IDs cannot add points twice. Score/observation OCR events do not
change the running score. Enable sharing before play; enabling/restarting sharing
starts a fresh snapshot and does not replay earlier events.
Deploy the updated scoreboard frontend and restart the desktop publisher to use
this display; older publisher snapshots retain their accepted-OCR score display.

The same Rayfin app includes a shared-device **Player check-in kiosk** at
`#register`. An organizer signs in once (and again if the Microsoft session
expires); players generate a Fabric-themed name and unique six-digit PIN without
their own Microsoft accounts. They photograph the saved identity screen.
The player pass stays visible for five seconds, then the station displays
**GAME IN PROGRESS** until a new game session opens that station for check-in.
Refreshing an occupied kiosk keeps it locked without redisplaying credentials.
Registrations link to the current game in Rayfin SQL and are relayed through
the desktop's durable Eventstream outbox as `user.register`, including the six-digit
PIN in `payload.pin` for normal registrations. PINs remain excluded from the public
scoreboard and logs, but are readable by users with access to raw Eventhouse events
and by anyone who can read the local outbox. Restrict those permissions accordingly.
See the [kiosk setup and delivery rules](scoreboard/README.md#player-registration-kiosk).
Station URLs require `?player=1#register` or `?player=2#register`. Each game has
one registration slot per player, including special entries. In the desktop,
**New Session** emits `session_end` for the announced old game before emitting
`new_session` for a fresh GUID and two empty registration slots.
Each station independently selects **Easy**, **Normal 3**, **Normal 4**, or **Hard** before
registration. The sign-in event carries numeric `payload.difficulty` as `0`, `3`,
`4`, or `5`, respectively. Only that player's Easy entry is excluded from leaderboard
rankings; the other player's selection is not inherited or changed.
Normal 4 uses a score multiplier of 1.25 and Hard uses 1.5, rounded once on the full current-game
total (nearest whole point, with halves rounded up). The publisher applies it
from the stored registration on every heartbeat, including points earned before
registration. Easy and Normal 3 use a multiplier of 1.
Deploy the updated schema, Functions and kiosk together, rebuild the publisher,
and restart desktop sharing before creating registrations to relay this new field.

Wide, flat score rows are stretched vertically for OCR when the median padded
digit-box width/height ratio is at least 1.3. This targets skins whose flattened
`1` was read as `7`; ordinary score proportions, saved teaching templates, crop
previews, and confidence thresholds are unchanged. Low-confidence results still
require confirmation and can remain unaccepted. - Milestone 1 (recorded video)

A local Windows desktop application that watches a **recorded video or live
HDMI capture device** for Dance Dance Revolution gameplay, lets you calibrate on-screen regions
(score, combo, and per-lane arrow receptors), reads the score/combo
regions with OCR and the arrow regions by comparing them to snapshots you
capture yourself, and turns *confirmed* changes into structured,
timestamped events you can review in a table and export as JSON Lines.
Video, screenshots, and reference images stay local. Event JSON leaves the
machine only when you explicitly enable one of the publishing options.

Both sources share region calibration, OCR-based score/combo recognition,
reference-based arrow and game-end detection, GUID game sessions, event export,
and optional Fabric/live-scoreboard publishing. Optional per-player judgment
regions match your captured Perfect/Great/Good/Bad/Miss images and correlate them with arrow
activity. General judgment-text OCR remains future work.

---

## 1. What this does and does not do today

**Does:**
- Open a local `.mp4`/`.avi`/`.mov`/`.mkv` file, play/pause/step through it frame by frame, and seek with a slider.
- Backward steps and seeks keep the preview responsive while background OCR is busy. Tracking resets without blocking the UI once recognition locks become available; only the newest requested frame is retained, and old-generation OCR results do not overwrite its recognition cards.
- Select **HDMI input**, choose a named HDMI-to-USB capture device such as **Hagibis**, and analyze its live frames, with optional local MP4 recording.
- Let you draw rectangles on the video frame and label each one with a player (P1/P2/Unknown) and a field kind: `score`, `combo`, or `arrow` (one box per direction - Up-Right/Up-Left/Down-Right/Down-Left/Center - per player), plus a shared `game_end` indicator.
- The **Add region** dialog asks only for player, field kind, and arrow direction when applicable. Region names are generated automatically, for example `P1 Score`, `P2 Center`, or `Game end`; no free-text name is required. Existing saved region labels remain readable.
- Save/load named layout profiles (JSON files under `layouts/`), tied to a specific source resolution, with an explicit warning (never a silent guess) if you load a layout onto a differently-sized video. If you continue, regions are fitted with one uniform scale and centered so different aspect ratios do not distort them. Arrow-lane reference snapshots (see below) travel with the layout and are resized by the same scale.
- **Save Layout** and **Save Layout As...** embed all captured arrow activity, judgment, and game-end references, plus taught score/combo digit samples, in the same JSON file. Load that file to restore them; no separate reference files are needed. Save again after teaching or changing references.
- Recognition cards pair P1 and P2 on shared rows: score, combo, then matching arrow directions. Different preview sizes, wrapped status text, missing regions, and drawing order do not shift unrelated cards beside one another.
- Matching P1/P2 recognition boxes use identical width and height: P1 supplies the size for each kind (and each arrow direction). Drawing or loading a matching pair resizes P2 around its center, shifting inside the source frame if needed; P1, unpaired boxes, and shared game-end boxes are unchanged. P2 boxes are not created automatically: draw and label each one normally, then its size matches P1. **Regions > Match P2 sizes to P1** also applies this to the current layout. With **Draw Region: OFF**, drag an existing box on the video to move it without resizing; release commits the position within frame bounds. Check the resized crop alignment and recapture examples if needed; existing references and per-player settings are retained. **Save Layout** persists the changed coordinates.
- Run OCR (EasyOCR, CPU) on each `score`/`combo` region once you click **Start Analysis**. Recognition runs on a single-threaded background worker without blocking source-rate playback. After each job it immediately processes the newest available frame, using a retained neighboring frame only when a result actually needs confirmation. It never builds an unbounded backlog.
- Teach score or combo digit references with one input: pause on a clear number, click **Teach score...** or **Teach combo...** in its region card, and enter all visible digits (including leading zeros). Draw the region tightly around the digits, excluding the COMBO label and arrows. References support background OCR verification; they never emit numeric events on their own. Combo yellow-digit and fast-live settings still apply, and low-confidence readings still need repeated evidence. Advance/play to analyze new frames after teaching. Save the layout to retain each region's samples.
- Combo teaching detects dark digit outlines on bright/detailed backgrounds separately from score digits. If automatic boxes are wrong, choose **Draw digit boxes manually** and drag one tight, non-overlapping box around each digit from left to right; a crosshair and live green rectangle show the box while dragging. Enter all digits, then **Learn**. **Use automatic boxes** restores detection. Live template verification uses the same combo outline preprocessing. Older score-style combo templates need reteaching; score templates remain valid. Manual boxes label teaching samples only, not fixed live positions, so changing combo lengths remains supported.
- For `arrow` regions: capture multiple **Active Arrow** snapshots on either player to identify activity in that direction. Perfect/Great/Good/Bad/Miss comes only from the player's judgment box, never from arrow images.
- Show, per region, the cropped image, the raw OCR text or arrow-classification state, and the currently accepted value/state.
- Apply a configurable temporal-consistency filter for score/combo and a separate edge-triggered activity detector for arrows; matched judgment pulses determine the outcomes - see section 6.
- Reject isolated combo decreases and implausible upward jumps caused by arrow occlusion. Repeated high-confidence lower readings can correct a falsely high accepted combo.
- Record `new_session` / `score_observed` / `score_changed` / `combo` / `judgement` / `arrow` events in a table, with gameplay recording on/off and clear-table controls. Each confirmed arrow activation immediately emits activity-only `arrow`; each recognized judgment later emits one `judgement` carrying the outcome and matched directions. New analysis never emits `arrow_hit`, `arrow_miss`, `judgment_hit`, or `judgment_miss`.
- Export recorded events to a local `.jsonl` file (one JSON object per line).
- Optionally publish recorded event JSON to a Fabric Eventstream custom endpoint, using SAS authentication, a durable local queue, background retries, and visible delivery status.

**Does not do (by design, for this milestone):**
- No live rewind, seeking, or audio capture. An HDMI output port on a laptop is not a capture input; use a Windows-compatible HDMI-to-USB capture device.
- No general judgment-word OCR or automatic judgment-tier interpretation, life gauge, or "final results" recognition. Judgment regions match user-captured images, not arbitrary words. Without a judgment region, arrow activity is visible but cannot produce hit/miss outcomes.
- No tracking of individual falling notes as they travel down the track - only the fixed receptor region per lane.
- No automatic network publishing: you must explicitly connect in the Events tab. Kiosk registrations have stable user-session IDs, but assigning registrations to P1/P2 gameplay and redeeming PINs are not implemented yet.
- No claim that OCR or arrow-similarity readings are accurate. See section 7 for real numbers measured against a real clip.

---

## 2. Requirements

- Windows 10/11.
- For HDMI: a connected video source and HDMI-to-USB capture card exposed to Windows as a camera. Allow camera access for apps and desktop apps in Windows privacy settings (including Python if listed), and close other apps using the card.
- Python 3.10+ available as `python` on your PATH (this project was built and tested against Python 3.12).
- About 1 GB of free disk space for the virtual environment and OCR model files.
- Internet access **the first time you run the app** (to download the EasyOCR model files, roughly 100 MB) and **the first time you run setup** (to download PyTorch and other packages). After that, everything runs fully offline.

---

## 3. Setup (one time)

Open PowerShell in the project folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

This creates a project-local virtual environment in `.venv\` and installs
everything needed. It deliberately installs PyTorch (a dependency of the
OCR library) from PyTorch's own CPU-only package index first, so you get a
~150-250 MB CPU build instead of a multi-gigabyte CUDA build you don't
need for this CPU-only app. If your network blocks `download.pytorch.org`
(e.g. behind a strict corporate proxy), the script prints a warning and
continues - the next step installs PyTorch from your normal package index
instead, which is CPU-only by default on Windows for current PyTorch
releases anyway, so this fallback is safe.

Setup takes a few minutes depending on your connection. You only need to
run it again if `requirements.txt` changes.

For an existing environment, the new named-device picker requires
`cv2-enumerate-cameras==1.4.0`. Re-run setup or install the added package:

```powershell
.\.venv\Scripts\python.exe -m pip install cv2-enumerate-cameras==1.4.0
```

---

## 4. Running the app

The desktop opens with a dark charcoal theme, blue primary actions, and
consistent styling across Video & Regions, Events, and calibration dialogs.
The two tabs stay flat and the same size when switching; the selected tab has
a blue underline, highlighted text, and a distinct background.
The Events table supports vertical and horizontal scrolling for smaller windows.
Windows-managed title bars and native file/message dialogs follow your OS theme.
No additional theme packages or setup steps are required.

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

The first time you run it, EasyOCR downloads its detection + recognition
model files (~100 MB total) to `%USERPROFILE%\.EasyOCR\`. You'll see
"Downloading detection model..." / "Downloading recognition model..." in
the console and "OCR engine: loading..." in the app's status bar. This
only happens once; later runs start with "OCR engine: ready" within a
second or two.

To run the tests instead:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
```

---

## 5. Walkthrough: calibrating and running on your own video

### Choosing Video file or HDMI input

The **Video source > Source** selector offers **Video file** (the default) and
**HDMI input**. No camera is opened automatically at startup or during device
discovery.

- **Video file:** click **Open Video...** and use the existing play, pause,
  frame-step, and seek controls.
- **HDMI input:** connect the console/video source to the capture card's HDMI
  input and connect the card to this PC over USB. Select **HDMI input**, choose
  **Hagibis (device 1)** (or your card's current name/index), then
  **Connect HDMI**. Device indices can change; use the name, not a remembered
  numeric index. **Refresh devices** updates the list after plugging in a card.
  When multiple devices are present, explicitly choose one so the built-in
  camera is not opened accidentally.

For **Cam Link** devices, the app requests **1920x1080 at 30 fps** before reading
frames; the driver's default 640x480 mode can return a black image even when an
HDMI signal is present. A rejected setting or mismatched reported mode produces
an explicit error. Other capture devices retain their driver defaults.
Cam Link 4K uses Windows' built-in UVC driver; no separate Elgato driver is
required. Connect it to a USB 3.x port and check the layout's resolution.

The first live frame starts the preview. Analysis remains off until you click
**Start Analysis**. Use **Freeze preview** to hold a frame for drawing regions,
teaching digits, or capturing arrow/game-end references; **Resume live** jumps
to the current feed, not buffered footage. Previously submitted OCR may still
complete while the preview is frozen. Live input has no frame stepping, rewind,
or seek, and does not capture audio.

To save HDMI footage, click **Record MP4** beside **Connect/Disconnect HDMI**.
Click **Stop recording** to finish the MP4 and choose its filename and location.
Canceling the save dialog keeps the temporary recording and changes the button to
**Save recording...**, so you can save it later. Starting another recording,
disconnecting, changing sources, or closing the app requires saving the pending
recording first; canceling keeps the current source/app open.
Recording captures the original full-resolution video without calibration
overlays or audio, independently of analysis, gameplay event recording, and
**Freeze preview**. Video stays local and is never sent to Fabric or Rayfin.
Encoding and file copying run in background workers with bounded frame buffering.
If encoding cannot keep up, the signal stalls, or the encoder fails, recording
stops with a visible error; any playable partial MP4 is offered for saving.
The MP4 uses the device's reported FPS (or measured capture cadence when absent),
repeating frames across capture gaps to preserve elapsed recording time.
Temporary footage uses the Windows temporary directory until saved; allow
enough free space there as well as at the chosen destination.

Live capture runs in a dedicated thread and retains only the latest frame,
avoiding an unbounded queue or blocking Tk on camera reads. Dimensions come
from the actual first frame; FPS is reported when supplied by the driver.
`frame_index` counts captured frames (gaps are expected if preview frames are
dropped), and `video_time_ms` is monotonic elapsed time since capture opened,
including time spent with the preview frozen. Live events use
`source_mode: "live"`; video files continue to use `"replay"`.

Connecting starts a fresh GUID session with a `new_session` event whose reason
is `hdmi_connected`. Calibrated game-end detection, both players, JSONL export,
Fabric publishing, and the live scoreboard work through the same pipeline.
Changing sources stops analysis and clears pending old-source OCR work; a
different source resolution prompts before fitting or removing current regions.
Saved layouts are not modified by that choice.

Use **Disconnect HDMI** before unplugging the card, or choose **Video file**
to switch back. **Cancel HDMI** cancels a pending connection. Connection failure,
lost frames, five-second frame stalls, and capture-resolution changes are
displayed explicitly; analysis stops rather than inventing events or treating
the live source as a completed recording. Reconnect to retry.

If Hagibis is listed but cannot open, close Camera/OBS/Teams or other capture
apps, verify the HDMI source is sending a supported signal, check the USB cable
and driver, and enable **Windows Settings > Privacy & security > Camera >
Let apps access your camera** and **Let desktop apps access your camera**.
Enable Python's camera permission too if it is listed, especially when using
Microsoft Store Python: its packaged-app camera access can differ from
Camera/OBS. A native Windows activation error of `0x80070005` means access was
denied; check these permissions even if another app can show the HDMI video.
Retry **Connect HDMI** after granting access. If settings are managed, ask your
administrator rather than bypassing the restriction.

An enumerated device does not prove a working HDMI signal or successful capture.
A driver that blocks during shutdown shows an error rather than silently opening
a second connection; wait and retry.

### 5a. Score / combo (OCR)

1. Choose a source as described above. For **Video file**, **Open Video...** and pick your `.mp4` file. Resolution, FPS, and frame count appear below the selector. For HDMI, connect the selected device to display the first live frame.
2. For files, use **Step >|** / **|< Step** or the slider to find a clear score/combo frame. For HDMI, wait for that frame and click **Freeze preview**. Avoid menus/loading screens; analysis must be started explicitly.
3. Click **Draw Region: OFF** to turn it **ON**. Click-drag a rectangle around the score digits on the canvas. When you release the mouse, a dialog asks for a label, player (P1/P2/Unknown), and field kind (`score`/`combo`/`arrow`/`game_end`). Repeat for combo. Turn Draw Region back **OFF** when done so you don't accidentally start new rectangles while browsing the video.
4. Adjust **Consistency frames** if needed (default 1). Numeric OCR runs in the background and may sample changing values, so one readable sample is accepted by default. Raise this only when repeated OCR noise is more important than capturing fast score/combo changes - see section 6.

For yellow combo digits with a white glow, try **Yellow-digit filter** in that combo's recognition row, then **Save Layout**. This optional per-region filter subtracts blue from the weaker of red and green, emphasizing yellow fill while suppressing white outlines and red/pink arrows. It is off by default, is restored when the layout is loaded (including rescaled layouts), and never affects score OCR. Turning it on or off clears pending evidence but preserves accepted values and existing events. It can improve `3`/`8` confusion on this skin, but can worsen other crops and cannot restore pixels hidden by an arrow. OCR confidence and acceptance thresholds are unchanged; a correctly read but low-confidence candidate remains unaccepted. Disable it for non-yellow digits or if recognition worsens.

Combo OCR automatically checks for a row of multiple large outlined digits with
at least one wide glyph, so narrow `1` digits in values such as `011` do not
disable the check. It first tries vertically stretched copies (2x, then 1.5x if needed) to
restore proportions for the text model. The saved calibration and displayed crop
are unchanged. Only a digits-only reading with at least 0.90 confidence can use
this shortcut; weak or mixed readings continue through the existing OCR path.
This applies without enabling any option, including to existing layouts.
It can recover wide digits that normal OCR misses or misreads, but confidence
is not an accuracy guarantee and obscured digits can still be missed.

With the yellow filter enabled, combo OCR also tries recognition directly on the original-proportion filtered crop, avoiding the expensive text-detection stage. It uses that result only for a single digits-only reading within the combo length limit and with at least 0.90 confidence. Otherwise it falls back to the existing detector on the same filtered crop. Confidence is never increased artificially, and normal confirmation and jump checks still apply. Clear crops can be much faster; obstructed or uncertain crops still incur detector latency, and either OCR path can make mistakes. Score OCR is unchanged.

Combo OCR allows the engine to recognize surrounding words such as "combo", then ignores text and mixed letter/number detections instead of forcing letters into digits. Spaces between digits are removed (`6 5` becomes `65`), but letters are never substituted or stripped out of mixed detections to manufacture a number. This applies with and without the yellow filter, including the fast path. A mixed detection is left unreadable until a clearer frame separates the number from the text; OCR can still mistake a letter for a digit. Score glyph OCR retains its digit allowlist.

For fresher combo updates, enable **Fast live combo** in that region's card and **Save Layout**. This opt-in mode runs recognition directly on the crop and never invokes slow text detection, even when the reading is uncertain. It works with or without the yellow filter. Readable candidates that differ from the accepted value are labeled **unconfirmed**; readings below 0.90 confidence cannot replace the accepted combo or emit combo-change events. Non-numeric/mixed results remain unreadable with a visible explanation. Existing jump and correction rules still apply. Keep the crop tight around the digits: this mode can miss readings the full detector would recover and can still make confident mistakes. Disable it to restore detector fallback. Old layouts default to off; saving/loading or rescaling preserves the choice. Toggling clears pending evidence without erasing accepted values or events. Score OCR remains unchanged and still contributes to the worker's total latency.

### 5b. Arrow lanes (activity identification)

1. With **Draw Region: ON**, draw a rectangle around one arrow's receptor (the fixed on-screen target the falling notes travel toward), and pick field kind **arrow** plus the matching **Arrow direction** (Up-Right/Up-Left/Down-Right/Down-Left/Center). Repeat for each of the 5 directions, and again for the other player if needed (up to 10 arrow boxes total for 2 players).
2. On each player, select **Add Active Arrow ref** on frames that show a clear colored note overlapping the receptor, not the empty receptor or white flash. Capture multiple appearances separately for P1 and P2; examples are not shared. **Clear Active Arrow refs** removes only that card's examples. Old Hit/Miss arrow images are ignored, not automatically reused: every lane needs fresh Active Arrow calibration.
   For every direction on either player, **Add Empty ref** captures the unused
   receptor with no note overlapping it. These optional negative examples prevent
   look-alike empty tiles from being treated as active. Active must meet the
   shape threshold and beat the best Empty mask match in its learned color by
   more than **0.02**. Empty samples without contrast in the learned colors
   cannot provide a negative veto; the diagnostics report this explicitly.
   Ties and near-ties stay idle. Capture multiple empty appearances if the receptor
   animates. Each card has independent Empty preview, clear, and **View / delete
   Empty refs...** controls; deleting Empty samples never removes Active samples.
3. Adjust **Arrow shape similarity** (default 0.55; higher = stricter/fewer false positives) and **Arrow confirm frames** (default 1, since real flashes are often brief) if needed - see section 6. **Judgment shape similarity** is a separate control (default 0.65); lowering it does not lower arrow matching. **Judgment confirm frames** independently defaults to 2; increasing it does not delay arrow activity.
4. Reference snapshots are saved as part of the region and travel with **Save Layout** / **Load Layout**, so you only need to capture them once per skin/resolution.
   **View / delete refs...** opens a scrollable gallery of every Active Arrow sample
   for that box. Delete individual examples without clearing the rest or changing
   the other player. Recognition and the card preview refresh immediately; close
   the gallery and **Save Layout** to persist deletions.
   Active and Empty examples are both saved, loaded, and rescaled with the layout.
   Existing colored Active samples are reused, but the default matcher now uses
   shapes rather than grayscale. Unusable or colorless samples are flagged,
   never silently treated as valid or replaced by grayscale matching.
   The displayed **active shape similarity** measures the best Active shape
   match; status explains when an Empty example overrides it.
   Cyan dashed bounds extend the search downward by half the calibrated box
   height, within frame bounds and without expanding across lanes. Green bounds
   remain the capture box; saved coordinates are not changed.
   Arrow diagnostics show source frame/time, positive/negative scores, threshold,
   expanded search bounds, best-match bounds, and calibration warnings.
5. Add a judgment box for each player and teach Perfect/Great/Good/Bad/Miss separately on each player's card. Only a matching judgment produces an outcome; activity alone never counts a hit or miss. Existing layout coordinates and judgment/digit samples are retained during recalibration.

There is no Appearance calibration. Old `appeared` references remain in saved
layouts but are ignored by recognition and previews. New events do not include
`arrow_appeared` or appearance-based response timing. The scoreboard's time-to-answer
and animal-comparison cards have no samples for new games and stay hidden;
older snapshots remain compatible. Active Arrow and judgment calibration are
all that is needed for outcomes and running points.

**Cumulative hit percentage** is `hits / (hits + misses) * 100` over detected
outcomes in the current game. Undetected notes are not counted. The four main
tiles (Best streak, Total detected steps, Cumulative hit percentage, Step Title)
remain visible even before the first snapshot or while sharing is inactive.
Unavailable values show **Waiting for data**, never invented zeroes. A known zero
total remains zero; accuracy waits until there is at least one detected outcome.
Step Title uses Warming up during active games until a title is available.
Score, arrow counts, hit pace, and per-lane accuracy sections also stay visible;
only optional legacy response-timing and animal comparisons still require data.
**Arrow accuracy** uses each lane's `hits / (hits + misses)`, not its share of total
hits. Unplayed lanes show **No attempts**; detected misses without hits show **0.0%**.
Unknown-direction outcomes do not alter lane accuracy. Older snapshots without
per-lane miss counts show a waiting state in this section.
Without Miss references in the judgment box, misses cannot be detected, so this percentage may
overstate actual performance.

The lane with the most detected hits
earns one title: **Up-Left Legend**, **Up-Right Royalty**, **Down-Left Dynamo**,
**Down-Right Champion**, or **Center-Step Superstar**, with its matching direction symbol.
The card shows the winning per-lane hit count. Unplayed lanes and
unknown-direction judgments are excluded. **All-Step Ace** requires equal positive
hit counts in all five lanes. A two-, three-, or four-way hit-count tie shows
**Joint leaders** with the tied directions. The Step Title tile remains visible
during active games, showing **Warming up** when no directional hit can earn a title
or per-lane data is unavailable. No minimum sample count is required.
Deploy the updated scoreboard to use hit-count titles. Existing snapshots with
per-lane hit counts are supported; per-lane miss data is not required for titles.

**Best streak** replaces the duplicate Most-hit lane tile. It shows the longest
run of successful recognized judgments in the current session, plus the current
run. Each non-BAD `judgement` hit adds one, including an unknown-direction or multi-arrow
judgment. BAD is a hit worth 5 points per arrow but leaves both streak values
unchanged; a Miss resets the current run but preserves the best. This is not the
game's OCR combo and is not inferred from arrow activity. Duplicate, synthetic,
and stale-session events do not count. Delayed judgments are ordered by video
time, then sequence, so a late Miss cannot incorrectly join two runs.
New sessions reset both values. Older publishers without streak fields show
Waiting for data; restart the desktop publisher and deploy the frontend to use
this metric. Missed recognition can still overstate or understate a streak.

The **Best streak** and **Total detected steps** tiles each have
50 captions that rotate every 15 seconds of live feed time without repeating
until the deck is exhausted. Players get different simultaneous captions.
Inactive, stale, and failed feeds pause rotation; new games reset it. These
captions never change the underlying numbers. Total detected steps adds player hits and
misses, including unknown-direction outcomes once; simultaneous arrows count
separately, so it is a detected-outcome count, not a physical step count. Missing
data shows a waiting state; a known total of zero remains visible, with waiting
text until the first detected outcome. Captions appear only when their tile has
the data needed to display them. Inactive feeds keep the complete waiting layout
without showing the previous game's names or values.

**Existing four-direction layouts:** loading a layout with Up/Down/Left/Right
arrow regions shows a warning and removes those unsupported arrow regions from
the loaded setup. Score, combo, game-end, and already-supported arrow regions
are preserved. Draw and teach the five new arrow regions per player; there is
no automatic mapping of old reference images. The saved file is not changed
until you explicitly save the updated layout.

The live scoreboard uses the same five direction names for hit counts and arrow
balance. Restart the updated desktop publisher and start a new session after
updating the scoreboard; old four-direction snapshots are rejected explicitly
rather than relabeled or used to invent new counts.

### 5c. Running analysis and reviewing events

#### Automatic game sessions

Each game has one in-memory UUID/GUID shared by both players and all its events.
Opening a video starts a fresh session; if no session has been announced yet,
the first analysis pass creates a `new_session` event before gameplay events.
Every new session starts with sequence 0 and gameplay events follow at sequence 1.
Session IDs are shown above the Events table and in its **Session ID** column.

To detect when a game ends:

1. Pause on a distinctive results banner or other end-of-game indicator.
2. Draw a region around it and select **game_end**. This is shared by the game,
   not assigned to P1/P2; only one game-end region is allowed.
3. In **Game session**, click **Add game-end ref**. Capture additional examples
   if needed, then **Save Layout** to retain the region and reference images.
   **Clear game-end refs** removes these examples.
4. Set **Game-end similarity threshold** (default 0.95) and
   **Game-end confirm frames** (default 2), then run analysis.

Once confirmed on distinct forward video frames, the indicator generates one
`new_session` event with the next GUID. Its payload contains `session_id`,
`previous_session_id`, `is_new_session: true`, and `reason: "game_end"`.
The event belongs to the **new** session; the previous session's events remain
in the table and JSONL export. Numeric/arrow tracking and live scoreboard totals
reset together. Already-running OCR finishes under the old GUID; queued old-game
work cannot emit into the new session.

While the end indicator remains active, gameplay readings are suppressed.
The detector rearms only after the indicator stops matching for the configured
confirmation count. Repeated frames and backward seeks do not retrigger a
boundary; seek playback does not reconstruct historical game IDs. Reopen the
video for a fresh pass. Image matching can be wrong: use a tight, distinctive
crop rather than a generic background or banner shown during gameplay.

Lifecycle events are still recorded and sent to connected publishers when
**Record events** is off; that switch suppresses gameplay events only.
Manual **New Session** retains its existing clear-history behavior, now leaving
the new session's lifecycle event in the table. GUIDs are not persisted across
app restarts; exported events and queued Fabric messages retain their original IDs.

#### Playback and event review

1. (Recommended) Give the layout a name in **Layout profile > Name** and click **Save Layout** so you don't have to redo calibration next time - this always saves into the app's built-in `layouts/` folder, keyed by that name, and shows up in the **Load** dropdown. If you'd rather choose exactly where the file goes (e.g. a USB drive, a project folder, alongside the video itself), use **Save Layout As...** instead, which opens a normal Windows file-save dialog defaulting to the `layouts/` folder. To load a layout saved that way (or from anywhere else on disk), use **Load Layout From...**, which opens a file-open dialog - as opposed to **Load Layout**, which only loads from the Name dropdown of layouts saved in the built-in folder. Loading a saved layout on a video of a different resolution will show an explicit prompt asking whether to fit the coordinates and arrow reference snapshots. The fit uses one uniform scale and centers the old coordinate space in the new frame, so wide, square, and portrait videos do not distort regions. It will never silently apply the wrong coordinates, whichever way you loaded it.
2. Click **Start Analysis**. Now step or play through the video. The right-hand panel shows, per region, the current crop and either (score/combo) raw OCR text/candidate/accepted value, or (arrow) the observed state (idle/active), similarity score, and reference thumbnails, plus a status line.

   Recognition is arranged in two compact columns: **Player 1** on the left and **Player 2** on the right. Each card stacks its aspect-ratio-preserving crop preview above its readings and wraps long status messages. Teaching, yellow-digit filters, and arrow-reference controls remain in the matching player's card. Regions assigned to **Unknown** appear in a separate section below both columns. Scroll the recognition panel to reach additional cards.

   To reduce playback overhead, unchanged arrow cards refresh their crop, similarity, and pending-count display at most ten times per second. Arrow-state changes, errors, unreadable crops, and tracking resets update immediately. This only paces the recognition panel: arrow analysis and event recording still run on every displayed video frame, and completed score/combo OCR results are displayed immediately.
   Detailed arrow/judgment diagnostics are collapsed by default. Select **Show
   diagnostics** on a card to inspect its latest details. Closed panels cache
   the latest text without updating Tk labels; open panels refresh at most four
   times per second. Opening a panel immediately displays its latest cached
   reading. Label wrapping is recalculated only when the card width changes,
   not whenever status text changes its height. Recognition, correlation and
   event delivery are unchanged.

   Each numeric card shows **OCR frame** and its video timestamp. Its crop and readings belong to that completed OCR job, not necessarily the frame currently playing on the left. OCR latency still applies, but unnecessary confirmation passes no longer hold the panel on older footage; pausing lets the worker catch up to the final displayed frame. Confirmation jobs process only pending fields, so confirming a score does not rerun unrelated combo OCR on the older frame.
3. Switch to the **Events** tab to see `new_session` / `score_changed` / `combo` / `judgement` / `arrow` rows appear as they're confirmed. Use **Record events** to pause gameplay recording without losing calibration, **Clear Table** to wipe the recorded/exportable list, and **Export to JSONL...** to save everything currently in the table to a file.
4. **New Session** resets the session ID, sequence counter, and all recognition state - use it if you want a clean slate on the same video without reloading it. Loading a different video or seeking (slider, or the frame-back button) also resets recognition state automatically, so a jump in the video never gets misread as a huge score change or a phantom arrow hit.

---

## 6. Recognition rules, explained plainly

**Score / combo:**
- **Score observations are not confirmed scores.** Each changed, readable OCR score emits `score_observed` immediately when recording is enabled, even while confirmation is pending or the value is rejected by acceptance rules. Its payload includes `observed_value`, original `raw_text`, `confirmed: false`, the previously accepted score, and the validation note; the event retains source frame/time and original OCR confidence. The table explicitly labels it **UNCONFIRMED** and JSONL preserves this distinction. Repeated unchanged observations are deduplicated. Only `score_changed` represents an accepted score; observations do not alter accepted tracking and may be wrong or decrease.
- **An unreadable frame never becomes zero.** If OCR can't read a region on a given frame (blocked, blank, format doesn't look like digits), the previously accepted value is simply kept until a new value is confirmed. You'll see "unreadable this frame (previous accepted value kept)" in the panel.
- **Scores can advance immediately and recover downward with confirmation.** Background OCR reads the score independently of templates. With the default consistency setting, an OCR reading with confidence of at least 0.70 can establish or increase the accepted score immediately. Lower-confidence initial scores and increases must repeat in at least two distinct, advancing video frames within two seconds. Readings below 0.30 confidence additionally require template agreement; without corroboration they are rejected. Missing confidence is rejected. A downward correction requires at least two matching readings, each at or above 0.70 confidence, within two seconds; templates cannot bypass this floor. Higher "Consistency frames" settings still apply. Invalid, low-confidence, or different readings restart correction evidence, and duplicate/old frames cannot confirm it. Confirmed corrections update the display and emit a normal `score_changed` event with previous/new values for the table, JSONL, and Fabric; existing history is not rewritten. Confidence remains the original OCR measurement. Repeated confidently wrong readings can still cause an incorrect correction; **New Session** remains available for a manual reset.
- **Score templates are supporting evidence, not ground truth.** Use **Teach score...** on clear frames and type all displayed digits, including leading zeros (commas are allowed). Check the preview's labels before Learn. Extraction retains dim zeros, supports wide/flat digits up to twice their height, and uses brighter-threshold passes to recover digits joined to HUD borders by shadows without duplicating boxes. It selects a consistent digit row and normalizes contrast. Mismatched counts are rejected. Partial reference sets can support OCR immediately. Older-format samples are ignored with a status message; repeated OCR remains available without teaching. The first new teaching replaces those outdated samples; subsequent teaching keeps all distinct examples. Save Layout persists changes. Animated/blended digits can still be misidentified.
- **Candidate is separate from accepted.** A readable number awaiting confirmation or rejected by acceptance rules remains visible as the candidate, with a pending/rejection explanation. It is not mislabeled as an OCR failure or an unreadable crop.
- **Ambiguous score readings are rejected, not corrected by guessing.** OCR must return exactly one digit per segmented box; multi-character results retain their original confidence and are rejected. Template matching compares foreground strokes (intersection over union), not shared black background, against the closest user-labeled example for each digit. The best digit must exceed the runner-up by at least 0.02 similarity and meet the 0.72 minimum match threshold. Template similarity is not an accuracy probability. Teaching does not rewrite accepted values or old events; use **New Session** to reset an incorrect baseline and event history (export first if you need to preserve them).
- **Accepted combos can recover downward.** A lower value needs at least two matching readings, each with OCR confidence of at least 0.90, on distinct advancing frames within two seconds. "Consistency frames" above two raises this requirement. A different candidate, low-confidence reading, or unreadable frame restarts confirmation; duplicate/old frames cannot add evidence. A single lower reading keeps the accepted combo unchanged. Confirmed decreases update the displayed accepted value and emit a `combo` event with only the current value in `payload.value`; no previous/new pair is sent. The Events table displays just that value. Existing events are not rewritten. This also allows genuine combo resets; repeated confidently wrong readings can still be accepted.
- **High-confidence combos can advance immediately.** With the default consistency setting, OCR confidence of at least 0.90 allows a single plausible combo reading to establish or advance the accepted value. Lower-confidence, unreadable, or implausible upward-jump readings are rejected. A confidently wrong initial reading can still establish a bad baseline; confidence is not a guarantee of correctness.
- **Confirmation depends on the field and confidence.** "Consistency frames" remains a configurable minimum: qualifying score/combo initial readings and increases may use 1; lower-confidence scores and all downward corrections require at least 2. Raising the setting above 1 also delays high-confidence score and combo readings. While OCR runs, the first neighboring frame and newest displayed frame are retained. The neighbor is processed only for pending confirmation rather than chaining old neighbors. Reprocessing a paused frame never adds evidence. Evidence expires after two seconds of video time and is restarted by a different candidate or invalid reading. Background OCR does not run on every source frame, so events can be delayed and brief changes missed. Preview playback remains independent.
- **Brief combo displays are retained for OCR.** A lightweight outline/contrast
  check keeps at most two advancing frames per combo region while the worker is
  busy, in addition to the neighbor/latest-frame slots and EOF safeguard.
  Blank frames do not overwrite these candidates. They are processed in frame
  order, only for the corresponding combo regions, with up to four priority
  passes before returning to current numeric sampling so scores cannot starve.
  Both players have independent buffers; seeks, calibration changes, and session
  generations invalidate older candidates. The check never reads a digit or
  emits an event: normal OCR confidence, plausibility, and distinct-frame
  confirmation still apply. The buffer favors recent candidates, not an
  exhaustive history, so sufficiently fast changes can still be skipped.
- **Important limitation the temporal filter cannot fix:** if OCR is *consistently* wrong for the same digits (a stable misread, not a one-frame glitch), the consistency filter will still "accept" that wrong value, because it only rejects readings that fail to repeat - it cannot know the true value. See section 7 for a real example of this happening. This is why the app always shows you the raw OCR text next to the accepted value, so you can visually sanity-check it.

**Arrow lanes:**
- **Classification uses cached colored-arrow shapes learned from your Active samples.** A half-resolution search uses near-native widths and native height, including the approaching note just below the receptor. The direction comes from the box configuration; the reference detects activity, not success. Missing or unusable Active calibration shows an error. Old Hit/Miss images are ignored. Empty examples are evaluated in the same learned-color space, not mixed with grayscale scores.
- **A frame is classified "idle"** whenever its best Active shape score falls below **Arrow shape similarity**, or an Empty mask wins or is within the required margin. Scores from the former grayscale matcher are not interchangeable with shape scores.
- **Activity is edge-triggered:** a confirmed active arrow immediately emits one `arrow` with `state: "active"` and `evidence: "arrow_activity"`, then rearms after confirmed idle. It never waits for a judgment or emits repeatedly while held active. These activity-only events have no hit/miss outcome or tier and never affect score or accuracy. A later recognized player judgment emits `judgement` and determines outcomes.
- **"Arrow confirm frames"** (default 1) can require a state to be seen for more than one frame before it's confirmed, if single-frame noise is producing false events - but raising it risks missing genuinely brief flashes, the same trade-off as consistency frames above.

**Correlating arrow activity with Perfect / Great / Good / Bad / Miss:**

**Independent player calibration:** P1 and P2 keep separate arrow activity images,
Perfect/Great/Good/Bad/Miss examples, and taught score/combo digits (including review/version
metadata). Capturing, clearing, or teaching on one card never changes the other player's
calibration. Matching box dimensions remain automatic, but equal sizes do not guarantee
equal framing, orientation, background, or animation. Align each player's crop and
capture examples from that player's footage. P2 can be calibrated without a P1 box.
Loading layouts retains each region's own samples. Layouts saved while sharing was
enabled may still contain identical copied samples: clear and recapture P2's references
and reteach its digits as needed. Nothing is deleted automatically.
**Save Layout** persists both sets; existing files are unchanged until saved.

1. Draw a tight region around P1's displayed judgment word, excluding combo digits, and choose kind **judgment**, player **P1**. Repeat for P2. These cards are paired on the same row.
2. Pause on PERFECT and choose **Add Perfect ref**; capture GREAT with **Add Great ref**, GOOD with **Add Good ref**, and BAD with **Add Bad ref**. All four count as hits, but retain their distinct judgment tiers. BAD awards 5 points per matched arrow and leaves streaks unchanged. Capture the full word during a clear, saturated phase of its animation. Lettering shape and hue are learned from your examples; colors are not hardcoded to tiers and no word OCR is used.
3. Capture **Add Miss ref** on a clear MISS. Each taught outcome can count after its confirmation requirement (two matching frames by default); untaught categories show a warning but do not block taught outcomes. The box rearms after 50 ms of source-video time without a taught outcome matching. Multiple examples per category are retained; capture several clean animation phases separately for each player.
   Use **Add No judgment ref** on each player's judgment card while no word is
   visible. Capture several background appearances that previously caused false
   matches. These optional negative examples are stored as `reference_images.empty`;
   a positive outcome must pass its shape threshold and beat the best
   No judgment mask match in the winning word's learned color by more than 0.02.
   Background samples without contrast in those colors provide no negative veto;
   the card reports this explicitly. A tie or near-tie is treated as no match
   and contributes to the normal 50 ms rearm interval. **Clear No judgment refs**
   affects only those samples; each category has a **View / delete ... refs...**
   gallery for individual removal. **Save Layout** preserves both positive and
   negative samples, and rescaling resizes both. Old `idle`/Blank samples remain
   ignored rather than silently becoming negative examples.
4. Keep each player's arrow reference calibration too. Each player uses its own references
   with its own screen boxes. **Save Layout** includes judgment regions and all their examples.

**Independent similarity thresholds and diagnostics**

- Select **Show diagnostics** on an arrow or judgment card to expand its details.
  Keep panels collapsed during normal play for lower UI overhead. State, score,
  status and error displays remain available while details are collapsed.
- **Letter-shape matching is the default judgment engine**, not an opt-in mode.
  It learns saturated lettering masks from each player's references, removes tiny
  components, and searches several sizes and positions at half resolution.
  Templates are cached when regions/references change, not rebuilt each frame.
  The original green rectangle remains the capture box; yellow dashed bounds
  show the expanded search area (40 pixels horizontally and 12 vertically per
  side for a 286x53 box, scaled proportionally and clipped to the source).
  Crops in the cards still show the capture box; diagnostics give the expanded
  search coordinates and best lettering bounds in source pixels.
- Existing layouts and references remain readable and are not rewritten.
  Clear colored-word samples can be reused. Unsaturated, invalid, or unusable
  examples generate calibration warnings; if no usable words remain, recognition
  reports a calibration error. There is no silent fallback to matching the
  full moving background. Capture a clear example or remove the unsuitable
  sample with the gallery. Partial usable calibration can still recognize its
  taught categories.
- **Arrow shape similarity** controls activity matching, including comparison with Empty
  samples, and defaults to 0.55. **Judgment shape similarity** controls
  Perfect/Great/Good/Bad/Miss shape matching only and defaults to 0.65; game-end
  similarity remains separate. These are runtime controls, not layout settings.
  The new shape score is not equivalent to the old grayscale score and is not
  an accuracy probability. Do not copy previous tuned grayscale thresholds blindly.
- **Arrow confirm frames** defaults to 1; **Judgment confirm frames** defaults to 2.
  Only distinct advancing frames with the same recognized word confirm a judgment.
  No-match, negative-reference suppression, unreadable frames, or ambiguity break
  pending confirmation without prematurely clearing a latched outcome. Changing
  the judgment frame setting clears pending confirmation but does not recount a
  word already held active. These controls are runtime-only; restart uses defaults.
  Older layouts still load without No judgment examples, but receive the new
  two-frame default. Set Judgment confirm frames to 1 to restore single-frame
  behavior if a genuinely brief word cannot be confirmed.
- Each judgment card shows the source frame/time, best category and similarity,
  threshold, runner-up category/score, and the gap between them. The best
  candidate must reach the judgment threshold and beat other categories by at
  least 0.02. A high score can still be ambiguous if the top two are too close.
  When No judgment examples exist, diagnostics also show their best similarity
  and explicitly report when they suppress a look-alike word.
- The diagnostic decision distinguishes **below threshold**, **ambiguous match**,
  **confirming frames**, **accepted/queued**, **latch blocked**, **recording off**,
  and **repeated frame**. It also shows the current latch and no-match elapsed
  time against the 50 ms rearm requirement. Missing calibration and out-of-bounds
  crops are reported explicitly.
- Status still reports pending correlation and counted outcomes. Diagnostics
  describe the current frame; they do not create events or infer judgments from
  arrow activity. Advance/play after changing settings to see fresh results.
  Lowering judgment similarity can also match backgrounds falsely, so compare
  the crop and runner-up scores instead of lowering it blindly.
  Negative references do not guarantee accurate recognition on unseen backgrounds;
  validate missed, duplicated, and misclassified words against footage before
  relying on the totals. Do not capture a visible judgment as a negative example.
- Judgment diagnostics also retain the **last direction decision**, including the
  word timestamp and each candidate arrow's offset relative to it. They distinguish
  no activation in the timing window, repeated activations of one lane, widely
  spaced candidate arrows, competing judgments, and a successful assignment.
  Pending judgments show the remaining settling time. The last decision remains
  visible on subsequent frames until tracking resets; it is not the current
  frame's recognition state and is not included in exported event payloads.

For players with a judgment region, receptor flashes alone no longer count outcomes.
A new confirmed judgment determines hit versus miss. Matching arrow
activity within **500 ms before** that judgment (including the same timestamp)
assigns its direction. There is **no lookahead**: activity starting after the
word's onset cannot be assigned to that word,
without inferring success from the receptor's appearance. Correlation waits
**500 ms of source-video time** after the judgment onset to check for competing judgments; confirmation
frames and frame delivery can add delay. Processing order and wall-clock speed
do not change the source-time window.
The same bounds apply to Perfect, Great, Good, Bad, Miss, and legacy ungraded success.
Arrow evidence is retained for up to 1,000 ms so early activity survives both a
delayed word and the settling interval. Event payloads expose
`correlation_lookback_ms: 500` and `correlation_lookahead_ms: 0`; the legacy
`correlation_window_ms` reports the larger allowance (500 ms).

For simultaneous steps, the same judgment applies to every matched arrow.
Each recognized Perfect/Great/Good/Bad/Miss emits exactly one **`judgement`** event
(note the event type spelling), including when directions are known.
`payload.outcome` is `"hit"` or `"miss"`, `payload.state` retains the same value,
and `payload.judgment` retains the tier (`"hit"` for legacy ungraded success).
`step_directions` lists all matched directions and `step_arrow_count` is its length.
`direction` is the matched direction for a single-arrow step and `null` for
multi-arrow or unknown-direction judgments. Arrow onsets must be within 150 ms
of each other and each must match only this judgment.

Each confirmed arrow activation already emitted an immediate `arrow`,
independently of whether a subsequent judgment says hit or miss. Its payload
contains the arrow's `direction`, `state: "active"`, `evidence: "arrow_activity"`,
and `activity_video_time_ms` (activation onset); the event timestamp is the frame
that confirms activity. There is no `outcome`, tier, or judgment-event link.
Activity is recorded even without a judgment box, subject to the normal
analysis/recording controls. Repeated paused frames and held-active arrows cannot
emit another activity event until the tracker rearms.
Correlation emits only the later `judgement`, never an additional arrow event.
Misses emit **no `arrow_miss` events**; their directions remain in the judgment
payload. Two simultaneous arrows produce two immediate activity events, then
one `judgement` if a word is recognized, whether it says Perfect or Miss.
Deduplicate deliveries by `event_id`; use `judgement` as the authoritative
source for player totals, lane counts, and points, never counting activity as
a successful hit. Multiply by `max(step_arrow_count, 1)` for detected outcome
totals and points. Unknown directions count once, not as zero steps.
No appearance-based response timing is generated.

If no arrow, repeated activity in one direction, widely spaced arrows, or competing judgments make the direction uncertain,
the `judgement` event is recorded for that player with
`direction: null`, rather than guessing or counting every arrow. These outcomes
contribute to player totals, hit percentage and hit pace, but not directional
counts, lane-based titles or response-time samples.
Unassigned outcomes have `step_arrow_count: 0` and empty `step_directions`;
this means the arrow count is unknown, not that no step occurred.
Each player's word only validates that player's arrow activity.
The payload's `judgment` field preserves `perfect`, `great`, `good`, or `miss`;
the common event type no longer encodes hit/miss in its name.
The Events table's Detail column and the judgment card's counted status show the
full judgment word (PERFECT, GREAT, GOOD, BAD, or MISS), with its direction when known.
Legacy ungraded events still show HIT/MISS; no judgment tier is invented.
This changes the emitted event contract: update downstream filters from
`arrow_miss` / `judgment_hit` / `judgment_miss` to `judgement` plus
`payload.outcome`. Restart the desktop to use it. JSONL export and Fabric
delivery use the same event shape. Previously recorded/queued events are not
rewritten or deleted; the scoreboard still accepts the legacy event types.
New `arrow` activity events must not be interpreted as successful hits:
the scoreboard explicitly excludes `evidence: "arrow_activity"` from metrics.
The activity event type was renamed from `arrow_hit` to `arrow`; update
downstream activity filters. Existing queued events are unchanged, and the
scoreboard still accepts older activity-only `arrow_hit` messages without scoring them.
Old layouts with Success (`hit`) references still count ungraded hits, without
inventing a tier. Once Perfect, Great, Good, or Bad references are added, the old
Success references are ignored; only the explicitly taught categories are matched.

A lingering word counts once, not once per frame or receptor flash. At least
50 ms of source-video time with no taught outcome matching is required before
any next judgment, even if the word changes category. Short matching gaps do not
rearm; ambiguous strong matches hold the latch and restart the no-match interval.
Pausing or repeatedly processing one frame cannot advance that interval.
A matching failure lasting 50 ms can look like disappearance and cause a recount;
keep crops tight and teach representative examples for the changing background.
Consecutive judgments without a detectable no-match/rearm interval between them
cannot be separated reliably and may be undercounted. Simultaneous arrows count
once per matched direction for that judgment, not once per displayed frame. This is calibrated image
evidence, not a complete chart/note tracker. Close competing or unrecognized
judgment matches hold the previous state during short gaps rather than immediately
recounting a lingering word.
Pending correlations are cleared on seek, source/layout changes, stop, recording
changes and New Session; paused/ending footage cannot finalize a pending window
without advancing video time. With no judgment outcome references, a calibration
error blocks outcomes for that player. Partial calibration recognizes taught
categories and warns about missing ones. Receptor flashes never substitute for
an unrecognized judgment. Players without a
judgment region produce no hit/miss outcomes, even with calibrated arrow activity.
- **Similarity scores are not calibrated accuracy probabilities.** Game-end matching uses grayscale normalized cross-correlation with a mean-brightness fallback for near-flat crops. Arrow and judgment matching use spatial correlation of learned-color masks, with separately tuned thresholds. Their scales differ from grayscale, and colors are learned from labeled examples rather than mapped to outcomes by the app.

**Both:**
- **Session changes wait for in-flight numeric analysis before resetting tracking.** The old frame finishes under its original GUID. Manual **New Session** clears old history; automatic game-end transitions retain it. Queued jobs carrying an old session ID or tracking generation are ignored, including work queued before a seek or region change.
- **End of video flushes the final displayed numeric frame.** It is retained separately from the confirmation neighbor, processed after that pair if needed, and never submitted twice. This guarantees an analysis attempt, not successful OCR or acceptance.
- **Preview frame skipping preserves explicit seeks.** Small catch-up gaps (up to eight frames) decode sequentially without rendering discarded frames, avoiding expensive codec seeks; larger gaps still seek forward. Restarting playback from EOF still resets recognition even when a delayed preview skips the first few frames.
- **Every accepted change emits exactly one event**, not one per frame it stays on screen.
- **Numeric/arrow recognition state is reset** whenever you seek/jump in the video, load a different video, edit the region list, capture/clear an image reference, or click New Session. The game-end detector retains its forward-frame watermark and active latch across seeks so the same end screen cannot repeatedly rotate GUIDs.
- **Confidence numbers** (OCR confidence, or arrow similarity score) are shown as-is from the underlying measurement and are not a calibrated accuracy probability.

---

## 7. What was actually verified with your footage (manual walkthrough)

Using your `p1.mp4` clip (1280x720, ~59.94 fps, 7545 frames, DDR cabinet
capture with an unconfirmed exact version), I extracted a real frame,
manually read the on-screen combo and score as ground truth, then ran the
same `OcrEngine`/`AnalysisSession` classes the app uses (not a
simplified stand-in) against it:

| Field  | Manually read (ground truth) | OCR raw text | Correct? |
|--------|------------------------------|---------------|----------|
| Combo  | 172                           | `1152`        | **No** |
| Score  | 348,777                       | `0348577`     | **No** (last 3 digits wrong) |

I then fed 40 consecutive real frames from that clip through the full
pipeline (`AnalysisSession.process_frame`, consistency = 3 frames):

- The **combo** reading was consistently misread the same wrong way
  across many consecutive frames (e.g. steadily reading `1158` when the
  true value was around 172), so it was "accepted" and an event was
  emitted with a **wrong** value. This is the exact limitation described
  in section 6 - a stable misread passes the consistency filter because
  it isn't transient.
- The **score** reading fluctuated frame to frame at first (garbled
  extra digits) but stabilized to a plausible, monotonically-increasing
  value after a few frames and correctly emitted `score_changed` events
  as the number changed.
- Per-frame OCR processing time for these two small regions was roughly
  **0.6-1.5 seconds per frame** on CPU (the very first frame took ~15s
  while PyTorch finished warming up). **This means analysis will not
  keep up with real-time video during Play** - use frame-stepping while
  calibrating, and expect processing a full clip to take substantially
  longer than its own runtime. The app's status bar always shows the
  last measured processing time so this is visible, not hidden.

**Conclusion:** the plumbing (video I/O, region cropping, OCR invocation,
temporal-consistency logic, event emission, JSONL export) all worked
correctly end-to-end on real footage. Raw OCR accuracy on this specific
clip's font/skin was poor out of the box and should not be trusted
without visually cross-checking the raw-OCR panel against the video, or
manually re-calibrating tighter/cleaner regions. I did not fabricate or
cherry-pick a passing result - the numbers above are what actually came
back. Improving OCR accuracy for this font (e.g. different
preprocessing, a different OCR engine, or per-title tuning) is follow-up
work, not something this milestone claims to have solved.

The `p2.mp4` clip (P2 side) was not yet run through this same manual
check - repeat the steps above (extract a frame, read the true values by
eye, compare to the app's raw OCR panel) before trusting any P2 readings.

**Arrow lanes (historical check, before activity-only calibration):** I did not have a calibrated layout with real Hit/Miss
reference snapshots (that step is deliberately yours to do, since only
you can identify a true hit/miss moment in your footage). To sanity-check
the *mechanism* rather than fabricate a full result, I cropped an
approximate bounding box around the "Up" arrow receptor in `p1.mp4`
(a historical four-direction check, before the five-direction change) and,
using `app.image_utils.similarity` (the same function the app uses),
compared 90 consecutive real frames against one frame known to be at a
hit moment (frame 2515, shown with a "Marvelous!!!" judgment on screen):

- Similarity to that reference formed a clear, repeating **pulse**: it
  rose from a noisy idle baseline (roughly 0.70-0.85) up to 0.94-1.00 for
  about 5 consecutive frames centered on each hit, then dropped back to
  the idle baseline - and this pulse repeated again ~42 frames later
  (~0.7s later, a plausible next note at a moderate tempo).
- This is genuine evidence that the receptor's on-screen appearance
  *does* change detectably around a hit for this skin, and that a
  handful of consecutive frames above a similarity threshold (the
  default 0.90 sits cleanly between the ~0.85 idle ceiling and the
  ~0.94+ pulse) is a realistic signal for the edge-triggered tracker to
  catch - which is exactly why the app is built this way.
- **What this does *not* prove:** I did not verify this pulse specifically
  means "hit" as opposed to some other unrelated animation, since I have
  no independent ground truth (audio, judgment-text OCR, or manual replay
  review) confirming what happened at that exact moment, and I have no
  labeled "miss" moment to test against at all. The bounding box was
  eyeballed from a single screenshot, not calibrated in the app. Treat
  this as evidence the underlying approach is sound, not as a validated
  hit/miss detector for your footage. In the current app, capture fresh
  Active Arrow references and separately teach judgment words; validate both
  against the footage before trusting any `judgement`/`arrow` events.

---

## 8. Troubleshooting

- **"OCR engine: FAILED to load"** - usually means setup didn't finish installing PyTorch/EasyOCR. Re-run `setup.ps1` and check the console for errors.
- **App window opens but stays blank / "No video loaded."`** - you haven't clicked **Open Video...** yet, or the file picker was cancelled.
- **"Could not open video"** - the file may be corrupted, in a codec OpenCV can't decode, or the path contains something unusual. Try a standard H.264 `.mp4` first.
- **I can't see the "Layout profile" box, "Start Analysis" button, or some recognition rows** - the left and right panels of the Video & Regions tab scroll independently once their controls no longer fit the window (this grows as you add more regions, especially several arrow lanes). Hover over the panel and scroll with your mouse wheel, or drag the scrollbar at its right edge, to reach anything below the visible area. The left panel also has a horizontal scrollbar along its bottom edge: drag it to reach **Start Analysis**, **New Session**, and other controls clipped by a narrow window. If controls seem to be missing entirely, scroll the panel before assuming a button doesn't exist.
- **Region drawing does nothing** - make sure **Draw Region** shows **ON** before click-dragging on the canvas.
- **The app seems frozen after drawing a rectangle** - the modal **Label region** dialog must be completed or canceled before interacting with the main window. It opens centered over the app on the same monitor, including displays with negative desktop coordinates.
- **Loading a layout pops up a resolution-mismatch warning** - this is intentional. The layout was calibrated against a different video size than the one currently open. Choose to fit it with uniform scaling and centered padding, or cancel and recalibrate. Coordinates are never silently reused across a resolution change.
- **Playback looks slow/choppy while analysis is running** - numeric OCR runs in the background and skips samples rather than slowing the video to OCR speed. Preview playback skips late frames to stay near source time and reuses video/crop images and unchanged region overlays to reduce rendering overhead. Heavy video decoding/rendering can still cause choppiness. Use frame-stepping for careful calibration. The last OCR-pass time describes recognition latency, not playback speed.
- **Values seem clearly wrong even though the app "accepted" them** - check the raw OCR text shown in the recognition panel next to the accepted value; a consistent misread will still pass the consistency filter (see section 6/7). Try a tighter crop around just the digits, excluding glow/outline effects.
- **Nothing appears in the Events tab** - confirm **Record events** is checked (Events tab) and that you clicked **Start Analysis** (Video & Regions tab, in the scrollable "Analysis" box - see the scrolling note above if you can't find it) - the app never runs OCR or emits events unless analysis is explicitly started. Also note that a continuously-animating on-screen score display can genuinely take a couple dozen frames before two consecutive OCR readings match and the first event is accepted - this is expected, not a bug (see section 6/7).
- **Arrow row requests fresh calibration** - use **Add Active Arrow ref** on either player's matching direction. Old Hit/Miss references are ignored. Also calibrate the player's judgment box before expecting outcomes.
- **Arrow events never fire even after capturing references** - check Active shape similarity and calibration diagnostics against **Arrow shape similarity**. Capture a clear colored incoming note, not a white receptor flash. Ensure the cyan search bounds include its approach. Inspect Empty examples if they suppress real activity.
- **Arrow events fire constantly / seem spurious** - your threshold may be too low, or the idle appearance is naturally close to your reference. Raise the threshold, tighten the crop, or raise "Arrow confirm frames" slightly.

---

## 9. Project layout

```
app/
  config.py          Central tunables (schema version, thresholds, paths)
  models.py           Region / LayoutProfile / Event data structures
  image_utils.py       Base64 encode/decode + generic image similarity (for arrow refs)
  layout_store.py     Save/load layout profiles, explicit resolution checks
  video_source.py     OpenCV video wrapper; detects seeks vs. sequential reads
  ocr_engine.py       EasyOCR wrapper (lazy-loaded, CPU only)
  recognition.py      TemporalValueTracker (score/combo) + ArrowStateTracker (arrows) + parsing/classification
  event_bus.py        Session/sequence bookkeeping, event envelope, JSONL export
  analysis.py         Wires video frames + regions + OCR/image-similarity + recognition + events together
  main.py             Entry point (python -m app.main)
  ui/
    app_window.py     Main window / controller
    video_tab.py       Playback, region drawing, recognition panel (numeric + arrow rows)
    events_tab.py       Events table, export, recording toggle
    region_dialog.py   Dialog for labeling a drawn region (incl. arrow direction)
tests/                 Automated tests (pytest)
layouts/                Saved layout profiles (JSON, incl. arrow reference snapshots) - not gitignored, small/text
sample_videos/           Suggested place to keep local clips (gitignored)
data/                    Suggested default location for JSONL exports (gitignored)
```

---

## 10. Event format

Every recorded event is a flat JSON object with these fields. A
score/combo change looks like:

```json
{
  "schema_version": "1.0",
  "event_id": "b3f8...uuid4",
  "session_id": "1a2b...uuid4",
  "sequence": 4,
  "source_mode": "replay",
  "frame_index": 512,
  "video_time_ms": 8546,
  "observed_at_utc": "2026-09-15T18:22:31.104512+00:00",
  "event_type": "score_changed",
  "player": "P1",
  "payload": {
    "field": "score",
    "region_label": "P1 Score",
    "previous_value": 348711,
    "new_value": 348777
  },
  "confidence": 0.83
}
```

A combo event also uses the same envelope, with `event_type: "combo"` and this
payload:

```json
{
  "field": "combo",
  "region_label": "P1 Combo",
  "value": 15
}
```

Only newly accepted values emit an event; repeated readings do not. Zero is a
valid current combo. Score change events retain their previous/new-value pair.
Update combo consumers from `combo_changed`/`payload.new_value` to
`combo`/`payload.value`, then restart the desktop. Existing queued events are
unchanged; the scoreboard still accepts the legacy combo format and sequences
new and old formats together per player.

A `judgement` event uses the same envelope, with the hit/miss outcome, tier,
and matched directions in the payload instead of previous/new values.
`confidence` holds the judgment reference similarity:

```json
{
  "schema_version": "1.0",
  "event_id": "9c1e...uuid4",
  "session_id": "1a2b...uuid4",
  "sequence": 5,
  "source_mode": "replay",
  "frame_index": 2515,
  "video_time_ms": 41959,
  "observed_at_utc": "2026-09-15T18:22:31.520000+00:00",
  "event_type": "judgement",
  "player": "P1",
  "payload": {
    "field": "judgment",
    "region_label": "P1 Judgment",
    "direction": "Up-Right",
    "outcome": "hit",
    "state": "hit",
    "judgment": "perfect",
    "judgment_id": "shared-judgment-uuid",
    "step_directions": ["Up-Right"],
    "step_arrow_count": 1,
    "evidence": "judgment_and_arrow",
    "judgment_video_time_ms": 41309,
    "correlation_window_ms": 500,
    "correlation_lookback_ms": 500,
    "correlation_lookahead_ms": 0
  },
  "confidence": 0.97
}
```

The arrow was recorded earlier as activity, without waiting for this judgment:

```json
{
  "event_type": "arrow",
  "payload": {
    "field": "arrow",
    "region_label": "P1 Up-Right",
    "direction": "Up-Right",
    "state": "active",
    "evidence": "arrow_activity",
    "activity_video_time_ms": 41309
  }
}
```

This abbreviated example omits the standard envelope fields. Activity never
implies a hit or miss and correlation emits no additional arrow event.
A Miss instead sets
`outcome`/`state`/`judgment` to `"miss"` on the single `judgement` event;
it has no accompanying arrow event.

`video_time_ms` is the position inside the video's own timeline;
`observed_at_utc` is the wall-clock UTC time the event was produced while
running the app - these are deliberately different clocks and are never
conflated. Recorded-video recognition uses `source_mode: "replay"`.
New outgoing event timestamps use ISO 8601 UTC with a `Z` suffix, independent of
the Windows timezone. Relayed registration `createdAt` values with explicit
offsets are converted to UTC while preserving their instant; timezone-less
registration values are rejected rather than guessed. Existing queued events
and history are not rewritten (`+00:00` also denotes UTC). Stored KQL functions
may add an `observed_at_local` display column; that does not change the UTC
event timestamp. JavaScript SQL heartbeat/registration dates serialize in UTC.
Explicit mapping samples use `"synthetic"` and `payload.is_test: true`;
HDMI capture uses `"live"` with capture-relative monotonic timestamps.

---

## 11. Automated tests

`tests/` covers, without needing any real video or OCR model:
- `test_recognition.py` - unreadable readings never zero/reset the accepted value; repeated identical frames emit one event, not many; genuine changes require N consecutive confirmations; a one-frame digit-animation glitch is rejected; `reset()` clears state and causes the next reading to be reported as a change from "unknown".
- `test_arrow_recognition.py` - image-similarity scoring (including a solid-color fallback since correlation is undefined for zero-contrast crops), `classify_arrow_state` (idle when no/insufficient match, never a guess), the edge-triggered `ArrowStateTracker` (fires once per pulse, handles idle->hit->idle->hit and direct hit->miss transitions, rejects single-frame blips when confirm-frames > 1, ignores unreadable frames), and the full `AnalysisSession` pipeline for arrow regions end to end.
- `test_models.py` - arrow region keys are unique per direction; `direction`/`reference_images` round-trip through `to_dict`/`from_dict`.
- `test_event_bus.py` - session/sequence numbering, new-session reset, clear-vs-export behavior, JSONL is one valid JSON object per line.
- `test_layout_store.py` - save/load round trip, the explicit resolution-mismatch error (never silent rescaling), aspect-ratio-preserving region fitting, arrow reference images being resized along with their region, and saving/loading to/from an arbitrary caller-chosen path outside the built-in `layouts/` folder (the Save As / Load From flow).
- `test_video_source.py` - sequential reads are not flagged as seeks; explicit seeks (including backward and to frame 0) are always flagged, using a small synthetically generated video.
- `test_analysis.py` - the full per-frame pipeline (regions -> OCR -> recognition -> events) using a fake OCR engine with scripted text, covering start/stop gating, unreadable frames, seek resets, the recording on/off toggle, and region-list changes resetting tracking.

Run them with `run_tests.ps1`. All 64 tests currently pass.

---

## 12. Later milestones (not implemented yet)

These are described only so scope is clear - none of this exists in the
code yet:

- General OCR of displayed judgment **text** and visible bonuses beyond the optional user-captured judgment image references.
- Tracking individual falling notes as they travel down the track (not just the fixed receptor), including observed color, without assuming what a color means musically.
- Treating consecutive identical judgments as potentially separate events, and leaving uncertain note-to-judgment associations unresolved rather than guessing.
- Player registration events and stable player UIDs linking sessions across plays.

---

## 13. Fabric Eventstream publishing

### Connect

1. In Fabric, publish an Eventstream with a **Custom endpoint** source. Obtain its **Event Hub** SAS connection string, including `EntityPath`. Use a send-only credential where available. Rotate any key that has been exposed.
2. Install dependencies using the existing setup, or run `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` for an existing environment.
3. Open the app's **Events** tab and select **Connect to Fabric...**. Paste the connection string into the masked local prompt once. On Windows it is saved in **Windows Credential Manager**, scoped to your Windows user on this machine, and reused on subsequent connections. It is not written to layouts, the outbox, or Git. Publishing still starts off after every app launch; click **Connect to Fabric...** to enable it. Alternatively, `DDR_EVENTSTREAM_CONNECTION_STRING` in the app process's environment overrides the saved credential and is not copied into the vault. Never paste credentials into chat or commit them.
4. Select **Send mapping sample** to explicitly enqueue a synthetic `score_changed` event (`source_mode: synthetic`, `payload.is_test: true`, score 1000 to 1250). This sample has its own session ID and does not modify recognition state.
5. Enable **Record events** and run analysis. All newly recorded events are sent, including **unconfirmed** `score_observed` events. Connecting does not backfill the current in-memory event table. Check **endpoint acknowledged** and **queued** counts, then verify the event in Fabric's preview or destination table. Sender acknowledgement does not prove downstream table ingestion.

### Delivery and failures

- The official `azure-eventhub` SDK sends JSON via AMQP over secure WebSockets (port 443). `session_id` is the partition key; envelopes, event IDs, source-frame timestamps and original confidence values are preserved. No Azure CLI login is required for this SAS connection.
- Pending JSON is persisted in `data/eventstream-outbox.sqlite3`, which is excluded from Git. Only service-acknowledged events are removed. Network failures retry after 1, 2, 4, ... up to 30 seconds; status shows errors without printing credentials or raw SDK exceptions.
- Delivery is **at least once**, not exactly once: an acknowledgement lost during a crash/network failure can cause a duplicate. Consumers should deduplicate by `event_id`. Do not treat `score_observed` as accepted gameplay scores; use `score_changed` for that.
- **Disconnect Fabric** stops forwarding new events and stops retries. An already in-flight send may finish. Unsent events remain queued and resume when you explicitly reconnect to the same endpoint, including after app restart. The app refuses to redirect a nonempty queue to a different endpoint. Rotating a key for the same endpoint is supported.
- **Forget saved connection** disconnects and removes the app's Windows Credential Manager entry (`DDR Video Event Extractor - Fabric Eventstream`). It does not delete pending events. Use it before entering a rotated key; the next connection prompts again. If you use the environment variable override, update that in your launcher instead. Credential-store failures are reported explicitly; the app never falls back to saving plaintext credentials.
- **Clear Table** and manual **New Session** clear local history/tracking as before; they do not cancel queued deliveries or delete already sent Fabric data. Automatic game-end transitions retain history. **Record events** off stops gameplay events, but session announcements and delivery of existing queued events continue. The explicit mapping-sample button still sends when recording is off.
- Disk/serialization failures are shown as **NOT QUEUED** in the Events tab; affected events remain in the local event table for export. Export before closing if this occurs. A durable queue cannot protect against disk failure. Pending events consume disk space until successfully delivered.
- Closing the app stops new capture and requests publisher shutdown. An interrupted send remains retryable from the durable queue on the next explicitly enabled connection.

Suggested Eventhouse column types: strings for IDs/event type/player/source mode, `long` for sequence/frame/video time, `datetime` for `observed_at_utc`, `real` for confidence, and `dynamic` for `payload`.

## 14. Privacy

Video reading, cropping, OCR and reference images stay local. Publishing is off
on every app launch until explicitly enabled. When enabled, recorded event JSON
(including region labels, player slots, scores, timestamps and session IDs) is
sent to the configured Fabric endpoint. Do not put sensitive information in
region labels. Credentials are supplied only through a masked local prompt or
process environment variable. Connections entered in the prompt are saved only
in the current user's Windows Credential Manager for this machine. Pending
event JSON is stored locally in the outbox.
