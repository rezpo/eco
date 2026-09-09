"""
Chrome Capture - Captura de ventanas de Chrome vía su protocolo de depuración (CDP).

Por qué existe este módulo: cuando una ventana de Chrome reproduce video con
aceleración por hardware y queda completamente oculta detrás de otra ventana,
`screencapture -l<id>` (y también ScreenCaptureKit) dejan de poder leer ese
contenido correctamente porque el video se compone fuera del backing store
normal que esas APIs usan. Pedirle el screenshot directamente a Chrome vía CDP
(`Page.captureScreenshot`) evita el problema por completo: Chrome renderiza su
propia página sin pasar por el compositor de ventanas de macOS.

Esto requiere que Chrome corra con `--remote-debugging-port=<PORT>`. Ese flag
solo puede activarse al iniciar el proceso, así que si Chrome ya está abierto
sin él, hay que reiniciarlo (`ensure_chrome_debugging` se encarga, pidiendo
confirmación antes de cerrar las ventanas del usuario).
"""

import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

CHROME_CDP_PORT = 9339
CHROME_APP_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_BOUNDS_TOLERANCE = 20  # píxeles de tolerancia al comparar bounds de ventana


def _http_get_json(url, timeout=2.0):
    """GET de una URL local y parseo de JSON. None si falla."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def is_debug_port_open(port=CHROME_CDP_PORT, timeout=1.0):
    """True si hay un Chrome escuchando su protocolo de depuración en el puerto."""
    version = _http_get_json(f"http://127.0.0.1:{port}/json/version", timeout=timeout)
    return bool(version and "webSocketDebuggerUrl" in version)


class _CDPConnection:
    """Cliente WebSocket mínimo y síncrono, suficiente para pedir-y-recibir una
    respuesta del Chrome DevTools Protocol. No depende de librerías externas."""

    def __init__(self, ws_url, timeout=5.0):
        parsed = urlparse(ws_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buffer = b""
        self._handshake(host, port, path)

    def _handshake(self, host, port, path):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(request.encode("ascii"))

        while b"\r\n\r\n" not in self._buffer:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("Chrome cerró la conexión durante el handshake de WebSocket")
            self._buffer += chunk

        header_end = self._buffer.index(b"\r\n\r\n")
        status_line = self._buffer[:header_end].split(b"\r\n", 1)[0]
        self._buffer = self._buffer[header_end + 4:]

        if b"101" not in status_line:
            raise ConnectionError(f"Handshake de WebSocket falló: {status_line!r}")

    def _recv_exact(self, n):
        while len(self._buffer) < n:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("Conexión cerrada leyendo un frame de WebSocket")
            self._buffer += chunk
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data

    def _send_text_frame(self, text):
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

        length = len(payload)
        if length <= 125:
            header = struct.pack("!BB", 0x81, 0x80 | length)
        elif length <= 0xFFFF:
            header = struct.pack("!BBH", 0x81, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", 0x81, 0x80 | 127, length)

        self._sock.sendall(header + mask + masked)

    def _recv_frame(self):
        """Lee un frame completo (reensamblando continuaciones) y devuelve su payload de texto."""
        payload = b""
        while True:
            first2 = self._recv_exact(2)
            fin = first2[0] & 0x80
            opcode = first2[0] & 0x0F
            masked = first2[1] & 0x80
            length = first2[1] & 0x7F

            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]

            mask_key = self._recv_exact(4) if masked else None
            data = self._recv_exact(length)
            if mask_key:
                data = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))

            if opcode == 0x9:  # ping -> pong y seguir esperando el frame real
                self._send_pong(data)
                continue
            if opcode == 0x8:  # close
                raise ConnectionError("Chrome cerró la conexión de WebSocket")

            payload += data
            if fin:
                return payload

    def _send_pong(self, data):
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        header = struct.pack("!BB", 0x8A, 0x80 | len(data))
        self._sock.sendall(header + mask + masked)

    def call(self, method, params=None, msg_id=1):
        """Envía un comando CDP y espera la respuesta con el mismo id (ignora eventos)."""
        self._send_text_frame(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            payload = self._recv_frame()
            try:
                message = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if message.get("id") == msg_id:
                return message

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


def _bounds_match(a, b, tolerance=_BOUNDS_TOLERANCE):
    return (
        abs(a["x"] - b["x"]) <= tolerance
        and abs(a["y"] - b["y"]) <= tolerance
        and abs(a["width"] - b["width"]) <= tolerance
        and abs(a["height"] - b["height"]) <= tolerance
    )


def find_chrome_tab_for_window(window_bounds, port=CHROME_CDP_PORT):
    """Encuentra la webSocketDebuggerUrl de la pestaña de Chrome cuya ventana
    coincide (por posición/tamaño en pantalla) con window_bounds
    ({"x","y","width","height"}, mismo formato que devuelve find_window).
    Devuelve None si no hay match o si el puerto de depuración no responde.
    """
    version = _http_get_json(f"http://127.0.0.1:{port}/json/version")
    tabs = _http_get_json(f"http://127.0.0.1:{port}/json")
    if not version or not tabs:
        return None

    browser_ws = version.get("webSocketDebuggerUrl")
    if not browser_ws:
        return None

    pages = [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if not pages:
        return None

    try:
        browser_conn = _CDPConnection(browser_ws)
    except (OSError, ConnectionError):
        return None

    try:
        for msg_id, page in enumerate(pages, start=1):
            resp = browser_conn.call("Browser.getWindowForTarget", {"targetId": page["id"]}, msg_id=msg_id)
            result = resp.get("result")
            if not result or "left" not in result.get("bounds", {}):
                continue
            bounds = result["bounds"]
            page_bounds = {
                "x": bounds.get("left", 0),
                "y": bounds.get("top", 0),
                "width": bounds.get("width", 0),
                "height": bounds.get("height", 0),
            }
            if _bounds_match(page_bounds, window_bounds):
                return page["webSocketDebuggerUrl"]
    finally:
        browser_conn.close()

    return None


def capture_chrome_window_screenshot(window_bounds, filepath, port=CHROME_CDP_PORT):
    """Captura la ventana de Chrome que coincide con window_bounds usando CDP.
    Devuelve True/False. No lanza excepciones: cualquier fallo se traduce en False
    para que el llamador pueda caer de vuelta al método clásico (screencapture)."""
    page_ws = find_chrome_tab_for_window(window_bounds, port=port)
    if not page_ws:
        return False

    try:
        conn = _CDPConnection(page_ws)
    except (OSError, ConnectionError):
        return False

    try:
        resp = conn.call("Page.captureScreenshot", {"format": "jpeg", "quality": 85})
        result = resp.get("result")
        if not result or "data" not in result:
            return False
        image_bytes = base64.b64decode(result["data"])
        if not image_bytes:
            return False
        with open(filepath, "wb") as f:
            f.write(image_bytes)
        return True
    except Exception:
        return False
    finally:
        conn.close()


def _chrome_is_running():
    try:
        res = subprocess.run(
            ["pgrep", "-x", "Google Chrome"], capture_output=True, text=True
        )
        return res.returncode == 0
    except Exception:
        return False


def chrome_will_restore_session():
    """True si Chrome está configurado para reabrir las pestañas de la sesión
    anterior al iniciar ('Continuar donde lo dejaste'). Si esto es False,
    cerrar Chrome para relanzarlo pierde las pestañas abiertas."""
    prefs_path = (
        Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
        / "Default" / "Preferences"
    )
    try:
        data = json.loads(prefs_path.read_text())
    except (OSError, ValueError):
        return False
    # restore_on_startup == 1 significa "Continuar donde lo dejaste".
    return data.get("session", {}).get("restore_on_startup") == 1


def relaunch_chrome_with_debugging(port=CHROME_CDP_PORT, wait_seconds=15):
    """Cierra Chrome (si está abierto) y lo vuelve a abrir con el flag de
    depuración remota, usando el mismo perfil de siempre (no uno nuevo). Chrome
    restaura las pestañas abiertas solo si chrome_will_restore_session() es True.
    Devuelve True si el puerto de depuración quedó disponible."""
    if _chrome_is_running():
        subprocess.run(
            ["osascript", "-e", 'tell application "Google Chrome" to quit'],
            capture_output=True,
        )
        deadline = time.time() + 10
        while _chrome_is_running() and time.time() < deadline:
            time.sleep(0.3)

    # Se invoca el binario directamente: `open -a ... --args` no garantiza que
    # Chrome reciba el flag si Launch Services considera que "ya hay una
    # instancia" en algún estado transitorio de cierre.
    subprocess.Popen(
        [CHROME_APP_BINARY, f"--remote-debugging-port={port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if is_debug_port_open(port):
            return True
        time.sleep(0.5)
    return False


def ensure_chrome_debugging(port=CHROME_CDP_PORT, printer=None, auto_confirm=False):
    """Verifica que Chrome tenga habilitada la depuración remota; si no,
    pregunta al usuario (stdin normal, modo no-interactivo del terminal) si
    quiere que eco reinicie Chrome para activarla. Devuelve True si al volver
    de esta función el puerto está disponible (ya sea porque ya lo estaba, o
    porque el reinicio funcionó)."""
    say = printer or print

    if is_debug_port_open(port):
        return True

    will_restore = chrome_will_restore_session()

    say("\n  [Chrome] Para que las capturas de video no se rompan al cambiar de "
        "ventana, Chrome necesita reiniciarse con depuración remota activada.")
    if will_restore:
        say("  [Chrome] Esto cerrará todas las ventanas de Chrome y las volverá a "
            "abrir; tienes 'Continuar donde lo dejaste' activado, así que tus "
            "pestañas deberían restaurarse solas.")
    else:
        say("  [Chrome] ATENCIÓN: no tienes activado 'Continuar donde lo dejaste' "
            "(chrome://settings/onStartup), así que reiniciar Chrome ahora "
            "CERRARÁ TODAS TUS PESTAÑAS ACTUALES SIN RESTAURARLAS.")
        say("  [Chrome] Recomendado: activa esa opción y vuelve a intentarlo, o "
            "guarda/anota lo que tengas abierto antes de continuar.")

    if not auto_confirm:
        prompt = "  [Chrome] ¿Reiniciar Chrome ahora de todos modos? [s/N] " if not will_restore \
            else "  [Chrome] ¿Reiniciar Chrome ahora? [s/N] "
        try:
            respuesta = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            respuesta = "n"
        if respuesta not in ("s", "si", "sí", "y", "yes"):
            say("  [Chrome] Se continuará con el método clásico de captura "
                "(puede fallar si hay video reproduciéndose y la ventana queda oculta).")
            return False

    say("  [Chrome] Reiniciando Chrome con depuración remota...")
    ok = relaunch_chrome_with_debugging(port=port)
    if ok:
        say("  [Chrome] Listo. Las capturas de esta ventana usarán el método robusto.")
    else:
        say("  [Chrome] No se pudo confirmar que la depuración remota quedó activa; "
            "se usará el método clásico de captura.")
    return ok
