#!/usr/bin/env python3
"""Run the test-only MV3 P0-T2 probe.

Offline mode is the default and performs only deterministic fixture checks.
`--headed` requires `--launch-chrome` for live inspection; without it the
script fails closed rather than attaching to an existing debug endpoint.
`--require-live` makes that isolated live inspection required and fails when
Chrome is unavailable. `--launch-chrome` is an explicit opt-in; it creates a
short-lived system-temporary profile unless an explicit disposable temporary
profile is supplied. Branded Chrome must use `--operator-assisted`: the
operator performs Chrome's documented Developer mode + Load unpacked flow in
the disposable window. The runner never calls Chrome's private extension APIs
or simulates the file picker.
The script never touches the default Chrome profile.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from artifact_envelope import envelope as add_envelope
from artifact_envelope import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_DIR = ROOT / "extension" / "probes"
DEFAULT_ARTIFACT_DIR = ROOT / "artifacts" / "p0-extension"
REQUIRED_PERMISSIONS = {"debugger", "nativeMessaging", "storage", "tabGroups", "tabs"}
EXPECTED_EXTENSION_NAME = "agentyc Phase 0 Probe"
EXPECTED_SERVICE_WORKER = "service_worker.js"
EXTENSION_ID_PATTERN = re.compile(r"^[a-p]{32}$")
MAX_WORKER_DIAGNOSTICS = 8
MAX_DIAGNOSTIC_STRING = 128
CHROME_LOAD_EXTENSION_REFUSAL = "--load-extension is not allowed in Google Chrome, ignoring."
OPERATOR_PERMISSION_STATUSES = frozenset({"recorded", "none_observed"})
DEFAULT_OPERATOR_TIMEOUT = 180.0
MAX_EXTENSION_FILES = 64
MAX_EXTENSION_BYTES = 8 * 1024 * 1024
WEBSOCKET_HEADER_LIMIT = 16 * 1024
WEBSOCKET_FRAME_LIMIT = 64 * 1024
RESULT_HANDOFF_LIMIT = 16 * 1024
WORKER_DISCOVERY_TIMEOUT = 8.0
WORKER_DISCOVERY_INTERVAL = 0.1
CONTROL_PAGE_IDENTITY_TIMEOUT = 5.0
CONTROL_PAGE_RETRY_INTERVAL = 0.1
WEBSOCKET_GUID = "258EA5-E914-47DA-95CA-C5AB0DC85B11"
_LAST_CLEANUP_OK = True


def _contains_control(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _manifest_extension_id(manifest: dict[str, Any]) -> str | None:
    """Derive the pinned unpacked-extension ID without exposing the key or ID."""
    key = manifest.get("key")
    if key is None:
        return None
    if not isinstance(key, str) or not key or _contains_control(key):
        return ""
    try:
        public_key = base64.b64decode(key, validate=True)
    except (binascii.Error, ValueError):
        return ""
    if not public_key:
        return ""
    digest = hashlib.sha256(public_key).digest()
    alphabet = "abcdefghijklmnop"
    return "".join(alphabet[byte >> 4] + alphabet[byte & 0x0F] for byte in digest[:16])


def load_manifest() -> dict[str, Any]:
    manifest = json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("manifest_version") != 3:
        raise ValueError("probe manifest is not MV3")
    if manifest.get("name") != EXPECTED_EXTENSION_NAME:
        raise ValueError("probe manifest is not the expected extension")
    if manifest.get("key") is not None and _manifest_extension_id(manifest) == "":
        raise ValueError("probe manifest key is invalid")
    background = manifest.get("background")
    if not isinstance(background, dict) or background.get("service_worker") != EXPECTED_SERVICE_WORKER:
        raise ValueError("probe manifest does not name the expected service worker")
    permissions = set(manifest.get("permissions", []))
    missing = REQUIRED_PERMISSIONS - permissions
    if missing:
        raise ValueError(f"probe manifest is missing permissions: {sorted(missing)}")
    for filename in ("service_worker.js", "probe.html", "probe.js", "fixture.html"):
        if not (EXTENSION_DIR / filename).is_file():
            raise ValueError(f"probe fixture is missing {filename}")
    return manifest


def _read_bounded_file(path: Path, maximum: int) -> bytes:
    if maximum < 0:
        raise ValueError("bounded file limit is invalid")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise ValueError("extension file could not be stat-ed") from error
    if size > maximum:
        raise ValueError("extension file exceeds the bounded read limit")
    data = bytearray()
    with path.open("rb") as handle:
        while len(data) <= maximum:
            chunk = handle.read(min(1024 * 1024, maximum - len(data) + 1))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                raise ValueError("extension file grew beyond the bounded read limit")
    if len(data) != size:
        raise ValueError("extension file changed while it was being hashed")
    return bytes(data)


def extension_tree_sha256(directory: Path, *, exclude: frozenset[str] = frozenset()) -> str:
    """Hash an extension tree without following symlinks or unbounded files."""
    root = Path(directory)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("extension staging directory is not a real directory")
    files: list[tuple[str, bytes]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError("extension tree must not contain symlinks")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("extension tree contains a non-file entry")
        relative = path.relative_to(root).as_posix()
        if relative in exclude:
            continue
        remaining = MAX_EXTENSION_BYTES - total_bytes
        if len(files) >= MAX_EXTENSION_FILES or remaining < 0:
            raise ValueError("extension tree exceeds the bounded staging limit")
        data = _read_bounded_file(path, remaining)
        total_bytes += len(data)
        files.append((relative, data))
    if not files:
        raise ValueError("extension staging directory is empty")
    digest = hashlib.sha256()
    for relative, data in files:
        encoded_name = relative.encode("utf-8")
        digest.update(struct.pack(">I", len(encoded_name)))
        digest.update(encoded_name)
        digest.update(struct.pack(">Q", len(data)))
        digest.update(data)
    return digest.hexdigest()


def stage_extension(profile_dir: Path, binding_nonce: str) -> tuple[Path, str, str]:
    """Copy and bind the exact probe selected by the operator."""
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", binding_nonce, re.IGNORECASE):
        raise ValueError("extension binding nonce is invalid")
    source_hash = extension_tree_sha256(EXTENSION_DIR)
    destination = profile_dir / "extension" / "probes"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(EXTENSION_DIR, destination, symlinks=False)
    binding = {"nonce": binding_nonce, "source_tree_sha256": source_hash}
    (destination / "probe_binding.json").write_text(
        json.dumps(binding, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    staged_source_hash = extension_tree_sha256(destination, exclude=frozenset({"probe_binding.json"}))
    if staged_source_hash != source_hash:
        raise ValueError("staged extension hash did not match the repository probe")
    staged_hash = extension_tree_sha256(destination)
    return destination, source_hash, staged_hash


def safe_artifact_dir(value: str) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        requested = ROOT / requested
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise SystemExit("artifact path components must not be symlinks")
        current = current.parent
    requested = requested.resolve()
    allowed = DEFAULT_ARTIFACT_DIR.resolve()
    if requested != allowed and allowed not in requested.parents:
        raise SystemExit("artifact directory must be inside artifacts/p0-extension/")
    return requested


def safe_profile_dir(value: str) -> Path:
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        raise SystemExit("profile directory must be an absolute disposable temporary path")
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise SystemExit("profile path components must not be symlinks")
        current = current.parent
    requested = requested.resolve(strict=False)
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if requested == temporary_root or temporary_root not in requested.parents:
        raise SystemExit("profile directory must be inside the system temporary directory")
    if requested.exists() and (not requested.is_dir() or any(requested.iterdir())):
        raise SystemExit("profile directory must be absent or empty disposable temporary storage")
    return requested


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


def chrome_endpoint(port: int, path: str) -> Any:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    with _NO_REDIRECT_OPENER.open(request, timeout=0.75) as response:
        return json.loads(response.read().decode("utf-8"))


def new_request_id() -> str:
    """Return a per-run UUID generated by the operating system CSPRNG."""
    return str(uuid.uuid4())


class DevToolsSocket:
    """Minimal bounded RFC 6455 WebSocket client for local Chrome CDP."""

    def __init__(self, url: str, timeout: float = 2.0) -> None:
        if timeout <= 0:
            raise ValueError("debugger websocket timeout must be positive")
        try:
            parsed = urllib.parse.urlparse(url)
            hostname = parsed.hostname
            port = parsed.port
        except (TypeError, ValueError) as error:
            raise ValueError("debugger websocket URL is invalid") from error
        if (
            parsed.scheme != "ws"
            or hostname not in {"127.0.0.1", "localhost"}
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or not parsed.path.startswith("/")
            or _contains_control(parsed.path)
            or _contains_control(parsed.query)
            or not 1 <= port <= 65535
        ):
            raise ValueError("debugger websocket must be a local ws URL without credentials or fragments")
        try:
            resolved_hosts = {
                sockaddr[-1][0]
                for sockaddr in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            }
        except OSError as error:
            raise ValueError("debugger websocket hostname could not be resolved") from error
        if not resolved_hosts or any(not ipaddress.ip_address(host).is_loopback for host in resolved_hosts):
            raise ValueError("debugger websocket hostname is not loopback")

        self.socket: socket.socket | None = None
        self.timeout = timeout
        self._receive_buffer = bytearray()
        self.next_id = 0
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request_target = parsed.path or "/"
        if parsed.query:
            request_target += f"?{parsed.query}"
        request = (
            f"GET {request_target} HTTP/1.1\r\n"
            f"Host: {hostname}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")

        raw_socket = socket.create_connection((hostname, port), timeout=timeout)
        self.socket = raw_socket
        try:
            raw_socket.settimeout(timeout)
            raw_socket.sendall(request)
            response = self._read_http_headers()
            self._validate_handshake(response, key)
        except (OSError, ValueError, TypeError, UnicodeError, struct.error):
            self.close()
            raise

    def _read_http_headers(self) -> bytes:
        if self.socket is None:
            raise OSError("debugger websocket is closed")
        data = bytearray()
        while True:
            end = data.find(b"\r\n\r\n")
            if end >= 0:
                header_end = end + 4
                self._receive_buffer.extend(data[header_end:])
                return bytes(data[:header_end])
            if len(data) >= WEBSOCKET_HEADER_LIMIT:
                raise ValueError("debugger websocket headers exceed the bound")
            chunk = self.socket.recv(min(4096, WEBSOCKET_HEADER_LIMIT - len(data)))
            if not chunk:
                raise OSError("debugger websocket closed during handshake")
            data.extend(chunk)

    def _validate_handshake(self, response: bytes, key: str) -> None:
        try:
            text = response.decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError("debugger websocket handshake was not ASCII") from error
        if not text.endswith("\r\n\r\n"):
            raise ValueError("debugger websocket handshake was not terminated")
        lines = text[:-2].split("\r\n")
        if not lines or lines[-1] != "":
            raise ValueError("debugger websocket handshake was not terminated")
        status = lines[0].split(" ", 2)
        if (
            len(status) != 3
            or status[0] != "HTTP/1.1"
            or status[1] != "101"
            or _contains_control(status[2])
        ):
            raise OSError("debugger websocket handshake failed")

        headers: dict[str, str] = {}
        for line in lines[1:-1]:
            if not line or line[0] in " \t" or ":" not in line:
                raise ValueError("debugger websocket response contained an invalid header")
            name, value = line.split(":", 1)
            if not name or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
                raise ValueError("debugger websocket response contained an invalid header name")
            if _contains_control(value):
                raise ValueError("debugger websocket response contained a control character")
            key_name = name.lower()
            if key_name in headers:
                raise ValueError("debugger websocket response repeated a header")
            headers[key_name] = value.strip(" \t")

        if headers.get("upgrade", "").lower() != "websocket":
            raise ValueError("debugger websocket response omitted Upgrade: websocket")
        connection_tokens = {
            token.strip().lower() for token in headers.get("connection", "").split(",") if token.strip()
        }
        if "upgrade" not in connection_tokens:
            raise ValueError("debugger websocket response omitted Connection: Upgrade")
        accept = headers.get("sec-websocket-accept")
        if accept is None:
            raise ValueError("debugger websocket response omitted Sec-WebSocket-Accept")
        expected_accept = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
        if not hmac.compare_digest(accept, expected_accept):
            raise ValueError("debugger websocket accept key did not match the request")
        if "sec-websocket-extensions" in headers:
            raise ValueError("debugger websocket negotiated an unsupported extension")
        if "sec-websocket-protocol" in headers:
            raise ValueError("debugger websocket negotiated an unrequested subprotocol")

    def _frame(self, payload: bytes, opcode: int = 1) -> bytes:
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError("debugger websocket payload must be bytes-like")
        payload = bytes(payload)
        if opcode not in {0, 1, 2, 8, 9, 10}:
            raise ValueError("debugger websocket opcode is invalid")
        if opcode >= 8 and len(payload) > 125:
            raise ValueError("debugger websocket control frame exceeds the bound")
        if len(payload) > WEBSOCKET_FRAME_LIMIT:
            raise ValueError("debugger message exceeds the probe bound")
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = bytes([0x80 | opcode, 0x80 | length])
        elif length <= 0xFFFF:
            header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", length)
        else:
            header = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack("!Q", length)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return header + mask + masked

    def _receive_frame(self) -> tuple[bool, int, bytes]:
        header = self._receive_exact(2)
        first, second = header
        fin = bool(first & 0x80)
        if first & 0x70:
            raise ValueError("debugger websocket used a reserved frame bit")
        opcode = first & 0x0F
        if opcode not in {0, 1, 2, 8, 9, 10}:
            raise ValueError("debugger websocket opcode is invalid")
        if second & 0x80:
            raise ValueError("debugger websocket server frame was masked")
        length_code = second & 0x7F
        if length_code < 126:
            length = length_code
        elif length_code == 126:
            length = struct.unpack("!H", self._receive_exact(2))[0]
            if length < 126:
                raise ValueError("debugger websocket used a non-minimal frame length")
        else:
            length = struct.unpack("!Q", self._receive_exact(8))[0]
            if length & (1 << 63):
                raise ValueError("debugger websocket frame length is invalid")
            if length < 65536:
                raise ValueError("debugger websocket used a non-minimal frame length")
        if length > WEBSOCKET_FRAME_LIMIT:
            raise ValueError("debugger frame exceeds the probe bound")
        if opcode >= 8 and (not fin or length > 125):
            raise ValueError("debugger websocket control frame is invalid")
        payload = self._receive_exact(length)
        if opcode == 8:
            self._validate_close_payload(payload)
        return fin, opcode, payload

    def _validate_close_payload(self, payload: bytes) -> None:
        if len(payload) == 1:
            raise ValueError("debugger websocket close frame has an invalid payload")
        if len(payload) < 2:
            return
        code = struct.unpack("!H", payload[:2])[0]
        valid_code = (
            (1000 <= code <= 1003)
            or (1007 <= code <= 1014)
            or (3000 <= code <= 4999)
        )
        if not valid_code:
            raise ValueError("debugger websocket close code is invalid")
        try:
            payload[2:].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("debugger websocket close reason is invalid UTF-8") from error

    def _receive(self) -> tuple[int, bytes]:
        """Receive one complete, unfragmented frame for compatibility with old callers."""
        fin, opcode, payload = self._receive_frame()
        if not fin or opcode == 0:
            raise ValueError("fragmented debugger websocket messages require the message reader")
        return opcode, payload

    def _receive_message(self) -> bytes:
        fragments = bytearray()
        message_opcode: int | None = None
        while True:
            fin, opcode, payload = self._receive_frame()
            if opcode == 9:
                if self.socket is None:
                    raise OSError("debugger websocket is closed")
                self.socket.sendall(self._frame(payload, opcode=10))
                continue
            if opcode == 10:
                continue
            if opcode == 8:
                try:
                    if self.socket is not None:
                        self.socket.sendall(self._frame(payload, opcode=8))
                except OSError:
                    pass
                raise OSError("debugger websocket closed")
            if opcode == 0:
                if message_opcode is None:
                    raise ValueError("debugger websocket continuation arrived without a message")
            elif opcode in {1, 2}:
                if message_opcode is not None:
                    raise ValueError("debugger websocket started a message before finishing the prior one")
                message_opcode = opcode
            else:
                raise ValueError("debugger websocket data opcode is invalid")
            if len(fragments) + len(payload) > WEBSOCKET_FRAME_LIMIT:
                raise ValueError("debugger websocket fragmented message exceeds the bound")
            fragments.extend(payload)
            if fin:
                if message_opcode != 1:
                    raise ValueError("debugger websocket returned a non-text CDP message")
                return bytes(fragments)

    def _receive_exact(self, size: int) -> bytes:
        if size < 0:
            raise ValueError("debugger websocket read size is invalid")
        result = bytearray()
        while len(result) < size:
            if self._receive_buffer:
                take = min(size - len(result), len(self._receive_buffer))
                result.extend(self._receive_buffer[:take])
                del self._receive_buffer[:take]
                continue
            if self.socket is None:
                raise OSError("debugger websocket is closed")
            chunk = self.socket.recv(min(size - len(result), WEBSOCKET_FRAME_LIMIT))
            if not chunk:
                raise OSError("debugger websocket closed")
            result.extend(chunk)
        return bytes(result)

    def command(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not isinstance(method, str) or not method or len(method) > 256:
            raise ValueError("debugger command method is invalid")
        if params is not None and not isinstance(params, dict):
            raise TypeError("debugger command parameters must be an object")
        self.next_id += 1
        command_id = self.next_id
        payload = json.dumps(
            {"id": command_id, "method": method, "params": params or {}},
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > WEBSOCKET_FRAME_LIMIT:
            raise ValueError("debugger command exceeds the probe bound")
        if self.socket is None:
            raise OSError("debugger websocket is closed")
        self.socket.sendall(self._frame(payload))
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                self.socket.settimeout(min(self.timeout, max(0.01, remaining)))
                frame = self._receive_message()
            except TimeoutError as error:
                raise TimeoutError("debugger command timed out") from error
            try:
                message = json.loads(frame.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("debugger websocket returned invalid JSON") from error
            if not isinstance(message, dict):
                raise TypeError("debugger websocket returned a non-object message")
            if "id" in message and not isinstance(message["id"], int):
                raise ValueError("debugger websocket returned an invalid command id")
            if message.get("id") == command_id:
                if "error" in message:
                    raise OSError("debugger command rejected")
                return message.get("result")
        raise TimeoutError("debugger command timed out")

    def close(self) -> None:
        sock = self.socket
        self.socket = None
        if sock is None:
            return
        try:
            sock.close()
        except OSError:
            pass


def _runtime_value(result: Any) -> Any:
    if not isinstance(result, dict) or result.get("exceptionDetails") is not None:
        raise ValueError("extension runtime evaluation failed")
    remote = result.get("result")
    if not isinstance(remote, dict) or "value" not in remote:
        raise ValueError("extension runtime evaluation returned no value")
    return remote["value"]


def _worker_candidate(target: Any, expected_port: int | None = None) -> tuple[str, str] | None:
    if not isinstance(target, dict) or target.get("type") != "service_worker":
        return None
    target_url = target.get("url")
    websocket_url = target.get("webSocketDebuggerUrl")
    if not isinstance(target_url, str) or not isinstance(websocket_url, str):
        return None
    try:
        parsed = urllib.parse.urlparse(target_url)
        extension_id = parsed.hostname
        parsed_port = parsed.port
    except ValueError:
        return None
    try:
        websocket = urllib.parse.urlparse(websocket_url)
        websocket_port = websocket.port
    except ValueError:
        return None
    expected_url = f"chrome-extension://{extension_id}/{EXPECTED_SERVICE_WORKER}"
    if (
        parsed.scheme != "chrome-extension"
        or parsed.username is not None
        or parsed.password is not None
        or parsed_port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != f"/{EXPECTED_SERVICE_WORKER}"
        or not isinstance(extension_id, str)
        or not EXTENSION_ID_PATTERN.fullmatch(extension_id)
        or target_url != expected_url
        or websocket.scheme != "ws"
        or websocket.hostname not in {"127.0.0.1", "localhost"}
        or websocket_port is None
        or expected_port is not None and websocket_port != expected_port
        or websocket.username is not None
        or websocket.password is not None
        or websocket.fragment
    ):
        return None
    return extension_id, websocket_url


def _expected_extension_manifest(manifest: dict[str, Any] | None) -> dict[str, Any]:
    if manifest is not None:
        return manifest
    return {
        "manifest_version": 3,
        "name": EXPECTED_EXTENSION_NAME,
        "version": "0.0.1",
        "permissions": sorted(REQUIRED_PERMISSIONS),
        "background": {"service_worker": EXPECTED_SERVICE_WORKER},
    }


def _is_expected_worker(identity: Any, extension_id: str, manifest: dict[str, Any]) -> bool:
    """Retained for offline negative tests; live probing does not attach to workers."""
    background = manifest.get("background")
    service_worker = background.get("service_worker") if isinstance(background, dict) else None
    expected_extension_id = _manifest_extension_id(manifest)
    if not isinstance(service_worker, str) or service_worker != EXPECTED_SERVICE_WORKER:
        return False
    return (
        isinstance(identity, dict)
        and (expected_extension_id is None or expected_extension_id == extension_id)
        and identity.get("runtime_id") == extension_id
        and identity.get("manifest_version") == manifest.get("manifest_version")
        and identity.get("name") == manifest.get("name")
        and identity.get("version") == manifest.get("version")
        and identity.get("service_worker") == service_worker
        and identity.get("origin") == f"chrome-extension://{extension_id}"
        and identity.get("pathname") == f"/{service_worker}"
    )


def _extension_page_candidate(
    target: Any,
    expected_port: int | None = None,
    expected_path: str = "/probe.html",
) -> tuple[str, str] | None:
    """Select only the exact stable extension control-page target."""
    if not isinstance(target, dict) or target.get("type") != "page":
        return None
    target_url = target.get("url")
    websocket_url = target.get("webSocketDebuggerUrl")
    if not isinstance(target_url, str) or not isinstance(websocket_url, str):
        return None
    try:
        parsed = urllib.parse.urlparse(target_url)
        websocket = urllib.parse.urlparse(websocket_url)
        extension_id = parsed.hostname
        websocket_port = websocket.port
    except ValueError:
        return None
    expected_url = f"chrome-extension://{extension_id}{expected_path}"
    if (
        parsed.scheme != "chrome-extension"
        or not isinstance(extension_id, str)
        or not EXTENSION_ID_PATTERN.fullmatch(extension_id)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != expected_path
        or target_url != expected_url
        or websocket.scheme != "ws"
        or websocket.hostname not in {"127.0.0.1", "localhost"}
        or websocket_port is None
        or expected_port is not None and websocket_port != expected_port
        or websocket.username is not None
        or websocket.password is not None
        or websocket.fragment
    ):
        return None
    return extension_id, websocket_url


def _is_expected_extension_page(
    identity: Any,
    extension_id: str,
    manifest: dict[str, Any],
) -> bool:
    background = manifest.get("background")
    service_worker = background.get("service_worker") if isinstance(background, dict) else None
    expected_extension_id = _manifest_extension_id(manifest)
    return (
        isinstance(identity, dict)
        and (expected_extension_id is None or expected_extension_id == extension_id)
        and identity.get("runtime_id") == extension_id
        and identity.get("manifest_version") == manifest.get("manifest_version")
        and identity.get("name") == manifest.get("name")
        and identity.get("version") == manifest.get("version")
        and identity.get("service_worker") == service_worker
        and identity.get("origin") == f"chrome-extension://{extension_id}"
        and identity.get("pathname") == "/probe.html"
    )


def _extension_page_identity_evidence(
    identity: Any,
    extension_id: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    background = manifest.get("background")
    service_worker = background.get("service_worker") if isinstance(background, dict) else None
    expected_extension_id = _manifest_extension_id(manifest)
    evidence: dict[str, Any] = {
        "status": "identity_mismatch",
        "runtime_id_matches_target": isinstance(identity, dict) and identity.get("runtime_id") == extension_id,
        "manifest_version_matches": isinstance(identity, dict) and identity.get("manifest_version") == manifest.get("manifest_version"),
        "name_matches": isinstance(identity, dict) and identity.get("name") == manifest.get("name"),
        "version_matches": isinstance(identity, dict) and identity.get("version") == manifest.get("version"),
        "service_worker_matches": isinstance(identity, dict) and identity.get("service_worker") == service_worker,
        "origin_matches": isinstance(identity, dict) and identity.get("origin") == f"chrome-extension://{extension_id}",
        "pathname_matches": isinstance(identity, dict) and identity.get("pathname") == "/probe.html",
    }
    if expected_extension_id is not None:
        evidence["manifest_key_id_matches_target"] = expected_extension_id == extension_id
    if isinstance(identity, dict):
        for field in ("name", "version", "service_worker"):
            value = _bounded_worker_diagnostic(field, identity.get(field))
            if value is not None:
                evidence[f"observed_{field}"] = value
    return evidence


def _bounded_diagnostic_string(value: Any) -> str | None:
    if not isinstance(value, str) or _contains_control(value):
        return None
    return value[:MAX_DIAGNOSTIC_STRING]


def _bounded_worker_diagnostic(field: str, value: Any) -> str | None:
    bounded = _bounded_diagnostic_string(value)
    if bounded is None:
        return None
    if field == "version" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", bounded):
        return None
    if field == "service_worker" and not re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", bounded):
        return None
    if field == "name" and (len(bounded) > MAX_DIAGNOSTIC_STRING or any(character in bounded for character in "/\\\\:")):
        return None
    return bounded


def _worker_identity_evidence(
    identity: Any,
    extension_id: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    background = manifest.get("background")
    service_worker = background.get("service_worker") if isinstance(background, dict) else None
    expected_extension_id = _manifest_extension_id(manifest)
    evidence: dict[str, Any] = {
        "status": "identity_mismatch",
        "runtime_id_matches_target": isinstance(identity, dict) and identity.get("runtime_id") == extension_id,
        "manifest_version_matches": isinstance(identity, dict) and identity.get("manifest_version") == manifest.get("manifest_version"),
        "name_matches": isinstance(identity, dict) and identity.get("name") == manifest.get("name"),
        "version_matches": isinstance(identity, dict) and identity.get("version") == manifest.get("version"),
        "service_worker_matches": isinstance(identity, dict) and identity.get("service_worker") == service_worker,
        "origin_matches": isinstance(identity, dict) and identity.get("origin") == f"chrome-extension://{extension_id}",
        "pathname_matches": isinstance(identity, dict) and identity.get("pathname") == f"/{service_worker}",
    }
    if expected_extension_id is not None:
        evidence["manifest_key_id_matches_target"] = expected_extension_id == extension_id
    if isinstance(identity, dict):
        for field in ("name", "version", "service_worker"):
            value = _bounded_worker_diagnostic(field, identity.get(field))
            if value is not None:
                evidence[f"observed_{field}"] = value
    return evidence


def _identity_probe_failure(phase: str) -> dict[str, str]:
    """Return bounded diagnostics for a transient worker-lifecycle failure."""
    return {
        "status": "identity_probe_failed",
        "phase": phase,
        "reason": "worker identity could not be verified",
    }


def _cleanup_owned_profile(profile: Path | None) -> bool:
    global _LAST_CLEANUP_OK
    if profile is None or not profile.exists():
        return True
    success = True
    try:
        shutil.rmtree(profile)
    except OSError:
        success = False
    if profile.exists():
        success = False
    if not success:
        _LAST_CLEANUP_OK = False
    return success


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _safe_stop_process(process: subprocess.Popen[bytes]) -> bool:
    global _LAST_CLEANUP_OK
    success = True
    owned_group = False
    try:
        try:
            owned_group = os.getpgid(process.pid) == process.pid
        except OSError:
            owned_group = False
        if process.poll() is None:
            try:
                if owned_group:
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except (OSError, AttributeError):
                try:
                    process.terminate()
                except OSError:
                    success = False
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                if owned_group:
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except (OSError, AttributeError):
                try:
                    process.kill()
                except OSError:
                    success = False
            try:
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                success = False
        if owned_group and _process_group_exists(process.pid):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                success = False
            time.sleep(0.1)
            if _process_group_exists(process.pid):
                success = False
    except (OSError, ValueError):
        success = False
    if process.poll() is not None and (not owned_group or not _process_group_exists(process.pid)):
        return success
    _LAST_CLEANUP_OK = False
    return False


def extension_probe_result(
    port: int,
    targets: list[dict[str, Any]],
    expected_manifest: dict[str, Any] | None = None,
    fixture_path: Path | None = None,
    *,
    binding_nonce: str | None = None,
    source_extension_hash: str | None = None,
    target_provider: Callable[[], list[dict[str, Any]] | None] | None = None,
) -> dict[str, Any]:
    """Run the probe through an identity-verified extension control page.

    The live path deliberately does not attach to an MV3 service-worker target.
    Chrome documents service workers as restartable, and a worker-shaped CDP
    target can disappear between discovery and evaluation. The control page is
    a stable extension context; its documented ``chrome.storage`` call wakes
    the worker, which then performs the debugger, tab-group, and Native
    Messaging checks.
    """
    request_id = new_request_id()
    manifest = _expected_extension_manifest(expected_manifest)
    current_targets = targets if isinstance(targets, list) else []
    candidate_count = 0
    identity_diagnostics: list[dict[str, Any]] = []
    identity_deadline = (
        time.monotonic() + CONTROL_PAGE_IDENTITY_TIMEOUT
        if target_provider is not None
        else None
    )

    def unavailable(limitation: str, verified_count: int = 0) -> dict[str, Any]:
        return {
            "status": "live_unavailable",
            "candidate_extension_page_count": candidate_count,
            "verified_extension_page_count": verified_count,
            "identity_diagnostics": identity_diagnostics,
            "control_page_identity_passed": False,
            "limitation": limitation,
        }

    while True:
        candidates = [
            _extension_page_candidate(target, port)
            for target in current_targets
        ]
        candidates = [candidate for candidate in candidates if candidate is not None]
        candidate_count = max(candidate_count, len(candidates))
        if not candidates:
            if target_provider is None or identity_deadline is None:
                return unavailable("the exact probe extension control page was not observed")
            remaining = identity_deadline - time.monotonic()
            if remaining <= 0:
                return unavailable("the exact probe extension control page was not observed")
            try:
                refreshed_targets = target_provider()
            except (OSError, ValueError, TypeError):
                refreshed_targets = None
            current_targets = refreshed_targets if isinstance(refreshed_targets, list) else []
            remaining = identity_deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(CONTROL_PAGE_RETRY_INTERVAL, remaining))
            continue

        verified: list[DevToolsSocket] = []
        transient_failure = False
        for extension_id, websocket_url in candidates:
            client: DevToolsSocket | None = None
            try:
                try:
                    client = DevToolsSocket(websocket_url)
                    client.command("Runtime.enable")
                except (OSError, ValueError, TypeError, TimeoutError, struct.error):
                    transient_failure = True
                    if len(identity_diagnostics) < MAX_WORKER_DIAGNOSTICS:
                        identity_diagnostics.append(_identity_probe_failure("control_page_connect"))
                    continue
                try:
                    identity_result = client.command(
                        "Runtime.evaluate",
                        {
                            "expression": "(() => { const manifest = chrome.runtime.getManifest(); return { runtime_id: chrome.runtime.id, manifest_version: manifest.manifest_version, name: manifest.name, version: manifest.version, service_worker: manifest.background?.service_worker ?? null, origin: location.origin, pathname: location.pathname }; })()",
                            "returnByValue": True,
                        },
                    )
                    identity = _runtime_value(identity_result)
                except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, TimeoutError, struct.error):
                    transient_failure = True
                    if len(identity_diagnostics) < MAX_WORKER_DIAGNOSTICS:
                        identity_diagnostics.append(_identity_probe_failure("control_page_evaluate"))
                    continue
                if _is_expected_extension_page(identity, extension_id, manifest):
                    verified.append(client)
                    client = None
                elif len(identity_diagnostics) < MAX_WORKER_DIAGNOSTICS:
                    identity_diagnostics.append(
                        _extension_page_identity_evidence(identity, extension_id, manifest)
                    )
            finally:
                if client is not None:
                    client.close()

        if len(verified) == 1:
            socket_client = verified[0]
            break
        if len(verified) > 1:
            for client in verified:
                client.close()
            return unavailable(
                "the exact probe extension control page was ambiguous after identity verification",
                len(verified),
            )
        if not transient_failure or target_provider is None or identity_deadline is None:
            limitation = (
                "the exact probe extension control page was not identity-verified"
                if identity_diagnostics
                else "the exact probe extension control page was not observed"
            )
            return unavailable(limitation)
        remaining = identity_deadline - time.monotonic()
        if remaining <= 0:
            return unavailable(
                "the exact probe extension control page was not identity-verified before the retry deadline"
            )
        time.sleep(min(CONTROL_PAGE_RETRY_INTERVAL, remaining))
        try:
            refreshed_targets = target_provider()
        except (OSError, ValueError, TypeError):
            refreshed_targets = None
        current_targets = refreshed_targets if isinstance(refreshed_targets, list) else []

    try:
        request_literal = json.dumps(request_id)
        fixture_literal = json.dumps(
            (fixture_path or EXTENSION_DIR / "fixture.html").resolve().as_uri()
        )
        trigger = socket_client.command(
            "Runtime.evaluate",
            {
                "expression": f"(async () => {{ await chrome.storage.local.remove('last_probe'); await chrome.storage.local.set({{run_probe: {{request_id: {request_literal}, fixture_url: {fixture_literal}}}}}); return true; }})()",
                "awaitPromise": True,
                "returnByValue": True,
            },
        )
        if _runtime_value(trigger) is not True:
            raise ValueError("extension probe trigger was not acknowledged")
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            result = socket_client.command(
                "Runtime.evaluate",
                {
                    "expression": "chrome.storage.local.get('last_probe').then((value) => value.last_probe ?? null)",
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            )
            value = _runtime_value(result)
            if value is None:
                time.sleep(0.1)
                continue
            encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
            if len(encoded) > RESULT_HANDOFF_LIMIT:
                return {
                    "status": "live_unavailable",
                    "candidate_extension_page_count": candidate_count,
                    "verified_extension_page_count": len(verified),
                    "control_page_identity_passed": True,
                    "limitation": "extension result handoff exceeded the probe bound",
                }
            if not isinstance(value, dict) or value.get("request_id") != request_id:
                time.sleep(0.1)
                continue
            permissions = value.get("permissions")
            permissions_match = isinstance(permissions, list) and set(permissions) == set(manifest.get("permissions", []))
            transcript = value.get("handshake_transcript")
            transcript_valid = transcript == ["hello_accepted", "probe_accepted"]
            binding_passed = (
                isinstance(binding_nonce, str)
                and isinstance(source_extension_hash, str)
                and value.get("binding_nonce") == binding_nonce
                and value.get("binding_source_tree_sha256") == source_extension_hash
            )
            required = {
                "extension_loaded": value.get("extension_loaded") is True,
                "extension_build_binding_passed": binding_passed,
                "fixture_identity_passed": value.get("fixture_identity_passed") is True,
                "debugger_command_passed": value.get("debugger_command_passed") is True,
                "debugger_event_received": value.get("debugger_event_received") is True,
                "tab_group_created": value.get("tab_group_created") is True,
                "native_messaging_passed": value.get("native_messaging_passed") is True,
                "debugger_cleanup_passed": value.get("debugger_cleanup_passed") is True,
                "cleanup_passed": value.get("cleanup_passed") is True,
                "screenshot_captured": value.get("screenshot_captured") is True,
                "extension_identity_passed": value.get("extension_version") == manifest.get("version") and permissions_match,
                "control_page_identity_passed": True,
            }
            if value.get("ok") is True and all(required.values()) and transcript_valid:
                return {
                    "status": "live_passed",
                    "candidate_extension_page_count": candidate_count,
                    "verified_extension_page_count": len(verified),
                    **required,
                    "chrome_mediated_native_messaging": True,
                    "screenshots": [{"captured": True, "format": "png"}],
                    "handshake_transcript": transcript,
                }
            return {
                "status": "live_unavailable",
                "candidate_extension_page_count": candidate_count,
                "verified_extension_page_count": len(verified),
                **required,
                "handshake_transcript": transcript if isinstance(transcript, list) else [],
                "limitation": "extension probe returned incomplete evidence",
            }
        return {
            "status": "live_unavailable",
            "candidate_extension_page_count": candidate_count,
            "verified_extension_page_count": len(verified),
            "control_page_identity_passed": True,
            "limitation": "extension probe result was not received before the deadline",
        }
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, TimeoutError, struct.error):
        return {
            "status": "live_unavailable",
            "candidate_extension_page_count": candidate_count,
            "verified_extension_page_count": len(verified),
            "control_page_identity_passed": True,
            "limitation": "extension result handoff failed closed",
        }
    finally:
        socket_client.close()


def wait_for_chrome(port: int, timeout: float = 8.0) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = chrome_endpoint(port, "/json/version")
            return value if isinstance(value, dict) else None
        except (OSError, urllib.error.URLError, ValueError):
            time.sleep(0.1)
    return None


def chrome_binary(value: str | None) -> str | None:
    if value:
        return value
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ]
    for candidate in candidates:
        if os.path.isabs(candidate) and Path(candidate).is_file():
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def build_chrome_command(
    executable: str,
    profile_dir: Path,
    port: int,
    *,
    extension_dir: Path | None,
    fixture_url: str | None,
    operator_assisted: bool,
) -> list[str]:
    """Build an owned disposable-browser command for either install lane."""
    command = [
        executable,
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--enable-automation",
        "--disable-background-networking",
        "--enable-logging=stderr",
        "--new-window",
    ]
    if operator_assisted:
        if extension_dir is not None or fixture_url is not None:
            raise ValueError("operator-assisted launch must not receive command-line extension loading inputs")
        return command
    if extension_dir is None or not fixture_url:
        raise ValueError("automated extension launch requires a staged extension and fixture URL")
    command.extend([f"--load-extension={extension_dir}", fixture_url])
    return command


def _owned_page_target(port: int, process: subprocess.Popen[bytes]) -> dict[str, Any] | None:
    if not _endpoint_belongs_to_process(port, process):
        return None
    try:
        targets = chrome_endpoint(port, "/json/list")
    except (OSError, urllib.error.URLError, ValueError):
        return None
    if not isinstance(targets, list):
        return None
    for target in targets:
        if (
            isinstance(target, dict)
            and target.get("type") == "page"
            and isinstance(target.get("webSocketDebuggerUrl"), str)
        ):
            return target
    return None


def navigate_page_target(websocket_url: str, url: str) -> None:
    if not isinstance(websocket_url, str) or not websocket_url:
        raise ValueError("owned page debugger URL is invalid")
    if not isinstance(url, str) or not url or _contains_control(url):
        raise ValueError("owned page URL is invalid")
    client = DevToolsSocket(websocket_url)
    try:
        result = client.command("Page.navigate", {"url": url})
        if not isinstance(result, dict) or result.get("errorText"):
            raise OSError("probe-owned page navigation was rejected")
    finally:
        client.close()


def navigate_owned_page(port: int, process: subprocess.Popen[bytes], url: str) -> str:
    """Navigate only a page in the probe-owned disposable browser."""
    target = _owned_page_target(port, process)
    if target is None:
        raise OSError("probe-owned page target was not observed")
    websocket_url = target["webSocketDebuggerUrl"]
    navigate_page_target(websocket_url, url)
    return websocket_url


def create_owned_target(port: int, process: subprocess.Popen[bytes], url: str) -> str:
    """Create one background target in the probe-owned browser only."""
    if not _endpoint_belongs_to_process(port, process):
        raise OSError("probe-owned browser endpoint was not observed")
    version = chrome_endpoint(port, "/json/version")
    websocket_url = version.get("webSocketDebuggerUrl") if isinstance(version, dict) else None
    if not isinstance(websocket_url, str):
        raise OSError("probe-owned browser websocket was not observed")
    client = DevToolsSocket(websocket_url)
    try:
        result = client.command(
            "Target.createTarget",
            {"url": url, "background": True},
        )
    finally:
        client.close()
    target_id = result.get("targetId") if isinstance(result, dict) else None
    if not isinstance(target_id, str) or not target_id:
        raise OSError("probe-owned extension control target was not created")
    return target_id


def close_owned_target(port: int, process: subprocess.Popen[bytes], target_id: str) -> bool:
    """Close only a target created by this disposable probe."""
    if not isinstance(target_id, str) or not target_id:
        return False
    if not _endpoint_belongs_to_process(port, process):
        return False
    try:
        version = chrome_endpoint(port, "/json/version")
        websocket_url = version.get("webSocketDebuggerUrl") if isinstance(version, dict) else None
        if not isinstance(websocket_url, str):
            return False
        client = DevToolsSocket(websocket_url)
        try:
            result = client.command("Target.closeTarget", {"targetId": target_id})
        finally:
            client.close()
        return isinstance(result, dict) and result.get("success") is True
    except (OSError, ValueError, TypeError, TimeoutError, struct.error):
        return False


def wait_for_extension_control_page(
    port: int,
    process: subprocess.Popen[bytes],
    extension_id: str,
    timeout: float = WORKER_DISCOVERY_TIMEOUT,
) -> bool:
    """Wait for the exact extension control page, not a worker-shaped target."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("control-page discovery timeout must be positive and finite")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _endpoint_belongs_to_process(port, process):
            return False
        try:
            targets = chrome_endpoint(port, "/json/list")
        except (OSError, urllib.error.URLError, ValueError):
            targets = None
        if isinstance(targets, list):
            for target in targets:
                candidate = _extension_page_candidate(target, port)
                if candidate is not None and candidate[0] == extension_id:
                    return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(WORKER_DISCOVERY_INTERVAL, remaining))
    return False


def wait_for_fixture_page(
    port: int,
    process: subprocess.Popen[bytes],
    fixture_url: str,
    timeout: float = WORKER_DISCOVERY_TIMEOUT,
) -> bool:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("fixture discovery timeout must be positive and finite")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _endpoint_belongs_to_process(port, process):
            return False
        try:
            targets = chrome_endpoint(port, "/json/list")
        except (OSError, urllib.error.URLError, ValueError):
            targets = None
        if isinstance(targets, list):
            for target in targets:
                if not (
                    isinstance(target, dict)
                    and target.get("type") == "page"
                    and target.get("url") == fixture_url
                    and isinstance(target.get("webSocketDebuggerUrl"), str)
                ):
                    continue
                client = DevToolsSocket(target["webSocketDebuggerUrl"])
                try:
                    ready = client.command(
                        "Runtime.evaluate",
                        {
                            "expression": "({ ready: document.readyState, title: document.title })",
                            "returnByValue": True,
                        },
                    )
                    value = _runtime_value(ready)
                    if isinstance(value, dict) and value.get("ready") == "complete" and value.get("title") == "agentyc P0 probe fixture":
                        return True
                except (OSError, ValueError, TypeError, KeyError, TimeoutError, struct.error):
                    pass
                finally:
                    client.close()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(WORKER_DISCOVERY_INTERVAL, remaining))
    return False


def claim_disposable_profile(profile: Path) -> None:
    """Claim an empty profile directory without following a final symlink."""
    if profile.is_symlink():
        raise ValueError("disposable profile must not be a symlink")
    if profile.exists():
        if not profile.is_dir() or any(profile.iterdir()):
            raise ValueError("the supplied disposable Chrome profile is not empty")
        return
    try:
        profile.mkdir(mode=0o700)
    except FileExistsError as error:
        if profile.is_symlink() or not profile.is_dir() or any(profile.iterdir()):
            raise ValueError("the supplied disposable Chrome profile is not empty") from error


def print_operator_instructions(staged_extension: Path, timeout: float) -> None:
    print("Operator action required in the disposable Chrome window:", file=sys.stderr)
    print("  1. Open chrome://extensions if it is not already visible.", file=sys.stderr)
    print("  2. Enable Developer mode.", file=sys.stderr)
    print("  3. Click Load unpacked and select this exact directory:", file=sys.stderr)
    print(f"     {staged_extension}", file=sys.stderr)
    print(f"  4. Leave the window open; the runner waits up to {timeout:.0f} seconds.", file=sys.stderr)
    print("  5. After the extension appears, answer the permission/policy prompt below.", file=sys.stderr)
    sys.stderr.flush()


def collect_operator_permission_status() -> str:
    """Collect a post-load operator acknowledgement; never trust a predeclared flag."""
    print(
        "After Load unpacked, enter permission/policy outcome "
        "(recorded, none_observed, shown_accepted, shown_denied, or policy_blocked): ",
        file=sys.stderr,
        end="",
        flush=True,
    )
    if not sys.stdin.isatty():
        return "not_recorded"
    try:
        value = input().strip()
    except (EOFError, OSError):
        return "not_recorded"
    if value == "none_observed":
        return value
    if value in {"recorded", "shown_accepted", "shown_denied", "policy_blocked"}:
        return "recorded"
    return "not_recorded"


def _chrome_load_extension_evidence(log_path: Path | None, log_handle: Any | None = None) -> dict[str, str] | None:
    if log_path is None:
        return None
    try:
        if log_handle is not None:
            log_handle.flush()
        log = log_path.read_bytes()[-64 * 1024 :].decode("utf-8", errors="replace")
    except OSError:
        return None
    if CHROME_LOAD_EXTENSION_REFUSAL not in log:
        return None
    return {
        "status": "refused",
        "reason": "branded_google_chrome_rejected_load_extension",
        "message": CHROME_LOAD_EXTENSION_REFUSAL,
    }


def _endpoint_belongs_to_process(port: int, process: subprocess.Popen[bytes]) -> bool:
    """Verify the launched process group owns the listener before using CDP."""
    lsof = shutil.which("lsof")
    if lsof is None:
        return False
    try:
        result = subprocess.run(
            [lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        listener_pids = {
            int(line[1:])
            for line in result.stdout.splitlines()
            if line.startswith("p") and line[1:].isdigit()
        }
        process_group = os.getpgid(process.pid)
        return any(os.getpgid(pid) == process_group for pid in listener_pids)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def wait_for_probe_worker(
    port: int,
    process: subprocess.Popen[bytes],
    timeout: float = WORKER_DISCOVERY_TIMEOUT,
    *,
    expected_extension_id: str | None = None,
) -> list[dict[str, Any]] | None:
    """Poll until the launched process exposes the pinned probe worker."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("worker discovery timeout must be positive and finite")
    if expected_extension_id is not None and not EXTENSION_ID_PATTERN.fullmatch(expected_extension_id):
        raise ValueError("expected extension ID is invalid")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _endpoint_belongs_to_process(port, process):
            return None
        try:
            targets = chrome_endpoint(port, "/json/list")
        except (OSError, urllib.error.URLError, ValueError):
            targets = None
        if isinstance(targets, list):
            candidates = [_worker_candidate(target, port) for target in targets]
            if any(
                candidate is not None
                and (expected_extension_id is None or candidate[0] == expected_extension_id)
                for candidate in candidates
            ):
                return targets
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(WORKER_DISCOVERY_INTERVAL, remaining))
    return None


def inspect_live(
    port: int,
    profile_dir: Path | None,
    launch: bool,
    binary: str | None,
    artifact_dir: Path,
    manifest: dict[str, Any] | None = None,
    *,
    operator_assisted: bool = False,
    operator_timeout: float = DEFAULT_OPERATOR_TIMEOUT,
) -> dict[str, Any]:
    global _LAST_CLEANUP_OK
    _LAST_CLEANUP_OK = True
    del artifact_dir
    load_method = "chrome_extensions_load_unpacked" if operator_assisted else "command_line_load_extension"
    common_evidence = {
        "operator_assisted": operator_assisted,
        "load_method": load_method,
        "load_extension_flag_used": not operator_assisted,
        "developer_private_used": False,
        "extensions_ui_dom_access": False,
        "permission_prompts": {
            "status": "not_recorded",
            "required_manual_review": operator_assisted,
            "collection": "post_load_operator_ack" if operator_assisted else "not_requested",
        },
        "instrumentation": {
            "service_worker_cdp": False,
            "owned_page_cdp": True,
            "product_transport": "extension_chrome_apis",
        },
    }
    if not launch:
        return {
            **common_evidence,
            "status": "live_unavailable",
            "limitation": (
                "existing-endpoint-not-isolated: refusing to attach to an existing "
                "Chrome debug endpoint; pass --launch-chrome to use a disposable "
                "temporary profile."
            ),
        }
    if not math.isfinite(operator_timeout) or operator_timeout <= 0:
        raise ValueError("operator timeout must be positive and finite")
    owned_profile: Path | None = profile_dir if launch and profile_dir is not None else None
    if launch:
        try:
            chrome_endpoint(port, "/json/version")
        except (OSError, urllib.error.URLError, ValueError):
            pass
        else:
            _cleanup_owned_profile(owned_profile)
            return {
                **common_evidence,
                "status": "live_unavailable",
                "limitation": f"debug port {port} is already in use; refusing to attach or launch ambiguously.",
            }
    process: subprocess.Popen[bytes] | None = None
    stderr_log_path: Path | None = None
    stderr_log: Any | None = None
    fixture_path: Path | None = None
    fixture: str | None = None
    source_extension_hash: str | None = None
    staged_extension_hash: str | None = None
    binding_nonce = new_request_id()
    operator_page_websocket: str | None = None
    control_target_id: str | None = None
    launched = False
    try:
        if launch:
            executable = chrome_binary(binary)
            if executable is None:
                return {**common_evidence, "status": "live_unavailable", "limitation": "Chrome binary was not found; install Chrome or pass --chrome-binary."}
            if profile_dir is None:
                owned_profile = Path(tempfile.mkdtemp(prefix="agentyc-p0-profile-"))
                profile_dir = owned_profile
            assert profile_dir is not None
            try:
                claim_disposable_profile(profile_dir)
            except (OSError, ValueError):
                return {**common_evidence, "status": "live_unavailable", "limitation": "the supplied disposable Chrome profile is not empty or is unsafe"}
            loaded_extension_dir, source_extension_hash, staged_extension_hash = stage_extension(profile_dir, binding_nonce)
            fixture_path = loaded_extension_dir / "fixture.html"
            fixture = fixture_path.resolve().as_uri()
            stderr_log_path = profile_dir / "chrome.stderr.log"
            stderr_log = stderr_log_path.open("wb")
            command = build_chrome_command(
                executable,
                profile_dir,
                port,
                extension_dir=None if operator_assisted else loaded_extension_dir,
                fixture_url=None if operator_assisted else fixture,
                operator_assisted=operator_assisted,
            )
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr_log,
                start_new_session=True,
            )
            launched = True
        version = wait_for_chrome(port)
        if version is None:
            return {**common_evidence, "status": "live_unavailable", "limitation": "Chrome did not expose the requested debug endpoint."}
        if process is None or not _endpoint_belongs_to_process(port, process):
            return {**common_evidence, "status": "live_unavailable", "limitation": "the debug endpoint owner could not be bound to the probe-launched Chrome process"}
        if profile_dir is None or fixture_path is None or fixture is None:
            raise OSError("owned probe state is incomplete")
        expected_extension_id = _manifest_extension_id(manifest or {})
        if not isinstance(expected_extension_id, str):
            raise OSError("probe manifest does not have a pinned extension identity")

        if operator_assisted:
            operator_page_websocket = navigate_owned_page(port, process, "chrome://extensions/")
            loaded_extension_dir = profile_dir / "extension" / "probes"
            print_operator_instructions(loaded_extension_dir, operator_timeout)
            common_evidence["permission_prompts"]["status"] = collect_operator_permission_status()
            if operator_page_websocket is None:
                raise OSError("probe-owned operator page was lost")
            navigate_page_target(operator_page_websocket, fixture)

        if not wait_for_fixture_page(port, process, fixture, timeout=operator_timeout if operator_assisted else WORKER_DISCOVERY_TIMEOUT):
            return {
                **common_evidence,
                "status": "live_unavailable",
                "source_extension_tree_sha256": source_extension_hash,
                "staged_extension_tree_sha256": staged_extension_hash,
                "limitation": "the owned fixture tab did not become ready",
            }

        control_page_url = f"chrome-extension://{expected_extension_id}/probe.html"
        control_target_id = create_owned_target(port, process, control_page_url)
        if not wait_for_extension_control_page(port, process, expected_extension_id):
            return {
                **common_evidence,
                "status": "live_unavailable",
                "source_extension_tree_sha256": source_extension_hash,
                "staged_extension_tree_sha256": staged_extension_hash,
                "limitation": "the exact loaded extension control page did not become ready",
            }
        try:
            observed_targets = chrome_endpoint(port, "/json/list")
        except (OSError, urllib.error.URLError, ValueError):
            observed_targets = []
        targets = observed_targets if isinstance(observed_targets, list) else []
        target_provider: Callable[[], list[dict[str, Any]] | None] | None = None
        if process is not None:
            def refresh_owned_targets() -> list[dict[str, Any]] | None:
                if not _endpoint_belongs_to_process(port, process):
                    return None
                try:
                    refreshed = chrome_endpoint(port, "/json/list")
                except (OSError, urllib.error.URLError, ValueError):
                    return None
                return refreshed if isinstance(refreshed, list) else None

            target_provider = refresh_owned_targets
            refreshed_targets = target_provider()
            if refreshed_targets is not None:
                targets = refreshed_targets
        if staged_extension_hash is not None:
            try:
                current_staged_hash = extension_tree_sha256(profile_dir / "extension" / "probes")
            except (OSError, ValueError):
                current_staged_hash = None
            if current_staged_hash != staged_extension_hash:
                return {
                    **common_evidence,
                    "status": "live_unavailable",
                    "source_extension_tree_sha256": source_extension_hash,
                    "staged_extension_tree_sha256": staged_extension_hash,
                    "limitation": "staged extension changed after launch; refusing to trust the loaded worker",
                }
        target_count = len(targets)
        load_evidence = _chrome_load_extension_evidence(stderr_log_path, stderr_log)
        extension = extension_probe_result(
            port,
            targets,
            expected_manifest=manifest,
            fixture_path=fixture_path,
            binding_nonce=binding_nonce,
            source_extension_hash=source_extension_hash,
            target_provider=target_provider,
        )
        control_page_cleanup_passed = close_owned_target(port, process, control_target_id) if control_target_id else False
        if control_target_id and not control_page_cleanup_passed:
            extension["status"] = "live_unavailable"
            extension["limitation"] = "probe control-page cleanup was not confirmed"
        if load_evidence is not None:
            extension["extension_load_evidence"] = load_evidence
            if extension.get("status") != "live_passed":
                extension["limitation"] = (
                    "branded Google Chrome refused --load-extension; the isolated probe extension "
                    "was not loaded; "
                    f"{extension.get('limitation', 'no complete extension evidence was observed')}"
                )
        status = extension.get("status", "live_unavailable")
        limitation = extension.get("limitation")
        permission_status = common_evidence["permission_prompts"].get("status")
        if operator_assisted and permission_status not in OPERATOR_PERMISSION_STATUSES:
            if status == "live_passed":
                status = "live_unavailable"
            limitation = limitation or "operator-assisted probe requires a post-load permission prompt acknowledgement"
        return {
            **common_evidence,
            "status": status,
            "chrome_version": version.get("Browser", "unknown"),
            "target_count": target_count,
            "candidate_extension_page_count": extension.get("candidate_extension_page_count", 0),
            "verified_extension_page_count": extension.get("verified_extension_page_count", 0),
            "control_page_identity_passed": extension.get("control_page_identity_passed", False),
            "control_page_cleanup_passed": control_page_cleanup_passed,
            "launched_by_probe": launched,
            "extension_loaded": extension.get("extension_loaded", False),
            "extension_identity_passed": extension.get("extension_identity_passed", False),
            "extension_build_binding_passed": extension.get("extension_build_binding_passed", False),
            "fixture_identity_passed": extension.get("fixture_identity_passed", False),
            "debugger_command_passed": extension.get("debugger_command_passed", False),
            "debugger_event_received": extension.get("debugger_event_received", False),
            "event_received": extension.get("debugger_event_received", False),
            "tab_group_created": extension.get("tab_group_created", False),
            "native_messaging_passed": extension.get("native_messaging_passed", False),
            "chrome_mediated_native_messaging": extension.get("chrome_mediated_native_messaging", False),
            "debugger_cleanup_passed": extension.get("debugger_cleanup_passed", False),
            "cleanup_passed": extension.get("cleanup_passed", False),
            "screenshot_captured": extension.get("screenshot_captured", False),
            "screenshots": extension.get("screenshots", []),
            "handshake_transcript": extension.get("handshake_transcript", []),
            "identity_diagnostics": extension.get("identity_diagnostics", []),
            "extension_load_evidence": extension.get("extension_load_evidence"),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "source_extension_tree_sha256": source_extension_hash,
            "staged_extension_tree_sha256": staged_extension_hash,
            "limitation": limitation,
        }
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, TimeoutError, struct.error, subprocess.SubprocessError):
        return {
            **common_evidence,
            "status": "live_unavailable",
            "limitation": "live Chrome inspection failed closed",
        }
    finally:
        if process is not None and not _safe_stop_process(process):
            _LAST_CLEANUP_OK = False
        if stderr_log is not None:
            try:
                stderr_log.close()
            except OSError:
                _LAST_CLEANUP_OK = False
        _cleanup_owned_profile(owned_profile)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--headed",
        action="store_true",
        help="request isolated live Chrome inspection; requires --launch-chrome",
    )
    parser.add_argument(
        "--require-live",
        "--required-live",
        dest="require_live",
        action="store_true",
        help="require live Chrome inspection and exit nonzero when unavailable",
    )
    parser.add_argument("--launch-chrome", action="store_true", help="explicitly launch isolated Chrome; never implied by default")
    parser.add_argument(
        "--operator-assisted",
        action="store_true",
        help="use Chrome's documented chrome://extensions Load unpacked UI flow",
    )

    parser.add_argument(
        "--operator-timeout",
        type=float,
        default=DEFAULT_OPERATOR_TIMEOUT,
        help="seconds to wait for the operator to load the unpacked extension",
    )
    parser.add_argument("--profile-dir", help="optional absolute disposable profile path inside the system temporary directory")
    parser.add_argument("--chrome-binary")
    parser.add_argument("--debug-port", type=int, default=9222)
    parser.add_argument("--artifact-dir", default="artifacts/p0-extension")
    args = parser.parse_args()
    if args.launch_chrome and not args.headed:
        parser.error("--launch-chrome requires --headed")
    if args.require_live and not args.headed:
        parser.error("--require-live requires --headed")
    if args.operator_assisted and (not args.headed or not args.launch_chrome):
        parser.error("--operator-assisted requires --headed --launch-chrome")
    if not math.isfinite(args.operator_timeout) or args.operator_timeout <= 0:
        parser.error("--operator-timeout must be positive and finite")
    artifact_dir = safe_artifact_dir(args.artifact_dir)
    profile_dir = safe_profile_dir(args.profile_dir) if args.profile_dir else None
    manifest = load_manifest()
    live_required = bool(args.require_live or args.launch_chrome)
    report: dict[str, Any] = {
        "probe": "P0-T2",
        "status": "offline_passed",
        "manifest_version": manifest["manifest_version"],
        "extension_version": manifest["version"],
        "permissions": manifest["permissions"],
        "mode": "headed" if args.headed else "offline",
        "live": {
            "requested": bool(args.headed),
            "required": live_required,
            "status": "not_requested",
        },
        "safety": {
            "default_chrome_launch": False,
            "default_profile_mutation": False,
            "raw_ids_logged": False,
            "secrets_logged": False,
            "fixture_only_mutation": True,
            "service_worker_cdp": False,
            "owned_page_cdp_test_instrumentation": True,
        },
        "handshake_transcript": [],
        "screenshots": [],
        "limitations": [],
    }
    if args.headed:
        live = inspect_live(
            args.debug_port,
            profile_dir,
            args.launch_chrome,
            args.chrome_binary,
            artifact_dir,
            manifest,
            operator_assisted=args.operator_assisted,
            operator_timeout=args.operator_timeout,
        )
        if not _LAST_CLEANUP_OK:
            live = {
                "status": "live_unavailable",
                "disposable_cleanup_passed": False,
                "limitation": "live probe cleanup failed; process or disposable profile may remain",
            }
        else:
            live["disposable_cleanup_passed"] = True
        report["live"] = {"requested": True, "required": live_required, **live}
        report["handshake_transcript"] = live.get("handshake_transcript", [])
        report["screenshots"] = live.get("screenshots", [])
        if live["status"] == "live_passed":
            report["status"] = "live_passed"
        elif live["status"] == "live_unavailable":
            report["status"] = "live_required_unavailable" if live_required else "live_optional_unavailable"
            report["limitations"].append(live["limitation"])
        elif live_required:
            # /json/version proves only that a debugging endpoint is reachable.
            # It does not prove that the MV3 extension, debugger permission,
            # tab-group mutation, and Native Messaging handshake succeeded.
            report["status"] = "live_required_unavailable"
            report["limitations"].append(
                "Chrome endpoint was observed, but the extension probe did not supply complete evidence; "
                "an endpoint alone cannot close the live gate."
            )
        else:
            report["status"] = "live_observed"
            if live.get("limitation"):
                report["limitations"].append(live["limitation"])
    else:
        report["limitations"].append("Real Chrome, extension installation, and Native Messaging registration were not requested.")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    add_envelope(report, kind="chrome-extension-probe")
    output = artifact_dir / "report.json"
    try:
        write_json_atomic(output, report)
    except (OSError, ValueError) as error:
        print(f"chrome probe error: {type(error).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["status"] == "live_required_unavailable" else 0


if __name__ == "__main__":
    raise SystemExit(main())
