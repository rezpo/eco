# Eco - Project Context for AI Agents

## Overview

Eco is a macOS internal audio recorder + AI meeting assistant. It captures system audio via BlackHole (virtual audio driver), transcribes in real-time using Deepgram, takes periodic screenshots of the meeting (still images, not video), and answers questions via LLM using both the transcript and the latest screenshots as context.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  System Audio│────▶│  BlackHole   │────▶│     Eco      │────▶│  Opus/WAV    │
│  (Meet, etc) │     │  (driver)    │     │  (recorder)  │     │  (output)    │
└──────────────┘     └──────┬───────┘     └──────┬───────┘     └──────────────┘
                             │                    │
                             │ screencapture       │ send_external_audio()
                             ▼                    ▼
                      ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
                      │  Screenshots │     │   Sidekick   │────▶│  Deepgram    │
                      │  (.jpg)      │     │  (AI asst)   │     │  (transcribe)│
                      └──────┬───────┘     └──────┬───────┘     └──────────────┘
                             │                    │
                             │ images             │ context
                             ▼                    ▼
                             └───────────▶ ┌──────────────┐
                                           │     LLM      │
                                           │  (MiMo, etc) │
                                           └──────────────┘
```

## Files

| File | Purpose |
|------|---------|
| `eco.py` | Main CLI entry point. Records audio, optionally compresses it to Opus, optionally takes periodic screenshots. Supports `--llm` flag to run sidekick alongside recording. |
| `sidekick.py` | AI meeting assistant. Transcribes audio via Deepgram, answers questions via LLM (with recent screenshots as image context), provides interactive CLI. |
| `llm_client.py` | Abstraction layer for multiple LLM providers (Anthropic, OpenAI, MiMo, MiniMax). Uses OpenAI-compatible API format. |
| `chrome_capture.py` | Captures Chrome windows via the Chrome DevTools Protocol (CDP) instead of macOS's screencapture, so video playing in an occluded Chrome window still captures correctly. Includes a minimal hand-rolled synchronous WebSocket client (no external dependency) and logic to relaunch Chrome with `--remote-debugging-port` when needed. |
| `compress_archive.py` | Standalone utility to batch-convert old WAV recordings on disk to Opus (dry-run by default; verifies duration before deleting originals). Not part of the `eco` package/CLI. |
| `setup.sh` | macOS installer: installs BlackHole 2ch and ffmpeg via Homebrew, then installs the `eco` Python package (pipx or an isolated venv). |
| `requirements.txt` | Python dependencies: sounddevice, numpy, soundfile, deepgram-sdk, anthropic, openai, python-dotenv. |
| `.env` | API keys: DEEPGRAM_API_KEY, MIMO_API_KEY, MIMO_BASE_URL, MIMO_MODEL, etc. |
| `.env.example` | Template for `.env` with all supported providers. |

## CLI Usage

```bash
# Record system audio only (standalone mode needs --start to actually begin)
eco --output --start

# Record + transcribe + LLM (combined mode)
eco --output --llm mimo --start

# Record microphone
eco --input --start

# Record both (system + mic)
eco --both --channels split --start

# Periodic screenshots (full screen or a specific window)
eco --output --screenshots --screenshot-interval 15 --start
eco --output --window "Zoom" --start   # implies --screenshots

# Options
eco --output --duration 120 --output-file meeting.wav --start
eco --output --no-compress --start     # keep the WAV instead of converting to Opus
eco --list-devices
```

Note: without `--llm`, `eco` requires the explicit `--start` flag to begin recording immediately (otherwise it just validates devices/args and exits). With `--llm`, `--start` controls whether recording/transcription begin immediately vs. waiting for the user to type `/start` inside the sidekick CLI.

## Key Technical Details

### Audio Pipeline

- **System capture**: BlackHole 2ch virtual audio device at 48kHz stereo
- **Deepgram transcription**: Batch API (`transcribe_file`), triggered by a dynamic VAD (see below), not a fixed interval
- **Resampling**: 48kHz stereo → 16kHz mono (decimation factor 3) before sending to Deepgram
- **WAV encoding**: PCM 16-bit, 48kHz, stereo (during capture)
- **Compression**: on `stop_recording()`, the WAV is converted to Opus mono (default 32k bitrate) via `ffmpeg` and the original WAV is deleted, unless `--no-compress` or `--keep-wav` is passed (see Audio Compression below)

### Deepgram Integration

The streaming WebSocket API (`listen.v1.connect`) doesn't work reliably with Deepgram SDK v6.1.1. The project uses the batch `transcribe_file` API instead, driven by a lightweight energy-based VAD (`DeepgramTranscriber._process_loop`):

1. Audio chunks accumulate; a dynamic noise floor (10th percentile of recent RMS values) separates speech from silence
2. A phrase is flushed to Deepgram when ~0.5s of trailing silence follows speech, or after ~12s (whichever comes first)
3. The flushed chunk is converted to WAV bytes (with header, needed for format detection) and sent to `client.listen.v1.media.transcribe_file()`
4. Transcript is added to the conversation context and appended to the recording's `.txt` sidecar file
5. `silence_timeout` (default 120s, `--silence-timeout`) tracks *continuous* silence across chunks and auto-quits the sidekick if exceeded

### Screenshot Pipeline

Enabled with `--screenshots` (periodic full-screen captures) or `--window <name/title/id>` (implies `--screenshots`, limited to one window):

1. `Eco._take_screenshots_loop` runs in a daemon thread, writing JPGs to `<output_file_stem>_screenshots/` every `--screenshot-interval` seconds (default 10s)
2. Window lookup/capture uses `screencapture -l<windowID>`; window IDs are resolved via embedded Swift/CoreGraphics snippets run through `subprocess.run(["swift", "-"], ...)` (`get_visible_windows`, `find_window` in `eco.py`)
3. If the targeted window is hidden/minimized/closed, the loop reuses the last successful screenshot (via `shutil.copy2`) instead of falling back to a full-screen capture, so a wrong (unrelated) screen is never captured
4. `Sidekick._handle_query` attaches the two most recent screenshots (as base64 images) to LLM chat requests when screenshots are active

### Chrome Capture (CDP) — why it exists and how it works

**Root cause (confirmed empirically, not theoretical):** when a Chrome window plays video with hardware-accelerated decoding and becomes fully occluded by another window, macOS's window capture (`screencapture -l<id>`, and ScreenCaptureKit too — both were tested) can no longer read the video content correctly: it either fails outright or the video area renders black, because that video surface is composited outside the normal window backing store that those APIs read from. This reproduces with any HTML5 `<video>` in Chrome, not just DRM'd content (tested and ruled out DRM as the cause) — it's specific to Chrome's hardware video compositing path, not to video-in-general (QuickTime Player and a plain `<video>`-less window both capture fine while occluded).

**Fix:** ask Chrome itself for the screenshot via its DevTools Protocol (`Page.captureScreenshot`), which renders the page from inside Chrome without touching the macOS window compositor at all. Verified working (timestamp kept advancing) on a genuinely-occluded window where the classic method failed/blacked out.

Implementation in `chrome_capture.py`:

1. Requires Chrome running with `--remote-debugging-port=9339` (`CHROME_CDP_PORT`) — this flag can only be set at process start.
2. `_CDPConnection` is a minimal synchronous WebSocket client (raw `socket`, hand-rolled RFC6455 framing) — deliberately not using the `websockets` package to avoid pulling asyncio into an otherwise fully synchronous/threaded codebase, and to avoid a new dependency.
3. `find_chrome_tab_for_window(window_bounds)` correlates a macOS `CGWindowID` (from `find_window`/`get_visible_windows` in `eco.py`, which now also report `x`/`y`) to a Chrome tab by **matching on-screen bounds**: it connects to the browser-level CDP endpoint, calls `Browser.getWindowForTarget` for each open tab, and picks the tab whose window bounds match (within `_BOUNDS_TOLERANCE` px). CDP's internal Chrome `windowId` has no relation to the macOS `CGWindowID`, so bounds-matching is the correlation mechanism — there is no direct ID mapping.
4. `Eco._capture_window` (in `eco.py`) tries `chrome_capture.capture_chrome_window_screenshot` first when the target window's owner is "Google Chrome"; any failure (port closed, no bounds match, tab closed) falls back to the classic `screencapture -l<id>` transparently, every capture attempt — no need to restart eco if Chrome gets the debug flag mid-session.
5. `main()` proactively calls `chrome_capture.ensure_chrome_debugging()` when `--window` resolves to a Chrome window at startup (before any recording thread starts, so stdin is still in cooked/normal mode — no conflict with sidekick's raw-mode reader). The interactive `/screencapture` command (already inside sidekick's raw terminal mode) only prints guidance instead of prompting, to avoid stdin contention with the char-by-char CLI reader.
6. **Known real-world hazard, hit during development:** relaunching Chrome means fully quitting it first (`osascript ... quit`), and if the user doesn't have `chrome://settings/onStartup` set to "Continue where you left off", all open tabs are lost (confirmed — this happened once during testing and tabs had to be recovered from history, not session restore). `chrome_will_restore_session()` reads `~/Library/Application Support/Google/Chrome/Default/Preferences` (`session.restore_on_startup == 1`) and `ensure_chrome_debugging` shows a hard warning instead of the generic one when it's not set, before asking for confirmation.
7. `relaunch_chrome_with_debugging` launches the Chrome binary directly (`CHROME_APP_BINARY`, a hardcoded `/Applications/...` path) rather than `open -a ... --args ...` — the latter was tried first and silently failed to pass the flag through (Chrome came back up with the debug port never open), so don't revert to it.

### Audio Compression

`compress_audio()` in `eco.py` runs at the end of every recording:

- Requires `ffmpeg` on `PATH` (installed by `setup.sh` via Homebrew); if missing, it prints a warning and keeps the WAV — it never crashes the recording
- Converts to Opus mono at `--audio-bitrate` (default `32k`) — roughly a 50x size reduction for voice with no perceptible loss for transcription/listening
- Deletes the original WAV unless `--keep-wav` is passed; `--no-compress` skips compression entirely
- `compress_archive.py` applies the same conversion in bulk to historical WAV files already on disk, verifying duration parity (via `ffprobe`) before ever deleting an original

### LLM Integration

All LLM providers use OpenAI-compatible API format via `openai` SDK:

- **MiMo**: `https://api.xiaomimimo.com/v1` (model: `mimo-v2.5-pro`)
- **Anthropic**: Direct SDK
- **OpenAI**: Direct SDK
- **MiniMax**: OpenAI-compatible endpoint

### Combined Mode (`--llm`)

When `--llm` is specified:

1. `Sidekick` created with `external_audio=True` (no own audio capture — `sidekick.py`'s own `_start_audio_capture` path, used only when running `sidekick.py` standalone, is never invoked in this mode)
2. Recording starts in a daemon thread (immediately if `--start`, otherwise once the user types `/start`)
3. Audio callback in `Eco._start_stream_recording`/`_start_mixed_recording` forwards data to sidekick via `send_external_audio()`
4. Sidekick converts stereo→mono, resamples 48kHz→16kHz, buffers for Deepgram's VAD-driven pipeline
5. Interactive CLI runs in the main thread in raw terminal mode, with a status bar, slash-command menu with autocomplete, and the full command set (see Commands Reference below)
6. Status bar refreshes every second in place (no separate timer thread)
7. On exit: `grabador.recording = False`, `stop_recording()` called (idempotent), `sys.exit(0)`

### Signal Handling

- In standalone mode: `SIGINT` sets `grabador.recording = False`
- In combined mode (`--llm`): `SIGINT` is reset to default (`SIG_DFL`), sidekick handles Ctrl+C via `KeyboardInterrupt`

### Idempotent Stop

`stop_recording()` uses a `_stopped` flag to prevent duplicate "Grabación Completada" messages when called from both the `finally` block and `_start_with_sidekick`.

## Environment Variables

```
DEEPGRAM_API_KEY=         # Required for transcription
MIMO_API_KEY=             # MiMo LLM
MIMO_BASE_URL=https://api.xiaomimimo.com/v1
MIMO_MODEL=mimo-v2.5-pro
ANTHROPIC_API_KEY=        # Optional
OPENAI_API_KEY=           # Optional
MINIMAX_API_KEY=          # Optional
```

## macOS Requirements

- macOS 10.15+
- Python 3.9+
- Homebrew
- BlackHole 2ch (audio driver)
- ffmpeg (optional; installed by `setup.sh` — only needed for Opus compression, absence degrades gracefully to WAV)
- Xcode Command Line Tools (provides the `swift` binary; only needed for `--window`/`/window-list`/`/screencapture`, since window enumeration/lookup shells out to `swift -` with an embedded Cocoa/CoreGraphics snippet — see `get_visible_windows`/`find_window` in `eco.py`)
- Microphone permission for Terminal app

## Known Issues

- Deepgram WebSocket streaming API (v1/v2) produces empty transcripts with SDK v6.1.1. Batch API works.
- Audio level from BlackHole is low (~-36 dB max). Deepgram still transcribes but accuracy may vary.
- `PaMacCore (AUHAL)` warnings from sounddevice are harmless macOS audio errors.
- Window-targeted screenshots (`--window`) silently return no matches if `swift` isn't available (Xcode Command Line Tools not installed) — falls through to "window not found" handling rather than raising.
- Chrome windows playing video, if fully occluded, break the classic `screencapture`/ScreenCaptureKit path (see "Chrome Capture (CDP)" above) — mitigated for Chrome specifically, unmitigated for other apps whose video also uses hardware-overlay compositing this way (none observed so far besides Chrome; QuickTime Player was tested and is unaffected).
- Relaunching Chrome to enable remote debugging fully quits it first; if `chrome://settings/onStartup` isn't "Continue where you left off", open tabs are lost (not just unfocused — actually closed, recoverable only via History, not session restore). `ensure_chrome_debugging` checks for this and warns hard before prompting, but the user can still opt to proceed and lose tabs.

## Commands Reference (Sidekick Interactive CLI)

| Command | Action |
|---------|--------|
| `/context` | Show conversation context |
| `/clear-context` (alias `/clear`) | Clear context |
| `/output-dir <path>` | Change output directory for audio/transcript files |
| `/start` | Start recording and transcription |
| `/pause` | Pause recording and transcription |
| `/resume` | Resume recording and transcription |
| `/stop` (alias `/stop-listening`) | Stop recording and transcription |
| `/silence-timeout <seconds>` | Configure the silence auto-quit timeout (0 disables it) |
| `/window-list` | List visible macOS windows and their IDs |
| `/screencapture` | Interactively pick (or change) which window to capture periodically |
| `/help` | Show help |
| `/quit` (alias `/exit`) | Exit sidekick |
| Any other text | Ask the LLM a question (with recent transcript context + latest screenshots, if active) |
