# Eco

Grabador de audio interno para macOS. Captura audio del sistema (Meet, videos, música) usando BlackHole como driver de audio virtual.

## Requisitos

- macOS 10.15+
- Python 3.9+
- Homebrew
- BlackHole 2ch (se instala automáticamente)

## Instalación

### Método 1: Localmente (Recomendado)

Clona este repositorio y ejecuta el script de instalación:

```bash
./setup.sh
```

El script instalará el driver virtual de audio **BlackHole 2ch** (vía Homebrew) y empaquetará e instalará `eco` como un comando global del sistema usando `pipx` (o un entorno virtual aislado en `~/.eco/venv` como alternativa).

### Método 2: Directo desde GitHub (Para otras Macs)

Si compartes el código en un repositorio de Git, cualquier usuario en otra Mac puede instalarlo directamente ejecutando:

```bash
# Instalar dependencias del sistema y pipx (si no se tienen)
brew install blackhole-2ch pipx
pipx ensurepath

# Instalar Eco directamente desde Git
pipx install git+https://github.com/tu-usuario/eco.git
```

## Configuración de API Keys

Para que el asistente de reuniones AI funcione desde cualquier directorio, puedes crear un archivo `.env` global en tu carpeta de usuario:

```bash
cp .env.example ~/.eco.env
nano ~/.eco.env  # Agrega tus claves de Deepgram, MiMo, Anthropic, etc.
```

Eco buscará automáticamente tus API keys en:
1. El directorio actual (`.env`)
2. Tu directorio home (`~/.eco.env`)
3. La carpeta estándar de configuraciones (`~/.config/eco/.env`)

### Configuración manual de Multi-Output Device

Después de la instalación, necesitas configurar tu salida de audio:

1. Abrir **Audio MIDI Setup** (Spotlight → "Audio MIDI Setup")
2. Click **+** → **Create Multi-Output Device**
3. Marcar: **BlackHole 2ch** + tus headphones/altavoces
4. Asegurar que **Built-in Output** esté arriba (Clock Source)
5. Habilitar **Drift Correction** en BlackHole
6. Right-click → **Use This Device For Sound Output**

### Permisos de micrófono

Preferencias del Sitema → Privacidad y Seguridad → Micrófono → habilitar Terminal (o tu app de terminal).

## Uso

Una vez instalado, puedes ejecutar `eco` directamente desde cualquier carpeta en tu terminal.

### Grabar audio del sistema

```bash
eco --output
```

Captura todo lo que suena en tu Mac (Meet, Spotify, videos, etc.).

### Grabar micrófono

```bash
eco --input
```

### Grabar ambos (sistema + micrófono)

```bash
# Mezclado (un solo canal estéreo)
eco --both --channels mix

# Canales separados (L=sistema, R=micrófono)
eco --both --channels split
```

### Opciones

```
--output          Graba solo audio del sistema (BlackHole)
--input           Graba solo micrófono
--both            Graba ambos (sistema + micrófono)
--duration N      Duración en segundos (default: hasta Ctrl+C)
--output-file F   Nombre del archivo WAV
--device NAME     Dispositivo específico (ver --list-devices)
--channels MODE   'split' (L/R) o 'mix' (mezclado) - solo con --both
--sample-rate N   Frecuencia de muestreo (default: 48000)
--list-devices    Muestra dispositivos de audio disponibles
```

### Ejemplos

```bash
# Grabar 2 minutos de Meet
eco --output --duration 120

# Grabar con nombre personalizado
eco --output --output-file reunion.wav

# Ver dispositivos disponibles
eco --list-devices

# Usar dispositivo específico
eco --input --device "soundcore"
```

## Cómo funciona

```
┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│  Aplicación  │────────▶│  BlackHole   │────────▶│  Eco         │
│  (Meet, etc) │         │  (driver)    │         │  (grabador)  │
└──────────────┘         └──────────────┘         └──────────────┘
                              │                         │
                              ▼                         ▼
                        Audio del sistema         Archivo WAV
```

BlackHole es un driver de audio virtual que captura todo el audio que el sistema envía a la salida. Al configurar un Multi-Output Device, el audio va tanto a BlackHole (para grabar) como a tus headphones (para escuchar).

## Troubleshooting

### BlackHole no aparece como dispositivo

```bash
brew reinstall blackhole-2ch
sudo killall -9 coreaudiod
```

### No se graba audio

1. Verificar que el Multi-Output Device esté configurado como salida
2. Verificar permisos de micrófono en Preferencias del Sistema
3. Verificar que BlackHole aparezca en `--list-devices`

### Audio con glitches

Habilitar **Drift Correction** en todos los dispositivos del Multi-Output excepto el Clock Source (Built-in Output).

### El volumen no funciona con Multi-Output

macOS no soporta cambio de volumen en Multi-Output Devices. Ajustar volumen individualmente en Audio MIDI Setup.

## Formato de salida

- **Formato**: WAV (PCM 16-bit)
- **Sample rate**: 48000 Hz (configurable con --sample-rate)
- **Canales**: 2 (estéreo)

## Licencia

MIT
