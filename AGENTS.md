# Eco - Project Context for AI Agents

## Overview

Eco is a macOS internal audio recorder + AI meeting assistant. It captures system audio via BlackHole (virtual audio driver), transcribes in real-time using Deepgram, and answers questions via LLM.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  System Audio│────▶│  BlackHole   │────▶│     Eco      │────▶│  WAV File    │
│  (Meet, etc) │     │  (driver)    │     │  (recorder)  │     │  (output)    │
└──────────────┘     └──────────────┘     └──────┬───────┘     └──────────────┘
                                                  │
                                                  │ send_external_audio()
                                                  ▼
                                           ┌──────────────┐     ┌──────────────┐
                                           │   Sidekick   │────▶│  Deepgram    │
                                           │  (AI asst)   │     │  (transcribe)│
                                           └──────┬───────┘     └──────────────┘
                                                  │
                                                  │ context
                                                  ▼
                                           ┌──────────────┐
                                           │     LLM      │
                                           │  (MiMo, etc) │
                                           └──────────────┘
```

## Files

| File | Purpose |
|------|---------|
| `eco.py` | Main CLI entry point. Records audio to WAV. Supports `--llm` flag to run sidekick alongside recording. |
| `sidekick.py` | AI meeting assistant. Transcribes audio via Deepgram, answers questions via LLM, provides interactive CLI. |
| `llm_client.py` | Abstraction layer for multiple LLM providers (Anthropic, OpenAI, MiMo, MiniMax). Uses OpenAI-compatible API format. |
| `setup.sh` | macOS installer: installs BlackHole 2ch via Homebrew and Python dependencies. |
| `requirements.txt` | Python dependencies: sounddevice, numpy, soundfile, deepgram-sdk, anthropic, openai, python-dotenv. |
| `.env` | API keys: DEEPGRAM_API_KEY, MIMO_API_KEY, MIMO_BASE_URL, MIMO_MODEL, etc. |
| `.env.example` | Template for `.env` with all supported providers. |

## CLI Usage

```bash
# Record system audio only
eco --output

# Record + transcribe + LLM (combined mode)
eco --output --llm mimo

# Record microphone
eco --input

# Record both (system + mic)
eco --both --channels split

# Options
eco --output --duration 120 --output-file meeting.wav
eco --list-devices
```

## Key Technical Details

### Audio Pipeline

- **System capture**: BlackHole 2ch virtual audio device at 48kHz stereo
- **Deepgram transcription**: Batch API (`transcribe_file`) with 5-second buffer intervals
- **Resampling**: 48kHz stereo → 16kHz mono (decimation factor 3) before sending to Deepgram
- **WAV encoding**: PCM 16-bit, 48kHz, stereo

### Deepgram Integration

The streaming WebSocket API (`listen.v1.connect`) doesn't work reliably with Deepgram SDK v6.1.1. The project uses the batch `transcribe_file` API instead:

1. Audio chunks accumulate in a buffer
2. Every 5 seconds, buffer is concatenated
3. Converted to WAV bytes (with header) for format detection
4. Sent to `client.listen.v1.media.transcribe_file()`
5. Transcript added to conversation context

### LLM Integration

All LLM providers use OpenAI-compatible API format via `openai` SDK:

- **MiMo**: `https://api.xiaomimimo.com/v1` (model: `mimo-v2.5-pro`)
- **Anthropic**: Direct SDK
- **OpenAI**: Direct SDK
- **MiniMax**: OpenAI-compatible endpoint

### Combined Mode (`--llm`)

When `--llm` is specified:

1. `Sidekick` created with `external_audio=True` (no own audio capture)
2. Recording starts in daemon thread
3. Audio callback forwards data to sidekick via `send_external_audio()`
4. Sidekick converts stereo→mono, resamples 48kHz→16kHz, buffers for Deepgram
5. Interactive CLI runs in main thread (`/context`, `/clear`, `/stop-listening`, `/quit`)
6. Timer thread prints elapsed time every 10 seconds on separate line
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
- Microphone permission for Terminal app

## Known Issues

- Deepgram WebSocket streaming API (v1/v2) produces empty transcripts with SDK v6.1.1. Batch API works.
- Audio level from BlackHole is low (~-36 dB max). Deepgram still transcribes but accuracy may vary.
- `PaMacCore (AUHAL)` warnings from sounddevice are harmless macOS audio errors.

## Commands Reference (Sidekick Interactive CLI)

| Command | Action |
|---------|--------|
| `/context` | Show conversation context |
| `/clear` | Clear context |
| `/stop-listening` | Stop recording |
| `/help` | Show help |
| `/quit` | Exit sidekick |
| Any text | Ask LLM a question |
