"""
Chrome Capture - Capture Chrome windows via Chrome DevTools Protocol (CDP).

Why this module exists: when a Chrome window plays video with hardware-accelerated
decoding and becomes fully occluded behind another window, `screencapture -l<id>`
(and ScreenCaptureKit) fail to read the video surface because it is composited
outside the normal window backing store. Requesting screenshots directly from
Chrome via CDP (`Page.captureScreenshot`) avoids this issue completely: Chrome
renders its own page surface without touching the macOS window compositor.

This requires Chrome to run with `--remote-debugging-port=<PORT>`. Modern Chrome
refuses to expose this port against the default user profile (as a security
measure against session/cookie theft), so Eco NEVER attempts to restart Chrome
or force this flag on. This module is used opportunistically if Chrome is
already running with that port open (e.g. on a separate `--user-data-dir`).
Otherwise, window capture transparently falls back to native macOS screencapture.
"""

import base64
import json
import os
import socket
import struct
import urllib.request
from urllib.parse import urlparse

CHROME_CDP_PORT = 9339
_BOUNDS_TOLERANCE = 20  # pixel tolerance when comparing window bounds


def _http_get_json(url, timeout=2.0):
    """GET a local URL and parse JSON. Returns None on failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def is_debug_port_open(port=CHROME_CDP_PORT, timeout=1.0):
    """True if Chrome is listening on its DevTools debugging port."""
    version = _http_get_json(f"http://127.0.0.1:{port}/json/version", timeout=timeout)
    return bool(version and "webSocketDebuggerUrl" in version)


class _CDPConnection:
    """Minimal synchronous WebSocket client for Chrome DevTools Protocol.
    Has no external third-party dependencies.
    """

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
                raise ConnectionError("Chrome closed connection during WebSocket handshake")
            self._buffer += chunk

        header_end = self._buffer.index(b"\r\n\r\n")
        status_line = self._buffer[:header_end].split(b"\r\n", 1)[0]
        self._buffer = self._buffer[header_end + 4:]

        if b"101" not in status_line:
            raise ConnectionError(f"WebSocket handshake failed: {status_line!r}")

    def _recv_exact(self, n):
        while len(self._buffer) < n:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("Connection closed while reading WebSocket frame")
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
        """Read a full frame (reassembling continuations) and return text payload."""
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

            if opcode == 0x9:  # ping -> respond pong and keep waiting
                self._send_pong(data)
                continue
            if opcode == 0x8:  # close
                raise ConnectionError("Chrome closed WebSocket connection")

            payload += data
            if fin:
                return payload

    def _send_pong(self, data):
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        header = struct.pack("!BB", 0x8A, 0x80 | len(data))
        self._sock.sendall(header + mask + masked)

    def call(self, method, params=None, msg_id=1):
        """Send a CDP command and await response matching msg_id (ignoring events)."""
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
    """Find the webSocketDebuggerUrl of the Chrome tab whose on-screen bounds
    match window_bounds ({"x","y","width","height"}).
    Returns None if no match or if debugging port does not respond.
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
    """Capture Chrome window matching window_bounds via CDP.
    Returns True/False. Does not raise: any error returns False so caller can fall
    back to standard macOS screencapture.
    """
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
