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
