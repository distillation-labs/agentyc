#!/usr/bin/env python3
"""Replay a bounded Phase 0 JSON trace without network or wall-clock access."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

_HERE = Path(__file__).resolve().parent
_HARNESS = _HERE.parent / "harness"
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

from deterministic import (  # noqa: E402
    DeterministicScheduler,
    Redactor,
    VirtualClock,
    stable_json,
)

_MAX_EVENTS = 10_000
_MAX_TRACE_BYTES = 4 * 1024 * 1024


class ReplayError(ValueError):
    """A user trace is invalid or exceeds the replay safety budget."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReplayError(f"{name} must be an object")
    return value


def load_trace(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise ReplayError("trace file is missing")
    if path.stat().st_size > _MAX_TRACE_BYTES:
        raise ReplayError("trace exceeds the size limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, json.JSONDecodeError):
            raise ReplayError(f"trace JSON is invalid at character {exc.pos}") from None
        raise ReplayError("trace could not be read") from None
    return _validate_trace(value)


def _validate_trace(value: Any) -> Mapping[str, Any]:
    trace = _mapping(value, "trace")
    if trace.get("schema_version") != 1:
        raise ReplayError("schema_version must be 1")
    seed = trace.get("seed", 0)
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ReplayError("seed must be a non-negative integer")
    events = trace.get("events")
    if not isinstance(events, list):
        raise ReplayError("events must be a list")
    if len(events) > _MAX_EVENTS:
        raise ReplayError("event limit exceeded")
    previous_at = 0
    for index, raw_event in enumerate(events):
        event = _mapping(raw_event, f"event {index}")
        at_ms = event.get("at_ms")
        if not isinstance(at_ms, int) or isinstance(at_ms, bool) or at_ms < previous_at:
            raise ReplayError("event times must be non-negative and ordered")
        kind = event.get("kind")
        if not isinstance(kind, str) or not kind or len(kind) > 128:
            raise ReplayError("event kind must be a bounded non-empty string")
        if "payload" in event and len(stable_json(event["payload"])) > 256 * 1024:
            raise ReplayError("event payload exceeds the size limit")
        previous_at = at_ms
    return trace


def replay(trace: Mapping[str, Any]) -> Mapping[str, Any]:
    trace = _validate_trace(trace)
    clock = VirtualClock()
    scheduler = DeterministicScheduler(clock, max_pending=_MAX_EVENTS)
    redactor = Redactor()
    emitted: list[dict[str, Any]] = []
    previous_at = 0

    for raw_event in trace["events"]:
        event = _mapping(raw_event, "event")
        at_ms = event["at_ms"]
        delay_ms = at_ms - previous_at
        previous_at = at_ms

        def emit(event: Mapping[str, Any] = event) -> None:
            emitted.append(
                redactor.redact(
                    {
                        "at_ms": clock.now_ms,
                        "kind": event["kind"],
                        "payload": event.get("payload", {}),
                    }
                )
            )

        scheduler.schedule(delay_ms, emit, label=str(event["kind"]))

    scheduler.run_until_idle(max_steps=_MAX_EVENTS)
    return {
        "schema_version": 1,
        "seed": trace["seed"],
        "event_count": len(emitted),
        "events": emitted,
    }


def _self_test() -> Mapping[str, Any]:
    return replay(
        {
            "schema_version": 1,
            "seed": 0,
            "events": [
                {"at_ms": 0, "kind": "started", "payload": {"space_id": "research"}},
                {"at_ms": 5, "kind": "fault", "payload": {"token": "hidden"}},
            ],
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", nargs="?", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test and args.trace is not None:
        parser.error("choose a trace or --self-test")
    if not args.self_test and args.trace is None:
        parser.error("a trace path or --self-test is required")
    try:
        result = _self_test() if args.self_test else replay(load_trace(args.trace))
    except ReplayError as exc:
        print(f"replay failed: {exc}", file=sys.stderr)
        return 2
    print(stable_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
