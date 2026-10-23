#!/usr/bin/env python3
"""
Eco - macOS Internal Audio Recorder
Uses BlackHole as a virtual audio driver to capture system audio.
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

# Audio compression: PCM WAV is needed during live capture, but as an archive format
# it stores voice at 1536 kbps. Opus mono at 32 kbps reduces file size ~54x without
# perceptible loss in intelligibility or transcription accuracy.
DEFAULT_AUDIO_BITRATE = "32k"


def compress_audio(wav_path, bitrate=DEFAULT_AUDIO_BITRATE, keep_original=False,
                   printer=None):
    """Convert a WAV file to Opus mono and delete original if conversion succeeds.

    Returns the path to the .opus file, or original WAV if conversion fails. Never
    deletes original without verifying that the output exists and has content.
    """
    import shutil
    import subprocess

    say = printer or print
    wav_path = Path(wav_path)

    if not wav_path.exists():
        return str(wav_path)

    if shutil.which("ffmpeg") is None:
        say("  [WARN] ffmpeg not found, keeping uncompressed WAV")
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
        say(f"  [WARN] Failed to compress audio, keeping WAV: {e}")
        if opus_path.exists():
            opus_path.unlink()
        return str(wav_path)

    if not opus_path.exists() or opus_path.stat().st_size == 0:
        say("  [WARN] Compression produced an empty file, keeping WAV")
        if opus_path.exists():
            opus_path.unlink()
        return str(wav_path)

    new_size = opus_path.stat().st_size
    ratio = original_size / new_size if new_size else 0
    say(f"  Compressed to Opus {bitrate} mono: "
        f"{original_size / 1048576:.1f} MB -> {new_size / 1048576:.1f} MB ({ratio:.0f}x)")

    if not keep_original:
        wav_path.unlink()

    return str(opus_path)


def list_devices():
    """Display available audio input and output devices."""
    devices = sd.query_devices()
    print("\n  Available Audio Devices")
    print("  " + "=" * 50)

    print("\n  OUTPUT:")
    print("  " + "-" * 50)
    for i, dev in enumerate(devices):
        if dev["max_output_channels"] > 0:
            default = " ← default" if i == sd.default.device[1] else ""
            print(f"    [{i}] {dev['name']}{default}")
            print(f"        Channels: {dev['max_output_channels']} | Sample rate: {int(dev['default_samplerate'])} Hz")

    print("\n  INPUT:")
    print("  " + "-" * 50)
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            default = " ← default" if i == sd.default.device[0] else ""
            print(f"    [{i}] {dev['name']}{default}")
            print(f"        Channels: {dev['max_input_channels']} | Sample rate: {int(dev['default_samplerate'])} Hz")

    print()


def find_device(name_pattern, kind="input"):
    """Find device by name pattern (case-insensitive)."""
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
    """Return number of channels for device."""
    devices = sd.query_devices()
    dev = devices[device_idx]
    if kind == "input":
        return min(dev["max_input_channels"], DEFAULT_CHANNELS)
    return min(dev["max_output_channels"], DEFAULT_CHANNELS)


def find_system_device():
    """Find device to record system audio (BlackHole)."""
    idx, name = find_device("blackhole", "input")
    if idx is not None:
        return idx, name

    idx, name = find_device("background music", "input")
    if idx is not None:
        return idx, name

    return None, None


def find_mic_device():
    """Find native microphone device."""
    for pattern in ["built-in", "macbook", "internal", "built-in microphone", "micrófono incorporado", "microfono incorporado"]:
        idx, name = find_device(pattern, "input")
        if idx is not None:
            return idx, name

    devices = sd.query_devices()
    default_idx = sd.default.device[0]
    if default_idx is not None and devices[default_idx]["max_input_channels"] > 0:
        return default_idx, devices[default_idx]["name"]

    return None, None


def find_both_device():
    """Find Aggregate Device or BlackHole to record both."""
    for pattern in ["aggregate", "eco"]:
        idx, name = find_device(pattern, "input")
        if idx is not None:
            return idx, name

    return find_system_device()


def get_visible_windows():
    """Return list of visible windows (dicts with id, owner, title, bounds, etc.)."""
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
    except Exception:
        return []


def list_visible_windows():
    """Display a list of visible windows and their IDs."""
    try:
        windows = get_visible_windows()
        if not windows:
            print("\n  No visible windows found.")
            return
        
        print("\n  Available Visible Windows")
        print("  " + "=" * 85)
        print(f"  {'ID':<10} | {'Application/Owner':<25} | {'Window Title'}")
        print("  " + "-" * 85)
        for w in windows:
            owner = w.get("owner", "Unknown")
            title = w.get("title", "")
            owner_trunc = owner[:25]
            print(f"  {w['id']:<10} | {owner_trunc:<25} | {title}")
        print()
    except Exception as e:
        print(f"  [ERROR] Failed to list windows: {e}")


def find_window(query):
    """Find window by ID, owner, or title. Returns dict with id, owner, title or None."""
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
    """Main audio recorder."""

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

        # "both" + "mix" requires two separate devices (system audio and mic),
        # so they are detected automatically regardless of explicit device_idx.
        needs_both_mix_detection = self.mode == "both" and self.channels_mode == "mix"

        if device_idx is not None and not needs_both_mix_detection:
            self.device_idx = device_idx
        else:
            if device_idx is not None and needs_both_mix_detection:
                print("  [WARN] --device does not apply with --both --channels mix "
                      "(system audio and microphone are detected automatically)")
            self._auto_detect_device()

    def reset_output_file(self):
        """Generate a new output filename for a new session."""
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
        """Attempt to capture a specific window. Uses CDP for Chrome if remote
        debugging is available; falls back to native screencapture -l<id> otherwise.
        """
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
        """Background periodic screenshot loop."""
        import subprocess
        import os
        import time
        import glob
        import shutil
        from pathlib import Path

        # Generate screenshots directory alongside the WAV file
        wav_path = Path(self.output_file)
        self.screenshots_dir = wav_path.parent / f"{wav_path.stem}_screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        
        msg = f"  [*] Screenshots directory: {self.screenshots_dir}"
        if hasattr(self, '_sidekick') and self._sidekick:
            self._sidekick.safe_print(msg)
        else:
            print(msg)

        # Allow terminal focus transitions to stabilize
        time.sleep(1.0)

        current_window_id = None
        current_window_owner = None
        current_window_bounds = None
        last_logged_state = None  # 'window', 'window_obscured', 'window_unavailable', 'fullscreen'

        def _bounds_of(w_info):
            return {"x": w_info["x"], "y": w_info["y"], "width": w_info["width"], "height": w_info["height"]}

        # Initialize last successful screenshot from disk if present
        existing_shots = sorted(glob.glob(str(self.screenshots_dir / "*.jpg")))
        last_successful_filepath = existing_shots[-1] if existing_shots else None

        # Window target initialization
        if self.window_query:
            w_info = find_window(self.window_query)
            if w_info:
                current_window_id = w_info["id"]
                current_window_owner = w_info["owner"]
                current_window_bounds = _bounds_of(w_info)
                msg_init = f"  [*] Capturing window: ID {current_window_id} ({w_info['owner']} - {w_info['title']})"
                if hasattr(self, '_sidekick') and self._sidekick:
                    self._sidekick.safe_print(msg_init)
                else:
                    print(msg_init)
                last_logged_state = 'window'
            else:
                msg_init = f"  [WARN] Window '{self.window_query}' not found at this time. Waiting for it to appear."
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

                    # Try resolving window ID if not known
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
                            # Re-resolve window ID in case of transition or title change
                            w_info = find_window(self.window_query)
                            if w_info:
                                current_window_id = w_info["id"]
                                current_window_owner = w_info["owner"]
                                current_window_bounds = _bounds_of(w_info)
                                time.sleep(0.5)
                                captured = self._capture_window(
                                    current_window_id, current_window_owner, current_window_bounds, filepath
                                )

                    if captured:
                        last_successful_filepath = filepath
                        if last_logged_state != 'window':
                            msg_state = f"  [*] Window active. Capturing ID {current_window_id}."
                            if hasattr(self, '_sidekick') and self._sidekick:
                                self._sidekick.safe_print(msg_state)
                            else:
                                print(msg_state)
                            last_logged_state = 'window'
                    else:
                        # Reuse last successful capture instead of falling back to full desktop
                        if last_successful_filepath and os.path.exists(last_successful_filepath):
                            try:
                                shutil.copy2(last_successful_filepath, filepath)
                                if last_logged_state != 'window_obscured':
                                    msg_state = f"  [*] Window '{self.window_query}' hidden or minimized. Reusing last capture."
                                    if hasattr(self, '_sidekick') and self._sidekick:
                                        self._sidekick.safe_print(msg_state)
                                    else:
                                        print(msg_state)
                                    last_logged_state = 'window_obscured'
                            except Exception as e:
                                msg_err = f"  [ERROR] Failed to duplicate capture: {e}"
                                if hasattr(self, '_sidekick') and self._sidekick:
                                    self._sidekick.safe_print(msg_err)
                                else:
                                    print(msg_err)
                        else:
                            if last_logged_state != 'window_unavailable':
                                msg_state = f"  [WARN] Window '{self.window_query}' unavailable and no previous capture exists."
                                if hasattr(self, '_sidekick') and self._sidekick:
                                    self._sidekick.safe_print(msg_state)
                                else:
                                    print(msg_state)
                                last_logged_state = 'window_unavailable'
                else:
                    # Standard full-screen capture
                    try:
                        subprocess.run(["screencapture", "-x", "-t", "jpg", filepath], check=True)
                    except Exception as e:
                        msg_err = f"  [WARN] Screenshot capture failed: {e}"
                        if hasattr(self, '_sidekick') and self._sidekick:
                            self._sidekick.safe_print(msg_err)
                        else:
                            print(msg_err)
                
                last_capture_time = now
                
            time.sleep(0.1)

    def _auto_detect_device(self):
        """Automatically detect input device based on chosen mode."""
        if self.mode == "output":
            idx, name = find_system_device()
            if idx is None:
                print("  [ERROR] BlackHole not found. Run: ./setup.sh")
                sys.exit(1)
            self.device_idx = idx
            print(f"  System device: {name}")

        elif self.mode == "input":
            idx, name = find_mic_device()
            if idx is None:
                print("  [ERROR] Microphone not found.")
                sys.exit(1)
            self.device_idx = idx
            print(f"  Microphone: {name}")

        elif self.mode == "both":
            if self.channels_mode == "split":
                idx, name = find_both_device()
                if idx is None:
                    print("  [ERROR] Aggregate Device not found.")
                    print("  Create one in Audio MIDI Setup that combines BlackHole + your microphone.")
                    sys.exit(1)
                self.device_idx = idx
                print(f"  Device: {name}")
            else:
                sys_idx, sys_name = find_system_device()
                mic_idx, mic_name = find_mic_device()
                if sys_idx is None:
                    print("  [ERROR] BlackHole not found. Run: ./setup.sh")
                    sys.exit(1)
                if mic_idx is None:
                    print("  [ERROR] Microphone not found.")
                    sys.exit(1)
                self.system_device_idx = sys_idx
                self.mic_device_idx = mic_idx
                self.device_idx = sys_idx
                print(f"  System: {sys_name}")
                print(f"  Microphone: {mic_name}")

    def _get_channels(self):
        """Return number of channels based on mode."""
        if self.mode == "both" and self.channels_mode == "split":
            return 2
        return DEFAULT_CHANNELS

    def start_recording(self):
        """Start audio recording session."""
        self.recording = True
        self.start_time = time.time()
        
        if self.screenshots:
            import threading
            self._screenshot_thread = threading.Thread(target=self._take_screenshots_loop, daemon=True)
            self._screenshot_thread.start()
        channels = self._get_channels()

        msg = [
            f"\n  Recording -> {self.output_file}",
            f"  Sample rate: {self.sample_rate} Hz | Channels: {channels}"
        ]
        if self.duration:
            msg.append(f"  Duration: {self.duration}s")
        if hasattr(self, '_sidekick') and self._sidekick:
            msg.append("  Use /stop or /quit to stop\n")
        else:
            msg.append("  Press Ctrl+C to stop\n")

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
        """Single-stream audio recording."""
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
        """Blended recording combining system audio and microphone."""
        with sf.SoundFile(
            self.output_file,
            mode="w",
            samplerate=self.sample_rate,
            channels=DEFAULT_CHANNELS,
            format="WAV",
            subtype="PCM_16"
        ) as self.file:
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
        """Stop recording and display summary."""
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

        # Move files to output_dir if it was changed dynamically
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
                msg = f"  [ERROR] Failed to move files to new directory: {e}"
                if hasattr(self, '_sidekick') and self._sidekick:
                    self._sidekick.safe_print(msg)
                else:
                    print(msg)

        # Compress before summary so reported size is final
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
        description="Eco - macOS Internal Audio Recorder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --output                        Record system audio
  %(prog)s --input                         Record microphone
  %(prog)s --both --channels split         Record both, separate channels
  %(prog)s --both --channels mix           Record both, mixed
  %(prog)s --output --duration 60          Record for 60 seconds
  %(prog)s --output --output-file meeting  Save as meeting.wav
  %(prog)s --list-devices                  View audio devices
  %(prog)s --output --llm mimo             Record + transcribe + LLM assistant
  %(prog)s --output --no-compress          Keep uncompressed WAV
  %(prog)s --output --audio-bitrate 24k    Compress more aggressively
        """
    )

    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    mode_group = parser.add_argument_group("recording modes")
    mode_group.add_argument("--output", action="store_true",
                           help="Record system audio only (BlackHole)")
    mode_group.add_argument("--input", action="store_true",
                           help="Record microphone only")
    mode_group.add_argument("--both", action="store_true",
                           help="Record both (system audio + microphone)")

    options = parser.add_argument_group("options")
    options.add_argument("--duration", "-d", type=int, metavar="N",
                        help="Duration in seconds (default: record until Ctrl+C)")
    options.add_argument("--output-file", "-o", metavar="FILE",
                        help="Output WAV filename (default: eco_YYYYMMDD_HHMMSS.wav)")
    options.add_argument("--device", "-D", metavar="NAME",
                        help="Target specific audio device (see --list-devices)")
    options.add_argument("--channels", "-c", choices=["split", "mix"], default="mix",
                        help="Channels for --both: 'split' (L/R) or 'mix' (blended)")
    options.add_argument("--sample-rate", "-r", type=int, default=DEFAULT_SAMPLE_RATE,
                        help=f"Sampling rate (default: {DEFAULT_SAMPLE_RATE})")
    options.add_argument("--list-devices", "-l", action="store_true",
                        help="Show available audio devices")
    options.add_argument("--llm", choices=["anthropic", "openai", "mimo", "minimax"],
                         help="Activate Sidekick (real-time transcription + LLM)")
    options.add_argument("--silence-timeout", type=float, default=120.0,
                         help="Silence timeout (seconds) before auto-quit (0 to disable, default: 120)")
    options.add_argument("--start", action="store_true",
                         help="Start recording/transcription immediately (default: False, waits for /start)")
    options.add_argument("--screenshots", action="store_true",
                         help="Take periodic screenshots during the meeting")
    options.add_argument("--screenshot-interval", type=int, default=10,
                         help="Interval in seconds between screenshots (default: 10)")
    options.add_argument("--window", metavar="NAME_OR_ID",
                         help="Filter screenshots by application name, window title, or ID. Use 'list' to view available windows.")

    audio_opts = parser.add_argument_group("audio file")
    audio_opts.add_argument("--no-compress", action="store_true",
                            help="Keep uncompressed WAV (converted to Opus by default)")
    audio_opts.add_argument("--keep-wav", action="store_true",
                            help="Keep original WAV in addition to compressed Opus")
    audio_opts.add_argument("--audio-bitrate", default=DEFAULT_AUDIO_BITRATE, metavar="RATE",
                            help=f"Bitrate of resulting Opus file (default: {DEFAULT_AUDIO_BITRATE})")

    args = parser.parse_args()

    print(f"\n  ╔═══════════════════════════════════════╗")
    print(f"  ║       Eco v{VERSION}                      ║")
    print(f"  ║       macOS Internal Audio Recorder   ║")
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
            if not chrome_capture.is_debug_port_open():
                print("  [i] You are capturing a Chrome window. If it is playing video and you "
                      "completely cover it with another window, the capture may freeze or turn black "
                      "until visible again (limitation of Chrome hardware video compositing). "
                      "Audio and transcription are unaffected.")

    if not args.output and not args.input and not args.both:
        parser.print_help()
        print("\n  [ERROR] Select a mode: --output, --input, or --both")
        sys.exit(1)

    if args.channels == "split" and not args.both:
        print("  [WARN] --channels only takes effect with --both, ignoring")

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
            print(f"  [ERROR] Device '{args.device}' not found")
            sys.exit(1)
        print(f"  Device: {device_name}")

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
            print("  [!] Recording not started. In standalone mode, pass the --start flag to begin recording immediately.")
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

    # Link sidekick to grabador for /stop command
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
