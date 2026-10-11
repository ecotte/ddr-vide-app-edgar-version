# DDR Video Event Extractor - Milestone 1 (recorded video)

A local Windows desktop application that watches a **recorded** Dance
Dance Revolution gameplay video, lets you calibrate on-screen regions
(score, combo, and per-lane arrow receptors), reads the score/combo
regions with OCR and the arrow regions by comparing them to snapshots you
capture yourself, and turns *confirmed* changes into structured,
timestamped events you can review in a table and export as JSON Lines.
Everything runs locally - no video, screenshot, or recognized text ever
leaves your machine.

This is **milestone 1** only: recorded-video playback, region calibration,
OCR-based score/combo recognition, calibration-based arrow hit/miss
detection, and event export. Live HDMI capture, judgment-text recognition,
and streaming to Microsoft Fabric are **not** implemented yet - see
"Later milestones" at the end of this document.

---

## 1. What this does and does not do today

**Does:**
- Open a local `.mp4`/`.avi`/`.mov`/`.mkv` file, play/pause/step through it frame by frame, and seek with a slider.
- Let you draw rectangles on the video frame and label each one with a player (P1/P2/Unknown) and a field kind: `score`, `combo`, or `arrow` (one box per arrow direction - Up/Down/Left/Right - per player).
- Save/load named layout profiles (JSON files under `layouts/`), tied to a specific source resolution, with an explicit warning (never a silent guess) if you load a layout onto a differently-sized video. If you continue, regions are fitted with one uniform scale and centered so different aspect ratios do not distort them. Arrow-lane reference snapshots (see below) travel with the layout and are resized by the same scale.
- Run OCR (EasyOCR, CPU) on each `score`/`combo` region once you click **Start Analysis**. Recognition runs on a single-threaded background worker so OCR does not saturate the CPU or block source-rate playback; when OCR is busy, analysis samples the latest available frames rather than slowing the video.
- For `arrow` regions: let you capture multiple "Hit" and/or "Miss" example snapshots directly from the video (pause on a frame that clearly shows that state, click "Add Hit ref"/"Add Miss ref"), then compare every later frame's crop against all those examples using generic image similarity - never a built-in assumption about what a hit or miss looks like for your skin.
- Show, per region, the cropped image, the raw OCR text or arrow-classification state, and the currently accepted value/state.
- Apply a configurable temporal-consistency filter for score/combo (a value is only "accepted" after N consecutive matching frames) and a separate edge-triggered debouncer for arrows (a hit/miss flash is reported once per pulse, since it's often brief rather than steady) - see section 6.
- Reject combo decreases and implausible jumps caused by arrows completely obscuring the combo digits, retaining the last accepted combo until a plausible higher value becomes visible again.
- Record `score_changed` / `combo_changed` / `arrow_hit` / `arrow_miss` events in a table, with recording on/off and clear-table controls.
- Export recorded events to a local `.jsonl` file (one JSON object per line).

**Does not do (by design, for this milestone):**
- No live/HDMI capture.
- No judgment-tier text recognition (e.g. "Marvelous"/"Great"/"Miss" as printed text), life gauge, or "final results" recognition. Arrow hit/miss is detected from the arrow receptor's appearance, not from reading judgment text.
- No tracking of individual falling notes as they travel down the track - only the fixed receptor region per lane.
- No network publishing of any kind (Fabric Eventstream comes in a later milestone, disabled by default even then).
- No claim that OCR or arrow-similarity readings are accurate. See section 7 for real numbers measured against a real clip.

---

## 2. Requirements

- Windows 10/11.
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

---

## 4. Running the app

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

### 5a. Score / combo (OCR)

1. **Open Video...** and pick your `.mp4` file. The video's resolution, fps, and frame count appear next to the button. The first frame is displayed.
2. Use **Step >|** / **|< Step** to move frame by frame, or drag the slider, to find a moment where the score and combo are clearly on screen (not a menu/loading screen - the app never assumes every screen is gameplay, so pick a frame where they're actually visible).
3. Click **Draw Region: OFF** to turn it **ON**. Click-drag a rectangle around the score digits on the canvas. When you release the mouse, a dialog asks for a label, player (P1/P2/Unknown), and field kind (`score`/`combo`/`arrow`). Repeat for combo. Turn Draw Region back **OFF** when done so you don't accidentally start new rectangles while browsing the video.
4. Adjust **Consistency frames** if needed (default 1). Numeric OCR runs in the background and may sample changing values, so one readable sample is accepted by default. Raise this only when repeated OCR noise is more important than capturing fast score/combo changes - see section 6.

### 5b. Arrow lanes (hit/miss, per direction per player)

1. With **Draw Region: ON**, draw a rectangle around one arrow's receptor (the fixed on-screen target the falling notes travel toward), and pick field kind **arrow** plus the matching **Arrow direction** (Up/Down/Left/Right). Repeat for each of the 4 directions, and again for the other player if needed (up to 8 boxes total for 2 players).
2. In the recognition panel on the right, each arrow region gets its own row with **Add Hit ref** / **Add Miss ref** buttons. Step or seek the video to frames that clearly show that lane in each state and add as many examples as needed. Classification uses the best match across all captured examples. **Clear Hit refs** and **Clear Miss refs** remove all examples for that state. You do not need both states - if your skin doesn't visibly distinguish misses at the receptor, only add hit references and misses simply won't be reported for that lane (never guessed).
3. Adjust **Arrow similarity threshold** (default 0.90; higher = stricter/fewer false positives) and **Arrow confirm frames** (default 1, since real flashes are often brief) if needed - see section 6.
4. Reference snapshots are saved as part of the region and travel with **Save Layout** / **Load Layout**, so you only need to capture them once per skin/resolution.

### 5c. Running analysis and reviewing events

1. (Recommended) Give the layout a name in **Layout profile > Name** and click **Save Layout** so you don't have to redo calibration next time - this always saves into the app's built-in `layouts/` folder, keyed by that name, and shows up in the **Load** dropdown. If you'd rather choose exactly where the file goes (e.g. a USB drive, a project folder, alongside the video itself), use **Save Layout As...** instead, which opens a normal Windows file-save dialog defaulting to the `layouts/` folder. To load a layout saved that way (or from anywhere else on disk), use **Load Layout From...**, which opens a file-open dialog - as opposed to **Load Layout**, which only loads from the Name dropdown of layouts saved in the built-in folder. Loading a saved layout on a video of a different resolution will show an explicit prompt asking whether to fit the coordinates and arrow reference snapshots. The fit uses one uniform scale and centers the old coordinate space in the new frame, so wide, square, and portrait videos do not distort regions. It will never silently apply the wrong coordinates, whichever way you loaded it.
2. Click **Start Analysis**. Now step or play through the video. The right-hand panel shows, per region, the current crop and either (score/combo) raw OCR text/candidate/accepted value, or (arrow) the observed state (idle/hit/miss), similarity score, and reference thumbnails, plus a status line.
3. Switch to the **Events** tab to see `score_changed` / `combo_changed` / `arrow_hit` / `arrow_miss` rows appear as they're confirmed. Use **Record events** to pause recording without losing calibration, **Clear Table** to wipe the recorded/exportable list, and **Export to JSONL...** to save everything currently in the table to a file.
4. **New Session** resets the session ID, sequence counter, and all recognition state - use it if you want a clean slate on the same video without reloading it. Loading a different video or seeking (slider, or the frame-back button) also resets recognition state automatically, so a jump in the video never gets misread as a huge score change or a phantom arrow hit.

---

## 6. Recognition rules, explained plainly

**Score / combo:**
- **An unreadable frame never becomes zero.** If OCR can't read a region on a given frame (blocked, blank, format doesn't look like digits), the previously accepted value is simply kept until a new value is confirmed. You'll see "unreadable this frame (previous accepted value kept)" in the panel.
- **Accepted scores never decrease.** Score OCR segments fixed-height digit glyphs so commas and other short punctuation are excluded, rejects low-confidence transition frames, and retains the previous score whenever a lower reading appears.
- **Accepted combos never decrease.** Lower readings and implausibly large jumps are treated as arrow occlusion or OCR noise, so they do not replace the accepted combo or emit events.
- **A value only becomes "accepted" (and only then emits an event) after N analyzed samples agree**, where N is the "Consistency frames" setting. The default is 1 because background OCR samples frames while score and combo can change rapidly. Raising N rejects more transient OCR errors but can prevent events entirely when each sampled frame contains a newer value. Combo readings also have a separate plausibility check that rejects unrealistic jumps caused by arrows obscuring the digits.
- **Important limitation the temporal filter cannot fix:** if OCR is *consistently* wrong for the same digits (a stable misread, not a one-frame glitch), the consistency filter will still "accept" that wrong value, because it only rejects readings that fail to repeat - it cannot know the true value. See section 7 for a real example of this happening. This is why the app always shows you the raw OCR text next to the accepted value, so you can visually sanity-check it.

**Arrow lanes:**
- **Classification only ever compares against snapshots you captured** from your own footage - there is no built-in idea of what a "hit" or "miss" looks like. If neither a Hit nor a Miss reference has been captured for a lane, it is always reported as idle (never a guess), and the panel tells you why.
- **A frame is classified "idle"** whenever its similarity to your best-matching reference falls below the **Arrow similarity threshold** - "idle" is the honest default, not a third guess.
- **Because a real hit/miss flash is often only visible for a few frames**, the arrow tracker is edge-triggered rather than a stability filter: it fires one `arrow_hit`/`arrow_miss` event the moment the classified state changes away from idle (or changes directly from one active state to another), and stays quiet while that same state persists, so a multi-frame flash still only produces one event.
- **"Arrow confirm frames"** (default 1) can require a state to be seen for more than one frame before it's confirmed, if single-frame noise is producing false events - but raising it risks missing genuinely brief flashes, the same trade-off as consistency frames above.
- **Similarity scores are a generic image-correlation measure** (grayscale normalized cross-correlation, with a mean-brightness fallback for near-flat/solid-color crops), not a calibrated accuracy probability, and carry no assumption about what any particular color or animation means.

**Both:**
- **Every accepted change emits exactly one event**, not one per frame it stays on screen.
- **Recognition state is reset** whenever you seek/jump in the video, load a different video, edit the region list, capture/clear an arrow reference, or click New Session - never carried over incorrectly across a discontinuity.
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
approximate bounding box around the "Up" arrow receptor in `p1.mp4` and,
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
- **Playback looks slow/choppy while analysis is running** - expected; OCR dominates per-frame time (see section 7). Use frame-stepping for careful calibration, and accept that full-clip analysis takes longer than the clip's own length.
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
    "region_label": "P1 Up",
    "direction": "Up",
    "state": "hit"
  },
  "confidence": 0.97
}
```

`video_time_ms` is the position inside the video's own timeline;
`observed_at_utc` is the wall-clock UTC time the event was produced while
running the app - these are deliberately different clocks and are never
conflated. `source_mode` is always `"replay"` in this milestone; `"live"`
and `"synthetic"` are reserved for later milestones/tests and are never
used for real recorded-video output.

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
- Live HDMI capture as a second `source_mode` alongside recorded-video replay.
- Publishing events to a Microsoft Fabric Eventstream Custom endpoint via the official `azure-eventhub` SDK, kept independent of capture, with a durable SQLite outbox, retries, stable event IDs across retries, and visible queue status (no exactly-once delivery promise).
- Publishing disabled by default until explicitly configured and enabled; credentials read only from environment variables, excluded from Git, never logged or requested in chat.

---

## 13. Privacy

All video reading, cropping, OCR, and event storage happen locally on
your machine. Nothing is uploaded anywhere in this milestone - there is
no network code in the app at all yet.
