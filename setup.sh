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
echo "  ║             ECO - Instalación         ║"
echo "  ╚═══════════════════════════════════════╝"
echo ""

# Check macOS
if [[ "$(uname)" != "Darwin" ]]; then
    error "Eco solo funciona en macOS"
    exit 1
fi

info "Detectado macOS $(sw_vers -productVersion)"

# Check/install Homebrew
if ! command -v brew &> /dev/null; then
    warn "Homebrew no encontrado. Instalando..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
    info "Homebrew encontrado: $(brew --version | head -1)"
fi

# Check/install BlackHole
if brew list blackhole-2ch &> /dev/null; then
    info "BlackHole 2ch ya está instalado"
else
    info "Instalando BlackHole 2ch..."
    brew install blackhole-2ch
fi

# Check/install Python dependencies & CLI package
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

setup_venv() {
    warn "No se pudo usar pipx. Instalando usando un entorno virtual manual..."
    INSTALL_DIR="$HOME/.eco"
    mkdir -p "$INSTALL_DIR"
    
    info "Creando entorno virtual en $INSTALL_DIR/venv..."
    python3 -m venv "$INSTALL_DIR/venv"
    
    info "Instalando Eco en el entorno virtual..."
    "$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
    "$INSTALL_DIR/venv/bin/pip" install "$SCRIPT_DIR" --quiet
    
    # Crear carpeta bin si no existe
    BIN_DIR="$HOME/.local/bin"
    mkdir -p "$BIN_DIR"
    
    info "Creando enlace en $BIN_DIR/eco..."
    ln -sf "$INSTALL_DIR/venv/bin/eco" "$BIN_DIR/eco"
    
    warn "Asegúrate de que $BIN_DIR esté en tu PATH."
    warn "Puedes agregarlo ejecutando: echo 'export PATH=\"\$PATH:\$HOME/.local/bin\"' >> ~/.zshrc"
}

info "Instalando Eco CLI..."
if command -v pipx &> /dev/null; then
    info "pipx detectado. Instalando con pipx..."
    pipx uninstall eco &> /dev/null || true
    pipx install "$SCRIPT_DIR" --force
    pipx ensurepath
elif command -v brew &> /dev/null; then
    info "pipx no detectado. Intentando instalar pipx via Homebrew..."
    brew install pipx
    # Obtener el path de pipx instalado por brew
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
echo "  ║                    INSTALACIÓN COMPLETA                       ║"
echo "  ╠═══════════════════════════════════════════════════════════════╣"
echo "  ║                                                               ║"
echo "  ║  Siguiente paso - Configurar Multi-Output Device:             ║"
echo "  ║                                                               ║"
echo "  ║  1. Abrir Audio MIDI Setup (Spotlight → 'Audio MIDI Setup')   ║"
echo "  ║  2. Click '+' → 'Create Multi-Output Device'                  ║"
echo "  ║  3. Marcar: BlackHole 2ch + tus headphones/altavoces          ║"
echo "  ║  4. Asegurar que Built-in Output esté arriba (Clock Source)    ║"
echo "  ║  5. Habilitar 'Drift Correction' en BlackHole                 ║"
echo "  ║  6. Right-click → 'Use This Device For Sound Output'          ║"
echo "  ║                                                               ║"
echo "  ║  Configuración de API Keys:                                   ║"
echo "  ║  Crea un archivo ~/.eco.env con tus credenciales              ║"
echo "  ║  (ej: DEEPGRAM_API_KEY, MIMO_API_KEY, etc.)                   ║"
echo "  ║                                                               ║"
echo "  ║  Luego:                                                       ║"
echo "  ║  Preferencias → Privacidad → Micrófono → habilitar Terminal   ║"
echo "  ║                                                               ║"
echo "  ║  Uso:                                                         ║"
echo "  ║  Ejecuta 'eco --help' en cualquier terminal.                  ║"
echo "  ║                                                               ║"
echo "  ╚═══════════════════════════════════════════════════════════════╝"
echo ""
