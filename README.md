# Eco

Grabador de audio interno para macOS. Captura audio del sistema (Meet, videos, música) usando BlackHole como driver de audio virtual, con transcripción y asistente de IA opcionales, y capturas de pantalla periódicas de la reunión.

## Requisitos

- macOS 10.15+
- Python 3.9+
- Homebrew
- BlackHole 2ch (se instala automáticamente)
- ffmpeg (se instala automáticamente; opcional — solo se usa para comprimir las grabaciones a Opus)

> [!WARNING]
> **Permisos de Administrador Requeridos:** La instalación del driver virtual **BlackHole 2ch** requiere privilegios de administrador (`sudo`) en macOS para poder copiar el controlador en la carpeta del sistema. Si estás en una Mac corporativa sin accesos de administrador, deberás solicitar a tu departamento de IT que instale BlackHole 2ch por ti o, alternativamente, utilizar Eco únicamente en modo micrófono (`eco --input`).

## Instalación

### Método 1: Localmente (Recomendado)

Clona este repositorio y ejecuta el script de instalación:

```bash
./setup.sh
```

El script instalará el driver virtual de audio **BlackHole 2ch** (vía Homebrew) y empaquetará e instalará `eco` como un comando global del sistema usando `pipx` (o un entorno virtual aislado en `~/.eco/venv` como alternativa).

> [!IMPORTANT]
> **Reiniciar la Terminal:** Si es la primera vez que instalas `pipx`, después de ejecutar `setup.sh` (o `pipx ensurepath` en el Método 2), debes cerrar y volver a abrir tu ventana de la terminal (o ejecutar `source ~/.zshrc` / `source ~/.bash_profile`) para que el sistema reconozca el comando `eco`.

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

### Configuración del Dispositivo de Salida Múltiple (Requerido)

Para poder capturar el audio de tu Mac (llamadas de Zoom/Teams, videos, música) **sin perder la audición en tus auriculares o altavoces**, debes crear un Dispositivo de Salida Múltiple:

1. **Abrir la Configuración de Audio MIDI:**
   - Presiona `Cmd + Barra espaciadora` (Spotlight).
   - Busca **Configuración de Audio MIDI** (o *Audio MIDI Setup*) y ábrela.

2. **Crear el Dispositivo de Salida Múltiple:**
   - Haz clic en el botón **`+`** en la esquina inferior izquierda.
   - Selecciona **Crear dispositivo de salida múltiple** (*Create Multi-Output Device*).

3. **Configurar las Salidas de Audio:**
   - Marca la casilla de **BlackHole 2ch** (el driver virtual).
   - Marca la casilla de tus auriculares físicos (AirPods, Bluetooth, cable) o los altavoces de tu Mac.

4. **Ajustar Sincronización y Drift Correction (Muy Importante):**
   - **Fuente del reloj** (*Clock Source*): Selecciona tus auriculares o altavoces físicos (esto define el ritmo maestro).
   - **Corrección de desviación** (*Drift Correction*): Márcala **únicamente en la casilla de BlackHole 2ch** (esto evita desfases y saltos de audio en grabaciones largas).

5. **Establecer como Salida por Defecto:**
   - Haz clic derecho sobre tu nuevo **Dispositivo de salida múltiple** en la lista de la izquierda y selecciona **Usar este dispositivo para la salida de sonido** (*Use This Device For Sound Output*).
   - *(También puedes hacerlo desde Ajustes del Sistema → Sonido → Salida, seleccionando el Dispositivo de salida múltiple).*

> [!TIP]
> **Control del Volumen en macOS:** Dado que macOS bloquea el control nativo de volumen cuando usas un dispositivo Multi-Output, puedes descargar y usar utilidades gratuitas como [eqMac](https://eqmac.app/) para poder subir y bajar el volumen directamente con las teclas multimedia del teclado de forma normal.

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
--output              Graba solo audio del sistema (BlackHole)
--input               Graba solo micrófono
--both                Graba ambos (sistema + micrófono)
--duration N          Duración en segundos (default: hasta Ctrl+C)
--output-file F       Nombre del archivo de audio
--device NAME         Dispositivo específico (ver --list-devices)
--channels MODE       'split' (L/R) o 'mix' (mezclado) - solo con --both
--sample-rate N       Frecuencia de muestreo (default: 48000)
--list-devices        Muestra dispositivos de audio disponibles
--start               Inicia la grabación de inmediato (sin esto, en modo
                      standalone Eco solo se prepara y no graba nada)

Asistente de IA (transcripción + LLM)
--llm PROVEEDOR       Activa el sidekick: anthropic, openai, mimo o minimax
--silence-timeout N   Segundos de silencio antes de auto-cerrar (default: 120,
                      0 para desactivar)

Capturas de pantalla
--screenshots           Toma capturas de pantalla periódicas de la reunión
--screenshot-interval N Intervalo en segundos entre capturas (default: 10)
--window NAME_OR_ID     Limita las capturas a una ventana/app (implica
                        --screenshots). Usa --window list para ver ventanas

Compresión de audio
--no-compress         Conserva el WAV sin comprimir (por defecto se
                      convierte a Opus y se borra el WAV)
--keep-wav            Conserva el WAV original además del Opus comprimido
--audio-bitrate RATE  Bitrate del Opus resultante (default: 32k)
```

### Ejemplos

```bash
# Grabar 2 minutos de Meet
eco --output --duration 120 --start

# Grabar con nombre personalizado
eco --output --output-file reunion.wav --start

# Ver dispositivos disponibles
eco --list-devices

# Usar dispositivo específico
eco --input --device "soundcore" --start

# Grabar + transcribir + preguntarle a un LLM durante la reunión
eco --output --llm mimo --start

# Grabar con capturas de pantalla de una ventana específica
eco --output --window "Zoom" --start

# Conservar el WAV original sin comprimir
eco --output --no-compress --start
```

## Asistente de reuniones (IA)

Con `--llm <proveedor>` (`anthropic`, `openai`, `mimo` o `minimax`), Eco levanta además **Sidekick**: transcribe el audio en tiempo real con Deepgram y abre una interfaz interactiva en la terminal donde puedes preguntarle al LLM sobre lo que se está hablando.

```bash
eco --output --llm mimo --start
```

Dentro de la interfaz, cualquier texto que no empiece con `/` se envía como pregunta al LLM (junto con el contexto reciente de la transcripción y, si hay capturas de pantalla activas, las últimas dos imágenes). Los comandos disponibles son:

| Comando | Acción |
|---------|--------|
| `/context` | Ver el contexto de transcripción actual |
| `/clear-context` | Limpiar el contexto |
| `/output-dir <ruta>` | Cambiar el directorio de salida de los archivos |
| `/start` | Iniciar grabación y transcripción |
| `/pause` | Pausar grabación y transcripción |
| `/resume` | Reanudar grabación y transcripción |
| `/stop` | Detener grabación y transcripción |
| `/silence-timeout <s>` | Configurar el tiempo de silencio antes de auto-cerrar |
| `/window-list` | Listar ventanas visibles de macOS y sus IDs |
| `/screencapture` | Elegir (o cambiar) qué ventana capturar periódicamente |
| `/help` | Mostrar la ayuda |
| `/quit` | Salir del sidekick |

Requiere `DEEPGRAM_API_KEY` configurada (ver [Configuración de API Keys](#configuración-de-api-keys)) y la API key del proveedor de LLM elegido.

## Capturas de pantalla

Con `--screenshots`, Eco toma una captura de pantalla completa cada `--screenshot-interval` segundos (10s por defecto) mientras graba. Con `--window <nombre, título o ID>` limita las capturas a una sola ventana o aplicación (y activa `--screenshots` automáticamente); usa `--window list` para ver las ventanas disponibles con sus IDs.

Las capturas se guardan como `.jpg` en una carpeta `<nombre_del_audio>_screenshots/` junto al archivo de audio. Si la ventana elegida se minimiza u oculta temporalmente, Eco reutiliza la última captura válida en vez de fotografiar el escritorio equivocado.

```bash
# Capturar solo la ventana de Zoom cada 15 segundos
eco --output --window "Zoom" --screenshot-interval 15 --start
```

### Limitación conocida: video de Chrome + ventana oculta

Cuando una ventana de Chrome con un video corriendo (una llamada, un tutorial, YouTube) queda **completamente oculta** detrás de otra ventana, esa captura puntual puede congelarse o verse en negro hasta que la ventana vuelva a estar visible — es una limitación de cómo Chrome compone video acelerado por hardware en macOS, no un bug de Eco (se confirmó que ni la API clásica de macOS ni ScreenCaptureKit pueden leer ese contenido mientras está oculto). Eco te avisa de esto al iniciar si detecta que estás capturando una ventana de Chrome.

**Esto no afecta el audio ni la transcripción** — solo esa captura de pantalla puntual. Podés seguir usando la computadora con normalidad; Eco simplemente reutiliza la última captura válida hasta que la ventana vuelva a estar visible.

Si Chrome ya corre con depuración remota habilitada (`--remote-debugging-port`, típicamente sobre un perfil separado con `--user-data-dir`), Eco la usa automáticamente para evitar el problema por completo — pero no la activa por ti: Chrome se niega a exponer ese modo sobre tu perfil normal por seguridad (para que nada pueda leer tus sesiones/cookies vía ese puerto), así que no hay una forma simple de habilitarlo sin usar un perfil de Chrome dedicado.

## Compresión y archivado de grabaciones

Por defecto, al terminar cada grabación Eco convierte el WAV a **Opus mono (32 kbps)** con `ffmpeg` y borra el WAV original — el audio de voz no pierde inteligibilidad ni precisión de transcripción, pero el archivo pesa ~50x menos. Usa `--no-compress` para conservar el WAV sin tocar, o `--keep-wav` para quedarte con ambos archivos. Si `ffmpeg` no está instalado, Eco avisa y simplemente conserva el WAV (nunca falla la grabación por esto).

Para comprimir en bloque grabaciones antiguas que ya tengas en disco, usa el script incluido `compress_archive.py`:

```bash
# Simula (no escribe nada) y muestra el ahorro estimado
python compress_archive.py ~/Desktop

# Convierte de verdad
python compress_archive.py ~/Desktop --apply

# Convierte y borra los WAV originales (solo tras verificar cada Opus)
python compress_archive.py ~/Desktop --apply --delete-originals
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

- **Captura**: WAV PCM 16-bit, 48000 Hz (configurable con `--sample-rate`), 2 canales (estéreo)
- **Archivo final**: Opus mono 32 kbps por defecto (usa `--no-compress` o `--keep-wav` para conservar el WAV)

## Licencia

MIT
