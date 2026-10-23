# Eco

macOS internal audio recorder and AI meeting assistant. Captures system audio (Meet, Zoom, videos, music) using BlackHole as a virtual audio driver, with optional real-time transcription, LLM-powered meeting assistance, and periodic screenshots.

## Requirements

- macOS 10.15+
- Python 3.9+
- Homebrew
- BlackHole 2ch (installed automatically via setup script)
- ffmpeg (installed automatically via setup script; optional — used to compress recordings to Opus)
- Xcode Command Line Tools (required if using `--window` window-targeted screenshots: `xcode-select --install`)

> [!WARNING]
> **Administrator Privileges Required:** Installing the **BlackHole 2ch** virtual driver requires administrator privileges (`sudo`) on macOS to copy the audio driver into the system folder. If you are on a corporate Mac without admin access, request IT to install BlackHole 2ch for you, or alternatively use Eco in microphone-only mode (`eco --input`).

## Installation

### Method 1: Local Clone (Recommended)

Clone this repository and run the setup script:

```bash
git clone https://github.com/rezpo/eco.git
cd eco
chmod +x setup.sh
./setup.sh
```

The script installs **BlackHole 2ch** and **ffmpeg** (via Homebrew) and installs `eco` as a global CLI command using `pipx` (or an isolated virtual environment in `~/.eco/venv` as fallback).

> [!IMPORTANT]
> **Restart Your Terminal:** If this is the first time you install `pipx`, after running `setup.sh`, restart your terminal window (or run `source ~/.zshrc`) so the `eco` command is recognized in your `$PATH`.

### Method 2: Direct from GitHub

Once the repository is available on GitHub:

```bash
# Install system dependencies and pipx
brew install blackhole-2ch pipx
pipx ensurepath

# Install Eco directly from Git
pipx install git+https://github.com/rezpo/eco.git
```

## API Key Configuration

To use the AI meeting assistant and transcription from any folder, configure your credentials in `~/.eco.env`:

```bash
cp .env.example ~/.eco.env
nano ~/.eco.env  # Add your Deepgram, MiMo, Anthropic, or OpenAI keys
```

Eco automatically searches for API keys in:
1. Current working directory (`.env`)
2. User home directory (`~/.eco.env`)
3. Standard config directory (`~/.config/eco/.env`)

### Multi-Output Device Setup (Required for System Audio)

To capture Mac system audio (calls, videos, music) **without losing sound in your headphones or speakers**, you must create a Multi-Output Device:

1. **Open Audio MIDI Setup:**
   - Press `Cmd + Space` (Spotlight).
   - Search for **Audio MIDI Setup** and open it.

2. **Create Multi-Output Device:**
   - Click the **`+`** button in the lower-left corner.
   - Select **Create Multi-Output Device**.

3. **Select Audio Outputs:**
   - Check **BlackHole 2ch** (virtual driver).
   - Check your physical headphones (AirPods, Bluetooth, 3.5mm) or Mac speakers.

4. **Clock Source & Drift Correction (Important):**
   - **Clock Source:** Select your physical headphones/speakers (master clock).
   - **Drift Correction:** Check **only for BlackHole 2ch** (prevents audio drift and glitches over long recordings).

5. **Set as Default Output:**
   - Right-click your new **Multi-Output Device** in the left sidebar and select **Use This Device For Sound Output** (or choose it in *System Settings -> Sound -> Output*).

> [!TIP]
> **Volume Control on macOS:** macOS disables native volume hotkeys when using a Multi-Output Device. You can use free third-party utilities such as [eqMac](https://eqmac.app/) to control output volume with your keyboard media keys as usual.

### Microphone & Screen Permissions

Open **System Settings -> Privacy & Security**:
- **Microphone**: Enable **Terminal** (or your terminal emulator, e.g. iTerm).
- **Screen & System Audio Recording** (if using `--screenshots` or `--window`): Enable **Terminal**.

## Usage

Once installed, run `eco` directly from any terminal window:

### Record System Audio

```bash
eco --output --start
```

Captures everything playing on your Mac (Meet, Zoom, YouTube, Spotify, etc.).

### Record Microphone

```bash
eco --input --start
```

### Record Both (System Audio + Microphone)

```bash
# Mixed into a single stereo stream
eco --both --channels mix --start

# Separate channels (Left = System Audio, Right = Microphone)
eco --both --channels split --start
```

### CLI Options

**Recording Mode** (choose exactly one):

| Flag | Description |
|------|-------------|
| `--output` | Records system audio only via BlackHole |
| `--input` | Records microphone only |
| `--both` | Records system audio and microphone simultaneously |

**Recording Controls:**

| Flag | Description |
|------|-------------|
| `--start` | Starts recording immediately. Without this flag, Eco only validates devices and arguments unless `--llm` is used |
| `--duration N` | Automatically stops recording after N seconds. Defaults to recording until `Ctrl+C` |
| `--output-file F` | Output filename (default: `eco_YYYYMMDD_HHMMSS`) |
| `--device NAME` | Target a specific audio input device (see `--list-devices`) |
| `--channels split\|mix` | For `--both` mode: `split` stores system and mic on separate channels (L/R), `mix` blends them into stereo (default) |
| `--sample-rate N` | Capture sample rate in Hz (default: 48000) |
| `--list-devices` | Lists available audio input devices and exits |

**Transcription & AI Assistant:**

| Flag | Description |
|------|-------------|
| `--llm PROVIDER` | Activates Sidekick: real-time transcription via Deepgram and interactive LLM assistant (`anthropic`, `openai`, `mimo`, `minimax`) |
| `--silence-timeout N` | In `--llm` mode: auto-quits after N seconds of continuous silence (default: 120s; set `0` to disable) |

**Screenshots:**

| Flag | Description |
|------|-------------|
| `--screenshots` | Takes periodic full-screen captures while recording |
| `--screenshot-interval N` | Seconds between screenshots (default: 10s) |
| `--window NAME_OR_ID` | Targets captures to a specific window/app (implies `--screenshots`). Use `--window list` to inspect visible windows and IDs |

**Audio Compression:**

| Flag | Description |
|------|-------------|
| `--no-compress` | Keeps the uncompressed WAV file without converting to Opus |
| `--keep-wav` | Compresses to Opus while retaining the original WAV |
| `--audio-bitrate RATE` | Output Opus bitrate (default: `32k`) |

### Examples

```bash
# Record 2 minutes of meeting audio
eco --output --duration 120 --start

# Record with a custom filename
eco --output --output-file meeting.wav --start

# List audio devices
eco --list-devices

# Record + real-time transcription + LLM assistant
eco --output --llm mimo --start

# Record with periodic window screenshots
eco --output --window "Zoom" --start

# Keep raw WAV without Opus compression
eco --output --no-compress --start
```

## AI Meeting Assistant (Sidekick)

When `--llm <provider>` is specified, Eco launches **Sidekick**: it streams audio to Deepgram for real-time transcription and provides an interactive terminal CLI where you can query the LLM about the ongoing meeting.

```bash
eco --output --llm mimo --start
```

In the interactive prompt, any text without a leading slash `/` is sent as a question to the LLM (with recent transcript context and the two most recent screenshots, if enabled).

### Available Commands

| Command | Action |
|---------|--------|
| `/context` | Display current meeting transcript context |
| `/clear-context` | Clear transcript context |
| `/output-dir <path>` | Change destination folder for recordings and transcripts |
| `/start` | Start recording and transcription |
| `/pause` | Pause recording and transcription |
| `/resume` | Resume recording and transcription |
| `/stop` | Stop recording and transcription |
| `/silence-timeout <s>` | Set silence auto-quit timeout in seconds |
| `/window-list` | List visible macOS windows and IDs |
| `/screencapture` | Interactively select or change the target capture window |
| `/help` | Show command reference |
| `/quit` | Exit Sidekick |

Requires `DEEPGRAM_API_KEY` and the chosen LLM provider's API key.

## Periodic Screenshots

Using `--screenshots`, Eco captures images every `--screenshot-interval` seconds (default 10s). Using `--window <name/title/id>` restricts capture to a specific application window.

Screenshots are saved as `.jpg` inside `<audio_file_stem>_screenshots/`. If the target window is minimized or hidden, Eco reuses the last valid frame rather than capturing the wrong desktop.

### Known Limitation: Occluded Chrome Video

When a Chrome window playing hardware-accelerated video becomes fully occluded behind another window, macOS's window capture APIs (`screencapture -l` and ScreenCaptureKit) cannot read the hardware video compositor surface and render black or freeze until visible again.

- **Audio and transcription are unaffected.**
- If Chrome is already running with `--remote-debugging-port=9339`, Eco automatically captures frames via Chrome DevTools Protocol (CDP) to bypass this limitation.

## Batch Compression Tool

To compress existing WAV recordings on disk to Opus mono (32 kbps):

```bash
# Dry-run (simulation and size estimation)
python compress_archive.py ~/Desktop

# Convert to Opus
python compress_archive.py ~/Desktop --apply

# Convert and delete verified WAV originals
python compress_archive.py ~/Desktop --apply --delete-originals
```

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

## Troubleshooting

### BlackHole does not appear in audio devices

```bash
brew reinstall blackhole-2ch
sudo killall -9 coreaudiod
```

### No audio recorded

1. Verify that **Multi-Output Device** is selected as the default macOS sound output.
2. Verify **Microphone** permissions for Terminal in System Settings.
3. Verify that **BlackHole 2ch** appears in `eco --list-devices`.

### Audio stutter or drift

Enable **Drift Correction** on all devices in the Multi-Output Device except the Clock Source (built-in output/headphones).

## License

MIT
