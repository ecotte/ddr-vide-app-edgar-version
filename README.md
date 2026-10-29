# DDR Video Event Extractor - Recorded video and HDMI capture

See the [architecture diagram and data-flow guide](docs/architecture.md) for the
desktop pipeline, both publishers, Rayfin hosting, Fabric items, authentication,
storage, and latency boundaries. - Milestone 1 (recorded video)

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
tab to run. The publisher and viewer each wait one second after the previous
request completes; latency is not guaranteed to be sub-second.
Sharing is off at startup, excludes synthetic samples, and does not change
the existing durable Fabric publishing path. - Milestone 1 (recorded video)

The same Rayfin app includes a shared-device **Player check-in kiosk** at
`#register`. An organizer signs in once (and again if the Microsoft session
expires); players generate a Fabric-themed name and unique six-digit PIN without
their own Microsoft accounts. They photograph the saved identity screen.
Registrations link to the current game in Rayfin SQL and are relayed through
the desktop's durable Eventstream outbox as `user.register`; PINs stay out of
telemetry. See the [kiosk setup and delivery rules](scoreboard/README.md#player-registration-kiosk).
Station URLs require `?player=1#register` or `?player=2#register`. Each game has
one registration slot per player, including special entries. In the desktop,
**New Session** emits `session_end` for the announced old game before emitting
`new_session` for a fresh GUID and two empty registration slots.

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
and optional Fabric/live-scoreboard publishing. Judgment-text recognition
remains future work - see "Later milestones" at the end of this document.

---

## 1. What this does and does not do today

**Does:**
- Open a local `.mp4`/`.avi`/`.mov`/`.mkv` file, play/pause/step through it frame by frame, and seek with a slider.
- Select **HDMI input**, choose a named HDMI-to-USB capture device such as **Hagibis**, and analyze its live frames without recording a video file.
- Let you draw rectangles on the video frame and label each one with a player (P1/P2/Unknown) and a field kind: `score`, `combo`, or `arrow` (one box per direction - Up-Right/Up-Left/Down-Right/Down-Left/Center - per player), plus a shared `game_end` indicator.
- Save/load named layout profiles (JSON files under `layouts/`), tied to a specific source resolution, with an explicit warning (never a silent guess) if you load a layout onto a differently-sized video. If you continue, regions are fitted with one uniform scale and centered so different aspect ratios do not distort them. Arrow-lane reference snapshots (see below) travel with the layout and are resized by the same scale.
- Run OCR (EasyOCR, CPU) on each `score`/`combo` region once you click **Start Analysis**. Recognition runs on a single-threaded background worker without blocking source-rate playback. After each job it immediately processes the newest available frame, using a retained neighboring frame only when a result actually needs confirmation. It never builds an unbounded backlog.
- Teach score-digit references with one input: pause on a clear score, click **Teach score...**, and enter all visible digits. References support background OCR verification; they never emit score events on their own. Advance/play to analyze new frames after teaching. Save the layout to retain the samples.
- For `arrow` regions: let you capture multiple "Hit" and/or "Miss" example snapshots directly from the video (pause on a frame that clearly shows that state, click "Add Hit ref"/"Add Miss ref"), then compare every later frame's crop against all those examples using generic image similarity - never a built-in assumption about what a hit or miss looks like for your skin.
- Show, per region, the cropped image, the raw OCR text or arrow-classification state, and the currently accepted value/state.
- Apply a configurable temporal-consistency filter for score/combo (a value is only "accepted" after N consecutive matching frames) and a separate edge-triggered debouncer for arrows (a hit/miss flash is reported once per pulse, since it's often brief rather than steady) - see section 6.
- Reject isolated combo decreases and implausible upward jumps caused by arrow occlusion. Repeated high-confidence lower readings can correct a falsely high accepted combo.
- Record `new_session` / `score_observed` / `score_changed` / `combo_changed` / `arrow_hit` / `arrow_miss` events in a table, with gameplay recording on/off and clear-table controls.
- Export recorded events to a local `.jsonl` file (one JSON object per line).
- Optionally publish recorded event JSON to a Fabric Eventstream custom endpoint, using SAS authentication, a durable local queue, background retries, and visible delivery status.

**Does not do (by design, for this milestone):**
- No live rewind, seeking, or audio capture. An HDMI output port on a laptop is not a capture input; use a Windows-compatible HDMI-to-USB capture device.
- No judgment-tier text recognition (e.g. "Marvelous"/"Great"/"Miss" as printed text), life gauge, or "final results" recognition. Arrow hit/miss is detected from the arrow receptor's appearance, not from reading judgment text.
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

The first live frame starts the preview. Analysis remains off until you click
**Start Analysis**. Use **Freeze preview** to hold a frame for drawing regions,
teaching digits, or capturing arrow/game-end references; **Resume live** jumps
to the current feed, not buffered footage. Previously submitted OCR may still
complete while the preview is frozen. Live input has no frame stepping, rewind,
or seek, and does not capture audio or save a video recording.

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

With this filter enabled, combo OCR first tries recognition directly on the crop, avoiding the expensive text-detection stage. It uses that result only for a single digits-only reading within the combo length limit and with at least 0.90 confidence. Otherwise it falls back to the existing detector on the same filtered crop. Confidence is never increased artificially, and normal confirmation and jump checks still apply. Clear crops can be much faster; obstructed or uncertain crops still incur detector latency, and either OCR path can make mistakes. Unfiltered combos and scores keep their existing OCR paths.

Combo OCR allows the engine to recognize surrounding words such as "combo", then ignores text and mixed letter/number detections instead of forcing letters into digits. Spaces between digits are removed (`6 5` becomes `65`), but letters are never substituted or stripped out of mixed detections to manufacture a number. This applies with and without the yellow filter, including the fast path. A mixed detection is left unreadable until a clearer frame separates the number from the text; OCR can still mistake a letter for a digit. Score glyph OCR retains its digit allowlist.

For fresher combo updates, enable **Fast live combo** in that region's card and **Save Layout**. This opt-in mode runs recognition directly on the crop and never invokes slow text detection, even when the reading is uncertain. It works with or without the yellow filter. Readable candidates that differ from the accepted value are labeled **unconfirmed**; readings below 0.90 confidence cannot replace the accepted combo or emit combo-change events. Non-numeric/mixed results remain unreadable with a visible explanation. Existing jump and correction rules still apply. Keep the crop tight around the digits: this mode can miss readings the full detector would recover and can still make confident mistakes. Disable it to restore detector fallback. Old layouts default to off; saving/loading or rescaling preserves the choice. Toggling clears pending evidence without erasing accepted values or events. Score OCR remains unchanged and still contributes to the worker's total latency.

### 5b. Arrow lanes (hit/miss, per direction per player)

1. With **Draw Region: ON**, draw a rectangle around one arrow's receptor (the fixed on-screen target the falling notes travel toward), and pick field kind **arrow** plus the matching **Arrow direction** (Up-Right/Up-Left/Down-Right/Down-Left/Center). Repeat for each of the 5 directions, and again for the other player if needed (up to 10 arrow boxes total for 2 players).
2. In the recognition panel on the right, each arrow region gets its own row with **Add Hit ref** / **Add Miss ref** buttons. Step or seek the video to frames that clearly show that lane in each state and add as many examples as needed. Classification uses the best match across all captured examples. **Clear Hit refs** and **Clear Miss refs** remove all examples for that state. You do not need both states - if your skin doesn't visibly distinguish misses at the receptor, only add hit references and misses simply won't be reported for that lane (never guessed).
3. Adjust **Arrow similarity threshold** (default 0.90; higher = stricter/fewer false positives) and **Arrow confirm frames** (default 1, since real flashes are often brief) if needed - see section 6.
4. Reference snapshots are saved as part of the region and travel with **Save Layout** / **Load Layout**, so you only need to capture them once per skin/resolution.

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
2. Click **Start Analysis**. Now step or play through the video. The right-hand panel shows, per region, the current crop and either (score/combo) raw OCR text/candidate/accepted value, or (arrow) the observed state (idle/hit/miss), similarity score, and reference thumbnails, plus a status line.

   Recognition is arranged in two compact columns: **Player 1** on the left and **Player 2** on the right. Each card stacks its aspect-ratio-preserving crop preview above its readings and wraps long status messages. Teaching, yellow-digit filters, and arrow-reference controls remain in the matching player's card. Regions assigned to **Unknown** appear in a separate section below both columns. Scroll the recognition panel to reach additional cards.

   To reduce playback overhead, unchanged arrow cards refresh their crop, similarity, and pending-count display at most ten times per second. Arrow-state changes, errors, unreadable crops, and tracking resets update immediately. This only paces the recognition panel: arrow analysis and event recording still run on every displayed video frame, and completed score/combo OCR results are displayed immediately.

   Each numeric card shows **OCR frame** and its video timestamp. Its crop and readings belong to that completed OCR job, not necessarily the frame currently playing on the left. OCR latency still applies, but unnecessary confirmation passes no longer hold the panel on older footage; pausing lets the worker catch up to the final displayed frame. Confirmation jobs process only pending fields, so confirming a score does not rerun unrelated combo OCR on the older frame.
3. Switch to the **Events** tab to see `new_session` / `score_changed` / `combo_changed` / `arrow_hit` / `arrow_miss` rows appear as they're confirmed. Use **Record events** to pause gameplay recording without losing calibration, **Clear Table** to wipe the recorded/exportable list, and **Export to JSONL...** to save everything currently in the table to a file.
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
- **Accepted combos can recover downward.** A lower value needs at least two matching readings, each with OCR confidence of at least 0.90, on distinct advancing frames within two seconds. "Consistency frames" above two raises this requirement. A different candidate, low-confidence reading, or unreadable frame restarts confirmation; duplicate/old frames cannot add evidence. A single lower reading keeps the accepted combo unchanged. Confirmed decreases update the displayed accepted value and emit a normal `combo_changed` event with the old and new values for the table, JSONL export, and Fabric delivery. Existing events are not rewritten. This also allows genuine combo resets; repeated confidently wrong readings can still be accepted.
- **High-confidence combos can advance immediately.** With the default consistency setting, OCR confidence of at least 0.90 allows a single plausible combo reading to establish or advance the accepted value. Lower-confidence, unreadable, or implausible upward-jump readings are rejected. A confidently wrong initial reading can still establish a bad baseline; confidence is not a guarantee of correctness.
- **Confirmation depends on the field and confidence.** "Consistency frames" remains a configurable minimum: qualifying score/combo initial readings and increases may use 1; lower-confidence scores and all downward corrections require at least 2. Raising the setting above 1 also delays high-confidence score and combo readings. While OCR runs, the first neighboring frame and newest displayed frame are retained. The neighbor is processed only for pending confirmation; accepted, rejected, and unreadable results go directly to the newest frame. After one confirmation pass the worker returns to the newest frame rather than chaining old neighbors. This bounds the waiting buffer to two frames; EOF also retains its final-frame safeguard. Reprocessing a paused frame never adds evidence. Evidence expires after two seconds of video time and is restarted by a different candidate or invalid reading. Background OCR does not run on every source frame, so events can be delayed and brief changes missed. Preview playback remains independent.
- **Important limitation the temporal filter cannot fix:** if OCR is *consistently* wrong for the same digits (a stable misread, not a one-frame glitch), the consistency filter will still "accept" that wrong value, because it only rejects readings that fail to repeat - it cannot know the true value. See section 7 for a real example of this happening. This is why the app always shows you the raw OCR text next to the accepted value, so you can visually sanity-check it.

**Arrow lanes:**
- **Classification only ever compares against snapshots you captured** from your own footage - there is no built-in idea of what a "hit" or "miss" looks like. If neither a Hit nor a Miss reference has been captured for a lane, it is always reported as idle (never a guess), and the panel tells you why.
- **A frame is classified "idle"** whenever its similarity to your best-matching reference falls below the **Arrow similarity threshold** - "idle" is the honest default, not a third guess.
- **Because a real hit/miss flash is often only visible for a few frames**, the arrow tracker is edge-triggered rather than a stability filter: it fires one `arrow_hit`/`arrow_miss` event the moment the classified state changes away from idle (or changes directly from one active state to another), and stays quiet while that same state persists, so a multi-frame flash still only produces one event.
- **"Arrow confirm frames"** (default 1) can require a state to be seen for more than one frame before it's confirmed, if single-frame noise is producing false events - but raising it risks missing genuinely brief flashes, the same trade-off as consistency frames above.
- **Similarity scores are a generic image-correlation measure** (grayscale normalized cross-correlation, with a mean-brightness fallback for near-flat/solid-color crops), not a calibrated accuracy probability, and carry no assumption about what any particular color or animation means.

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

**Arrow lanes:** I did not have a calibrated layout with real Hit/Miss
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
  hit/miss detector for your footage - you still need to capture real
  Hit/Miss references yourself and check the raw similarity numbers in
  the panel before trusting any `arrow_hit`/`arrow_miss` events.

---

## 8. Troubleshooting

- **"OCR engine: FAILED to load"** - usually means setup didn't finish installing PyTorch/EasyOCR. Re-run `setup.ps1` and check the console for errors.
- **App window opens but stays blank / "No video loaded."`** - you haven't clicked **Open Video...** yet, or the file picker was cancelled.
- **"Could not open video"** - the file may be corrupted, in a codec OpenCV can't decode, or the path contains something unusual. Try a standard H.264 `.mp4` first.
- **I can't see the "Layout profile" box, "Start Analysis" button, or some recognition rows** - the left and right panels of the Video & Regions tab scroll independently once their controls no longer fit the window (this grows as you add more regions, especially several arrow lanes). Hover over the panel and scroll with your mouse wheel, or drag the scrollbar at its right edge, to reach anything below the visible area. If controls seem to be missing entirely, this is almost always the cause - scroll down before assuming a button doesn't exist.
- **Region drawing does nothing** - make sure **Draw Region** shows **ON** before click-dragging on the canvas.
- **Loading a layout pops up a resolution-mismatch warning** - this is intentional. The layout was calibrated against a different video size than the one currently open. Choose to fit it with uniform scaling and centered padding, or cancel and recalibrate. Coordinates are never silently reused across a resolution change.
- **Playback looks slow/choppy while analysis is running** - numeric OCR runs in the background and skips samples rather than slowing the video to OCR speed. Preview playback skips late frames to stay near source time and reuses video/crop images and unchanged region overlays to reduce rendering overhead. Heavy video decoding/rendering can still cause choppiness. Use frame-stepping for careful calibration. The last OCR-pass time describes recognition latency, not playback speed.
- **Values seem clearly wrong even though the app "accepted" them** - check the raw OCR text shown in the recognition panel next to the accepted value; a consistent misread will still pass the consistency filter (see section 6/7). Try a tighter crop around just the digits, excluding glow/outline effects.
- **Nothing appears in the Events tab** - confirm **Record events** is checked (Events tab) and that you clicked **Start Analysis** (Video & Regions tab, in the scrollable "Analysis" box - see the scrolling note above if you can't find it) - the app never runs OCR or emits events unless analysis is explicitly started. Also note that a continuously-animating on-screen score display can genuinely take a couple dozen frames before two consecutive OCR readings match and the first event is accepted - this is expected, not a bug (see section 6/7).
- **Arrow row says "No hit/miss reference captured yet"** - expected until you click **Capture as Hit**/**Capture as Miss** on that row at least once; nothing is guessed in the meantime.
- **Arrow events never fire even after capturing references** - check the live "similarity" number in the panel against your **Arrow similarity threshold**; if real frames never get close to your captured reference, try a tighter/cleaner crop, a lower threshold, or re-capturing the reference from a clearer example frame.
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

An arrow hit/miss event uses the same envelope, with `direction` and
`state` in the payload instead of previous/new values, and `confidence`
holding the similarity score to the matched reference:

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
  "event_type": "arrow_hit",
  "player": "P1",
  "payload": {
    "field": "arrow",
    "region_label": "P1 Up-Right",
    "direction": "Up-Right",
    "state": "hit"
  },
  "confidence": 0.97
}
```

`video_time_ms` is the position inside the video's own timeline;
`observed_at_utc` is the wall-clock UTC time the event was produced while
running the app - these are deliberately different clocks and are never
conflated. Recorded-video recognition uses `source_mode: "replay"`.
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

- Recognizing displayed judgment **text** (e.g. "Marvelous!!!" as printed on screen, as opposed to the receptor-flash hit/miss detection this milestone adds) and visible bonuses.
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
