"""
Eco Sidekick - Asistente IA para reuniones
Captura audio en tiempo real, transcribe con Deepgram, y responde preguntas con LLM.
"""

import asyncio
import json
import os
import select
import shutil
import signal
import sys
import termios
import threading
import time
import tty
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd

from llm_client import get_llm_client, list_available_providers

# Deepgram imports
from deepgram import DeepgramClient

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    from pathlib import Path
    # 1. CWD .env (highest priority)
    load_dotenv(dotenv_path=Path.cwd() / ".env")
    # 2. ~/.eco.env (user home fallback)
    load_dotenv(dotenv_path=Path.home() / ".eco.env")
    # 3. ~/.config/eco/.env (standard config folder fallback)
    load_dotenv(dotenv_path=Path.home() / ".config" / "eco" / ".env")
except ImportError:
    pass

VERSION = "1.1.0"
DEFAULT_SAMPLE_RATE = 16000  # Deepgram works best with 16kHz
BLOCK_SIZE = 1024
SLASH_MENU_HEIGHT = 25
TRANSCRIBE_INTERVAL_SECONDS = 1.0  # Time in seconds to buffer audio before transcribing
CONTEXT_MAX_LINES = 100  # Maximum lines in context buffer
CONTEXT_WINDOW_SECONDS = 300  # 5 minutes of context


class ConversationContext:
    """Manages conversation context with timestamps."""

    def __init__(self, max_lines=CONTEXT_MAX_LINES):
        self.lines = deque(maxlen=max_lines)
        self.lock = threading.Lock()

    def add(self, speaker: str, text: str, timestamp: float = None):
        """Add a line to the context."""
        if timestamp is None:
            timestamp = time.time()
        with self.lock:
            self.lines.append({
                "speaker": speaker,
                "text": text,
                "timestamp": timestamp,
                "time_str": datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
            })

    def get_context(self, seconds: int = CONTEXT_WINDOW_SECONDS) -> str:
        """Get recent context as formatted string."""
        cutoff = time.time() - seconds
        with self.lock:
            recent = [l for l in self.lines if l["timestamp"] >= cutoff]

        if not recent:
            return ""

        lines = []
        for entry in recent:
            lines.append(f"[{entry['time_str']}] {entry['speaker']}: {entry['text']}")
        return "\n".join(lines)

    def clear(self):
        """Clear all context."""
        with self.lock:
            self.lines.clear()


class DeepgramTranscriber:
    """Real-time transcription using Deepgram batch API with periodic buffer processing."""

    def __init__(self, context: ConversationContext, api_key: str = None, on_transcript=None, on_silence_timeout=None, silence_timeout=120.0):
        self.context = context
        self.api_key = api_key or os.getenv("DEEPGRAM_API_KEY", "")
        self.is_running = False
        self._client = None
        self._buffer = []
        self._buffer_lock = threading.Lock()
        self._process_thread = None
        self._sample_rate = 16000
        self.on_transcript = on_transcript
        self.on_silence_timeout = on_silence_timeout
        self.silence_timeout = float(silence_timeout) if silence_timeout is not None else 0.0
        
        # VAD State Variables
        self.rms_history = deque(maxlen=200)  # Keep last ~10 seconds of chunk RMS values
        self.accumulated_chunks = []
        self.silence_count = 0
        self.has_speech = False
        self.continuous_silence_duration = 0.0

    def start(self, sample_rate: int = DEFAULT_SAMPLE_RATE):
        """Start Deepgram transcription."""
        if not self.api_key:
            raise ValueError("DEEPGRAM_API_KEY no configurada")

        self._client = DeepgramClient(api_key=self.api_key)
        self._sample_rate = sample_rate
        self.is_running = True

        # Start background thread that processes audio buffer periodically
        self._process_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._process_thread.start()
        print("  [Deepgram] Transcripción iniciada")

    def send_audio(self, audio_data: np.ndarray):
        """Add audio data to buffer for processing."""
        if not self.is_running:
            return
        with self._buffer_lock:
            self._buffer.append(audio_data.copy())

    def _process_loop(self):
        """Periodically process buffered audio and transcribe using Dynamic VAD."""
        import io
        import wave

        SILENCE_LIMIT_CHUNKS = 8   # Approx 0.5s of silence (endpointing)
        MAX_PHRASE_CHUNKS = 190    # Approx 12s max phrase duration
        MIN_SPEECH_CHUNKS = 8      # Require at least 0.5s of audio to transcribe

        while self.is_running:
            time.sleep(0.1)  # Check every 100ms
            if not self.is_running:
                break

            # Grab new chunks from buffer
            with self._buffer_lock:
                if not self._buffer:
                    new_chunks = []
                else:
                    new_chunks = self._buffer
                    self._buffer = []

            if not new_chunks and not self.accumulated_chunks:
                continue

            # Process new chunks
            for chunk in new_chunks:
                self.accumulated_chunks.append(chunk)
                
                # Compute absolute mean amplitude (fast RMS approximation)
                rms = float(np.mean(np.abs(chunk)))
                self.rms_history.append(rms)
                
                # Dynamic noise floor calculation (10th percentile of history)
                if len(self.rms_history) > 10:
                    sorted_history = sorted(list(self.rms_history))
                    idx = max(0, int(len(sorted_history) * 0.10))
                    noise_floor = sorted_history[idx]
                else:
                    noise_floor = 0.0002
                
                # Speech detection
                is_speech = rms > (noise_floor * 1.8) and rms > 0.0003
                
                if is_speech:
                    self.has_speech = True
                    self.silence_count = 0
                    self.continuous_silence_duration = 0.0
                else:
                    if self.has_speech:
                        self.silence_count += 1
                    # Track continuous silence duration in seconds
                    chunk_duration = len(chunk) / self._sample_rate
                    self.continuous_silence_duration += chunk_duration
                        
            # Check if we should trigger transcription
            total_chunks = len(self.accumulated_chunks)
            
            trigger = False
            if self.has_speech and total_chunks >= MIN_SPEECH_CHUNKS:
                if self.silence_count >= SILENCE_LIMIT_CHUNKS:
                    trigger = True
                elif total_chunks >= MAX_PHRASE_CHUNKS:
                    trigger = True
                    
            # Clear silent buffers if they accumulate with no speech to prevent memory leaks
            if not self.has_speech and total_chunks > MAX_PHRASE_CHUNKS:
                self.accumulated_chunks = []
                self.silence_count = 0

            # Check for silence timeout (auto-quit)
            if self.silence_timeout > 0.0 and self.continuous_silence_duration >= self.silence_timeout:
                if self.on_silence_timeout:
                    self.on_silence_timeout()
                    self.continuous_silence_duration = 0.0

            if not trigger:
                continue

            # Trigger transcription
            chunks_to_transcribe = self.accumulated_chunks
            self.accumulated_chunks = []
            self.silence_count = 0
            self.has_speech = False

            # Concatenate chunks
            audio = np.concatenate(chunks_to_transcribe)

            # Convert to int16
            if audio.dtype != np.int16:
                audio_int16 = (audio * 32767).astype(np.int16)
            else:
                audio_int16 = audio

            # Create WAV bytes (Deepgram needs header for format detection)
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)  # 16-bit
                w.setframerate(self._sample_rate)
                w.writeframes(audio_int16.tobytes())

            wav_bytes = buf.getvalue()

            # Transcribe using batch API
            try:
                response = self._client.listen.v1.media.transcribe_file(
                    request=wav_bytes,
                    model="nova-2",
                    language="multi",
                )
                transcript = response.results.channels[0].alternatives[0].transcript
                if transcript.strip():
                    self.context.add("Participante", transcript.strip())
                    if self.on_transcript:
                        self.on_transcript(transcript.strip())
                    else:
                        print(f"\n  [Transcript] {transcript.strip()}")
                        print("  > ", end="", flush=True)
            except Exception:
                pass  # Silence/noise chunks won't produce transcripts

    def transcribe_remaining(self):
        """Force transcription of any remaining audio in accumulated_chunks."""
        import io
        import wave
        
        with self._buffer_lock:
            remaining = self._buffer
            self._buffer = []
            
        chunks = self.accumulated_chunks + remaining
        self.accumulated_chunks = []
        
        if not chunks:
            return
            
        audio = np.concatenate(chunks)
        # Require at least 0.5s of audio to bother transcribing
        if len(audio) < self._sample_rate * 0.5:
            return
            
        if audio.dtype != np.int16:
            audio_int16 = (audio * 32767).astype(np.int16)
        else:
            audio_int16 = audio

        buf = io.BytesIO()
        with wave.open(buf, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self._sample_rate)
            w.writeframes(audio_int16.tobytes())

        wav_bytes = buf.getvalue()
        try:
            response = self._client.listen.v1.media.transcribe_file(
                request=wav_bytes,
                model="nova-2",
                language="multi",
            )
            transcript = response.results.channels[0].alternatives[0].transcript
            if transcript.strip():
                self.context.add("Participante", transcript.strip())
                if self.on_transcript:
                    self.on_transcript(transcript.strip())
                else:
                    print(f"\n  [Transcript] {transcript.strip()}")
        except Exception:
            pass

    def stop(self):
        """Stop transcription."""
        self.is_running = False
        if self._process_thread:
            self._process_thread.join(timeout=1.0)
        self.transcribe_remaining()


class Sidekick:
    """Main Sidekick class - AI assistant for meetings."""

    def __init__(self, llm_provider: str = None, device_idx: int = None, external_audio: bool = False, silence_timeout: float = 120.0, auto_start: bool = False):
        self.context = ConversationContext()
        self.transcriber = None
        self.llm_client = None
        self.llm_provider = llm_provider
        self.device_idx = device_idx
        self.is_running = False
        self._audio_stream = None
        self.external_audio = external_audio
        self.silence_timeout = silence_timeout
        self._grabador = None
        self.user_input = ""
        self.cursor_idx = 0
        self.menu_selected_idx = 0
        self.stdout_lock = threading.Lock()
        self.output_dir = str(Path.home() / "Desktop")
        
        self.paused = False
        self.total_paused_time = 0.0
        self.pause_start_time = None
        self.auto_start = auto_start

        # Initialize LLM
        try:
            self.llm_client = get_llm_client(llm_provider)
            providers = list_available_providers()
            print(f"  LLM disponible: {', '.join(providers)}")
        except ValueError as e:
            print(f"  [WARN] {e}")
            print("  El sidekick funcionará sin LLM (solo transcripción)")

    def _find_blackhole_device(self) -> Optional[int]:
        """Find BlackHole input device."""
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if "blackhole" in dev["name"].lower() and dev["max_input_channels"] > 0:
                return i
        return None

    def start(self):
        """Start the sidekick."""
        # Find audio device (only if not using external audio)
        if not self.external_audio:
            if self.device_idx is None:
                self.device_idx = self._find_blackhole_device()
                if self.device_idx is None:
                    print("  [ERROR] BlackHole no encontrado. Ejecuta: ./setup.sh")
                    return

        self.is_running = True

        if self.auto_start:
            # Initialize Deepgram
            try:
                self.transcriber = DeepgramTranscriber(
                    self.context, 
                    on_transcript=self.handle_incoming_transcript,
                    on_silence_timeout=self.handle_silence_timeout,
                    silence_timeout=self.silence_timeout
                )
                self.transcriber.start(DEFAULT_SAMPLE_RATE)
            except ValueError as e:
                print(f"  [ERROR] {e}")
                return
            except Exception as e:
                print(f"  [ERROR] No se pudo iniciar Deepgram: {e}")
                return

            self.start_time = time.time()

            # Start audio capture in background (only if not using external audio)
            if not self.external_audio:
                self._start_audio_capture()
        else:
            self.start_time = None

        # Run interactive CLI
        self._run_interactive_cli()

    def _start_audio_capture(self):
        """Start capturing audio in background."""
        device_channels = 1  # Deepgram prefers mono

        def audio_callback(indata, frames, time_info, status):
            if self.paused:
                return
            if status:
                pass  # Ignore status for now

            # Convert to mono if needed
            if indata.ndim > 1 and indata.shape[1] > 1:
                audio_mono = np.mean(indata, axis=1)
            else:
                audio_mono = indata.flatten()

            # Send to Deepgram
            if self.transcriber and self.transcriber.is_running:
                self.transcriber.send_audio(audio_mono)

        try:
            self._audio_stream = sd.InputStream(
                device=self.device_idx,
                channels=device_channels,
                samplerate=DEFAULT_SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                callback=audio_callback
            )
            self._audio_stream.start()
            print("  [*] Captura de audio iniciada\n")
        except Exception as e:
            print(f"  [ERROR] No se pudo iniciar captura de audio: {e}")

    @property
    def elapsed_time(self) -> float:
        if hasattr(self, '_grabador') and self._grabador and self._grabador.start_time:
            g = self._grabador
            if g.paused and g.pause_start_time:
                return g.pause_start_time - g.start_time - g.total_paused_time
            return time.time() - g.start_time - g.total_paused_time
        if hasattr(self, 'start_time') and self.start_time:
            if self.paused and self.pause_start_time:
                return self.pause_start_time - self.start_time - self.total_paused_time
            return time.time() - self.start_time - self.total_paused_time
        return 0.0

    def handle_incoming_transcript(self, transcript: str):
        self.safe_print(f"\n> {transcript}")
        if hasattr(self, '_grabador') and self._grabador and self._grabador.output_file:
            try:
                wav_path = Path(self._grabador.output_file)
                wav_path.parent.mkdir(parents=True, exist_ok=True)
                txt_path = wav_path.with_suffix(".txt")
                time_str = datetime.now().strftime("%H:%M:%S")
                with open(txt_path, "a", encoding="utf-8") as f:
                    f.write(f"[{time_str}] {transcript}\n")
            except Exception:
                pass

    def handle_silence_timeout(self):
        """Callback triggered when continuous silence exceeds timeout limit."""
        self.safe_print(f"\n[Auto-Quit] Se detectaron más de {int(self.silence_timeout)} segundos de silencio continuo. Saliendo...")
        self.is_running = False

    def _format_shaded_block(self, text: str) -> str:
        import shutil
        width = shutil.get_terminal_size().columns
        block_width = min(80, width - 6)
        
        bg_color = "\033[48;5;236m"   # Dark gray background
        text_color = "\033[38;5;252m" # Light gray text
        reset = "\033[0m"
        
        lines = []
        # Simple word wrap
        for paragraph in text.split("\n"):
            if not paragraph.strip():
                lines.append("")
                continue
            words = paragraph.split(" ")
            current_line = []
            for word in words:
                test_line = " ".join(current_line + [word])
                if len(test_line) <= block_width - 4:
                    current_line.append(word)
                else:
                    lines.append(" ".join(current_line))
                    current_line = [word]
            if current_line:
                lines.append(" ".join(current_line))
                
        # Construct the block string
        block_lines = []
        block_lines.append(f"  {bg_color}" + " " * block_width + reset)
        for line in lines:
            padded_line = line.ljust(block_width - 4)
            block_lines.append(f"  {bg_color}  {text_color}{padded_line}  {reset}")
        block_lines.append(f"  {bg_color}" + " " * block_width + reset)
        
        return "\n".join(block_lines)

    def _clear_interactive_area(self, menu_len):
        import shutil
        _, height = shutil.get_terminal_size()
        start_row = height - 1 - menu_len
        sys.stdout.write(f"\033[{start_row};1H\033[J")
        sys.stdout.flush()

    def _draw_interactive_area(self):
        import shutil
        width, height = shutil.get_terminal_size()

        show_slash = self.user_input.startswith("/")
        menu_len = SLASH_MENU_HEIGHT if show_slash else 0
        scroll_bottom = height - 2 - menu_len

        # Configure scrolling region dynamically
        sys.stdout.write(f"\033[1;{scroll_bottom}r")

        status_row = height
        input_row = scroll_bottom + 1

        # 1. Prepare status bar
        elapsed_secs = self.elapsed_time
        mins, secs = divmod(int(elapsed_secs), 60)
        time_str = f"{mins:02d}:{secs:02d}"
        
        provider_name = "MiMo V2.5 Pro"
        if self.llm_client:
            from llm_client import MiMoClient, AnthropicClient, OpenAIClient, MiniMaxClient
            if isinstance(self.llm_client, MiMoClient):
                provider_name = "MiMo V2.5 Pro"
            elif isinstance(self.llm_client, AnthropicClient):
                provider_name = f"Anthropic - {self.llm_client.model}"
            elif isinstance(self.llm_client, OpenAIClient):
                provider_name = f"OpenAI - {self.llm_client.model}"
            elif isinstance(self.llm_client, MiniMaxClient):
                provider_name = f"MiniMax - {self.llm_client.model}"
        elif self.llm_provider:
            provider_name = self.llm_provider.capitalize()
        else:
            provider_name = "Ninguno"
            
        # Determine status text and dot color
        is_rec_active = False
        if hasattr(self, '_grabador') and self._grabador and self._grabador.recording:
            is_rec_active = True
        elif not self.external_audio and self._audio_stream:
            is_rec_active = True

        if self.paused:
            status_text = "paused"
            dot_color = "\033[33m"  # Yellow
        elif not is_rec_active and not (self.transcriber and self.transcriber.is_running):
            status_text = "stopped"
            dot_color = "\033[90m"  # Gray
        else:
            status_text = "listening"
            dot_color = "\033[31m"  # Red

        left_plain = f"● {status_text} - {time_str} | llm - {provider_name} | Directorio de salida - {self.output_dir}"
        right_plain = "list commands with /"
        
        left_colored = f"{dot_color}●\033[0m {status_text} - {time_str} \033[90m|\033[0m \033[92mllm - {provider_name}\033[0m \033[90m|\033[0m \033[92mDirectorio de salida - {self.output_dir}\033[0m"
        right_colored = "\033[90mlist commands with /\033[0m"
        
        num_spaces = width - len(left_plain) - len(right_plain)
        if num_spaces < 1:
            num_spaces = 1
        status_bar = f"{left_colored}{' ' * num_spaces}{right_colored}"

        # 2. Prepare slash menu
        menu_lines = []
        if show_slash:
            menu_items = []
            if self.user_input.startswith("/output-dir "):
                parts = self.user_input.split(maxsplit=1)
                arg = parts[1] if len(parts) > 1 else ""
                suggestions = self._get_path_suggestions(arg)
                for display_name, is_dir, name in suggestions[:10]:
                    desc = "directory" if is_dir else "file"
                    menu_items.append((display_name, desc))
                if not menu_items:
                    menu_items.append(("[No matches found]", ""))
            else:
                menu_items = [
                    ("/context", "get current context"),
                    ("/clear-context", "clear current context"),
                    ("/output-dir", "change output directory for .WAV and .TXT files"),
                    ("/start", "start recording and transcription"),
                    ("/pause", "pause recording and transcription"),
                    ("/resume", "resume recording and transcription"),
                    ("/stop", "stop recording and transcription"),
                    ("/silence-timeout", "set custom silence timeout in seconds"),
                    ("/window-list", "list visible macOS windows and IDs"),
                    ("/screencapture", "select window to capture periodically"),
                    ("/help", "show help"),
                    ("/quit", "quit sidekick")
                ]
            
            menu_height = 21 if self.user_input.startswith("/output-dir ") else SLASH_MENU_HEIGHT
            menu_lines = [""] * menu_height
            
            # Fill with default spacers (cleared lines)
            for idx in range(menu_height):
                menu_lines[idx] = " " * width
                
            # Populate item lines
            for idx, (cmd, desc) in enumerate(menu_items):
                plain_text = f"    {cmd:<18}{desc}"
                menu_lines[idx * 2 + 1] = f"\033[90m{plain_text}\033[0m"
                
            # Focus formatting (padding and highlighted spacers)
            sel_idx = self.menu_selected_idx
            if sel_idx >= len(menu_items):
                sel_idx = max(0, len(menu_items) - 1)
                self.menu_selected_idx = sel_idx
                
            if menu_items:
                # Spacer above selected item gets focus background
                menu_lines[sel_idx * 2] = f"\033[30;102m{' ' * width}\033[0m"
                
                # Selected item gets focus background, "➔" indicator, and horizontal padding
                cmd, desc = menu_items[sel_idx]
                selected_text = f"  ➔  {cmd:<18}{desc}"
                padded_selected = selected_text.ljust(width)
                menu_lines[sel_idx * 2 + 1] = f"\033[30;102m{padded_selected}\033[0m"
                
                # Spacer below selected item gets focus background
                menu_lines[sel_idx * 2 + 2] = f"\033[30;102m{' ' * width}\033[0m"

        # 3. Draw slash menu if any
        if menu_lines:
            for idx, line in enumerate(menu_lines):
                row = input_row + 1 + idx
                sys.stdout.write(f"\033[{row};1H\033[K{line}")
                
        # 4. Draw status bar at status_row
        sys.stdout.write(f"\033[{status_row};1H\033[K{status_bar}")
        
        # 5. Draw prompt line at input_row
        prompt_display = self.user_input
        if self.user_input == "/":
            prompt_display = "/\033[90m<COMMAND>\033[0m"
        sys.stdout.write(f"\033[{input_row};1H\033[K{prompt_display}")
        
        # 6. Move cursor to the typing column on the prompt line
        col = self.cursor_idx + 1
        sys.stdout.write(f"\033[{input_row};{col}H")
        sys.stdout.flush()

    def _get_path_suggestions(self, typed_path: str) -> list:
        """Get directory and file suggestions based on the typed path."""
        import os
        from pathlib import Path
        
        # Expand user directory (~)
        expanded = os.path.expanduser(typed_path)
        
        if not typed_path:
            # If nothing is typed, default to current working directory
            parent_dir = os.getcwd()
            prefix = ""
        elif expanded.endswith("/") or os.path.isdir(expanded):
            parent_dir = expanded
            prefix = ""
        else:
            parent_dir = os.path.dirname(expanded) or "."
            prefix = os.path.basename(expanded)
            
        suggestions = []
        try:
            if os.path.isdir(parent_dir):
                for name in os.listdir(parent_dir):
                    if name.startswith(".") and not prefix.startswith("."):
                        continue
                    if name.lower().startswith(prefix.lower()):
                        full_path = os.path.join(parent_dir, name)
                        is_dir = os.path.isdir(full_path)
                        display_name = name + ("/" if is_dir else "")
                        suggestions.append((display_name, is_dir, name))
        except Exception:
            pass
            
        # Sort suggestions: directories first, then files
        suggestions.sort(key=lambda x: (not x[1], x[0].lower()))
        return suggestions

    def _update_menu_selection(self):
        """Automatically updates menu selection based on the current command prefix typed."""
        if self.user_input.startswith("/"):
            if self.user_input.startswith("/output-dir "):
                parts = self.user_input.split(maxsplit=1)
                arg = parts[1] if len(parts) > 1 else ""
                num_items = len(self._get_path_suggestions(arg)[:10]) or 1
                if self.menu_selected_idx >= num_items:
                    self.menu_selected_idx = 0
            else:
                menu_items = ["/context", "/clear-context", "/output-dir", "/start", "/pause", "/resume", "/stop", "/silence-timeout", "/window-list", "/screencapture", "/help", "/quit"]
                typed = self.user_input.strip().lower()
                cmd_part = typed.split()[0]
                for idx, cmd in enumerate(menu_items):
                    if cmd.startswith(cmd_part):
                        self.menu_selected_idx = idx
                        break

    def safe_print(self, text: str):
        """Prints text safely by clearing the interactive area first, printing, and then redrawing."""
        with self.stdout_lock:
            import shutil
            width, height = shutil.get_terminal_size()
            
            show_slash = self.user_input.startswith("/")
            menu_len = SLASH_MENU_HEIGHT if show_slash else 0
            scroll_bottom = height - 2 - menu_len
            
            # Print at the bottom line of the scroll region
            sys.stdout.write(f"\033[{scroll_bottom};1H\033[K")
            
            if not text.endswith("\n"):
                text += "\n"
            sys.stdout.write(text)
            sys.stdout.flush()
            
            # Redraw interactive area
            if self.is_running:
                self._draw_interactive_area()

    def _run_interactive_cli(self):
        """Run interactive command line interface using raw terminal mode."""
        import tty
        import termios
        import select
        import shutil
        import fcntl
        import os
        
        fd = sys.stdin.fileno()
        try:
            old_settings = termios.tcgetattr(fd)
            self.old_settings = old_settings
        except termios.error:
            # If not a TTY, fallback to standard input
            self._run_fallback_cli()
            return

        # Print newlines to fill screen buffer so absolute positioning is valid immediately
        _, height = shutil.get_terminal_size()
        sys.stdout.write("\n" * height)
        sys.stdout.flush()

        self.user_input = ""
        self.cursor_idx = 0
        
        # Initial draw
        with self.stdout_lock:
            self._draw_interactive_area()

        last_time_update = time.time()
        
        try:
            tty.setcbreak(fd)
            while self.is_running:
                now = time.time()
                # 1. Update timer if needed (every second)
                if now - last_time_update >= 1.0:
                    last_time_update = now
                    with self.stdout_lock:
                        show_slash = self.user_input.startswith("/")
                        menu_len = SLASH_MENU_HEIGHT if show_slash else 0
                        self._clear_interactive_area(menu_len)
                        self._draw_interactive_area()

                # 2. Check for keypress with a small timeout
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    try:
                        char_bytes = os.read(fd, 1)
                        if not char_bytes:
                            break
                        char = char_bytes.decode('utf-8', errors='ignore')
                    except OSError:
                        break
                    
                    # Handle Ctrl+C, Ctrl+D
                    if char == '\x03' or char == '\x04':
                        self.is_running = False
                        break
                        
                    # Handle Escape sequences (Arrow keys)
                    elif char == '\x1b':
                        # Set non-blocking temporarily to read rest of sequence
                        old_fl = fcntl.fcntl(fd, fcntl.F_GETFL)
                        fcntl.fcntl(fd, fcntl.F_SETFL, old_fl | os.O_NONBLOCK)
                        
                        seq = ""
                        try:
                            seq_bytes = os.read(fd, 4)
                            seq = seq_bytes.decode('utf-8', errors='ignore')
                        except OSError:
                            seq = ""
                        finally:
                            fcntl.fcntl(fd, fcntl.F_SETFL, old_fl)
                            
                        if seq in ("[A", "OA"): # Up Arrow
                            if self.user_input.startswith("/"):
                                num_items = 12
                                if self.user_input.startswith("/output-dir "):
                                    parts = self.user_input.split(maxsplit=1)
                                    arg = parts[1] if len(parts) > 1 else ""
                                    num_items = len(self._get_path_suggestions(arg)[:10]) or 1
                                self.menu_selected_idx = (self.menu_selected_idx - 1) % num_items
                        elif seq in ("[B", "OB"): # Down Arrow
                            if self.user_input.startswith("/"):
                                num_items = 12
                                if self.user_input.startswith("/output-dir "):
                                    parts = self.user_input.split(maxsplit=1)
                                    arg = parts[1] if len(parts) > 1 else ""
                                    num_items = len(self._get_path_suggestions(arg)[:10]) or 1
                                self.menu_selected_idx = (self.menu_selected_idx + 1) % num_items
                        elif seq in ("[C", "OC"): # Right Arrow
                            self.cursor_idx = min(len(self.user_input), self.cursor_idx + 1)
                        elif seq in ("[D", "OD"): # Left Arrow
                            self.cursor_idx = max(0, self.cursor_idx - 1)
                            
                        with self.stdout_lock:
                            show_slash = self.user_input.startswith("/")
                            menu_len = SLASH_MENU_HEIGHT if show_slash else 0
                            self._clear_interactive_area(menu_len)
                            self._draw_interactive_area()
                            
                    # Handle Backspace
                    elif char in ('\x7f', '\x08'):
                        if self.cursor_idx > 0:
                            old_show_slash = self.user_input.startswith("/")
                            self.user_input = self.user_input[:self.cursor_idx-1] + self.user_input[self.cursor_idx:]
                            self.cursor_idx -= 1
                            
                            self._update_menu_selection()
                            
                            with self.stdout_lock:
                                clear_menu_len = SLASH_MENU_HEIGHT if old_show_slash else 0
                                self._clear_interactive_area(clear_menu_len)
                                self._draw_interactive_area()
                                
                    # Handle Tab (Autocomplete)
                    elif char == '\t':
                        if self.user_input.startswith("/"):
                            if self.user_input.startswith("/output-dir "):
                                parts = self.user_input.split(maxsplit=1)
                                arg = parts[1] if len(parts) > 1 else ""
                                suggestions = self._get_path_suggestions(arg)
                                if suggestions and self.menu_selected_idx < len(suggestions):
                                    display_name, is_dir, real_name = suggestions[self.menu_selected_idx]
                                    expanded = os.path.expanduser(arg)
                                    if not arg:
                                        parent_part = ""
                                    elif expanded.endswith("/") or os.path.isdir(expanded):
                                        parent_part = arg
                                        if not parent_part.endswith("/"):
                                            parent_part += "/"
                                    else:
                                        last_slash = arg.rfind("/")
                                        if last_slash != -1:
                                            parent_part = arg[:last_slash + 1]
                                        else:
                                            parent_part = ""
                                    
                                    new_path = parent_part + real_name
                                    if is_dir:
                                        new_path += "/"
                                    self.user_input = "/output-dir " + new_path
                                    self.cursor_idx = len(self.user_input)
                            else:
                                menu_items = ["/context", "/clear-context", "/output-dir", "/start", "/pause", "/resume", "/stop", "/silence-timeout", "/window-list", "/screencapture", "/help", "/quit"]
                                selected_cmd = menu_items[self.menu_selected_idx]
                                
                                # Preserve any typed arguments
                                parts = self.user_input.split(maxsplit=1)
                                if len(parts) > 1:
                                    self.user_input = selected_cmd + " " + parts[1]
                                else:
                                    self.user_input = selected_cmd + " "
                                    
                                self.cursor_idx = len(self.user_input)
                            
                            with self.stdout_lock:
                                self._clear_interactive_area(SLASH_MENU_HEIGHT)
                                self._draw_interactive_area()
                                
                    # Handle Enter
                    elif char in ('\n', '\r'):
                        input_to_process = self.user_input.strip()
                        
                        # Resolve partial or unmatched command using current menu selection
                        if input_to_process.startswith("/"):
                            menu_items = ["/context", "/clear-context", "/output-dir", "/start", "/pause", "/resume", "/stop", "/silence-timeout", "/window-list", "/screencapture", "/help", "/quit"]
                            parts = input_to_process.split(maxsplit=1)
                            cmd_part = parts[0].lower()
                            
                            valid_cmds = menu_items + ["/clear", "/stop-listening", "/exit", "/screecapture"]
                            if cmd_part not in valid_cmds:
                                selected_cmd = menu_items[self.menu_selected_idx]
                                if len(parts) > 1:
                                    input_to_process = selected_cmd + " " + parts[1]
                                else:
                                    input_to_process = selected_cmd
                                    
                            # Check if the command requires arguments (e.g., /output-dir, /silence-timeout)
                            cmd_resolved = input_to_process.split(maxsplit=1)[0].lower()
                            if cmd_resolved in ("/output-dir", "/silence-timeout"):
                                resolved_parts = input_to_process.split(maxsplit=1)
                                if len(resolved_parts) < 2:
                                    # Populate input with the command and a trailing space instead of executing
                                    self.user_input = cmd_resolved + " "
                                    self.cursor_idx = len(self.user_input)
                                    
                                    with self.stdout_lock:
                                        self._clear_interactive_area(SLASH_MENU_HEIGHT)
                                        self._draw_interactive_area()
                                    continue  # Keep input open for arguments
                                    
                        # Clear interactive area permanently before processing
                        with self.stdout_lock:
                            show_slash = self.user_input.startswith("/")
                            menu_len = SLASH_MENU_HEIGHT if show_slash else 0
                            self._clear_interactive_area(menu_len)
                        
                        self.user_input = ""
                        self.cursor_idx = 0
                        self.menu_selected_idx = 0
                        
                        if input_to_process:
                            if input_to_process.startswith("/"):
                                self._handle_command(input_to_process)
                            else:
                                self._handle_query(input_to_process)
                                
                        with self.stdout_lock:
                            self._draw_interactive_area()
                            
                    # Handle regular character insertion
                    elif len(char) == 1 and ord(char) >= 32:
                        old_show_slash = self.user_input.startswith("/")
                        self.user_input = self.user_input[:self.cursor_idx] + char + self.user_input[self.cursor_idx:]
                        self.cursor_idx += 1
                        
                        self._update_menu_selection()
                        
                        with self.stdout_lock:
                            clear_menu_len = SLASH_MENU_HEIGHT if old_show_slash else 0
                            self._clear_interactive_area(clear_menu_len)
                            self._draw_interactive_area()
                            
        finally:
            # Reset scrolling region to full screen
            sys.stdout.write("\033[r")
            sys.stdout.flush()
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except termios.error:
                pass

    def _run_fallback_cli(self):
        """Fallback CLI when termios is not available."""
        import select
        print("  > ", end="", flush=True)
        while self.is_running:
            try:
                # Wait for input with a small timeout (e.g. 0.1 seconds)
                r, _, _ = select.select([sys.stdin], [], [], 0.1)
                if not r:
                    continue
                user_input = sys.stdin.readline()
                if not user_input:  # EOF
                    break
                user_input = user_input.strip()
                if not user_input:
                    print("  > ", end="", flush=True)
                    continue
                if user_input.startswith("/"):
                    self._handle_command(user_input)
                else:
                    self._handle_query(user_input)
                print("  > ", end="", flush=True)
            except (EOFError, KeyboardInterrupt):
                break

    def _handle_command(self, command: str):
        """Handle special commands."""
        self.safe_print(command)
        
        cmd = command.lower().split()[0]

        if cmd == "/quit" or cmd == "/exit":
            self.is_running = False

        elif cmd == "/stop" or cmd == "/stop-listening":
            recording_stopped = False
            transcription_stopped = False
            if hasattr(self, '_grabador') and self._grabador and self._grabador.recording:
                self._grabador.recording = False
                recording_stopped = True
            
            if self.transcriber and self.transcriber.is_running:
                self.transcriber.stop()
                transcription_stopped = True

            if not self.external_audio and self._audio_stream:
                try:
                    self._audio_stream.stop()
                    self._audio_stream.close()
                except Exception:
                    pass
                self._audio_stream = None
                recording_stopped = True
            
            if recording_stopped or transcription_stopped:
                self.safe_print("[x] Grabación y transcripción detenidas")
            else:
                self.safe_print("[!] No hay grabación ni transcripción activa")

        elif cmd == "/start":
            is_recording = False
            if hasattr(self, '_grabador') and self._grabador and self._grabador.recording:
                is_recording = True
            elif not self.external_audio and self._audio_stream:
                is_recording = True
            
            if is_recording or (self.transcriber and self.transcriber.is_running):
                self.safe_print("[!] Ya hay una sesión de grabación o transcripción activa")
                return

            self.paused = False
            self.total_paused_time = 0.0
            self.pause_start_time = None
            
            try:
                self.transcriber = DeepgramTranscriber(
                    self.context, 
                    on_transcript=self.handle_incoming_transcript,
                    on_silence_timeout=self.handle_silence_timeout,
                    silence_timeout=self.silence_timeout
                )
                self.transcriber.start(DEFAULT_SAMPLE_RATE)
            except Exception as e:
                self.safe_print(f"[ERROR] Error al iniciar Deepgram: {e}")
                return

            if hasattr(self, '_grabador') and self._grabador:
                self._grabador.reset_output_file()
                import threading
                recording_thread = threading.Thread(target=self._grabador.start_recording, daemon=True)
                recording_thread.start()

            if not self.external_audio:
                self._start_audio_capture()

            self.start_time = time.time()
            self.safe_print("[+] Grabación y transcripción iniciadas")

        elif cmd == "/pause":
            if self.paused:
                self.safe_print("[!] La grabación y transcripción ya están pausadas")
                return

            is_active = False
            if hasattr(self, '_grabador') and self._grabador and self._grabador.recording:
                is_active = True
            elif not self.external_audio and self._audio_stream:
                is_active = True
            elif self.transcriber and self.transcriber.is_running:
                is_active = True
            
            if not is_active:
                self.safe_print("[!] No hay ninguna grabación o transcripción activa para pausar")
                return

            self.paused = True
            self.pause_start_time = time.time()
            if hasattr(self, '_grabador') and self._grabador:
                self._grabador.paused = True
                self._grabador.pause_start_time = self.pause_start_time
            
            self.safe_print("[||] Grabación y transcripción pausadas")

        elif cmd == "/resume":
            if not self.paused:
                self.safe_print("[!] La grabación y transcripción no están pausadas")
                return
            
            self.paused = False
            now = time.time()
            if self.pause_start_time:
                self.total_paused_time += now - self.pause_start_time
            self.pause_start_time = None
            
            if hasattr(self, '_grabador') and self._grabador:
                if self._grabador.pause_start_time:
                    self._grabador.total_paused_time += now - self._grabador.pause_start_time
                self._grabador.paused = False
                self._grabador.pause_start_time = None
            
            self.safe_print("[+] Grabación y transcripción reanudadas")

        elif cmd == "/output-dir":
            parts = command.split(maxsplit=1)
            if len(parts) < 2:
                self.safe_print(f"[DIR] Directorio de salida actual: {self.output_dir}")
            else:
                new_dir = parts[1].strip()
                new_dir_path = Path(new_dir).expanduser().absolute()
                self.output_dir = str(new_dir_path)
                if hasattr(self, '_grabador') and self._grabador:
                    self._grabador.output_dir = self.output_dir
                self.safe_print(f"[DIR] Directorio de salida configurado a: {self.output_dir}")

        elif cmd == "/silence-timeout":
            parts = command.split()
            if len(parts) < 2:
                self.safe_print(f"[SILENCE] Límite de silencio actual: {self.silence_timeout} segundos (0 para desactivar)")
            else:
                try:
                    val = float(parts[1])
                    if val < 0:
                        raise ValueError("El tiempo no puede ser negativo")
                    self.silence_timeout = val
                    if self.transcriber:
                        self.transcriber.silence_timeout = val
                    if val == 0:
                        self.safe_print("[SILENCE] Límite de silencio desactivado")
                    else:
                        self.safe_print(f"[SILENCE] Límite de silencio configurado a: {val} segundos")
                except ValueError:
                    self.safe_print("[!] Valor inválido. Uso: /silence-timeout <segundos>")

        elif cmd == "/context":
            context = self.context.get_context()
            if context:
                output = ["[Contexto actual]", "-" * 40]
                for line in context.split("\n"):
                    output.append(f"  {line}")
                output.append("-" * 40)
                self.safe_print("\n" + "\n".join(output) + "\n")
            else:
                self.safe_print("\n[No hay contexto aún]\n")

        elif cmd == "/clear" or cmd == "/clear-context":
            self.context.clear()
            self.safe_print("[-] Contexto limpiado")

        elif cmd == "/window-list":
            import io
            from contextlib import redirect_stdout
            from eco import list_visible_windows
            f = io.StringIO()
            with redirect_stdout(f):
                list_visible_windows()
            self.safe_print(f.getvalue())

        elif cmd in ("/screencapture", "/screecapture"):
            # 1. Check if recording/transcription is active
            was_active = False
            if hasattr(self, '_grabador') and self._grabador and self._grabador.recording:
                was_active = True
            elif not self.external_audio and self._audio_stream:
                was_active = True
            elif self.transcriber and self.transcriber.is_running:
                was_active = True

            # 2. Stop if active
            if was_active:
                self._handle_command("/stop")

            # 3. Interactive arrow selection
            import termios
            import tty
            import fcntl
            import os
            import select
            import shutil
            from eco import get_visible_windows, find_window

            fd = sys.stdin.fileno()
            try:
                old_settings = termios.tcgetattr(fd)
                tty.setcbreak(fd)
            except termios.error:
                old_settings = None

            if old_settings is None:
                # Fallback non-interactive / simple text input if not a TTY
                print("  Ingrese el nombre de la aplicación, título o ID de la ventana a capturar (o presione Enter para pantalla completa):")
                try:
                    target = input("  > ").strip()
                except (KeyboardInterrupt, EOFError):
                    target = ""
            else:
                windows = get_visible_windows()
                choices = [{"id": None, "owner": "Pantalla Completa", "title": ""}]
                for w in windows:
                    choices.append({
                        "id": w.get("id"),
                        "owner": w.get("owner", "Desconocido"),
                        "title": w.get("title", "")
                    })

                selected_idx = 0
                scroll_offset = 0
                max_visible = 10
                
                num_choice_lines = min(len(choices), max_visible)
                total_lines_to_draw = 1 + num_choice_lines + 1
                
                # Print empty lines to prepare screen space
                for _ in range(total_lines_to_draw):
                    sys.stdout.write("\n")
                sys.stdout.flush()
                lines_drawn = total_lines_to_draw

                cancelled = False
                try:
                    while True:
                        if selected_idx < scroll_offset:
                            scroll_offset = selected_idx
                        elif selected_idx >= scroll_offset + max_visible:
                            scroll_offset = selected_idx - max_visible + 1

                        # Move cursor up to the start of our drawing block
                        sys.stdout.write(f"\033[{lines_drawn}A")
                        
                        width, height = shutil.get_terminal_size()
                        
                        # 1. Header
                        header = "  Seleccione la ventana a capturar (Use ↑/↓ para navegar, Enter para confirmar, Esc/q para cancelar):"
                        if len(header) > width - 2:
                            header = header[:width-5] + "..."
                        sys.stdout.write(f"\033[K\033[1;96m{header}\033[0m\n")
                        
                        # 2. Choices
                        for i in range(scroll_offset, scroll_offset + num_choice_lines):
                            choice = choices[i]
                            if choice["id"] is None:
                                text = "   [Pantalla Completa]"
                            else:
                                text = f"   ID: {choice['id']:<10} | {choice['owner']:<20} | {choice['title']}"
                            
                            display_text = text[:width-4]
                            
                            if i == selected_idx:
                                # Selected row with green background
                                padded_text = f"  ➔ {display_text[3:]}".ljust(width - 4)
                                sys.stdout.write(f"\033[K\033[30;102m  {padded_text}  \033[0m\n")
                            else:
                                sys.stdout.write(f"\033[K{display_text}\n")
                        
                        # 3. Footer
                        footer = f"  [Opción {selected_idx + 1} de {len(choices)}]"
                        if len(choices) > max_visible:
                            footer += " (Use ↑/↓ para desplazar)"
                        if len(footer) > width - 2:
                            footer = footer[:width-5] + "..."
                        sys.stdout.write(f"\033[K\033[90m{footer}\033[0m\n")
                        sys.stdout.flush()
                        
                        r, _, _ = select.select([fd], [], [], 0.1)
                        if not r:
                            continue
                            
                        try:
                            char_bytes = os.read(fd, 1)
                            if not char_bytes:
                                break
                            char = char_bytes.decode('utf-8', errors='ignore')
                        except OSError:
                            break
                            
                        if char in ('\x03', '\x04'):
                            cancelled = True
                            break
                        elif char == '\x1b':
                            old_fl = fcntl.fcntl(fd, fcntl.F_GETFL)
                            fcntl.fcntl(fd, fcntl.F_SETFL, old_fl | os.O_NONBLOCK)
                            seq = ""
                            try:
                                seq_bytes = os.read(fd, 4)
                                seq = seq_bytes.decode('utf-8', errors='ignore')
                            except OSError:
                                seq = ""
                            finally:
                                fcntl.fcntl(fd, fcntl.F_SETFL, old_fl)
                                
                            if not seq:  # Just ESC key
                                cancelled = True
                                break
                            elif seq in ("[A", "OA"):
                                selected_idx = (selected_idx - 1) % len(choices)
                            elif seq in ("[B", "OB"):
                                selected_idx = (selected_idx + 1) % len(choices)
                        elif char in ('q', 'Q'):
                            cancelled = True
                            break
                        elif char in ('\n', '\r'):
                            break
                finally:
                    # Clear menu from terminal screen
                    sys.stdout.write(f"\033[{lines_drawn}A\033[J")
                    sys.stdout.flush()
                    # Restore original terminal settings
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

                if cancelled:
                    self.safe_print("  [x] Selección cancelada.")
                    if was_active:
                        self._handle_command("/start")
                    return

                selected_choice = choices[selected_idx]
                target = str(selected_choice["id"]) if selected_choice["id"] is not None else ""

            # Configure grabador with new window
            if hasattr(self, '_grabador') and self._grabador:
                if target:
                    self._grabador.window_query = target
                    self._grabador.screenshots = True
                    w_info = find_window(target)
                    if w_info:
                        self.safe_print(f"  [+] Capturando ventana: ID {w_info['id']} ({w_info['owner']} - {w_info['title']})")
                        if w_info.get("owner") == "Google Chrome":
                            import chrome_capture
                            if not chrome_capture.is_debug_port_open():
                                self.safe_print(
                                    "  [i] Si esta ventana reproduce video, las capturas pueden fallar al "
                                    "quedar oculta detrás de otra. Para evitarlo, sal (/quit) y vuelve a "
                                    "iniciar eco con --window apuntando a esta ventana: te ofrecerá "
                                    "reiniciar Chrome con depuración remota automáticamente."
                                )
                    else:
                        self.safe_print(f"  [WARN] Ventana '{target}' no encontrada en este momento. Se usará pantalla completa.")
                else:
                    self._grabador.window_query = None
                    self.safe_print("  [+] Configurado para capturar pantalla completa.")

            # Start / resume recording and transcription
            self._handle_command("/start")

        elif cmd == "/help":
            output = [
                "[Comandos disponibles]:",
                "  /context             - Ver contexto actual",
                "  /clear-context       - Limpiar contexto",
                "  /output-dir <d>      - Configurar directorio de salida WAV",
                "  /start               - Iniciar grabación y transcripción",
                "  /pause               - Pausar grabación y transcripción",
                "  /resume              - Reanudar grabación y transcripción",
                "  /stop                - Detener grabación y transcripción",
                "  /silence-timeout <s> - Configurar tiempo de silencio en segundos (0 para desactivar)",
                "  /window-list         - Mostrar ventanas visibles y sus IDs",
                "  /screencapture       - Cambiar ventana capturada periódicamente",
                "  /help                - Mostrar esta ayuda",
                "  /quit                - Salir del sidekick",
                "Escribe cualquier pregunta para consultar al LLM"
            ]
            self.safe_print("\n".join(output))

        else:
            self.safe_print(f"[!] Comando desconocido: {command}")

    def _handle_query(self, query: str):
        """Handle user query to LLM."""
        if not self.llm_client:
            self.safe_print("[!] No hay LLM configurado. Solo disponible transcripción.")
            return

        self.safe_print(f"\n[Pregunta]: {query}")

        # Buscar las últimas capturas de pantalla de la reunión
        import os
        images = []
        if hasattr(self, '_grabador') and self._grabador and getattr(self._grabador, 'screenshots', False):
            screenshots_dir = getattr(self._grabador, 'screenshots_dir', None)
            if screenshots_dir and os.path.isdir(screenshots_dir):
                try:
                    files = [os.path.join(screenshots_dir, f) for f in os.listdir(screenshots_dir) if f.endswith(".jpg")]
                    files.sort()
                    # Tomar las últimas 2 imágenes
                    images = files[-2:]
                except Exception:
                    pass

        with self.stdout_lock:
            if images:
                sys.stdout.write("  \033[90m[*] Cargando capturas de pantalla de la reunión...\033[0m\n")
            sys.stdout.write("  \033[90m... Pensando ...\033[0m\n")
            sys.stdout.flush()

        try:
            context = self.context.get_context()
            response = self.llm_client.chat(query, context=context, images=images)
            
            with self.stdout_lock:
                lines_to_clear = 2 if images else 1
                for _ in range(lines_to_clear):
                    sys.stdout.write("\033[1A\033[K")
                sys.stdout.flush()
                
            shaded_response = self._format_shaded_block(response)
            self.safe_print(f"{shaded_response}\n")
        except Exception as e:
            with self.stdout_lock:
                lines_to_clear = 2 if images else 1
                for _ in range(lines_to_clear):
                    sys.stdout.write("\033[1A\033[K")
                sys.stdout.flush()
            self.safe_print(f"[ERROR] Error: {e}")

    def stop(self):
        """Stop the sidekick."""
        self.is_running = False
        if self.transcriber:
            self.transcriber.stop()
        if self._audio_stream:
            self._audio_stream.stop()
            self._audio_stream.close()
        # Reset scrolling region to full screen
        sys.stdout.write("\033[r")
        sys.stdout.flush()
        print("\n  [-] Sidekick detenido")

    def send_external_audio(self, audio_data: np.ndarray):
        """Send audio data from external source (e.g., eco recorder)."""
        if self.paused:
            return
        if self.transcriber and self.transcriber.is_running:
            # Convert stereo to mono if needed
            if audio_data.ndim > 1 and audio_data.shape[1] > 1:
                audio_mono = np.mean(audio_data, axis=1)
            else:
                audio_mono = audio_data.flatten()

            # Resample: eco captures at 48kHz, Deepgram expects 16kHz
            # 48000 / 16000 = 3, so take every 3rd sample
            target_rate = DEFAULT_SAMPLE_RATE  # 16000
            source_rate = 48000
            if source_rate != target_rate:
                ratio = source_rate // target_rate  # 3
                audio_mono = audio_mono[::ratio]

            self.transcriber.send_audio(audio_mono)


def main():
    """Main entry point for sidekick."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="eco-sidekick",
        description="Eco Sidekick - Asistente IA para reuniones",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  %(prog)s                          Usar LLM disponible
  %(prog)s --llm anthropic          Usar Claude
  %(prog)s --llm openai             Usar GPT-4
  %(prog)s --llm mimo               Usar MiMo
  %(prog)s --list-devices           Ver dispositivos de audio
        """
    )

    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--llm", choices=["anthropic", "openai", "mimo", "minimax"],
                       help="Proveedor LLM a usar")
    parser.add_argument("--device", "-D", metavar="NAME",
                       help="Dispositivo de audio específico")
    parser.add_argument("--list-devices", "-l", action="store_true",
                       help="Muestra dispositivos de audio disponibles")
    parser.add_argument("--silence-timeout", type=float, default=120.0,
                         help="Tiempo de silencio (segundos) antes de auto-cerrar (0 para desactivar, por defecto 120)")

    args = parser.parse_args()

    print(f"\n  ╔═══════════════════════════════════════╗")
    print(f"  ║    Eco Sidekick v{VERSION}               ║")
    print(f"  ║    Tu asistente IA para reuniones      ║")
    print(f"  ╚═══════════════════════════════════════╝\n")

    if args.list_devices:
        devices = sd.query_devices()
        print("  Dispositivos de Audio:")
        print("  " + "=" * 50)
        for i, dev in enumerate(devices):
            if dev["max_input_channels"] > 0:
                print(f"    [{i}] {dev['name']}")
                print(f"        Canales: {dev['max_input_channels']}")
        return

    # Find device
    device_idx = None
    if args.device:
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if args.device.lower() in dev["name"].lower() and dev["max_input_channels"] > 0:
                device_idx = i
                break
        if device_idx is None:
            print(f"  [ERROR] Dispositivo '{args.device}' no encontrado")
            return

    # Start sidekick
    sidekick = Sidekick(
        llm_provider=args.llm, 
        device_idx=device_idx,
        silence_timeout=args.silence_timeout
    )

    # Handle Ctrl+C
    def signal_handler(sig, frame):
        sidekick.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    sidekick.start()


if __name__ == "__main__":
    main()
