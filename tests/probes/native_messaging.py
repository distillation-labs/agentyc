#!/usr/bin/env python3
"""Offline Native Messaging framing and policy probe.

This module deliberately has no third-party dependencies. It models the Chrome
Native Messaging 32-bit little-endian length prefix and applies a stricter
bounded envelope policy before any message can reach a broker.
"""

from __future__ import annotations

import json
import struct
import unittest
from dataclasses import dataclass
from enum import Enum
from typing import Any

MAX_FRAME_BYTES = 1024 * 1024
MAX_CHUNK_BYTES = 64 * 1024
MAX_ENVELOPE_BYTES = 64 * 1024
MAX_ASSEMBLY_BYTES = 64 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_CUMULATIVE_FRAME_BYTES = 4 * MAX_FRAME_BYTES
MAX_IN_FLIGHT_BYTES = 2 * 1024 * 1024
SUPPORTED_VERSION = 1
DEFAULT_ORIGIN = "chrome-extension://p0probe"


class RejectCode(str, Enum):
    OVERSIZE = "oversize"
    TRUNCATED = "truncated"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_JSON = "invalid_json"
    WRONG_ORIGIN = "wrong_origin"
    REPLAY = "replay"
    UNSUPPORTED_VERSION = "unsupported_version"
    INVALID_ENVELOPE = "invalid_envelope"
    BUDGET = "budget_exceeded"


class ProtocolError(ValueError):
    def __init__(self, code: RejectCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail or code.value
        super().__init__(f"{code.value}: {self.detail}")


@dataclass(frozen=True)
class Frame:
    payload: bytes

    @property
    def size(self) -> int:
        return len(self.payload)


class FrameDecoder:
    """Incremental decoder that never allocates beyond the declared bound."""

    def __init__(self, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        self.max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()
        self._declared: int | None = None
        self.total_payload_bytes = 0
        self.total_wire_bytes = 0

    def feed(self, chunk: bytes) -> list[Frame]:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("chunk must be bytes-like")
        incoming = bytes(chunk)
        if len(incoming) > MAX_CHUNK_BYTES:
            raise ProtocolError(RejectCode.BUDGET, "chunk exceeds the read bound")
        self.total_wire_bytes += len(incoming)
        if self.total_wire_bytes > MAX_CUMULATIVE_FRAME_BYTES:
            raise ProtocolError(RejectCode.BUDGET, "cumulative frame budget exceeded")
        if len(self._buffer) + len(incoming) > self.max_frame_bytes + 4:
            raise ProtocolError(RejectCode.OVERSIZE, "input buffer exceeds one frame")
        self._buffer.extend(incoming)
        frames: list[Frame] = []
        while True:
            if self._declared is None:
                if len(self._buffer) < 4:
                    break
                self._declared = struct.unpack_from("<I", self._buffer)[0]
                del self._buffer[:4]
                if self._declared > self.max_frame_bytes:
                    raise ProtocolError(RejectCode.OVERSIZE, "declared frame is above the bound")
            if len(self._buffer) < self._declared:
                break
            payload = bytes(self._buffer[: self._declared])
            del self._buffer[: self._declared]
            self._declared = None
            self.total_payload_bytes += len(payload)
            frames.append(Frame(payload))
        return frames

    def finish(self) -> str:
        if self._declared is not None or self._buffer:
            raise ProtocolError(RejectCode.TRUNCATED, "EOF arrived inside a frame")
        return "clean_eof"


def encode_frame(payload: bytes) -> bytes:
    if len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError(RejectCode.OVERSIZE, "cannot encode an oversized frame")
    return struct.pack("<I", len(payload)) + payload


def encode_json(envelope: dict[str, Any]) -> bytes:
    payload = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(payload) > MAX_ENVELOPE_BYTES:
        raise ProtocolError(RejectCode.OVERSIZE, "envelope exceeds the control-message bound")
    return encode_frame(payload)


class BrokerRegistry:
    """A process-local singleton registry used to prove reconnect idempotence."""

    def __init__(self) -> None:
        self._brokers: dict[str, str] = {}

    def connect(self, broker_key: str) -> str:
        if broker_key not in self._brokers:
            self._brokers[broker_key] = f"broker-{len(self._brokers) + 1}"
        return self._brokers[broker_key]

    @property
    def broker_count(self) -> int:
        return len(self._brokers)


class EnvelopeSession:
    def __init__(self, expected_origin: str = DEFAULT_ORIGIN, registry: BrokerRegistry | None = None) -> None:
        self.expected_origin = expected_origin
        self.registry = registry or BrokerRegistry()
        self.broker_id = self.registry.connect("p0")
        self._nonce: str | None = None
        self._seen_message_ids: set[str] = set()
        self.accepted_count = 0
        self.in_flight_bytes = 0
        self.artifact_bytes = 0

    def accept(self, frame: Frame) -> dict[str, Any]:
        if frame.size > MAX_ENVELOPE_BYTES:
            raise ProtocolError(RejectCode.OVERSIZE, "control envelope exceeds the bound")
        self.in_flight_bytes += frame.size
        if self.in_flight_bytes > MAX_IN_FLIGHT_BYTES:
            raise ProtocolError(RejectCode.BUDGET, "in-flight byte budget exceeded")
        try:
            try:
                text = frame.payload.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ProtocolError(RejectCode.INVALID_UTF8, str(error)) from error
            try:
                envelope = json.loads(text)
            except json.JSONDecodeError as error:
                raise ProtocolError(RejectCode.INVALID_JSON, str(error)) from error
            if not isinstance(envelope, dict):
                raise ProtocolError(RejectCode.INVALID_ENVELOPE, "envelope must be an object")
            if envelope.get("version") != SUPPORTED_VERSION:
                raise ProtocolError(RejectCode.UNSUPPORTED_VERSION, "unsupported protocol version")
            if envelope.get("origin") != self.expected_origin:
                raise ProtocolError(RejectCode.WRONG_ORIGIN, "origin is not exactly allowlisted")
            message_id = envelope.get("message_id")
            nonce = envelope.get("nonce")
            if not isinstance(message_id, str) or not message_id or len(message_id) > 128:
                raise ProtocolError(RejectCode.INVALID_ENVELOPE, "invalid message_id")
            if message_id in self._seen_message_ids:
                raise ProtocolError(RejectCode.REPLAY, "message_id was already accepted")
            if not isinstance(nonce, str) or not nonce or len(nonce) > 128:
                raise ProtocolError(RejectCode.INVALID_ENVELOPE, "invalid nonce")
            if envelope.get("kind") == "hello":
                if self._nonce is not None:
                    raise ProtocolError(RejectCode.REPLAY, "second hello is a replay")
                self._nonce = nonce
            elif self._nonce != nonce:
                raise ProtocolError(RejectCode.REPLAY, "nonce is not bound to this session")
            if "payload" not in envelope or not isinstance(envelope["payload"], dict):
                raise ProtocolError(RejectCode.INVALID_ENVELOPE, "payload must be an object")
            assembly_bytes = envelope["payload"].get("assembly_bytes", frame.size)
            artifact_bytes = envelope["payload"].get("artifact_bytes", 0)
            if not isinstance(assembly_bytes, int) or assembly_bytes < 0 or assembly_bytes > MAX_ASSEMBLY_BYTES:
                raise ProtocolError(RejectCode.BUDGET, "assembly budget exceeded")
            if not isinstance(artifact_bytes, int) or artifact_bytes < 0 or self.artifact_bytes + artifact_bytes > MAX_ARTIFACT_BYTES:
                raise ProtocolError(RejectCode.BUDGET, "artifact budget exceeded")
            self.artifact_bytes += artifact_bytes
            self._seen_message_ids.add(message_id)
            self.accepted_count += 1
            return envelope
        finally:
            self.in_flight_bytes -= frame.size


def classify_disconnect(*, parser: FrameDecoder, host_alive: bool) -> str:
    """Differentiate a clean pipe close from a live host that vanished mid-frame."""
    try:
        parser.finish()
    except ProtocolError as error:
        if error.code is RejectCode.TRUNCATED and not host_alive:
            return "host_crash_mid_frame"
        raise
    return "clean_eof" if host_alive else "host_crash"


def _envelope(message_id: str, nonce: str = "n-1", *, origin: str = DEFAULT_ORIGIN, version: int = 1, kind: str = "probe") -> dict[str, Any]:
    return {
        "version": version,
        "origin": origin,
        "message_id": message_id,
        "nonce": nonce,
        "kind": kind,
        "payload": {"fixture": "offline"},
    }


def run_deterministic_suite() -> dict[str, Any]:
    cases: dict[str, str] = {}
    session = EnvelopeSession()
    hello = encode_json(_envelope("m-hello", kind="hello"))
    probe = encode_json(_envelope("m-probe"))

    decoder = FrameDecoder()
    decoded: list[Frame] = []
    for byte in hello:
        decoded.extend(decoder.feed(bytes([byte])))
    session.accept(decoded[0])
    cases["fragmented_frame"] = "passed"
    session.accept(Frame(probe[4:]))

    def rejected(name: str, action: Any, expected: RejectCode) -> None:
        try:
            action()
        except ProtocolError as error:
            if error.code is not expected:
                raise AssertionError(f"{name}: expected {expected}, got {error.code}") from error
            cases[name] = f"rejected:{error.code.value}"
        else:
            raise AssertionError(f"{name}: accepted malformed input")

    partial_header = FrameDecoder()
    partial_header.feed(b"\x05\x00")
    rejected("truncated_frame", partial_header.finish, RejectCode.TRUNCATED)
    truncated = FrameDecoder()
    truncated.feed(b"\x05\x00\x00\x00ab")
    rejected("truncated_payload", truncated.finish, RejectCode.TRUNCATED)
    invalid_utf8 = Frame(b"\xff\xfe")
    rejected("invalid_utf8", lambda: EnvelopeSession().accept(invalid_utf8), RejectCode.INVALID_UTF8)
    rejected("invalid_json", lambda: EnvelopeSession().accept(Frame(b"not-json")), RejectCode.INVALID_JSON)
    wrong_origin_payload = encode_json(_envelope("wrong", origin="chrome-extension://other/"))[4:]
    rejected("wrong_origin", lambda: EnvelopeSession().accept(Frame(wrong_origin_payload)), RejectCode.WRONG_ORIGIN)
    # Keep the replay and version cases on a clean session so the case itself is isolated.
    replay_session = EnvelopeSession()
    replay_payload = encode_json(_envelope("replay", kind="hello"))[4:]
    replay_session.accept(Frame(replay_payload))
    rejected("replayed_message", lambda: replay_session.accept(Frame(replay_payload)), RejectCode.REPLAY)
    rejected("unsupported_version", lambda: EnvelopeSession().accept(Frame(encode_json(_envelope("v2", version=2))[4:])), RejectCode.UNSUPPORTED_VERSION)
    oversized_header = struct.pack("<I", MAX_FRAME_BYTES + 1)
    rejected("oversized_frame", lambda: FrameDecoder().feed(oversized_header), RejectCode.OVERSIZE)
    rejected("oversized_envelope", lambda: EnvelopeSession().accept(Frame(b"{" + b"x" * MAX_ENVELOPE_BYTES + b"}")), RejectCode.OVERSIZE)

    clean = FrameDecoder()
    cases["clean_eof"] = classify_disconnect(parser=clean, host_alive=True)
    crashed = FrameDecoder()
    crashed.feed(b"\x08\x00\x00\x00partial")
    cases["host_crash"] = classify_disconnect(parser=crashed, host_alive=False)
    registry = BrokerRegistry()
    first = EnvelopeSession(registry=registry).broker_id
    second = EnvelopeSession(registry=registry).broker_id
    if first != second or registry.broker_count != 1:
        raise AssertionError("reconnect created a second broker")
    cases["reconnect_single_broker"] = "passed"
    return {
        "status": "passed",
        "limits": {
            "max_frame_bytes": MAX_FRAME_BYTES,
            "max_chunk_bytes": MAX_CHUNK_BYTES,
            "max_envelope_bytes": MAX_ENVELOPE_BYTES,
            "max_assembly_bytes": MAX_ASSEMBLY_BYTES,
            "max_artifact_bytes": MAX_ARTIFACT_BYTES,
            "max_cumulative_frame_bytes": MAX_CUMULATIVE_FRAME_BYTES,
            "max_in_flight_bytes": MAX_IN_FLIGHT_BYTES,
        },
        "cases": cases,
        "accepted_messages": session.accepted_count,
        "notes": ["offline deterministic; no Chrome or native host launch"],
    }


class NativeMessagingTests(unittest.TestCase):
    def test_suite(self) -> None:
        self.assertEqual(run_deterministic_suite()["status"], "passed")

    def test_frame_encoding_is_little_endian(self) -> None:
        self.assertEqual(encode_frame(b"abc"), b"\x03\x00\x00\x00abc")

    def test_wrong_origin_is_rejected_before_acceptance(self) -> None:
        session = EnvelopeSession()
        payload = encode_json(_envelope("wrong", origin="chrome-extension://other/"))[4:]
        with self.assertRaises(ProtocolError) as raised:
            session.accept(Frame(payload))
        self.assertEqual(raised.exception.code, RejectCode.WRONG_ORIGIN)
        self.assertEqual(session.accepted_count, 0)


if __name__ == "__main__":
    unittest.main()
