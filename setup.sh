#!/bin/bash
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo ""
echo "  ╔═══════════════════════════════════════╗"
echo "  ║             ECO - Installation        ║"
echo "  ╚═══════════════════════════════════════╝"
echo ""

# Check macOS
if [[ "$(uname)" != "Darwin" ]]; then
    error "Eco only works on macOS"
    exit 1
fi

info "Detected macOS $(sw_vers -productVersion)"

# Check/install Homebrew
if ! command -v brew &> /dev/null; then
    warn "Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
    info "Homebrew found: $(brew --version | head -1)"
fi

# Check/install BlackHole
if brew list blackhole-2ch &> /dev/null; then
    info "BlackHole 2ch is already installed"
else
    info "Installing BlackHole 2ch..."
    brew install blackhole-2ch
fi

# Check/install ffmpeg (used to compress WAV recordings to Opus).
# Optional dependency: if it fails, Eco continues working and preserves uncompressed WAV.
if brew list ffmpeg &> /dev/null; then
    info "ffmpeg is already installed"
else
    info "Installing ffmpeg (Opus audio compression)..."
    if ! brew install ffmpeg; then
        warn "Could not install ffmpeg. Eco will still work, but will save"
        warn "uncompressed recordings (WAV) instead of Opus."
    fi
fi

# Check/install Python dependencies & CLI package
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

setup_venv() {
    warn "pipx not found or unavailable. Installing using a manual virtual environment..."
    INSTALL_DIR="$HOME/.eco"
    mkdir -p "$INSTALL_DIR"
    
    info "Creating virtual environment in $INSTALL_DIR/venv..."
    python3 -m venv "$INSTALL_DIR/venv"
    
    info "Installing Eco in virtual environment..."
    "$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
    "$INSTALL_DIR/venv/bin/pip" install "$SCRIPT_DIR" --quiet
    
    BIN_DIR="$HOME/.local/bin"
    mkdir -p "$BIN_DIR"
    
    info "Creating symlink at $BIN_DIR/eco..."
    ln -sf "$INSTALL_DIR/venv/bin/eco" "$BIN_DIR/eco"
    
    warn "Ensure $BIN_DIR is in your PATH."
    warn "You can add it by running: echo 'export PATH=\"\$PATH:\$HOME/.local/bin\"' >> ~/.zshrc"
}

info "Installing Eco CLI..."
if command -v pipx &> /dev/null; then
    info "pipx detected. Installing with pipx..."
    pipx uninstall eco &> /dev/null || true
    pipx install "$SCRIPT_DIR" --force
    pipx ensurepath
elif command -v brew &> /dev/null; then
    info "pipx not detected. Attempting to install pipx via Homebrew..."
    brew install pipx
    BREW_PREFIX=$(brew --prefix)
    PIPX_PATH="$BREW_PREFIX/bin/pipx"
    if [ -f "$PIPX_PATH" ]; then
        "$PIPX_PATH" uninstall eco &> /dev/null || true
        "$PIPX_PATH" install "$SCRIPT_DIR" --force
        "$PIPX_PATH" ensurepath
    else
        setup_venv
    fi
else
    setup_venv
fi

echo ""
echo "  ╔═══════════════════════════════════════════════════════════════╗"
echo "  ║                   INSTALLATION COMPLETE                       ║"
echo "  ╠═══════════════════════════════════════════════════════════════╣"
echo "  ║                                                               ║"
echo "  ║  Next Step - Configure Multi-Output Device:                   ║"
echo "  ║                                                               ║"
echo "  ║  1. Open Audio MIDI Setup (Spotlight -> 'Audio MIDI Setup')   ║"
echo "  ║  2. Click '+' -> 'Create Multi-Output Device'                 ║"
echo "  ║  3. Check: BlackHole 2ch + your headphones/speakers           ║"
echo "  ║  4. Ensure Built-in Output is on top (Clock Source)           ║"
echo "  ║  5. Enable 'Drift Correction' on BlackHole 2ch                ║"
echo "  ║  6. Right-click -> 'Use This Device For Sound Output'         ║"
echo "  ║                                                               ║"
echo "  ║  API Key Configuration:                                       ║"
echo "  ║  Create a ~/.eco.env file with your credentials               ║"
echo "  ║  (e.g., DEEPGRAM_API_KEY, MIMO_API_KEY, etc.)                 ║"
echo "  ║                                                               ║"
echo "  ║  Then:                                                        ║"
echo "  ║  System Settings -> Privacy & Security -> Microphone          ║"
echo "  ║  -> enable Terminal                                           ║"
echo "  ║                                                               ║"
echo "  ║  Usage:                                                       ║"
echo "  ║  Run 'eco --help' in any terminal.                            ║"
echo "  ║                                                               ║"
echo "  ╚═══════════════════════════════════════════════════════════════╝"
echo ""
