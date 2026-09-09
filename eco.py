#!/usr/bin/env python3
"""
Eco - Grabador de audio interno para macOS
Usa BlackHole como driver de audio virtual para capturar audio del sistema.
"""

import argparse
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    # 1. CWD .env (highest priority)
    load_dotenv(dotenv_path=Path.cwd() / ".env")
    # 2. ~/.eco.env (user home fallback)
    load_dotenv(dotenv_path=Path.home() / ".eco.env")
    # 3. ~/.config/eco/.env (standard config folder fallback)
    load_dotenv(dotenv_path=Path.home() / ".config" / "eco" / ".env")
except ImportError:
    pass

import numpy as np
import sounddevice as sd
import soundfile as sf

VERSION = "1.1.0"
DEFAULT_SAMPLE_RATE = 48000
DEFAULT_CHANNELS = 2
BLOCK_SIZE = 1024

# Compresión de audio: el WAV PCM es necesario durante la captura, pero como
# archivo es voz guardada a 1536 kbps. Opus mono a 32 kbps la reduce ~54x sin
# perder inteligibilidad ni precisión de transcripción.
DEFAULT_AUDIO_BITRATE = "32k"


def compress_audio(wav_path, bitrate=DEFAULT_AUDIO_BITRATE, keep_original=False,
                   printer=None):
    """Convierte un WAV a Opus mono y borra el original si la conversión es válida.

    Devuelve la ruta del .opus, o la del WAV si no se pudo convertir. Nunca borra
    el original sin antes verificar que el resultado existe y tiene contenido.
    """
    import shutil
    import subprocess

    say = printer or print
    wav_path = Path(wav_path)

    if not wav_path.exists():
        return str(wav_path)

    if shutil.which("ffmpeg") is None:
        say("  [WARN] ffmpeg no encontrado, se conserva el WAV sin comprimir")
        return str(wav_path)

    opus_path = wav_path.with_suffix(".opus")
    original_size = wav_path.stat().st_size

    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y",
             "-i", str(wav_path),
             "-c:a", "libopus", "-b:a", bitrate, "-ac", "1",
             str(opus_path)],
            check=True, capture_output=True, timeout=1800,
        )
    except Exception as e:
        say(f"  [WARN] No se pudo comprimir el audio, se conserva el WAV: {e}")
        if opus_path.exists():
            opus_path.unlink()
        return str(wav_path)

    if not opus_path.exists() or opus_path.stat().st_size == 0:
        say("  [WARN] La compresión produjo un archivo vacío, se conserva el WAV")
        if opus_path.exists():
            opus_path.unlink()
        return str(wav_path)

    new_size = opus_path.stat().st_size
    ratio = original_size / new_size if new_size else 0
    say(f"  Comprimido a Opus {bitrate} mono: "
        f"{original_size / 1048576:.1f} MB -> {new_size / 1048576:.1f} MB ({ratio:.0f}x)")

    if not keep_original:
        wav_path.unlink()

    return str(opus_path)


def list_devices():
    """Muestra dispositivos de audio disponibles."""
    devices = sd.query_devices()
    print("\n  Dispositivos de Audio Disponibles")
    print("  " + "=" * 50)

    print("\n  OUTPUT (salida):")
    print("  " + "-" * 50)
    for i, dev in enumerate(devices):
        if dev["max_output_channels"] > 0:
            default = " ← default" if i == sd.default.device[1] else ""
            print(f"    [{i}] {dev['name']}{default}")
            print(f"        Canales: {dev['max_output_channels']} | Sample rate: {int(dev['default_samplerate'])} Hz")

    print("\n  INPUT (entrada):")
    print("  " + "-" * 50)
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            default = " ← default" if i == sd.default.device[0] else ""
            print(f"    [{i}] {dev['name']}{default}")
            print(f"        Canales: {dev['max_input_channels']} | Sample rate: {int(dev['default_samplerate'])} Hz")

    print()


def find_device(name_pattern, kind="input"):
    """Busca un dispositivo por nombre (case-insensitive)."""
    devices = sd.query_devices()
    name_lower = name_pattern.lower()

    for i, dev in enumerate(devices):
        if kind == "input" and dev["max_input_channels"] > 0:
            if name_lower in dev["name"].lower():
                return i, dev["name"]
        elif kind == "output" and dev["max_output_channels"] > 0:
            if name_lower in dev["name"].lower():
                return i, dev["name"]

    return None, None


def get_device_channels(device_idx, kind="input"):
    """Retorna el número de canales del dispositivo."""
    devices = sd.query_devices()
    dev = devices[device_idx]
    if kind == "input":
        return min(dev["max_input_channels"], DEFAULT_CHANNELS)
    return min(dev["max_output_channels"], DEFAULT_CHANNELS)


def find_system_device():
    """Encuentra el dispositivo para grabar audio del sistema (BlackHole)."""
    idx, name = find_device("blackhole", "input")
    if idx is not None:
        return idx, name

    idx, name = find_device("background music", "input")
    if idx is not None:
        return idx, name

    return None, None


def find_mic_device():
    """Encuentra el micrófono nativo."""
    for pattern in ["built-in", "macbook", "internal", "micrófono incorporado", "microfono incorporado"]:
        idx, name = find_device(pattern, "input")
        if idx is not None:
            return idx, name

    devices = sd.query_devices()
    default_idx = sd.default.device[0]
    if default_idx is not None and devices[default_idx]["max_input_channels"] > 0:
        return default_idx, devices[default_idx]["name"]

    return None, None


def find_both_device():
    """Encuentra Aggregate Device o BlackHole para grabar ambos."""
    for pattern in ["aggregate", "eco"]:
        idx, name = find_device(pattern, "input")
        if idx is not None:
            return idx, name

    return find_system_device()


def get_visible_windows():
    """Retorna una lista de ventanas visibles (dicts con id, owner, title, etc)."""
    swift_code = """
    import Cocoa
    import CoreGraphics

    let options = CGWindowListOption(arrayLiteral: .excludeDesktopElements, .optionAll)
    let windowListInfo = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: AnyObject]] ?? []

    let excludeOwners = ["AutoFill", "Autorrelleno", "Autorrellenar", "Open and Save Panel Service", "FocusProxy", "loginwindow", "Window Server"]

    var results: [[String: Any]] = []
    for info in windowListInfo {
        guard let layer = info[kCGWindowLayer as String] as? Int, layer == 0 else { continue }
        guard let windowID = info[kCGWindowNumber as String] as? Int else { continue }
        let ownerName = info[kCGWindowOwnerName as String] as? String ?? "Unknown"
        let windowName = info[kCGWindowName as String] as? String ?? ""
        let isOnscreen = info[kCGWindowIsOnscreen as String] as? Bool ?? false
        
        var x = 0.0
        var y = 0.0
        var width = 0.0
        var height = 0.0
        if let boundsDict = info[kCGWindowBounds as String] as? [String: CGFloat] {
            x = boundsDict["X"] ?? 0.0
            y = boundsDict["Y"] ?? 0.0
            width = boundsDict["Width"] ?? 0.0
            height = boundsDict["Height"] ?? 0.0
        }

        guard width > 200 && height > 200 else { continue }
        guard !excludeOwners.contains(ownerName) else { continue }

        results.append([
            "id": windowID,
            "owner": ownerName,
            "title": windowName,
            "isOnscreen": isOnscreen,
            "x": x,
            "y": y,
            "width": width,
            "height": height
        ])
    }

    results.sort { (a, b) -> Bool in
        let aHasTitle = !(a["title"] as? String ?? "").isEmpty
        let bHasTitle = !(b["title"] as? String ?? "").isEmpty
        if aHasTitle != bHasTitle {
            return aHasTitle
        }
        
        let aWidth = a["width"] as? Double ?? 0.0
        let aHeight = a["height"] as? Double ?? 0.0
        let bWidth = b["width"] as? Double ?? 0.0
        let bHeight = b["height"] as? Double ?? 0.0
        let aArea = aWidth * aHeight
        let bArea = bWidth * bHeight
        if abs(aArea - bArea) > 10000 {
            return aArea > bArea
        }
        
        let aOnscreen = a["isOnscreen"] as? Bool ?? false
        let bOnscreen = b["isOnscreen"] as? Bool ?? false
        if aOnscreen != bOnscreen {
            return aOnscreen
        }
        
        return aArea > bArea
    }

    if let jsonData = try? JSONSerialization.data(withJSONObject: results, options: []),
       let jsonString = String(data: jsonData, encoding: .utf8) {
        print(jsonString)
    }
    """
    import subprocess
    import json
    try:
        res = subprocess.run(["swift", "-"], input=swift_code, text=True, capture_output=True, check=True)
        return json.loads(res.stdout.strip())
    except Exception as e:
        return []


def list_visible_windows():
    """Muestra una lista de ventanas visibles en el sistema y sus IDs."""
    try:
        windows = get_visible_windows()
        if not windows:
            print("\n  No se encontraron ventanas visibles.")
            return
        
        print("\n  Ventanas Visibles Disponibles")
        print("  " + "=" * 85)
        print(f"  {'ID':<10} | {'Aplicación/Owner':<25} | {'Título de Ventana'}")
        print("  " + "-" * 85)
        for w in windows:
            owner = w.get("owner", "Desconocido")
            title = w.get("title", "")
            owner_trunc = owner[:25]
            print(f"  {w['id']:<10} | {owner_trunc:<25} | {title}")
        print()
    except Exception as e:
        print(f"  [ERROR] No se pudieron listar las ventanas: {e}")


def find_window(query):
    """Busca una ventana por ID, owner o título. Retorna dict con id, owner, title o None."""
    swift_code = """
    import Cocoa
    import CoreGraphics

    let arguments = CommandLine.arguments
    guard arguments.count > 1 else {
        exit(1)
    }
    let query = arguments[1].lowercased()

    let options = CGWindowListOption(arrayLiteral: .excludeDesktopElements, .optionAll)
    let windowListInfo = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: AnyObject]] ?? []

    let excludeOwners = ["AutoFill", "Autorrelleno", "Autorrellenar", "Open and Save Panel Service", "FocusProxy", "loginwindow", "Window Server"]

    var matches: [[String: Any]] = []
    for info in windowListInfo {
        guard let layer = info[kCGWindowLayer as String] as? Int, layer == 0 else { continue }
        guard let windowID = info[kCGWindowNumber as String] as? Int else { continue }
        let ownerName = info[kCGWindowOwnerName as String] as? String ?? ""
        let windowName = info[kCGWindowName as String] as? String ?? ""
        let isOnscreen = info[kCGWindowIsOnscreen as String] as? Bool ?? false
        
        var x = 0.0
        var y = 0.0
        var width = 0.0
        var height = 0.0
        if let boundsDict = info[kCGWindowBounds as String] as? [String: CGFloat] {
            x = boundsDict["X"] ?? 0.0
            y = boundsDict["Y"] ?? 0.0
            width = boundsDict["Width"] ?? 0.0
            height = boundsDict["Height"] ?? 0.0
        }

        guard width > 200 && height > 200 else { continue }
        guard !excludeOwners.contains(ownerName) else { continue }

        let matchQuery = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let isIdMatch = String(windowID) == matchQuery
        let isOwnerMatch = ownerName.lowercased().contains(matchQuery)
        let isTitleMatch = windowName.lowercased().contains(matchQuery)

        if isIdMatch || isOwnerMatch || isTitleMatch {
            matches.append([
                "id": windowID,
                "owner": ownerName,
                "title": windowName,
                "isOnscreen": isOnscreen,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "area": width * height,
                "hasTitle": !windowName.isEmpty
            ])
        }
    }

    matches.sort { (a, b) -> Bool in
        let aHasTitle = a["hasTitle"] as? Bool ?? false
        let bHasTitle = b["hasTitle"] as? Bool ?? false
        if aHasTitle != bHasTitle {
            return aHasTitle
        }
        
        let aArea = a["area"] as? Double ?? 0.0
        let bArea = b["area"] as? Double ?? 0.0
        if abs(aArea - bArea) > 10000 {
            return aArea > bArea
        }
        
        let aOnscreen = a["isOnscreen"] as? Bool ?? false
        let bOnscreen = b["isOnscreen"] as? Bool ?? false
        if aOnscreen != bOnscreen {
            return aOnscreen
        }
        
        return aArea > bArea
    }

    if let bestMatch = matches.first {
        let result: [String: Any] = [
            "id": bestMatch["id"]!,
            "owner": bestMatch["owner"]!,
            "title": bestMatch["title"]!,
            "x": bestMatch["x"]!,
            "y": bestMatch["y"]!,
            "width": bestMatch["width"]!,
            "height": bestMatch["height"]!
        ]
        if let jsonData = try? JSONSerialization.data(withJSONObject: result, options: []),
           let jsonString = String(data: jsonData, encoding: .utf8) {
            print(jsonString)
            exit(0)
        }
    }
    exit(1)
    """
    import subprocess
    import json
    try:
        res = subprocess.run(["swift", "-", str(query)], input=swift_code, text=True, capture_output=True)
        if res.returncode == 0:
            return json.loads(res.stdout.strip())
    except Exception:
        pass
    return None


class Eco:
    """Grabador de audio principal."""

    def __init__(self, mode, channels_mode="mix", device_idx=None,
                 sample_rate=DEFAULT_SAMPLE_RATE, duration=None, output_file=None,
                 screenshots=False, screenshot_interval=10, window_query=None,
                 compress=True, audio_bitrate=DEFAULT_AUDIO_BITRATE,
                 keep_wav=False):
        self.mode = mode
        self.channels_mode = channels_mode
        self.sample_rate = sample_rate
        self.duration = duration
        self.recording = False
        self.start_time = None
        self.frames_written = 0
        self.file = None
        self._stopped = False
        
        self.paused = False
        self.total_paused_time = 0.0
        self.pause_start_time = None

        self.screenshots = screenshots
        self.screenshot_interval = screenshot_interval
        self.window_query = window_query
        self.screenshots_dir = None
        self._screenshot_thread = None

        self.compress = compress
        self.audio_bitrate = audio_bitrate
        self.keep_wav = keep_wav

        self.output_dir = str(Path.home() / "Desktop")
        self.output_file_param = output_file

        if output_file:
            p = Path(output_file)
            if p.is_absolute():
                self.output_file = str(p)
            else:
                self.output_file = str(Path(self.output_dir) / p)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.output_file = str(Path(self.output_dir) / f"eco_{timestamp}.wav")

        # "both" + "mix" siempre necesita dos dispositivos por separado (sistema
        # y micrófono): un solo --device no alcanza para elegir ambos, así que
        # se detectan automáticamente sin importar si se pasó device_idx.
        needs_both_mix_detection = self.mode == "both" and self.channels_mode == "mix"

        if device_idx is not None and not needs_both_mix_detection:
            self.device_idx = device_idx
        else:
            if device_idx is not None and needs_both_mix_detection:
                print("  [WARN] --device no aplica con --both --channels mix "
                      "(se detectan automáticamente el audio del sistema y el micrófono)")
            self._auto_detect_device()

    def reset_output_file(self):
        """Genera un nuevo nombre de archivo de salida."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if self.output_file_param:
            p = Path(self.output_file_param)
            stem = p.stem
            suffix = p.suffix or ".wav"
            self.output_file = str(Path(self.output_dir) / f"{stem}_{timestamp}{suffix}")
        else:
            self.output_file = str(Path(self.output_dir) / f"eco_{timestamp}.wav")
        self._stopped = False
        self.frames_written = 0
        self.start_time = None
        self.total_paused_time = 0.0
        self.pause_start_time = None
        self.paused = False
        self.screenshots_dir = None

    def _capture_window(self, window_id, owner, bounds, filepath):
        """Intenta capturar una ventana específica. Si es de Chrome y tiene
        depuración remota activa, usa CDP (robusto ante video oculto);
        si no, cae al método clásico screencapture -l<id>."""
        import subprocess
        import chrome_capture

        if owner == "Google Chrome" and bounds:
            try:
                if chrome_capture.capture_chrome_window_screenshot(bounds, filepath):
                    return True
            except Exception:
                pass

        try:
            subprocess.run(["screencapture", "-x", f"-l{window_id}", "-t", "jpg", filepath], check=True)
            return True
        except Exception:
            return False

    def _take_screenshots_loop(self):
        """Loop de captura de pantalla periódica en segundo plano."""
        import subprocess
        import os
        import time
        import glob
        import shutil
        from pathlib import Path

        # Generar ruta del directorio de screenshots al lado del archivo WAV
        wav_path = Path(self.output_file)
        self.screenshots_dir = wav_path.parent / f"{wav_path.stem}_screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        
        msg = f"  [*] Carpeta de capturas: {self.screenshots_dir}"
        if hasattr(self, '_sidekick') and self._sidekick:
            self._sidekick.safe_print(msg)
        else:
            print(msg)

        # Esperar un segundo para que los cambios de terminal/foco se completen y estabilicen
        time.sleep(1.0)

        current_window_id = None
        current_window_owner = None
        current_window_bounds = None
        last_logged_state = None  # Puede ser 'window', 'window_obscured', 'window_unavailable', 'fullscreen'

        def _bounds_of(w_info):
            return {"x": w_info["x"], "y": w_info["y"], "width": w_info["width"], "height": w_info["height"]}

        # Inicializar la última captura exitosa desde el disco si existe
        existing_shots = sorted(glob.glob(str(self.screenshots_dir / "*.jpg")))
        last_successful_filepath = existing_shots[-1] if existing_shots else None

        # Inicialización de la ventana
        if self.window_query:
            w_info = find_window(self.window_query)
            if w_info:
                current_window_id = w_info["id"]
                current_window_owner = w_info["owner"]
                current_window_bounds = _bounds_of(w_info)
                msg_init = f"  [*] Capturando ventana: ID {current_window_id} ({w_info['owner']} - {w_info['title']})"
                if hasattr(self, '_sidekick') and self._sidekick:
                    self._sidekick.safe_print(msg_init)
                else:
                    print(msg_init)
                last_logged_state = 'window'
            else:
                msg_init = f"  [WARN] Ventana '{self.window_query}' no encontrada en este momento. Se esperará a que aparezca."
                if hasattr(self, '_sidekick') and self._sidekick:
                    self._sidekick.safe_print(msg_init)
                else:
                    print(msg_init)
                last_logged_state = 'window_unavailable'

        last_capture_time = 0

        while self.recording:
            if self.paused:
                time.sleep(0.1)
                continue

            now = time.time()
            if now - last_capture_time >= self.screenshot_interval:
                timestamp = datetime.fromtimestamp(now).strftime("%Y%m%d_%H%M%S")
                filename = f"screenshot_{timestamp}.jpg"
                filepath = str(self.screenshots_dir / filename)

                if self.window_query:
                    captured = False

                    # Si no tenemos ID, intentamos resolverlo
                    if current_window_id is None:
                        w_info = find_window(self.window_query)
                        if w_info:
                            current_window_id = w_info["id"]
                            current_window_owner = w_info["owner"]
                            current_window_bounds = _bounds_of(w_info)

                    if current_window_id is not None:
                        captured = self._capture_window(
                            current_window_id, current_window_owner, current_window_bounds, filepath
                        )
                        if not captured:
                            # Re-resolver ID por si cambió o hubo un glitch de transición
                            w_info = find_window(self.window_query)
                            if w_info:
                                current_window_id = w_info["id"]
                                current_window_owner = w_info["owner"]
                                current_window_bounds = _bounds_of(w_info)
                                # Esperar medio segundo antes del reintento
                                time.sleep(0.5)
                                captured = self._capture_window(
                                    current_window_id, current_window_owner, current_window_bounds, filepath
                                )

                    if captured:
                        last_successful_filepath = filepath
                        if last_logged_state != 'window':
                            msg_state = f"  [*] Ventana activa. Capturando ID {current_window_id}."
                            if hasattr(self, '_sidekick') and self._sidekick:
                                self._sidekick.safe_print(msg_state)
                            else:
                                print(msg_state)
                            last_logged_state = 'window'
                    else:
                        # En vez de fallback a pantalla completa, reutilizamos la última captura exitosa
                        if last_successful_filepath and os.path.exists(last_successful_filepath):
                            try:
                                shutil.copy2(last_successful_filepath, filepath)
                                if last_logged_state != 'window_obscured':
                                    msg_state = f"  [*] Ventana '{self.window_query}' oculta o minimizada. Reutilizando última captura."
                                    if hasattr(self, '_sidekick') and self._sidekick:
                                        self._sidekick.safe_print(msg_state)
                                    else:
                                        print(msg_state)
                                    last_logged_state = 'window_obscured'
                            except Exception as e:
                                msg_err = f"  [ERROR] Falló duplicación de captura: {e}"
                                if hasattr(self, '_sidekick') and self._sidekick:
                                    self._sidekick.safe_print(msg_err)
                                else:
                                    print(msg_err)
                        else:
                            if last_logged_state != 'window_unavailable':
                                msg_state = f"  [WARN] Ventana '{self.window_query}' no disponible y sin captura previa."
                                if hasattr(self, '_sidekick') and self._sidekick:
                                    self._sidekick.safe_print(msg_state)
                                else:
                                    print(msg_state)
                                last_logged_state = 'window_unavailable'
                else:
                    # Captura de pantalla completa normal
                    try:
                        subprocess.run(["screencapture", "-x", "-t", "jpg", filepath], check=True)
                    except Exception as e:
                        msg_err = f"  [WARN] Falló captura de pantalla: {e}"
                        if hasattr(self, '_sidekick') and self._sidekick:
                            self._sidekick.safe_print(msg_err)
                        else:
                            print(msg_err)
                
                last_capture_time = now
                
            time.sleep(0.1)

    def _auto_detect_device(self):
        """Detecta automáticamente el dispositivo según el modo."""
        if self.mode == "output":
            idx, name = find_system_device()
            if idx is None:
                print("  [ERROR] BlackHole no encontrado. Ejecuta: ./setup.sh")
                sys.exit(1)
            self.device_idx = idx
            print(f"  Dispositivo sistema: {name}")

        elif self.mode == "input":
            idx, name = find_mic_device()
            if idx is None:
                print("  [ERROR] Micrófono no encontrado.")
                sys.exit(1)
            self.device_idx = idx
            print(f"  Micrófono: {name}")

        elif self.mode == "both":
            if self.channels_mode == "split":
                idx, name = find_both_device()
                if idx is None:
                    print("  [ERROR] No se encontró Aggregate Device.")
                    print("  Crea uno en Audio MIDI Setup que combine BlackHole + tu micrófono.")
                    sys.exit(1)
                self.device_idx = idx
                print(f"  Dispositivo: {name}")
            else:
                sys_idx, sys_name = find_system_device()
                mic_idx, mic_name = find_mic_device()
                if sys_idx is None:
                    print("  [ERROR] BlackHole no encontrado. Ejecuta: ./setup.sh")
                    sys.exit(1)
                if mic_idx is None:
                    print("  [ERROR] Micrófono no encontrado.")
                    sys.exit(1)
                self.system_device_idx = sys_idx
                self.mic_device_idx = mic_idx
                self.device_idx = sys_idx
                print(f"  Sistema: {sys_name}")
                print(f"  Micrófono: {mic_name}")

    def _get_channels(self):
        """Retorna el número de canales según el modo."""
        if self.mode == "both" and self.channels_mode == "split":
            return 2
        return DEFAULT_CHANNELS

    def start_recording(self):
        """Inicia la grabación."""
        self.recording = True
        self.start_time = time.time()
        
        if self.screenshots:
            import threading
            self._screenshot_thread = threading.Thread(target=self._take_screenshots_loop, daemon=True)
            self._screenshot_thread.start()
        channels = self._get_channels()

        msg = [
            f"\n  Grabando → {self.output_file}",
            f"  Sample rate: {self.sample_rate} Hz | Canales: {channels}"
        ]
        if self.duration:
            msg.append(f"  Duración: {self.duration}s")
        if hasattr(self, '_sidekick') and self._sidekick:
            msg.append("  Usa /stop o /quit para detener\n")
        else:
            msg.append("  Presiona Ctrl+C para detener\n")

        msg_str = "\n".join(msg)
        if hasattr(self, '_sidekick') and self._sidekick:
            self._sidekick.safe_print(msg_str)
        else:
            print(msg_str)

        try:
            if self.mode == "both" and self.channels_mode == "mix":
                self._start_mixed_recording()
            else:
                self._start_stream_recording(channels)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_recording()

    def _start_stream_recording(self, channels):
        """Grabación con un solo stream."""
        device_channels = get_device_channels(self.device_idx, "input")
        file_channels = channels

        with sf.SoundFile(
            self.output_file,
            mode="w",
            samplerate=self.sample_rate,
            channels=file_channels,
            format="WAV",
            subtype="PCM_16"
        ) as wf:
            stream_active = True

            def callback(indata, frames, time_info, status):
                nonlocal stream_active
                if self.paused:
                    return
                if status:
                    if not (hasattr(self, '_sidekick') and self._sidekick):
                        print(f"  [WARN] {status}")

                data = indata.copy()
                if device_channels == 1 and file_channels == 2:
                    data = np.column_stack([data, data])

                wf.write(data)
                self.frames_written += frames

                # Send audio to sidekick if linked
                if hasattr(self, '_sidekick') and self._sidekick:
                    self._sidekick.send_external_audio(data)

                # Only print timer when NOT in sidekick mode
                # (sidekick mode uses a separate timer thread)
                if not (hasattr(self, '_sidekick') and self._sidekick):
                    elapsed = time.time() - self.start_time - self.total_paused_time
                    if self.paused and self.pause_start_time:
                        elapsed = self.pause_start_time - self.start_time - self.total_paused_time
                    mins, secs = divmod(int(elapsed), 60)
                    print(f"\r  ● {mins:02d}:{secs:02d}", end="", flush=True)

                if self.duration:
                    elapsed = time.time() - self.start_time - self.total_paused_time
                    if self.paused and self.pause_start_time:
                        elapsed = self.pause_start_time - self.start_time - self.total_paused_time
                    if elapsed >= self.duration:
                        self.recording = False
                        stream_active = False

            with sd.InputStream(
                device=self.device_idx,
                channels=device_channels,
                samplerate=self.sample_rate,
                blocksize=BLOCK_SIZE,
                callback=callback
            ):
                while self.recording and stream_active:
                    time.sleep(0.1)

    def _start_mixed_recording(self):
        """Grabación mezclada de sistema + micrófono."""
        with sf.SoundFile(
            self.output_file,
            mode="w",
            samplerate=self.sample_rate,
            channels=DEFAULT_CHANNELS,
            format="WAV",
            subtype="PCM_16"
        ) as self.file:
            system_chunk = []
            mic_chunk = []
            chunk_lock = {"system": None, "mic": None}

            def system_callback(indata, frames, time_info, status):
                chunk_lock["system"] = indata.copy()

            def mic_callback(indata, frames, time_info, status):
                chunk_lock["mic"] = indata.copy()

            def mix_and_write():
                if self.paused:
                    chunk_lock["system"] = None
                    chunk_lock["mic"] = None
                    return
                if chunk_lock["system"] is not None and chunk_lock["mic"] is not None:
                    sys_data = chunk_lock["system"]
                    mic_data = chunk_lock["mic"]

                    if sys_data.ndim == 1:
                        sys_data = sys_data.reshape(-1, 1)
                    if mic_data.ndim == 1:
                        mic_data = mic_data.reshape(-1, 1)

                    min_len = min(len(sys_data), len(mic_data))
                    sys_data = sys_data[:min_len]
                    mic_data = mic_data[:min_len]

                    if sys_data.shape[1] == 1 and mic_data.shape[1] == 1:
                        mixed = np.column_stack([sys_data, mic_data])
                    elif sys_data.shape[1] == 2 and mic_data.shape[1] == 1:
                        mic_stereo = np.column_stack([mic_data, mic_data])
                        mixed = (sys_data * 0.5 + mic_stereo * 0.5)
                    elif sys_data.shape[1] == 1 and mic_data.shape[1] == 2:
                        sys_stereo = np.column_stack([sys_data, sys_data])
                        mixed = (sys_stereo * 0.5 + mic_data * 0.5)
                    else:
                        mixed = (sys_data * 0.5 + mic_data * 0.5)

                    self.file.write(mixed.astype(np.float32))
                    self.frames_written += min_len
                    chunk_lock["system"] = None
                    chunk_lock["mic"] = None

                    elapsed = time.time() - self.start_time - self.total_paused_time
                    if self.paused and self.pause_start_time:
                        elapsed = self.pause_start_time - self.start_time - self.total_paused_time

                    if not (hasattr(self, '_sidekick') and self._sidekick):
                        mins, secs = divmod(int(elapsed), 60)
                        print(f"\r  ● {mins:02d}:{secs:02d}", end="", flush=True)

                    if self.duration and elapsed >= self.duration:
                        self.recording = False

            with sd.InputStream(
                device=self.system_device_idx,
                channels=DEFAULT_CHANNELS,
                samplerate=self.sample_rate,
                blocksize=BLOCK_SIZE,
                callback=system_callback
            ), sd.InputStream(
                device=self.mic_device_idx,
                channels=DEFAULT_CHANNELS,
                samplerate=self.sample_rate,
                blocksize=BLOCK_SIZE,
                callback=mic_callback
            ):
                while self.recording:
                    mix_and_write()
                    sd.sleep(10)

    def stop_recording(self):
        """Detiene la grabación y muestra resumen."""
        if self._stopped:
            return
        self._stopped = True
        self.recording = False
        
        if self.start_time:
            elapsed = time.time() - self.start_time - self.total_paused_time
            if self.paused and self.pause_start_time:
                elapsed = self.pause_start_time - self.start_time - self.total_paused_time
        else:
            elapsed = 0

        # Move file to self.output_dir if changed
        current_path = Path(self.output_file)
        desired_dir = Path(self.output_dir)
        current_txt_path = current_path.with_suffix(".txt")
        if current_path.parent != desired_dir:
            new_path = desired_dir / current_path.name
            new_txt_path = desired_dir / current_txt_path.name
            try:
                desired_dir.mkdir(parents=True, exist_ok=True)
                import shutil
                if current_path.exists():
                    shutil.move(str(current_path), str(new_path))
                    self.output_file = str(new_path)
                if current_txt_path.exists():
                    shutil.move(str(current_txt_path), str(new_txt_path))
            except Exception as e:
                msg = f"  [ERROR] No se pudo mover los archivos al nuevo directorio: {e}"
                if hasattr(self, '_sidekick') and self._sidekick:
                    self._sidekick.safe_print(msg)
                else:
                    print(msg)

        # Comprimir antes del resumen para que el tamaño informado sea el final
        if self.compress:
            def _say(msg):
                if hasattr(self, '_sidekick') and self._sidekick:
                    self._sidekick.safe_print(msg)
                else:
                    print(msg)

            self.output_file = compress_audio(
                self.output_file,
                bitrate=self.audio_bitrate,
                keep_original=self.keep_wav,
                printer=_say,
            )

        output_path = Path(self.output_file)
        txt_path = output_path.with_suffix(".txt")
        if output_path.exists():
            size_mb = output_path.stat().st_size / (1024 * 1024)
        else:
            size_mb = 0

        mins, secs = divmod(int(elapsed), 60)

        summary = [
            "\nRecording ended",
            f"Duration: {mins:02d}:{secs:02d}",
            f"Audio file: {self.output_file}"
        ]
        if txt_path.exists():
            summary.append(f"Transcription file: {txt_path}")
        summary.append(f"Audio file size: {size_mb:.2f} MB\n")
        
        summary_str = "\n".join(summary)
        if hasattr(self, '_sidekick') and self._sidekick:
            self._sidekick.safe_print(summary_str)
        else:
            print(summary_str)


def main():
    parser = argparse.ArgumentParser(
        prog="eco",
        description="Eco - Grabador de audio interno para macOS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  %(prog)s --output                        Grabar audio del sistema
  %(prog)s --input                         Grabar micrófono
  %(prog)s --both --channels split         Grabar ambos, canales separados
  %(prog)s --both --channels mix           Grabar ambos, mezclado
  %(prog)s --output --duration 60          Grabar 60 segundos
  %(prog)s --output --output-file reunion  Guardar como reunion.wav
  %(prog)s --list-devices                  Ver dispositivos de audio
  %(prog)s --output --llm mimo             Grabar + transcribir + LLM
  %(prog)s --output --no-compress          Conservar el WAV sin comprimir
  %(prog)s --output --audio-bitrate 24k    Comprimir más agresivamente
        """
    )

    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    mode_group = parser.add_argument_group("modos de grabación")
    mode_group.add_argument("--output", action="store_true",
                           help="Graba solo audio del sistema (BlackHole)")
    mode_group.add_argument("--input", action="store_true",
                           help="Graba solo micrófono")
    mode_group.add_argument("--both", action="store_true",
                           help="Graba ambos (sistema + micrófono)")

    options = parser.add_argument_group("opciones")
    options.add_argument("--duration", "-d", type=int, metavar="N",
                        help="Duración en segundos (default: hasta Ctrl+C)")
    options.add_argument("--output-file", "-o", metavar="FILE",
                        help="Nombre del archivo WAV (default: eco_YYYYMMDD_HHMMSS.wav)")
    options.add_argument("--device", "-D", metavar="NAME",
                        help="Dispositivo de audio específico (ver --list-devices)")
    options.add_argument("--channels", "-c", choices=["split", "mix"], default="mix",
                        help="Canales con --both: 'split' (L/R) o 'mix' (mezclado)")
    options.add_argument("--sample-rate", "-r", type=int, default=DEFAULT_SAMPLE_RATE,
                        help=f"Frecuencia de muestreo (default: {DEFAULT_SAMPLE_RATE})")
    options.add_argument("--list-devices", "-l", action="store_true",
                        help="Muestra dispositivos de audio disponibles")
    options.add_argument("--llm", choices=["anthropic", "openai", "mimo", "minimax"],
                         help="Activa sidekick (transcripción + LLM)")
    options.add_argument("--silence-timeout", type=float, default=120.0,
                         help="Tiempo de silencio (segundos) antes de auto-cerrar (0 para desactivar, por defecto 120)")
    options.add_argument("--start", action="store_true",
                         help="Inicia la grabación/transcripción inmediatamente (default: False, espera a /start)")
    options.add_argument("--screenshots", action="store_true",
                         help="Toma capturas de pantalla periódicas de la reunión")
    options.add_argument("--screenshot-interval", type=int, default=10,
                         help="Intervalo en segundos entre capturas de pantalla (default: 10)")
    options.add_argument("--window", metavar="NAME_OR_ID",
                         help="Filtra las capturas de pantalla por nombre de aplicación, título de ventana o ID. Usa 'list' para ver las ventanas disponibles.")

    audio_opts = parser.add_argument_group("archivo de audio")
    audio_opts.add_argument("--no-compress", action="store_true",
                            help="Conserva el WAV sin comprimir (por defecto se convierte a Opus)")
    audio_opts.add_argument("--keep-wav", action="store_true",
                            help="Guarda el WAV original además del Opus comprimido")
    audio_opts.add_argument("--audio-bitrate", default=DEFAULT_AUDIO_BITRATE, metavar="RATE",
                            help=f"Bitrate del Opus resultante (default: {DEFAULT_AUDIO_BITRATE})")

    args = parser.parse_args()

    print(f"\n  ╔═══════════════════════════════════════╗")
    print(f"  ║       Eco v{VERSION}                      ║")
    print(f"  ║       Grabador de Audio Interno        ║")
    print(f"  ╚═══════════════════════════════════════╝\n")

    if args.list_devices:
        list_devices()
        return

    if args.window and args.window.lower() == "list":
        list_visible_windows()
        return

    if args.window:
        args.screenshots = True

        w_info = find_window(args.window)
        if w_info and w_info.get("owner") == "Google Chrome":
            import chrome_capture
            if args.window.strip().isdigit():
                print("  [WARN] Pasaste un ID de ventana: si Chrome se reinicia para activar "
                      "la depuración remota, ese ID cambiará. Usa un nombre (ej. --window \"Chrome\") "
                      "en vez de un ID al capturar ventanas de Chrome.")
            chrome_capture.ensure_chrome_debugging()

    if not args.output and not args.input and not args.both:
        parser.print_help()
        print("\n  [ERROR] Selecciona un modo: --output, --input, o --both")
        sys.exit(1)

    if args.channels == "split" and not args.both:
        print("  [WARN] --channels solo tiene efecto con --both, ignorando")

    if args.output:
        mode = "output"
    elif args.input:
        mode = "input"
    else:
        mode = "both"

    device_idx = None
    if args.device:
        device_idx, device_name = find_device(args.device, "input")
        if device_idx is None:
            print(f"  [ERROR] Dispositivo '{args.device}' no encontrado")
            sys.exit(1)
        print(f"  Dispositivo: {device_name}")

    grabador = Eco(
        mode=mode,
        channels_mode=args.channels,
        device_idx=device_idx,
        sample_rate=args.sample_rate,
        duration=args.duration,
        output_file=args.output_file,
        screenshots=args.screenshots,
        screenshot_interval=args.screenshot_interval,
        window_query=args.window,
        compress=not args.no_compress,
        audio_bitrate=args.audio_bitrate,
        keep_wav=args.keep_wav,
    )

    signal.signal(signal.SIGINT, lambda *_: setattr(grabador, 'recording', False))

    # If --llm is specified, start sidekick along with recording
    if args.llm:
        _start_with_sidekick(grabador, args.llm, args.silence_timeout, auto_start=args.start)
    else:
        if args.start:
            grabador.start_recording()
        else:
            print("  [!] Grabación no iniciada. En modo standalone, debes usar el flag --start para iniciar la grabación inmediatamente.")
            sys.exit(0)


def _start_with_sidekick(grabador, llm_provider, silence_timeout=120.0, auto_start=False):
    """Start recording with sidekick (transcription + LLM)."""
    import threading
    from sidekick import Sidekick
 
    # Remove SIGINT handler — let sidekick handle Ctrl+C via KeyboardInterrupt
    signal.signal(signal.SIGINT, signal.SIG_DFL)
 
    # Start sidekick with external audio mode
    sidekick = Sidekick(
        llm_provider=llm_provider, 
        device_idx=grabador.device_idx, 
        external_audio=True,
        silence_timeout=silence_timeout,
        auto_start=auto_start
    )

    # Link sidekick to grabador for /stop-listening command
    sidekick._grabador = grabador

    # Link grabador to sidekick for audio forwarding
    grabador._sidekick = sidekick

    if auto_start:
        # Start recording in a daemon thread
        recording_thread = threading.Thread(target=grabador.start_recording, daemon=True)
        recording_thread.start()

    # Start sidekick (initializes Deepgram, sets is_running, runs CLI)
    # Blocks until user exits via /quit or Ctrl+C
    sidekick.start()

    # Clean up: stop recording and exit
    grabador.recording = False
    if not grabador._stopped:
        grabador.stop_recording()
    sys.exit(0)


if __name__ == "__main__":
    main()
