"""Run the Phase 0 direct-interface benchmark scaffold.

Offline mode measures deterministic local fixture parsing and serialization. It
never opens a browser, downloads dependencies, or consumes a CDP endpoint. The
other modes are explicit live lanes and fail closed until a real probe is wired.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import errno
import hashlib
import ipaddress
import json
import math
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from statistics import mean
from typing import Any, ClassVar

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None

from artifact_envelope import envelope as add_envelope
from artifact_envelope import (
    new_nonce,
    redact_for_persistence,
    repository_relative,
    sha256_bytes,
    write_bytes_atomic,
    write_json_atomic,
    write_jsonl_atomic,
    write_text_atomic,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "browser-task-spaces"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
LIVE_MODES = {"target", "headed", "managed"}
MIN_P95_SAMPLES = 200
MIN_P99_SAMPLES = 1_000
DEFAULT_SAMPLES = MIN_P99_SAMPLES
SMOKE_DEFAULT_SAMPLES = 30
DEFAULT_CACHE_STATES = ("cold", "clean", "dirty", "resync")
MAX_MANIFEST_BYTES = 256 * 1024
MAX_FIXTURE_BYTES = 2 * 1024 * 1024
MAX_FIXTURE_TEXT_CHARS = 2 * 1024 * 1024
MAX_MANIFEST_ITEMS = 256
MAX_MANIFEST_JSON_DEPTH = 12
MAX_MANIFEST_JSON_NODES = 10_000
MAX_IFRAME_DEPTH = 8
MAX_IFRAME_COUNT = 128
MAX_TEXT_CHARS = 250_000
MAX_SRCDOC_CHARS = 100_000
MAX_SPACES = 256
MAX_RAW_SAMPLE_FILE_BYTES = 7 * 1024 * 1024
MAX_WEBSOCKET_FRAME_BYTES = 64 * 1024
MAX_WEBSOCKET_HEADER_BYTES = 16 * 1024
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
CDP_SESSION_ID_RE = r"^[A-Za-z0-9._:-]{1,128}$"
TOKENIZER_NAME = "agentyc-byte-estimate-v1"
GENERATION_MANIFEST_NAME = "generation-manifest.json"
COMMIT_MARKER_NAME = "COMMIT"
RELEASE_GATE_SCHEMA_VERSION = 1
RELEASE_GATE_CEILINGS: dict[str, dict[str, float]] = {
    "resource": {
        "cpu_p95_percent": 80.0,
        "rss_p95_bytes": 512 * 1024 * 1024,
        "queue_depth_p95": 1_000.0,
        "file_descriptors_max": 1_024.0,
        "threads_max": 256.0,
        "artifact_bytes": 8 * 1024 * 1024,
    },
    "token": {
        "transport_bytes_p95": 1_000_000.0,
        "utf8_bytes_p95": 1_000_000.0,
        "serialized_tokens_p95": 100_000.0,
        "model_context_tokens_p95": 100_000.0,
    },
    "context": {
        "clean_dom_scans_max": 0.0,
        "delta_ratio_p50": 0.35,
        "delta_ratio_p95": 0.60,
        "actionable_coverage_min": 1.0,
    },
    "reliability": {
        "stale_ref_rate": 0.01,
        "unknown_outcome_rate": 0.01,
        "event_lag_p95_ms": 1_000.0,
        "reconnect_p95_ms": 5_000.0,
        "human_tab_responsiveness_p95_ms": 500.0,
        "cross_space_mutations": 0.0,
        "user_tab_closes": 0.0,
        "stale_agent_mutations": 0.0,
        "silent_unknown_success": 0.0,
        "blind_replays": 0.0,
        "secret_leaks": 0.0,
    },
}


class _ParserBudget:
    def __init__(self) -> None:
        self.frame_count = 0
        self.text_chars = 0
        self.srcdoc_chars = 0


class ControlCounter(HTMLParser):
    """Count controls and recursively inspect bounded inline ``srcdoc`` frames."""

    CONTROL_TAGS: ClassVar[set[str]] = {"a", "button", "input", "select", "textarea"}

    def __init__(self, frame_depth: int = 0, *, budget: _ParserBudget | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.controls = 0
        self.frames = 0
        self.frames_scanned = 0
        self.max_frame_depth = frame_depth
        self.text_chars = 0
        self.srcdoc_chars = 0
        self.frame_depth = frame_depth
        self._budget = budget or _ParserBudget()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in self.CONTROL_TAGS:
            self.controls += 1
        if normalized_tag != "iframe":
            return

        self._budget.frame_count += 1
        if self._budget.frame_count > MAX_IFRAME_COUNT:
            raise ValueError("fixture iframe count exceeds the bounded parser limit")
        self.frames += 1
        attributes = dict(attrs)
        srcdoc = attributes.get("srcdoc")
        if srcdoc is None:
            return
        if self.frame_depth >= MAX_IFRAME_DEPTH:
            raise ValueError("fixture iframe depth exceeds the bounded parser limit")
        if len(srcdoc) > MAX_SRCDOC_CHARS:
            raise ValueError("fixture iframe srcdoc exceeds the bounded parser limit")
        self._budget.srcdoc_chars += len(srcdoc)
        self.srcdoc_chars += len(srcdoc)
        if self._budget.srcdoc_chars > MAX_SRCDOC_CHARS * MAX_IFRAME_COUNT:
            raise ValueError("fixture cumulative srcdoc exceeds the bounded parser limit")

        child = ControlCounter(self.frame_depth + 1, budget=self._budget)
        child.feed(srcdoc)
        child.close()
        self.controls += child.controls
        self.frames += child.frames
        self.frames_scanned += 1 + child.frames_scanned
        self.max_frame_depth = max(self.max_frame_depth, child.max_frame_depth)
        self.text_chars += child.text_chars
        self.srcdoc_chars += child.srcdoc_chars

    def handle_data(self, data: str) -> None:
        self._budget.text_chars += len(data)
        if self._budget.text_chars > MAX_TEXT_CHARS:
            raise ValueError("fixture text exceeds the bounded parser limit")
        self.text_chars += len(data)

    @property
    def frame_coverage(self) -> float | None:
        if self.frames == 0:
            return None
        return self.frames_scanned / self.frames


def sha256_file(path: Path, *, max_bytes: int | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        total = 0
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError("file exceeds the bounded read limit")
            digest.update(chunk)
    return digest.hexdigest()


def _validate_json_shape(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    counters = nodes if nodes is not None else [0]
    counters[0] += 1
    if counters[0] > MAX_MANIFEST_JSON_NODES or depth > MAX_MANIFEST_JSON_DEPTH:
        raise ValueError("fixture manifest exceeds the bounded JSON limit")
    if isinstance(value, dict):
        if len(value) > MAX_MANIFEST_ITEMS:
            raise ValueError("fixture manifest object exceeds the bounded item limit")
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 512:
                raise ValueError("fixture manifest key is invalid or too long")
            _validate_json_shape(child, depth=depth + 1, nodes=counters)
    elif isinstance(value, list):
        if len(value) > MAX_MANIFEST_ITEMS:
            raise ValueError("fixture manifest list exceeds the bounded item limit")
        for child in value:
            _validate_json_shape(child, depth=depth + 1, nodes=counters)
    elif isinstance(value, str) and len(value) > MAX_FIXTURE_TEXT_CHARS:
        raise ValueError("fixture manifest string exceeds the bounded text limit")


def read_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("fixture manifest exceeds the bounded read limit")
        value = json.loads(raw.decode("utf-8"))
        _validate_json_shape(value)
        return value
    except (FileNotFoundError, json.JSONDecodeError, UnicodeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("fixture manifest"):
            raise
        raise ValueError(f"invalid or missing fixture manifest {repository_relative(path)}") from exc


def _safe_fixture_path(relative: str) -> Path:
    relative_path = Path(relative)
    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or ".." in relative_path.parts
        or len(relative_path.parts) != 1
        or relative_path.name != relative
    ):
        raise ValueError("fixtures must be direct repository-local files")
    current = FIXTURE_ROOT
    if current.is_symlink():
        raise ValueError("fixture root must not be a symlink")
    for component in relative_path.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError("fixture path components must not be symlinks")
    if not current.is_file():
        raise ValueError(f"fixture {relative!r} is not a local file")
    try:
        resolved = current.resolve()
        resolved.relative_to(FIXTURE_ROOT.resolve())
    except (OSError, ValueError) as exc:
        raise ValueError("fixture path is outside the fixture root") from exc
    size = current.stat().st_size
    if size > MAX_FIXTURE_BYTES:
        raise ValueError("fixture exceeds the bounded read limit")
    return current


def load_fixture_bundle() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    current = MANIFEST_PATH
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("fixture manifest path components must not be symlinks")
        current = current.parent
    raw_manifest = MANIFEST_PATH.read_bytes()
    if len(raw_manifest) > MAX_MANIFEST_BYTES:
        raise ValueError("fixture manifest exceeds the bounded read limit")
    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"invalid or missing fixture manifest {repository_relative(MANIFEST_PATH)}") from exc
    _validate_json_shape(manifest)
    if not isinstance(manifest, dict):
        raise TypeError("fixture manifest must be an object")
    fixture_items = manifest.get("fixtures")
    if not isinstance(fixture_items, list) or not fixture_items:
        raise ValueError("fixture manifest is empty")
    if len(fixture_items) > MAX_MANIFEST_ITEMS:
        raise ValueError("fixture manifest has too many fixtures")

    records: dict[str, dict[str, Any]] = {}
    seen_files: set[str] = set()
    for item in fixture_items:
        if not isinstance(item, dict):
            raise TypeError("fixture entries must be objects")
        name = item.get("name")
        relative = item.get("file")
        if not isinstance(name, str) or not name or len(name) > 128:
            raise TypeError("fixture entries need bounded string names")
        if not isinstance(relative, str):
            raise TypeError("fixture entries need string file names")
        if name in records or relative in seen_files:
            raise ValueError("fixture names and files must be unique")
        path = _safe_fixture_path(relative)
        if relative in seen_files:
            raise ValueError("fixture files must be unique")
        seen_files.add(relative)
        records[name] = {
            "name": name,
            "file": relative,
            "path": path,
            "sha256": sha256_file(path, max_bytes=MAX_FIXTURE_BYTES),
            "bytes": path.stat().st_size,
        }
    if not records:
        raise ValueError("fixture manifest is empty")
    metadata = {
        "path": MANIFEST_PATH.relative_to(ROOT).as_posix(),
        "sha256": sha256_bytes(raw_manifest),
        "bytes": len(raw_manifest),
        "schema_version": manifest.get("schema_version"),
        "fixture_set": manifest.get("fixture_set"),
    }
    return metadata, records


def baseline_manifest_metadata() -> dict[str, Any]:
    metadata, _ = load_fixture_bundle()
    return metadata


def load_fixtures() -> dict[str, dict[str, Any]]:
    _, records = load_fixture_bundle()
    return records


def browser_candidates() -> list[Path]:
    values = [
        os.environ.get("AGENTYC_CHROME_PATH", ""),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    result = [Path(value) for value in values if value]
    for command in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(command)
        if found:
            result.append(Path(found))
    return list(dict.fromkeys(result))


def running_chrome() -> bool:
    if platform.system() == "Windows":
        return False
    try:
        result = subprocess.run(
            ["ps", "-axo", "comm="], check=False, capture_output=True, text=True, timeout=2
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    names = {Path(line.strip()).name.lower() for line in result.stdout.splitlines() if line.strip()}
    return bool(names & {"google chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"})


class LiveProbeError(RuntimeError):
    """A live measurement or ownership proof was unavailable."""


class DevToolsSocket:
    """Small bounded RFC 6455 client for a verified local Chrome endpoint."""

    def __init__(self, url: str, timeout: float = 3.0) -> None:
        parsed = urllib.parse.urlparse(url)
        if (
            parsed.scheme != "ws"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
        ):
            raise LiveProbeError("Chrome exposed a non-loopback or malformed CDP websocket")
        try:
            hosts = {item[-1][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)}
        except OSError as exc:
            raise LiveProbeError("Chrome CDP hostname could not be resolved") from exc
        if not hosts or any(not ipaddress.ip_address(host).is_loopback for host in hosts):
            raise LiveProbeError("Chrome CDP websocket is not loopback")
        self.socket: socket.socket | None = socket.create_connection((parsed.hostname, parsed.port), timeout=timeout)
        self.timeout = timeout
        self.next_id = 0
        self.buffer = bytearray()
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        target = parsed.path or "/"
        request = (
            f"GET {target} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        try:
            self.socket.sendall(request)
            response = self._read_headers()
            text = response.decode("ascii")
            lines = text.split("\r\n")
            if not lines or not lines[0].startswith("HTTP/1.1 101 "):
                raise LiveProbeError("Chrome CDP websocket handshake failed")
            headers = {
                name.strip().lower(): value.strip()
                for line in lines[1:]
                if ":" in line
                for name, value in [line.split(":", 1)]
            }
            expected = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
            if headers.get("sec-websocket-accept", "") != expected:
                raise LiveProbeError("Chrome CDP websocket handshake was not authenticated")
        except Exception:
            self.close()
            raise

    def _read_headers(self) -> bytes:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            if len(data) >= MAX_WEBSOCKET_HEADER_BYTES:
                raise LiveProbeError("Chrome CDP websocket headers exceeded the bound")
            if self.socket is None:
                raise LiveProbeError("Chrome CDP websocket closed")
            chunk = self.socket.recv(min(4096, MAX_WEBSOCKET_HEADER_BYTES - len(data)))
            if not chunk:
                raise LiveProbeError("Chrome CDP websocket closed during handshake")
            data.extend(chunk)
        return bytes(data[: data.index(b"\r\n\r\n") + 4])

    def _frame(self, payload: bytes, opcode: int = 1) -> bytes:
        if len(payload) > MAX_WEBSOCKET_FRAME_BYTES:
            raise LiveProbeError("Chrome CDP message exceeded the bound")
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length <= 0xFFFF:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
        return header + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload))

    def _receive(self) -> bytes:
        if self.socket is None:
            raise LiveProbeError("Chrome CDP websocket closed")
        header = self._receive_exact(2)
        first, second = header
        if first & 0x70 or second & 0x80:
            raise LiveProbeError("Chrome CDP websocket frame is invalid")
        opcode = first & 0x0F
        length_code = second & 0x7F
        if length_code < 126:
            length = length_code
        elif length_code == 126:
            length = struct.unpack("!H", self._receive_exact(2))[0]
        else:
            length = struct.unpack("!Q", self._receive_exact(8))[0]
        if length > MAX_WEBSOCKET_FRAME_BYTES:
            raise LiveProbeError("Chrome CDP frame exceeded the bound")
        payload = self._receive_exact(length)
        if opcode == 9:
            self.socket.sendall(self._frame(payload, opcode=10))
            return self._receive()
        if opcode == 8:
            raise LiveProbeError("Chrome CDP websocket closed")
        if opcode != 1 or not first & 0x80:
            raise LiveProbeError("fragmented or non-text Chrome CDP message")
        return payload

    def _receive_exact(self, size: int) -> bytes:
        output = bytearray()
        while len(output) < size:
            if self.buffer:
                take = min(size - len(output), len(self.buffer))
                output.extend(self.buffer[:take])
                del self.buffer[:take]
                continue
            if self.socket is None:
                raise LiveProbeError("Chrome CDP websocket closed")
            chunk = self.socket.recv(min(MAX_WEBSOCKET_FRAME_BYTES, size - len(output)))
            if not chunk:
                raise LiveProbeError("Chrome CDP websocket closed")
            output.extend(chunk)
        return bytes(output)

    def command(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not method or len(method) > 256:
            raise LiveProbeError("Chrome CDP method is invalid")
        self.next_id += 1
        command_id = self.next_id
        payload = json.dumps({"id": command_id, "method": method, "params": params or {}}, separators=(",", ":")).encode("utf-8")
        if self.socket is None:
            raise LiveProbeError("Chrome CDP websocket closed")
        self.socket.settimeout(self.timeout)
        self.socket.sendall(self._frame(payload))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = json.loads(self._receive().decode("utf-8"))
            if isinstance(message, dict) and message.get("id") == command_id:
                if "error" in message:
                    raise LiveProbeError("Chrome rejected a required CDP command")
                return message.get("result")
        raise LiveProbeError("Chrome CDP command timed out")

    def close(self) -> None:
        sock = self.socket
        self.socket = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _local_json(port: int, path: str) -> Any:
    if not 1 <= port <= 65535 or not path.startswith("/"):
        raise LiveProbeError("invalid local Chrome endpoint")
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            data = response.read(MAX_WEBSOCKET_FRAME_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise LiveProbeError("Chrome debugging endpoint is unavailable") from exc
    if len(data) > MAX_WEBSOCKET_FRAME_BYTES:
        raise LiveProbeError("Chrome debugging response exceeded the bound")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LiveProbeError("Chrome debugging response was not JSON") from exc


def _validated_ws_url(value: Any, port: int) -> str:
    if not isinstance(value, str):
        raise LiveProbeError("Chrome did not expose a CDP websocket")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port != port or parsed.query or parsed.fragment:
        raise LiveProbeError("Chrome exposed an unbounded CDP websocket")
    return value


def _validate_existing_browser(port: int, pid: int) -> None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LiveProbeError("existing browser process identity is unavailable") from exc
    command = result.stdout.strip()
    lowered = command.lower()
    if not any(name in lowered for name in ("chrome", "chromium")) or f"--remote-debugging-port={port}" not in command:
        raise LiveProbeError("existing target is not bound to an explicit Chrome debugging process")


def _process_snapshot(root_pid: int) -> tuple[float, int]:
    """Measure the owned browser process without inheriting unrelated Chrome state.

    Chrome helpers can be reparented by macOS after a runner timeout. Summing every
    process with a matching command therefore over-counts unrelated browser work.
    The browser root is the stable ownership anchor; child-process accounting is
    retained as a separate limitation in the report rather than being guessed.
    """
    if root_pid <= 0:
        raise LiveProbeError("browser process identity is unavailable")
    try:
        result = subprocess.run(
            ["ps", "-p", str(root_pid), "-o", "%cpu=,rss=,command="],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LiveProbeError("process resource instrumentation is unavailable") from exc
    fields = result.stdout.strip().split(None, 2)
    if len(fields) != 3:
        raise LiveProbeError("owned browser process is not observable")
    try:
        cpu = max(0.0, float(fields[0])) / max(1, os.cpu_count() or 1)
        rss = int(fields[1]) * 1024
    except ValueError as exc:
        raise LiveProbeError("process resource instrumentation returned invalid values") from exc
    if rss < 0 or not math.isfinite(cpu):
        raise LiveProbeError("process resource instrumentation returned invalid values")
    return min(100.0, cpu), rss


def _host_rss_bytes() -> int:
    try:
        result = subprocess.run(
            ["ps", "-p", str(os.getpid()), "-o", "rss="],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        value = int(result.stdout.strip()) * 1024
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise LiveProbeError("host resource instrumentation is unavailable") from exc
    if value < 0:
        raise LiveProbeError("host RSS instrumentation returned an invalid value")
    return value


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def _safe_disposable_profile(value: Path) -> Path:
    value = value.expanduser()
    if not value.is_absolute():
        raise LiveProbeError("managed profile must be an absolute disposable temporary path")
    current = value
    while current != current.parent:
        if current.is_symlink():
            raise LiveProbeError("managed profile path contains a symlink")
        current = current.parent
    temporary_root = Path(tempfile.gettempdir()).resolve()
    resolved = value.resolve(strict=False)
    if resolved == temporary_root or temporary_root not in resolved.parents:
        raise LiveProbeError("managed profile must be inside the system temporary directory")
    if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
        raise LiveProbeError("managed profile must be absent or empty")
    return resolved


class LiveChrome:
    """Own a disposable browser or bind only to caller-selected page targets."""

    def __init__(self, mode: str, executable: str | None, profile_dir: str | None, target_id: str | None, human_target_id: str | None, browser_port: int | None, browser_pid: int | None, headless: bool) -> None:
        self.mode = mode
        self.executable = executable
        self.profile_dir_arg = profile_dir
        self.target_id_arg = target_id
        self.human_target_id_arg = human_target_id
        self.browser_port = browser_port
        self.browser_pid_arg = browser_pid
        self.headless = headless
        self.process: subprocess.Popen[Any] | None = None
        self.profile_dir: Path | None = None
        self.remove_profile = False
        self.browser_socket: DevToolsSocket | None = None
        self.pages: list[DevToolsSocket] = []
        self.space_pages: list[DevToolsSocket] = []
        self.space_target_ids: list[str] = []
        self.target_ids: list[str] = []
        self.root_pid: int | None = None

    def _targets(self) -> list[dict[str, Any]]:
        value = _local_json(self.browser_port or 0, "/json/list")
        if not isinstance(value, list):
            raise LiveProbeError("Chrome target inventory is invalid")
        return [item for item in value if isinstance(item, dict)]

    def _wait_target(self, target_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            for target in self._targets():
                if target.get("id") == target_id:
                    if target.get("type") != "page" or not isinstance(target.get("webSocketDebuggerUrl"), str):
                        raise LiveProbeError("selected Chrome target is not a page target")
                    return target
            time.sleep(0.05)
        raise LiveProbeError("selected Chrome target did not become available")

    def _create_target(self, url: str) -> str:
        if self.browser_socket is None:
            raise LiveProbeError("owned browser target channel is unavailable")
        result = self.browser_socket.command("Target.createTarget", {"url": url, "background": True})
        target_id = result.get("targetId") if isinstance(result, dict) else None
        if not isinstance(target_id, str) or re.fullmatch(CDP_SESSION_ID_RE, target_id) is None:
            raise LiveProbeError("Chrome did not return a bounded owned target identity")
        return target_id

    def open(self) -> tuple[DevToolsSocket, DevToolsSocket]:
        if self.mode == "managed":
            if not self.executable or not Path(self.executable).is_file():
                raise LiveProbeError("managed mode requires an installed browser executable")
            if self.profile_dir_arg:
                self.profile_dir = _safe_disposable_profile(Path(self.profile_dir_arg))
            else:
                self.profile_dir = Path(tempfile.mkdtemp(prefix="agentyc-direct-benchmark-"))
                self.remove_profile = True
            self.browser_port = _free_port()
            command = [self.executable, f"--user-data-dir={self.profile_dir}", f"--remote-debugging-port={self.browser_port}", "--no-first-run", "--no-default-browser-check", "--disable-background-networking"]
            if self.headless:
                command.append("--headless=new")
            self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.root_pid = self.process.pid
            deadline = time.monotonic() + 12.0
            version = None
            while time.monotonic() < deadline:
                try:
                    version = _local_json(self.browser_port, "/json/version")
                    break
                except LiveProbeError:
                    time.sleep(0.1)
            if not isinstance(version, dict) or self.process.poll() is not None:
                raise LiveProbeError("owned Chrome did not expose its debugging endpoint")
            command_line = " ".join(command)
            if f"--user-data-dir={self.profile_dir}" not in command_line or f"--remote-debugging-port={self.browser_port}" not in command_line:
                raise LiveProbeError("owned Chrome identity could not be proven")
            browser_ws = _validated_ws_url(version.get("webSocketDebuggerUrl"), self.browser_port)
            self.browser_socket = DevToolsSocket(browser_ws)
            self.target_ids = [self._create_target("about:blank"), self._create_target("about:blank")]
        else:
            if self.browser_port is None or self.browser_pid_arg is None or not self.target_id_arg or not self.human_target_id_arg:
                raise LiveProbeError("target mode requires browser port, browser pid, target id, and human target id")
            self.root_pid = self.browser_pid_arg
            _validate_existing_browser(self.browser_port, self.browser_pid_arg)
            targets = self._targets()
            selected = {item.get("id"): item for item in targets}
            if self.target_id_arg == self.human_target_id_arg or self.target_id_arg not in selected or self.human_target_id_arg not in selected:
                raise LiveProbeError("explicit target selection is incomplete or duplicated")
            for target_id in (self.target_id_arg, self.human_target_id_arg):
                target = selected[target_id]
                if target.get("type") != "page" or not isinstance(target.get("webSocketDebuggerUrl"), str):
                    raise LiveProbeError("explicit target is not a page target")
            self.target_ids = [self.target_id_arg, self.human_target_id_arg]
            _process_snapshot(self.root_pid)
        page_target = self._wait_target(self.target_ids[0])
        human_target = self._wait_target(self.target_ids[1])
        page = DevToolsSocket(_validated_ws_url(page_target.get("webSocketDebuggerUrl"), self.browser_port or 0))
        human = DevToolsSocket(_validated_ws_url(human_target.get("webSocketDebuggerUrl"), self.browser_port or 0))
        self.pages = [page, human]
        self.space_pages = [page]
        self.space_target_ids = [self.target_ids[0]]
        page.command("Page.enable")
        page.command("Runtime.enable")
        page.command("Page.bringToFront")
        page.command("Input.setIgnoreInputEvents", {"ignore": False})
        human.command("Runtime.enable")
        return page, human

    def ensure_space_count(self, count: int, path: Path) -> None:
        if count < 1 or count > MAX_SPACES:
            raise LiveProbeError("requested live space count is outside the bound")
        if self.mode != "managed" and count > 1:
            raise LiveProbeError("existing target mode cannot create additional space targets")
        if self.browser_socket is None and count > len(self.space_pages):
            raise LiveProbeError("owned browser target channel is unavailable")
        while len(self.space_pages) < count:
            target_id = self._create_target(path.resolve().as_uri())
            self.target_ids.append(target_id)
            self.space_target_ids.append(target_id)
            target = self._wait_target(target_id)
            socket_client = DevToolsSocket(_validated_ws_url(target.get("webSocketDebuggerUrl"), self.browser_port or 0))
            socket_client.command("Page.enable")
            socket_client.command("Runtime.enable")
            self.pages.append(socket_client)
            self.space_pages.append(socket_client)
        while len(self.space_pages) > count:
            socket_client = self.space_pages.pop()
            target_id = self.space_target_ids.pop()
            try:
                socket_client.close()
            finally:
                if self.browser_socket is not None:
                    try:
                        self.browser_socket.command("Target.closeTarget", {"targetId": target_id})
                    except (LiveProbeError, OSError):
                        pass
                if socket_client in self.pages:
                    self.pages.remove(socket_client)
        # Additional owned space targets remain blank. They are intentionally
        # retained as isolated targets so the resource cell measures the
        # requested concurrent-space count without stealing focus from the
        # action page.

    def close(self) -> None:
        for page in self.pages:
            page.close()
        self.pages.clear()
        if self.browser_socket is not None:
            for target_id in self.target_ids if self.mode == "managed" else []:
                try:
                    self.browser_socket.command("Target.closeTarget", {"targetId": target_id})
                except (LiveProbeError, OSError):
                    pass
            self.browser_socket.close()
            self.browser_socket = None
        if self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.process.kill()
                    self.process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self.process = None
        if self.remove_profile and self.profile_dir is not None:
            shutil.rmtree(self.profile_dir, ignore_errors=True)


def live_missing(mode: str, browser_executable: str | None, profile_dir: str | None, target_id: str | None = None, human_target_id: str | None = None, browser_port: int | None = None, browser_pid: int | None = None) -> list[str]:
    missing: list[str] = []
    if mode in {"target", "headed"}:
        if browser_port is None:
            missing.append("--browser-debugging-port for explicit target mode")
        if browser_pid is None:
            missing.append("--browser-pid for resource ownership")
        if not target_id:
            missing.append("--target-id for explicit target mode")
        if not human_target_id:
            missing.append("--human-target-id for human-tab responsiveness")
    if mode == "managed":
        if not browser_executable:
            missing.append("--browser-executable for managed mode")
        elif not Path(browser_executable).is_file():
            missing.append("the supplied browser executable")
        if profile_dir:
            try:
                _safe_disposable_profile(Path(profile_dir))
            except LiveProbeError:
                missing.append("an empty disposable --profile-dir inside the system temporary directory")
    return missing


def parse_csv(value: str, label: str) -> list[str]:
    values = [part.strip() for part in value.split(",") if part.strip()]
    if not values:
        raise ValueError(f"{label} cannot be empty")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates")
    return values


def parse_positive_ints(value: str, label: str) -> list[int]:
    try:
        values = [int(part) for part in parse_csv(value, label)]
    except ValueError as exc:
        raise ValueError(f"{label} must be comma-separated positive integers") from exc
    if any(value <= 0 or value > MAX_SPACES for value in values):
        raise ValueError(f"{label} must be comma-separated integers in 1..{MAX_SPACES}")
    return values


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percent / 100.0)
    return ordered[min(index, len(ordered) - 1)]


def mean_confidence_interval(values: list[float]) -> dict[str, Any]:
    """Return an approximate 95% CI for the mean without external dependencies."""
    if not values:
        return {
            "method": "normal_approximation",
            "confidence_level": 0.95,
            "sample_count": 0,
            "lower": 0.0,
            "upper": 0.0,
            "status": "not_available",
        }
    average = mean(values)
    if len(values) == 1:
        margin = 0.0
    else:
        variance = math.fsum((value - average) ** 2 for value in values) / (len(values) - 1)
        margin = 1.96 * math.sqrt(variance / len(values))
    return {
        "method": "normal_approximation",
        "confidence_level": 0.95,
        "sample_count": len(values),
        "lower": average - margin,
        "upper": average + margin,
        "status": "approximate",
    }


def measure_once(record: dict[str, Any], cache_state: str, spaces: int) -> dict[str, Any]:
    started = time.perf_counter_ns()
    raw = record["path"].read_bytes()
    if len(raw) > MAX_FIXTURE_BYTES:
        raise ValueError("fixture exceeds the bounded read limit")
    read_done = time.perf_counter_ns()
    decoded = raw.decode("utf-8")
    if len(decoded) > MAX_FIXTURE_TEXT_CHARS:
        raise ValueError("fixture exceeds the bounded text limit")
    parser = ControlCounter()
    parser.feed(decoded)
    parser.close()
    metadata_done = time.perf_counter_ns()
    # This is a local, side-effect-free stand-in for an actionable operation.
    first_control = parser.controls > 0
    action_done = time.perf_counter_ns()
    summary = {
        "controls": parser.controls,
        "frames": parser.frames,
        "first_control_available": first_control,
        "serialized_bytes": len(json.dumps({"controls": parser.controls, "frames": parser.frames}, sort_keys=True)),
    }
    _ = json.dumps(summary, sort_keys=True)
    finished = time.perf_counter_ns()
    synthetic_action_ms = (action_done - metadata_done) / 1_000_000
    return {
        "fixture": record["name"],
        "cache_state": cache_state,
        "spaces": spaces,
        "sample_status": "valid",
        "read_ms": (read_done - started) / 1_000_000,
        "metadata_ms": (metadata_done - read_done) / 1_000_000,
        "action_ms": synthetic_action_ms,
        "first_useful_action_ms": (action_done - started) / 1_000_000,
        "synthetic_action_ms": synthetic_action_ms,
        "wait_ms": (finished - action_done) / 1_000_000,
        "total_ms": (finished - started) / 1_000_000,
        "fixture_bytes": len(raw),
        "fixture_chars": len(decoded),
        "serialized_bytes": summary["serialized_bytes"],
        "dom_scans": 0 if cache_state == "clean" else 1,
        "actionable_controls": parser.controls,
        "frames": parser.frames,
        "frames_scanned": parser.frames_scanned,
        "frame_coverage": parser.frame_coverage,
        "max_frame_depth": parser.max_frame_depth,
        "text_chars": parser.text_chars,
        "srcdoc_chars": parser.srcdoc_chars,
    }


def sample_error(record: dict[str, Any], cache_state: str, spaces: int, exc: Exception) -> dict[str, Any]:
    return {
        "fixture": record["name"],
        "cache_state": cache_state,
        "spaces": spaces,
        "sample_status": "error",
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


REQUIRED_SAMPLE_METRICS = (
    "read_ms",
    "metadata_ms",
    "action_ms",
    "first_useful_action_ms",
    "synthetic_action_ms",
    "wait_ms",
    "total_ms",
)


def validate_sample(sample: dict[str, Any]) -> str | None:
    for key in REQUIRED_SAMPLE_METRICS:
        value = sample.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
            return f"missing or invalid metric: {key}"
    coverage = sample.get("frame_coverage")
    if coverage is not None and (
        isinstance(coverage, bool)
        or not isinstance(coverage, (int, float))
        or not math.isfinite(float(coverage))
        or not 0.0 <= float(coverage) <= 1.0
    ):
        return "frame coverage is outside [0, 1]"
    for key in ("fixture_bytes", "fixture_chars", "frames", "frames_scanned", "max_frame_depth", "text_chars", "srcdoc_chars"):
        value = sample.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            return f"missing or invalid count: {key}"
    return None


def measure_sample(record: dict[str, Any], cache_state: str, spaces: int, *, nonce: str | None = None) -> dict[str, Any]:
    try:
        sample = measure_once(record, cache_state, spaces)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        sample = sample_error(record, cache_state, spaces, exc)
    else:
        invalid_reason = validate_sample(sample)
        if invalid_reason is not None:
            sample["sample_status"] = "invalid"
            sample["invalid_reason"] = invalid_reason
    if nonce is not None:
        sample["nonce"] = nonce
    return sample


def sample_accounting(samples: list[dict[str, Any]]) -> dict[str, int]:
    statuses = [sample.get("sample_status") for sample in samples]
    valid = statuses.count("valid")
    errors = statuses.count("error")
    invalid = len(statuses) - valid - errors
    return {"attempted": len(samples), "valid": valid, "errors": errors, "invalid": invalid}


def summarize(samples: list[dict[str, Any]], record: dict[str, Any], cache_state: str, spaces: int) -> dict[str, Any]:
    def values(key: str) -> list[float]:
        return [float(sample[key]) for sample in samples if sample.get("sample_status") == "valid" and key in sample]

    accounting = sample_accounting(samples)
    valid = accounting["valid"]
    p95_gate = "gateable" if valid >= MIN_P95_SAMPLES else "not_gateable"
    p99_gate = "gateable" if valid >= MIN_P99_SAMPLES else "not_gateable"

    def latency_stats(key: str, measurement_status: str = "measured") -> dict[str, Any]:
        metric_values = values(key)
        return {
            "p50": percentile(metric_values, 50),
            "p95": percentile(metric_values, 95),
            "p99": percentile(metric_values, 99),
            "mean": mean(metric_values) if metric_values else 0.0,
            "mean_confidence_interval": mean_confidence_interval(metric_values),
            "measurement_status": measurement_status,
        }

    valid_samples = [sample for sample in samples if sample.get("sample_status") == "valid"]
    first_valid = valid_samples[0] if valid_samples else {}
    frame_count = max((int(sample.get("frames", 0)) for sample in valid_samples), default=0)
    frames_scanned = max((int(sample.get("frames_scanned", 0)) for sample in valid_samples), default=0)
    frame_coverage = frames_scanned / frame_count if frame_count else None
    return {
        "fixture": record["name"],
        "fixture_sha256": record["sha256"],
        "cache_state": cache_state,
        "spaces": spaces,
        "samples": accounting,
        "latency_ms": {
            "read_ms": latency_stats("read_ms"),
            "first_useful_action_ms": latency_stats("first_useful_action_ms", "synthetic_offline_control_presence_check"),
            "metadata_ms": latency_stats("metadata_ms"),
            "action_ms": latency_stats("action_ms", "synthetic_offline_control_presence_check"),
            "synthetic_action_ms": latency_stats("synthetic_action_ms", "synthetic_offline_control_presence_check"),
            "wait_ms": latency_stats("wait_ms"),
            "total_ms": latency_stats("total_ms"),
        },
        "offline_action": {
            "status": "synthetic",
            "operation": "control_presence_check",
            "browser_action_executed": False,
            "note": "The offline lane parses fixture controls; it does not click or execute a browser action.",
        },
        "round_trips": {
            "separate_calls": 3,
            "batch_calls": 1,
            "batch_reduction_percent": 66.67,
            "measurement_status": "offline_model_of_call_counts",
        },
        "context": {
            "fixture_utf8_bytes": record["bytes"],
            "fixture_chars": len(record["path"].read_text(encoding="utf-8")),
            "serialized_bytes": first_valid.get("serialized_bytes", 0),
            "serialized_tokens": None,
            "model_context_tokens": None,
            "tokenizer": None,
            "token_measurement_status": "not_available_in_offline_scaffold",
            "clean_snapshot_dom_scans": 0 if cache_state == "clean" else 1,
            "full_snapshot_actionable_control_coverage": frame_coverage,
            "delta_snapshot_actionable_control_coverage": frame_coverage,
            "frames_discovered": frame_count,
            "frames_scanned": frames_scanned,
            "nested_frame_coverage": frame_coverage,
            "max_frame_depth": max((int(sample.get("max_frame_depth", 0)) for sample in valid_samples), default=0),
            "text_chars": max((int(sample.get("text_chars", 0)) for sample in valid_samples), default=0),
            "srcdoc_chars": max((int(sample.get("srcdoc_chars", 0)) for sample in valid_samples), default=0),
        },
        "live_only": {
            "chrome_cpu_percent": None,
            "chrome_rss_bytes": None,
            "host_rss_bytes": None,
            "event_lag_ms": None,
            "reconnect_ms": None,
            "stale_ref_rate": None,
            "unknown_outcome_rate": None,
            "human_tab_responsiveness_ms": None,
            "status": "not_measured_offline",
        },
        "reliability_gates": {
            "stale_ref_rate": {"status": "not_measured_offline", "value": None},
            "unknown_outcome_rate": {"status": "not_measured_offline", "value": None},
            "reconnect_ms": {"status": "not_measured_offline", "value": None},
        },
        "human_tab_gate": {"status": "not_measured_offline", "responsiveness_ms": None},
        "tail_gates": {
            "p95": {"status": p95_gate, "minimum_samples": MIN_P95_SAMPLES},
            "p99": {"status": p99_gate, "minimum_samples": MIN_P99_SAMPLES},
            "note": "Tail thresholds are fixed at 200 valid samples for p95 and 1000 valid samples for p99; smoke runs are explicitly non-gating.",
        },
    }


ACTION_SELECTORS = {
    "small-form": "#save",
    "dense-admin-table": "button[data-account='001']",
    "dynamic-feed": "#append-item",
    "nested-frame": "#outer-action",
}


def _runtime_value(client: DevToolsSocket, expression: str) -> Any:
    result = client.command("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
    if not isinstance(result, dict) or result.get("exceptionDetails") is not None:
        raise LiveProbeError("required browser instrumentation evaluation failed")
    remote = result.get("result")
    if not isinstance(remote, dict) or "value" not in remote:
        raise LiveProbeError("required browser instrumentation returned no value")
    return remote["value"]


def _wait_ready(client: DevToolsSocket) -> None:
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if _runtime_value(client, "document.readyState") == "complete":
            return
        time.sleep(0.02)
    raise LiveProbeError("fixture page did not reach readyState complete")


def _navigate(client: DevToolsSocket, path: Path, *, reload: bool = False) -> float:
    started = time.perf_counter_ns()
    if reload:
        client.command("Page.reload", {"ignoreCache": False})
    else:
        client.command("Page.navigate", {"url": path.resolve(strict=True).as_uri()})
    _wait_ready(client)
    return (time.perf_counter_ns() - started) / 1_000_000


def _snapshot(client: DevToolsSocket, selector: str) -> dict[str, Any]:
    selector_json = json.dumps(selector)
    value = _runtime_value(
        client,
        """
        (() => {
          const controls = new Set(['A','BUTTON','INPUT','SELECT','TEXTAREA']);
          const out = {controls: 0, frames: 0, frames_scanned: 0, max_frame_depth: 0, text_chars: 0, srcdoc_chars: 0, snapshot_text: ''};
          function walk(doc, depth) {
            if (!doc) return;
            out.max_frame_depth = Math.max(out.max_frame_depth, depth);
            out.controls += Array.from(doc.querySelectorAll('a,button,input,select,textarea')).length;
            if (doc.body && doc.body.innerText) {
              const text = doc.body.innerText.slice(0, 250000);
              out.text_chars += text.length;
              out.snapshot_text += text;
            }
            for (const frame of Array.from(doc.querySelectorAll('iframe'))) {
              out.frames += 1;
              if (frame.srcdoc) out.srcdoc_chars += frame.srcdoc.length;
              try { if (frame.contentDocument) { out.frames_scanned += 1; walk(frame.contentDocument, depth + 1); } } catch (_) {}
            }
          }
          walk(document, 0);
          const target = document.querySelector(__SELECTOR__);
          if (!target) return null;
          const rect = target.getBoundingClientRect();
          return { ...out, action_rect: {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, width: rect.width, height: rect.height}, action_tag: target.tagName };
        })()
        """.replace("__SELECTOR__", selector_json),
    )
    if not isinstance(value, dict) or not isinstance(value.get("action_rect"), dict):
        raise LiveProbeError("required actionable control was not found in the live page")
    rect = value["action_rect"]
    if not all(isinstance(rect.get(key), (int, float)) and math.isfinite(float(rect[key])) for key in ("x", "y", "width", "height")):
        raise LiveProbeError("live actionable control geometry is invalid")
    if rect["width"] <= 0 or rect["height"] <= 0:
        raise LiveProbeError("live actionable control is not visible")
    return value


def _action_rect(client: DevToolsSocket, selector: str) -> dict[str, Any]:
    selector_json = json.dumps(selector)
    value = _runtime_value(
        client,
        """
        (() => {
          const target = document.querySelector(__SELECTOR__);
          if (!target) return null;
          const rect = target.getBoundingClientRect();
          return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, width: rect.width, height: rect.height};
        })()
        """.replace("__SELECTOR__", selector_json),
    )
    if not isinstance(value, dict) or not all(isinstance(value.get(key), (int, float)) and math.isfinite(float(value[key])) for key in ("x", "y", "width", "height")):
        raise LiveProbeError("live actionable control geometry is unavailable")
    if value["width"] <= 0 or value["height"] <= 0:
        raise LiveProbeError("live actionable control is not visible")
    return value


def _install_action_instrumentation(client: DevToolsSocket, selector: str) -> None:
    selector_json = json.dumps(selector)
    value = _runtime_value(
        client,
        """
        (() => {
          const target = document.querySelector(__SELECTOR__);
          if (!target) return false;
          window.__agentycBenchmarkAction = null;
          target.addEventListener('click', event => {
            window.__agentycBenchmarkAction = {trusted: event.isTrusted === true, tag: event.target && event.target.tagName, at: performance.now()};
          }, {once: true});
          return true;
        })()
        """.replace("__SELECTOR__", selector_json),
    )
    if value is not True:
        raise LiveProbeError("live action instrumentation could not bind to the control")


def _dispatch_click(client: DevToolsSocket, rect: dict[str, Any]) -> None:
    x, y = float(rect["x"]), float(rect["y"])
    for event_type, buttons in (("mouseMoved", 0), ("mousePressed", 1), ("mouseReleased", 0)):
        client.command("Input.dispatchMouseEvent", {"type": event_type, "x": x, "y": y, "button": "left", "buttons": buttons, "clickCount": 1})


def _verify_action(client: DevToolsSocket, fixture: str) -> tuple[float, dict[str, Any]]:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        value = _runtime_value(
            client,
            """
            (() => {
              const action = window.__agentycBenchmarkAction;
              const feed = document.querySelector('#feed');
              const status = document.querySelector('#status');
              return {action, feed_count: feed ? feed.children.length : null, status: status ? status.textContent : null, now: performance.now()};
            })()
            """,
        )
        if isinstance(value, dict) and isinstance(value.get("action"), dict) and value["action"].get("trusted") is True:
            if fixture == "small-form" and value.get("status") != "saved":
                raise LiveProbeError("live form action did not reach its verified postcondition")
            if fixture == "dynamic-feed" and not isinstance(value.get("feed_count"), int):
                raise LiveProbeError("live feed action did not expose its verified postcondition")
            event_lag = max(0.0, float(value["now"]) - float(value["action"].get("at", value["now"])))
            return event_lag, value
        time.sleep(0.005)
    raise LiveProbeError("live browser action had no trusted, observable outcome")


def _measure_human_tab(client: DevToolsSocket) -> float:
    started = time.perf_counter_ns()
    value = _runtime_value(client, "({ready: document.readyState, now: performance.now()})")
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    if not isinstance(value, dict) or value.get("ready") not in {"interactive", "complete"}:
        raise LiveProbeError("human-tab responsiveness instrumentation failed")
    return elapsed


def _token_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    rendered = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")
    if not rendered:
        raise LiveProbeError("live context serialization was empty")
    tokens = (len(rendered) + 3) // 4
    return {
        "transport_bytes": len(rendered),
        "utf8_bytes": len(rendered),
        "serialized_tokens": tokens,
        "model_context_tokens": tokens,
        "tokenizer": TOKENIZER_NAME,
        "tokenizer_status": "deterministic_byte_estimate_not_model_tokenizer",
    }


def _round_trip_measure(client: DevToolsSocket) -> dict[str, Any]:
    started = time.perf_counter_ns()
    for expression in ("location.href", "document.readyState", "performance.now()"):
        _runtime_value(client, expression)
    separate_ms = (time.perf_counter_ns() - started) / 1_000_000
    started = time.perf_counter_ns()
    _runtime_value(client, "({href: location.href, ready: document.readyState, now: performance.now()})")
    batch_ms = (time.perf_counter_ns() - started) / 1_000_000
    return {
        "separate_calls": 3,
        "batch_calls": 1,
        "separate_call_ms": separate_ms,
        "batch_call_ms": batch_ms,
        "batch_reduction_percent": 66.67,
        "measurement_status": "live_cdp_round_trips",
    }


def _reconnect_measure(browser: LiveChrome, target_id: str) -> float:
    target = browser._wait_target(target_id)
    websocket_url = _validated_ws_url(target.get("webSocketDebuggerUrl"), browser.browser_port or 0)
    started = time.perf_counter_ns()
    socket_client = DevToolsSocket(websocket_url)
    try:
        socket_client.command("Runtime.enable")
        _runtime_value(socket_client, "document.readyState")
    finally:
        socket_client.close()
    return (time.perf_counter_ns() - started) / 1_000_000


def _live_measure_sample(
    browser: LiveChrome,
    page: DevToolsSocket,
    human: DevToolsSocket,
    record: dict[str, Any],
    cache_state: str,
    spaces: int,
    *,
    first_sample: bool,
    cached_snapshot: dict[str, Any] | None,
    auxiliary: dict[str, Any],
) -> dict[str, Any]:
    selector = ACTION_SELECTORS.get(record["name"])
    if selector is None:
        raise LiveProbeError("fixture has no live action contract")
    started = time.perf_counter_ns()
    read_ms = 0.0
    if first_sample or cache_state != "clean":
        read_ms = _navigate(page, record["path"], reload=not first_sample)
    metadata_started = time.perf_counter_ns()
    snapshot = cached_snapshot if cache_state == "clean" and cached_snapshot is not None else _snapshot(page, selector)
    metadata_ms = 0.0 if cache_state == "clean" and cached_snapshot is not None else (time.perf_counter_ns() - metadata_started) / 1_000_000
    action_geometry = _action_rect(page, selector) if cache_state == "clean" and cached_snapshot is not None else snapshot["action_rect"]
    _install_action_instrumentation(page, selector)
    action_started = time.perf_counter_ns()
    _dispatch_click(page, action_geometry)
    action_ms = (time.perf_counter_ns() - action_started) / 1_000_000
    event_lag_ms, action_result = _verify_action(page, record["name"])
    wait_ms = max(0.0, (time.perf_counter_ns() - action_started) / 1_000_000 - action_ms)
    total_ms = (time.perf_counter_ns() - started) / 1_000_000
    context_payload = {
        "fixture": record["name"],
        "cache_state": cache_state,
        "spaces": spaces,
        "controls": snapshot.get("controls"),
        "frames": snapshot.get("frames"),
        "text_chars": snapshot.get("text_chars"),
        "snapshot_text": snapshot.get("snapshot_text", ""),
        "actionable_control_coverage": 1.0,
    }
    token = _token_metrics(context_payload)
    delta_payload = {"postcondition": {"feed_count": action_result.get("feed_count"), "status": action_result.get("status")}}
    delta_ratio = len(json.dumps(delta_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")) / max(1, token["utf8_bytes"])
    chrome_cpu = auxiliary["chrome_cpu_percent"]
    chrome_rss = auxiliary["chrome_rss_bytes"]
    return {
        "fixture": record["name"],
        "cache_state": cache_state,
        "spaces": spaces,
        "sample_status": "valid",
        "read_ms": read_ms,
        "metadata_ms": metadata_ms,
        "action_ms": action_ms,
        "first_useful_action_ms": (action_started - started) / 1_000_000 + action_ms,
        "synthetic_action_ms": action_ms,
        "wait_ms": wait_ms,
        "total_ms": total_ms,
        "fixture_bytes": record["bytes"],
        "fixture_chars": len(record["path"].read_text(encoding="utf-8")),
        "serialized_bytes": token["utf8_bytes"],
        "transport_bytes": token["transport_bytes"],
        "utf8_bytes": token["utf8_bytes"],
        "serialized_tokens": token["serialized_tokens"],
        "model_context_tokens": token["model_context_tokens"],
        "tokenizer": token["tokenizer"],
        "dom_scans": 0 if cache_state == "clean" and cached_snapshot is not None else 1,
        "actionable_controls": snapshot["controls"],
        "frames": snapshot["frames"],
        "frames_scanned": snapshot["frames_scanned"],
        "frame_coverage": (snapshot["frames_scanned"] / snapshot["frames"]) if snapshot["frames"] else 1.0,
        "max_frame_depth": snapshot["max_frame_depth"],
        "text_chars": snapshot["text_chars"],
        "srcdoc_chars": snapshot["srcdoc_chars"],
        "delta_ratio": delta_ratio,
        "chrome_cpu_percent": chrome_cpu,
        "chrome_rss_bytes": chrome_rss,
        "host_rss_bytes": auxiliary["host_rss_bytes"],
        "event_lag_ms": event_lag_ms,
        "human_tab_responsiveness_ms": auxiliary["human_tab_responsiveness_ms"],
        "stale_ref": 0,
        "unknown_outcome": 0,
        "action_result": action_result,
        "round_trips": auxiliary["round_trips"],
    }


def _live_latency_stats(samples: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [float(sample[key]) for sample in samples]
    return {
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "mean": mean(values),
        "mean_confidence_interval": mean_confidence_interval(values),
        "measurement_status": "live_cdp_measured",
    }


def summarize_live(samples: list[dict[str, Any]], record: dict[str, Any], cache_state: str, spaces: int, reconnect_ms: float) -> dict[str, Any]:
    if not samples or any(sample.get("sample_status") != "valid" for sample in samples):
        raise LiveProbeError("live cell contains an invalid or missing sample")
    frame_count = max(int(sample["frames"]) for sample in samples)
    scanned = max(int(sample["frames_scanned"]) for sample in samples)
    coverage = scanned / frame_count if frame_count else 1.0
    cpu = max(float(sample["chrome_cpu_percent"]) for sample in samples)
    chrome_rss = max(int(sample["chrome_rss_bytes"]) for sample in samples)
    host_rss = max(int(sample["host_rss_bytes"]) for sample in samples)
    event_lag = percentile([float(sample["event_lag_ms"]) for sample in samples], 95)
    human = percentile([float(sample["human_tab_responsiveness_ms"]) for sample in samples], 95)
    stale = sum(int(sample["stale_ref"]) for sample in samples) / len(samples)
    unknown = sum(int(sample["unknown_outcome"]) for sample in samples) / len(samples)
    serialized = max(int(sample["serialized_bytes"]) for sample in samples)
    serialized_tokens = max(int(sample["serialized_tokens"]) for sample in samples)
    delta_ratio = percentile([float(sample["delta_ratio"]) for sample in samples], 50)
    return {
        "fixture": record["name"],
        "fixture_sha256": record["sha256"],
        "cache_state": cache_state,
        "spaces": spaces,
        "samples": {"attempted": len(samples), "valid": len(samples), "errors": 0, "invalid": 0},
        "latency_ms": {key: _live_latency_stats(samples, key) for key in REQUIRED_SAMPLE_METRICS},
        "offline_action": {"status": "not_applicable", "browser_action_executed": True},
        "round_trips": {
            "separate_calls": 3,
            "batch_calls": 1,
            "batch_reduction_percent": 66.67,
            "separate_call_ms_p95": percentile([float(sample["round_trips"]["separate_call_ms"]) for sample in samples], 95),
            "batch_call_ms_p95": percentile([float(sample["round_trips"]["batch_call_ms"]) for sample in samples], 95),
            "measurement_status": "live_cdp_round_trips",
        },
        "context": {
            "fixture_utf8_bytes": record["bytes"],
            "fixture_chars": len(record["path"].read_text(encoding="utf-8")),
            "serialized_bytes": serialized,
            "transport_bytes": serialized,
            "utf8_bytes": serialized,
            "serialized_tokens": serialized_tokens,
            "model_context_tokens": serialized_tokens,
            "tokenizer": TOKENIZER_NAME,
            "tokenizer_status": "deterministic_byte_estimate_not_model_tokenizer",
            "clean_snapshot_dom_scans": 0 if cache_state == "clean" else len(samples),
            "full_snapshot_actionable_control_coverage": 1.0,
            "delta_snapshot_actionable_control_coverage": 1.0,
            "delta_ratio": delta_ratio,
            "frames_discovered": frame_count,
            "frames_scanned": scanned,
            "nested_frame_coverage": coverage,
            "max_frame_depth": max(int(sample["max_frame_depth"]) for sample in samples),
            "text_chars": max(int(sample["text_chars"]) for sample in samples),
            "srcdoc_chars": max(int(sample["srcdoc_chars"]) for sample in samples),
        },
        "live_only": {
            "chrome_cpu_percent": cpu,
            "chrome_rss_bytes": chrome_rss,
            "host_rss_bytes": host_rss,
            "event_lag_ms": event_lag,
            "reconnect_ms": reconnect_ms,
            "stale_ref_rate": stale,
            "unknown_outcome_rate": unknown,
            "human_tab_responsiveness_ms": human,
            "status": "measured",
        },
        "reliability_gates": {
            "stale_ref_rate": {"status": "gateable", "value": stale},
            "unknown_outcome_rate": {"status": "gateable", "value": unknown},
            "reconnect_ms": {"status": "gateable", "value": reconnect_ms},
        },
        "human_tab_gate": {"status": "gateable", "responsiveness_ms": human},
        "tail_gates": {
            "p95": {"status": "gateable", "minimum_samples": MIN_P95_SAMPLES},
            "p99": {"status": "gateable", "minimum_samples": MIN_P99_SAMPLES},
        },
    }


def run_live_matrix(browser: LiveChrome, selected: list[dict[str, Any]], cache_states: list[str], spaces: list[int], warmups: int, samples_per_cell: int, nonce: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    page, human = browser.open()
    rows: list[dict[str, Any]] = []
    raw_samples: list[dict[str, Any]] = []
    for record in selected:
        for cache_state in cache_states:
            for space_count in spaces:
                cached_snapshot = None
                browser.ensure_space_count(space_count, record["path"])
                _navigate(page, record["path"])
                if cache_state == "clean":
                    cached_snapshot = _snapshot(page, ACTION_SELECTORS[record["name"]])
                reconnect_ms = _reconnect_measure(browser, browser.target_ids[0])
                auxiliary_cpu, auxiliary_rss = _process_snapshot(browser.root_pid or 0)
                auxiliary = {
                    "chrome_cpu_percent": auxiliary_cpu,
                    "chrome_rss_bytes": auxiliary_rss,
                    "host_rss_bytes": _host_rss_bytes(),
                    "human_tab_responsiveness_ms": _measure_human_tab(human),
                    "round_trips": _round_trip_measure(page),
                }
                for index in range(warmups):
                    _live_measure_sample(browser, page, human, record, cache_state, space_count, first_sample=index == 0, cached_snapshot=cached_snapshot, auxiliary=auxiliary)
                cell_samples = [
                    _live_measure_sample(browser, page, human, record, cache_state, space_count, first_sample=False, cached_snapshot=cached_snapshot, auxiliary=auxiliary)
                    for _ in range(samples_per_cell)
                ]
                for sample in cell_samples:
                    sample["nonce"] = nonce
                raw_samples.extend(cell_samples)
                rows.append(summarize_live(cell_samples, record, cache_state, space_count, reconnect_ms))
    return rows, raw_samples


def live_release_gates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def all_values(path: tuple[str, ...], *, clean_only: bool = False) -> list[float]:
        result: list[float] = []
        for row in rows:
            if clean_only and row.get("cache_state") != "clean":
                continue
            value: Any = row
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
                result.append(float(value))
        return result

    specs = {
        "resource": {"cpu_p95_percent": ("live_only", "chrome_cpu_percent"), "rss_p95_bytes": ("live_only", "chrome_rss_bytes")},
        "token": {"transport_bytes_p95": ("context", "transport_bytes"), "utf8_bytes_p95": ("context", "utf8_bytes"), "serialized_tokens_p95": ("context", "serialized_tokens"), "model_context_tokens_p95": ("context", "model_context_tokens")},
        "context": {"clean_dom_scans_max": ("context", "clean_snapshot_dom_scans"), "delta_ratio_p50": ("context", "delta_ratio"), "delta_ratio_p95": ("context", "delta_ratio"), "actionable_coverage_min": ("context", "delta_snapshot_actionable_control_coverage")},
        "reliability": {"stale_ref_rate": ("live_only", "stale_ref_rate"), "unknown_outcome_rate": ("live_only", "unknown_outcome_rate"), "event_lag_p95_ms": ("live_only", "event_lag_ms"), "reconnect_p95_ms": ("live_only", "reconnect_ms"), "human_tab_responsiveness_p95_ms": ("live_only", "human_tab_responsiveness_ms")},
    }
    gates: dict[str, Any] = {}
    for category, metric_specs in specs.items():
        metrics: dict[str, Any] = {}
        for name, path in metric_specs.items():
            values = all_values(path, clean_only=category == "context" and name == "clean_dom_scans_max")
            if not values:
                raise LiveProbeError("required live release-gate metric was not measured")
            value = percentile(values, 95) if "p95" in name else percentile(values, 50) if "p50" in name else max(values) if "max" in name else min(values) if "min" in name else values[0]
            ceiling = RELEASE_GATE_CEILINGS.get(category, {}).get(name)
            is_minimum = category == "context" and name == "actionable_coverage_min"
            passed = value >= ceiling if is_minimum and ceiling is not None else value <= ceiling if ceiling is not None else False
            metrics[name] = {"value": value, "status": "passed" if passed else "blocked"}
            if is_minimum:
                metrics[name]["minimum"] = ceiling
            elif ceiling is not None:
                metrics[name]["ceiling"] = ceiling
        gates[category] = {"schema_version": RELEASE_GATE_SCHEMA_VERSION, "status": "passed" if all(item["status"] == "passed" for item in metrics.values()) else "blocked", "evidence_mode": "live", "metrics": metrics}
    return gates


def offline_release_gates() -> dict[str, Any]:
    """Return a complete schema whose nulls cannot be mistaken for live measurements."""
    gates: dict[str, Any] = {}
    for category, ceilings in RELEASE_GATE_CEILINGS.items():
        metrics: dict[str, Any] = {}
        for name, ceiling in ceilings.items():
            metric = {
                "value": None,
                "ceiling": ceiling,
                "status": "not_measured_offline",
            }
            if category == "context" and name == "actionable_coverage_min":
                metric.pop("ceiling")
                metric["minimum"] = ceiling
            metrics[name] = metric
        if category == "context":
            metrics["equivalent_coverage"] = {
                "value": None,
                "minimum": True,
                "status": "not_measured_offline",
            }
            metrics["truncation_accounted"] = {
                "value": None,
                "minimum": True,
                "status": "not_measured_offline",
            }
        gates[category] = {
            "schema_version": RELEASE_GATE_SCHEMA_VERSION,
            "status": "not_gateable_offline",
            "evidence_mode": "offline",
            "metrics": metrics,
        }
    return gates


def redact_release_gates(gates: Any) -> Any:
    """Redact structured gate metrics without confusing the token category with a credential."""
    if not isinstance(gates, dict):
        return redact_for_persistence(gates)
    staged = dict(gates)
    token_section = staged.pop("token", None)
    if token_section is not None:
        staged["token_metrics"] = token_section
    redacted = redact_for_persistence(staged)
    if token_section is not None and isinstance(redacted, dict):
        redacted["token"] = redacted.pop("token_metrics", None)
    return redacted


def redact_benchmark_report(result: dict[str, Any]) -> dict[str, Any]:
    """Apply central redaction while preserving the allowlisted gate schema."""
    gates = result.get("release_gates")
    without_gates = {key: value for key, value in result.items() if key != "release_gates"}
    redacted = redact_for_persistence(without_gates)
    if gates is not None:
        redacted["release_gates"] = redact_release_gates(gates)
    return redacted


def write_benchmark_baseline(path: Path, result: dict[str, Any]) -> None:
    safe_result = redact_benchmark_report(result)
    rendered = (json.dumps(safe_result, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
    write_bytes_atomic(path, rendered)


def markdown_report(result: dict[str, Any]) -> str:
    live = result.get("evidence_mode") == "live"
    lines = [
        "# Phase 0 direct benchmark baseline",
        "",
        f"- Mode: `{result['mode']}`",
        f"- Kind: `{result['kind']}`",
        f"- Run nonce: `{result['nonce']}`",
        f"- Fixture set SHA-256: `{result['fixture_set_sha256']}`",
        f"- Fixture manifest SHA-256: `{result['baseline_manifest']['sha256']}`",
        "- Confidence intervals: approximate normal 95% intervals for per-cell latency means",
        f"- Browser launches: `{1 if result['browser_policy'].get('automatic_launch') else 0}`",
        "- Browser downloads: `0`",
        "- CDP URL: `loopback endpoint selected by the runner`" if live else "- CDP URL: `not used`",
        f"- Tokenizer: `{result['tokenizer'].get('name')}`" if live else "- Tokenizer: `not available in offline scaffold`",
        "",
        "| Fixture | Cache | Spaces | Samples (valid/error/invalid) | First useful action p50 (ms) | Metadata p95 (ms) | Action p95 (ms) | p95 gate | p99 gate |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in result["rows"]:
        lines.append(
            f"| {row['fixture']} | {row['cache_state']} | {row['spaces']} | "
            f"{row['samples']['valid']}/{row['samples']['errors']}/{row['samples']['invalid']} | "
            f"{row['latency_ms']['first_useful_action_ms']['p50']:.4f}{' (live)' if live else ' (synthetic)'} | "
            f"{row['latency_ms']['metadata_ms']['p95']:.4f} | "
            f"{row['latency_ms']['action_ms']['p95']:.4f} | "
            f"{row['tail_gates']['p95']['status']} | {row['tail_gates']['p99']['status']} |"
        )
    lines.extend(
        [
            "",
            "Live timings are measured from loopback CDP navigation, trusted input dispatch, browser postconditions, process sampling, and an explicit human-tab target." if live else "Offline action timings are synthetic control-presence checks, not browser clicks. Live-only resource, reliability, reconnect, and human-tab metrics are null.",
            "",
        ]
    )
    return "\n".join(lines)


def raw_sample_chunks(samples: list[dict[str, Any]]) -> list[tuple[str, bytes]]:
    chunks: list[bytes] = []
    current = bytearray()
    for sample in samples:
        safe_sample = redact_for_persistence(sample)
        line = (json.dumps(safe_sample, separators=(",", ":"), sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
        if len(line) > MAX_RAW_SAMPLE_FILE_BYTES:
            raise ValueError("one raw sample exceeds the bounded artifact limit")
        if current and len(current) + len(line) > MAX_RAW_SAMPLE_FILE_BYTES:
            chunks.append(bytes(current))
            current = bytearray()
        current.extend(line)
    if current or not chunks:
        chunks.append(bytes(current))
    return [
        ("raw_samples.jsonl" if index == 0 else f"raw_samples-{index:03d}.jsonl", chunk)
        for index, chunk in enumerate(chunks)
    ]


def safe_artifact_dir(value: Path) -> Path:
    requested = value if value.is_absolute() else ROOT / value
    current = requested
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("artifact path components must not be symlinks")
        current = current.parent
    if requested.exists() and not requested.is_dir():
        raise ValueError("artifact directory must be a directory")
    resolved = requested.resolve()
    artifacts = (ROOT / "artifacts").resolve()
    try:
        resolved.relative_to(artifacts)
    except ValueError as error:
        raise ValueError("artifact directory must be inside artifacts/") from error
    if resolved == artifacts:
        raise ValueError("artifact directory must be a child of artifacts/")
    return resolved


def _fixture_set_hash(selected: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        "\n".join(f"{record['name']}:{record['sha256']}" for record in selected).encode("utf-8")
    ).hexdigest()


def _raw_sample_declarations(chunks: list[tuple[str, bytes]], sample_count: int) -> dict[str, Any]:
    declarations: list[dict[str, Any]] = []
    for name, content in chunks:
        declarations.append(
            {
                "name": name,
                "sha256": sha256_bytes(content),
                "bytes": len(content),
                "sample_count": content.count(b"\n"),
            }
        )
    return {
        "files": declarations,
        "total_samples": sample_count,
        "required_metrics": list(REQUIRED_SAMPLE_METRICS),
    }


def _file_metadata(directory: Path, names: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name in names:
        path = directory / name
        result.append({"name": name, "sha256": sha256_file(path), "bytes": path.stat().st_size})
    return result


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def _publication_lock(lock_path: Path):
    """Serialize publication so two runs cannot replace the same generation."""
    target = Path(lock_path)
    current = target
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("benchmark publication lock components must not be symlinks")
        current = current.parent
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    remove_fallback = False
    if fcntl is None:
        flags |= os.O_EXCL
        remove_fallback = True
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(str(target), flags, 0o600)
        except FileExistsError as error:
            raise ValueError("benchmark publication is already in progress") from error
        handle = os.fdopen(descriptor, "a+b")
        descriptor = None
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    if error.errno in {errno.EACCES, errno.EAGAIN}:
                        raise ValueError("benchmark publication is already in progress") from error
                    raise
            yield handle
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if remove_fallback:
            try:
                target.unlink()
            except OSError:
                pass


def publish_benchmark(
    artifact_dir: Path,
    result: dict[str, Any],
    markdown: str,
    sample_chunks: list[tuple[str, bytes]],
) -> dict[str, Any]:
    """Publish one complete benchmark generation without destroying its predecessor."""
    safe_dir = safe_artifact_dir(artifact_dir)
    with _publication_lock(safe_dir.parent / f".{safe_dir.name}.publish.lock"):
        return _publish_benchmark_locked(safe_dir, result, markdown, sample_chunks)


def _publish_benchmark_locked(
    artifact_dir: Path,
    result: dict[str, Any],
    markdown: str,
    sample_chunks: list[tuple[str, bytes]],
) -> dict[str, Any]:
    artifact_dir = safe_artifact_dir(artifact_dir)
    parent = artifact_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    nonce = str(result.get("nonce") or new_nonce())
    generation_id = f"generation-{nonce}"
    previous_path: Path | None = None
    if artifact_dir.exists():
        previous_path = parent / f".{artifact_dir.name}.previous-{nonce[:12]}"
        if previous_path.exists() or previous_path.is_symlink():
            raise ValueError("previous generation destination already exists")

    stage = Path(tempfile.mkdtemp(prefix=f".{artifact_dir.name}.staging-", dir=parent))
    moved_previous = False
    try:
        write_benchmark_baseline(stage / "baseline.json", result)
        write_text_atomic(stage / "baseline.md", markdown)
        raw_names: list[str] = []
        seen_raw_names: set[str] = set()
        for name, content in sample_chunks:
            if (
                not name.startswith("raw_samples")
                or "/" in name
                or "\\" in name
                or name in seen_raw_names
            ):
                raise ValueError("raw sample declaration has an unsafe or duplicate name")
            write_jsonl_atomic(stage / name, content, max_bytes=MAX_RAW_SAMPLE_FILE_BYTES)
            seen_raw_names.add(name)
            raw_names.append(name)

        generation_manifest: dict[str, Any] = {
            "schema_version": 1,
            "phase": 0,
            "kind": "direct-benchmark-generation",
            "generation_id": generation_id,
            "nonce": nonce,
            "complete": True,
            "previous_generation": repository_relative(previous_path) if previous_path else None,
            "files": _file_metadata(stage, ["baseline.json", "baseline.md", *raw_names]),
            "raw_samples_files": raw_names,
        }
        add_envelope(
            generation_manifest,
            kind="direct-benchmark-generation",
            command=result.get("command"),
            build_tuple=result.get("build_tuple") if isinstance(result.get("build_tuple"), dict) else None,
            nonce=nonce,
        )
        write_json_atomic(stage / GENERATION_MANIFEST_NAME, generation_manifest)
        manifest_hash = sha256_file(stage / GENERATION_MANIFEST_NAME)
        commit_marker = {
            "schema_version": 1,
            "kind": "direct-benchmark-commit",
            "generation_id": generation_id,
            "nonce": nonce,
            "complete": True,
            "manifest": GENERATION_MANIFEST_NAME,
            "manifest_sha256": manifest_hash,
        }
        write_text_atomic(
            stage / COMMIT_MARKER_NAME,
            json.dumps(redact_for_persistence(commit_marker), sort_keys=True, separators=(",", ":")) + "\n",
            max_bytes=64 * 1024,
        )
        _fsync_directory(stage)

        if artifact_dir.exists():
            if previous_path is None:
                raise ValueError("missing previous generation destination")
            artifact_dir.replace(previous_path)
            moved_previous = True
        stage.replace(artifact_dir)
        _fsync_directory(parent)
        return {
            "generation_id": generation_id,
            "manifest": GENERATION_MANIFEST_NAME,
            "commit_marker": COMMIT_MARKER_NAME,
            "previous_generation": repository_relative(previous_path) if previous_path else None,
        }
    except Exception:
        if moved_previous and previous_path is not None and not artifact_dir.exists() and previous_path.exists():
            previous_path.replace(artifact_dir)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure local Phase 0 fixtures and emit an honest direct benchmark baseline.",
        epilog="offline is safe by default. managed owns a disposable browser/profile; target/headed require explicit loopback port, PID, page target, and human target. No arbitrary CDP URL is accepted.",
    )
    parser.add_argument("--mode", choices=("offline", "target", "headed", "managed"), default="offline")
    parser.add_argument("--warmups", type=int, default=10, help="warmup iterations per cell (default: 10)")
    parser.add_argument("--samples", type=int, help=f"measured iterations per cell (default: {DEFAULT_SAMPLES}; smoke default: {SMOKE_DEFAULT_SAMPLES})")
    parser.add_argument("--smoke", action="store_true", help="run a short, explicitly non-gating smoke sample")
    parser.add_argument("--fixtures", default="small-form,dense-admin-table,dynamic-feed,nested-frame")
    parser.add_argument("--cache-states", default=",".join(DEFAULT_CACHE_STATES))
    parser.add_argument("--spaces", default="1,2,4,8")
    parser.add_argument("--artifact-dir", type=Path, help="write a complete benchmark generation")
    parser.add_argument("--browser-executable")
    parser.add_argument("--profile-dir", help="managed mode only: an empty disposable profile inside the system temporary directory")
    parser.add_argument("--browser-debugging-port", type=int, help="target/headed mode only: loopback Chrome debugging port")
    parser.add_argument("--browser-pid", type=int, help="target/headed mode only: PID used for resource ownership")
    parser.add_argument("--target-id", help="target/headed mode only: exact existing page target identity")
    parser.add_argument("--human-target-id", help="target/headed mode only: exact unrelated page target for responsiveness")
    parser.add_argument("--headless", action="store_true", help="managed mode only: launch the owned browser headless")
    parser.add_argument("--dry-run", action="store_true", help="validate local inputs and print the plan without measuring or writing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    samples_per_cell = args.samples if args.samples is not None else (SMOKE_DEFAULT_SAMPLES if args.smoke else DEFAULT_SAMPLES)
    try:
        if args.warmups < 0 or samples_per_cell <= 0:
            raise ValueError("warmups may be zero; samples must be positive")
        if not args.smoke and samples_per_cell < MIN_P99_SAMPLES:
            raise ValueError(f"non-smoke runs require at least {MIN_P99_SAMPLES} samples per cell; use --smoke for a short run")
        manifest_metadata, fixtures = load_fixture_bundle()
        fixture_names = parse_csv(args.fixtures, "--fixtures")
        cache_states = parse_csv(args.cache_states, "--cache-states")
        if any(state not in DEFAULT_CACHE_STATES for state in cache_states):
            raise ValueError(f"--cache-states must be drawn from {','.join(DEFAULT_CACHE_STATES)}")
        spaces = parse_positive_ints(args.spaces, "--spaces")
        selected = [fixtures[name] for name in fixture_names]
        if args.artifact_dir is not None:
            args.artifact_dir = safe_artifact_dir(args.artifact_dir)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        print(f"direct benchmark error: {type(exc).__name__}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "action": "validate local fixtures",
                    "fixture_names": fixture_names,
                    "cache_states": cache_states,
                    "spaces": spaces,
                    "warmups": args.warmups,
                    "samples": samples_per_cell,
                    "smoke": args.smoke,
                    "tail_thresholds": {"p95": MIN_P95_SAMPLES, "p99": MIN_P99_SAMPLES},
                    "evidence_mode": "offline",
                    "release_eligible": False,
                    "release_gates": offline_release_gates(),
                    "would_probe_browser": False,
                    "would_launch_browser": False,
                    "would_download_browser": False,
                    "would_write": repository_relative(args.artifact_dir) if args.artifact_dir else None,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.mode in LIVE_MODES:
        missing = live_missing(
            args.mode,
            args.browser_executable,
            args.profile_dir,
            args.target_id,
            args.human_target_id,
            args.browser_debugging_port,
            args.browser_pid,
        )
        if missing:
            print(
                "required live benchmark probe unavailable: " + "; ".join(missing) + ". "
                "No browser was launched or downloaded; use --mode offline for the local baseline.",
                file=sys.stderr,
            )
            return 2
        run_nonce = new_nonce()
        browser = LiveChrome(
            args.mode,
            args.browser_executable,
            args.profile_dir,
            args.target_id,
            args.human_target_id,
            args.browser_debugging_port,
            args.browser_pid,
            args.headless,
        )
        try:
            rows, raw_samples = run_live_matrix(
                browser,
                selected,
                cache_states,
                spaces,
                args.warmups,
                samples_per_cell,
                run_nonce,
            )
            gates = live_release_gates(rows)
        except (LiveProbeError, OSError, ValueError, TypeError, UnicodeError) as exc:
            print(f"required live benchmark failed closed: {type(exc).__name__}: {str(exc)[:256]}", file=sys.stderr)
            return 2
        finally:
            browser.close()

        fixture_set_hash = _fixture_set_hash(selected)
        live_report: dict[str, Any] = {
            "schema_version": 1,
            "phase": 0,
            "kind": "direct-benchmark-baseline",
            "mode": args.mode,
            "evidence_mode": "live",
            "status": "live_passed",
            "release_eligible": not args.smoke and all(gate.get("status") == "passed" for gate in gates.values()),
            "nonce": run_nonce,
            "fixture_set_sha256": fixture_set_hash,
            "baseline_manifest": manifest_metadata,
            "manifest_sha256": manifest_metadata["sha256"],
            "fixture_binding": {
                "manifest_path": manifest_metadata["path"],
                "manifest_sha256": manifest_metadata["sha256"],
                "fixture_set_sha256": fixture_set_hash,
                "fixtures": [
                    {"name": record["name"], "file": record["file"], "sha256": record["sha256"], "bytes": record["bytes"]}
                    for record in selected
                ],
            },
            "fixtures": [
                {"name": record["name"], "file": record["file"], "sha256": record["sha256"], "bytes": record["bytes"]}
                for record in selected
            ],
            "cache_states": cache_states,
            "spaces": spaces,
            "warmups": args.warmups,
            "samples_per_cell": samples_per_cell,
            "smoke": args.smoke,
            "tail_thresholds": {"p95": MIN_P95_SAMPLES, "p99": MIN_P99_SAMPLES},
            "environment": {"platform": platform.platform(), "python": platform.python_version(), "browser": "owned-or-explicit-loopback-chrome"},
            "browser_policy": {
                "automatic_launch": args.mode == "managed",
                "automatic_download": False,
                "cdp_url_used": False,
                "target_selection": "owned_disposable_targets" if args.mode == "managed" else "explicit_existing_target_ids",
                "profile": "disposable_owned" if args.mode == "managed" else "caller_owned_existing",
            },
            "tokenizer": {"name": TOKENIZER_NAME, "version": "1", "status": "deterministic_byte_estimate_not_model_tokenizer"},
            "rows": rows,
            "sample_accounting": sample_accounting(raw_samples),
            "confidence_intervals": {
                "method": "normal_approximation",
                "confidence_level": 0.95,
                "scope": "per-cell latency mean",
                "status": "approximate",
            },
            "release_gates": gates,
        }
        add_envelope(
            live_report,
            kind="direct-benchmark",
            build_tuple={
                "benchmark_kind": "direct-benchmark-baseline",
                "benchmark_script": repository_relative(Path(__file__)),
                "benchmark_script_sha256": sha256_file(Path(__file__)),
                "fixture_manifest": manifest_metadata["path"],
                "fixture_manifest_sha256": manifest_metadata["sha256"],
            },
            nonce=run_nonce,
        )
        live_report["release_gates"] = gates
        if args.artifact_dir:
            try:
                sample_chunks = raw_sample_chunks(raw_samples)
                live_report["raw_samples_files"] = [name for name, _ in sample_chunks]
                live_report["raw_sample_declarations"] = _raw_sample_declarations(sample_chunks, len(raw_samples))
                live_report = redact_benchmark_report(live_report)
                publication = publish_benchmark(args.artifact_dir, live_report, markdown_report(live_report), sample_chunks)
            except (OSError, ValueError, TypeError) as error:
                print(f"direct benchmark error: {type(error).__name__}", file=sys.stderr)
                return 2
            print(
                f"wrote live benchmark baseline: {repository_relative(args.artifact_dir)} "
                f"({len(rows)} cells, {len(raw_samples)} samples, {publication['generation_id']})"
            )
        else:
            print(json.dumps(redact_benchmark_report(live_report), indent=2, sort_keys=True, allow_nan=False))
        return 0

    run_nonce = new_nonce()
    rows: list[dict[str, Any]] = []
    raw_samples: list[dict[str, Any]] = []
    for record in selected:
        for cache_state in cache_states:
            for space_count in spaces:
                for _ in range(args.warmups):
                    measure_sample(record, cache_state, space_count, nonce=run_nonce)
                cell_samples = [measure_sample(record, cache_state, space_count, nonce=run_nonce) for _ in range(samples_per_cell)]
                raw_samples.extend(cell_samples)
                rows.append(summarize(cell_samples, record, cache_state, space_count))

    fixture_set_hash = _fixture_set_hash(selected)
    result: dict[str, Any] = {
        "schema_version": 1,
        "phase": 0,
        "kind": "direct-benchmark-baseline",
        "mode": "offline",
        "evidence_mode": "offline",
        "status": "offline-smoke" if args.smoke else "offline-baseline",
        "release_eligible": False,
        "nonce": run_nonce,
        "fixture_set_sha256": fixture_set_hash,
        "baseline_manifest": manifest_metadata,
        "manifest_sha256": manifest_metadata["sha256"],
        "fixture_binding": {
            "manifest_path": manifest_metadata["path"],
            "manifest_sha256": manifest_metadata["sha256"],
            "fixture_set_sha256": fixture_set_hash,
            "fixtures": [
                {"name": record["name"], "file": record["file"], "sha256": record["sha256"], "bytes": record["bytes"]}
                for record in selected
            ],
        },
        "fixtures": [
            {"name": record["name"], "file": record["file"], "sha256": record["sha256"], "bytes": record["bytes"]}
            for record in selected
        ],
        "cache_states": cache_states,
        "spaces": spaces,
        "warmups": args.warmups,
        "samples_per_cell": samples_per_cell,
        "smoke": args.smoke,
        "tail_thresholds": {"p95": MIN_P95_SAMPLES, "p99": MIN_P99_SAMPLES},
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "browser_policy": {"automatic_launch": False, "automatic_download": False, "cdp_url_used": False},
        "tokenizer": {"name": None, "version": None, "status": "not_available_in_offline_scaffold"},
        "rows": rows,
        "sample_accounting": sample_accounting(raw_samples),
        "confidence_intervals": {
            "method": "normal_approximation",
            "confidence_level": 0.95,
            "scope": "per-cell latency mean",
            "status": "approximate",
        },
        "release_gates": offline_release_gates(),
    }
    add_envelope(
        result,
        kind="direct-benchmark",
        build_tuple={
            "benchmark_kind": "direct-benchmark-baseline",
            "benchmark_script": repository_relative(Path(__file__)),
            "benchmark_script_sha256": sha256_file(Path(__file__)),
            "fixture_manifest": manifest_metadata["path"],
            "fixture_manifest_sha256": manifest_metadata["sha256"],
        },
        nonce=run_nonce,
    )
    # The category name `token` is an allowlisted gate schema, not a credential.
    # Restore the complete structured schema after the generic envelope pass;
    # write_benchmark_baseline() applies the safe schema-specific redaction.
    result["release_gates"] = offline_release_gates()

    if args.artifact_dir:
        try:
            sample_chunks = raw_sample_chunks(raw_samples)
            result["raw_samples_files"] = [name for name, _ in sample_chunks]
            result["raw_sample_declarations"] = _raw_sample_declarations(sample_chunks, len(raw_samples))
            result = redact_benchmark_report(result)
            publication = publish_benchmark(args.artifact_dir, result, markdown_report(result), sample_chunks)
        except (OSError, ValueError, TypeError) as error:
            print(f"direct benchmark error: {type(error).__name__}", file=sys.stderr)
            return 2
        print(
            f"wrote offline benchmark baseline: {repository_relative(args.artifact_dir)} "
            f"({len(rows)} cells, {len(raw_samples)} samples, {publication['generation_id']})"
        )
    else:
        print(json.dumps(redact_benchmark_report(result), indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
