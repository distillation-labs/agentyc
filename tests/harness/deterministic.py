"""Small deterministic primitives for Phase 0 probes and replay tests.

This module deliberately has no wall-clock, thread, network, browser, or random
source dependencies. Callers own the inputs and can replay them byte-for-byte.
"""

from __future__ import annotations

import heapq
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


class VirtualClock:
    """A monotonic millisecond clock advanced only by the test harness."""

    def __init__(self, start_ms: int = 0) -> None:
        if not isinstance(start_ms, int) or isinstance(start_ms, bool) or start_ms < 0:
            raise ValueError("start_ms must be a non-negative integer")
        self._now_ms = start_ms

    @property
    def now_ms(self) -> int:
        return self._now_ms

    def advance_to(self, target_ms: int) -> None:
        if not isinstance(target_ms, int) or isinstance(target_ms, bool):
            raise TypeError("target_ms must be an integer")
        if target_ms < self._now_ms:
            raise ValueError("virtual time cannot move backwards")
        self._now_ms = target_ms

    def advance_by(self, duration_ms: int) -> None:
        if not isinstance(duration_ms, int) or isinstance(duration_ms, bool):
            raise TypeError("duration_ms must be an integer")
        if duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        self._now_ms += duration_ms


@dataclass(order=True)
class _Scheduled:
    due_ms: int
    sequence: int
    callback: Callable[[], Any] = field(compare=False)
    label: str = field(compare=False)


class DeterministicScheduler:
    """A bounded FIFO scheduler ordered by virtual due time and insertion order."""

    def __init__(self, clock: VirtualClock | None = None, max_pending: int = 10_000) -> None:
        if not isinstance(max_pending, int) or isinstance(max_pending, bool) or max_pending <= 0:
            raise ValueError("max_pending must be a positive integer")
        self.clock = clock or VirtualClock()
        self.max_pending = max_pending
        self._sequence = 0
        self._queue: list[_Scheduled] = []

    def schedule(self, delay_ms: int, callback: Callable[[], Any], label: str = "task") -> None:
        if not isinstance(delay_ms, int) or isinstance(delay_ms, bool) or delay_ms < 0:
            raise ValueError("delay_ms must be a non-negative integer")
        if not callable(callback):
            raise TypeError("callback must be callable")
        if not isinstance(label, str) or not label or len(label) > 128:
            raise ValueError("label must be a non-empty bounded string")
        if len(self._queue) >= self.max_pending:
            raise RuntimeError("scheduler pending-task limit exceeded")
        self._sequence += 1
        heapq.heappush(
            self._queue,
            _Scheduled(self.clock.now_ms + delay_ms, self._sequence, callback, label),
        )

    def run_next(self) -> str | None:
        if not self._queue:
            return None
        task = heapq.heappop(self._queue)
        self.clock.advance_to(task.due_ms)
        task.callback()
        return task.label

    def run_until_idle(self, max_steps: int | None = None) -> int:
        limit = self.max_pending if max_steps is None else max_steps
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise ValueError("max_steps must be a non-negative integer")
        steps = 0
        while self._queue:
            if steps >= limit:
                raise RuntimeError("scheduler step limit exceeded")
            self.run_next()
            steps += 1
        return steps


class Redactor:
    """Recursively remove secret and raw-browser identity fields from output."""

    _secret_key = re.compile(
        r"(?:token|secret|password|passwd|cookie|authorization|credential|private[_-]?key|websocket[_-]?url)",
        re.IGNORECASE,
    )
    _raw_id_key = re.compile(
        r"(?:raw[_-]?id|cdp[_-]?id|backend[_-]?node[_-]?id|target[_-]?id|session[_-]?id|tab[_-]?id|group[_-]?id)",
        re.IGNORECASE,
    )
    _secret_text = re.compile(r"(?i)(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+")

    def __init__(self, max_depth: int = 20) -> None:
        if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth <= 0:
            raise ValueError("max_depth must be positive")
        self.max_depth = max_depth

    def redact(self, value: Any) -> Any:
        return self._redact(value, depth=0)

    def _redact(self, value: Any, depth: int) -> Any:
        if depth > self.max_depth:
            return "<depth-limit>"
        if isinstance(value, Mapping):
            output: dict[str, Any] = {}
            for key in sorted(value, key=lambda item: str(item)):
                name = str(key)
                if self._secret_key.search(name) or self._raw_id_key.search(name):
                    output[name] = "<redacted>"
                else:
                    output[name] = self._redact(value[key], depth + 1)
            return output
        if isinstance(value, list):
            return [self._redact(item, depth + 1) for item in value]
        if isinstance(value, tuple):
            return [self._redact(item, depth + 1) for item in value]
        if isinstance(value, str):
            return self._secret_text.sub("<redacted>", value)
        return value


def stable_json(value: Any) -> str:
    """Serialize redacted-compatible values with stable key and UTF-8 rules."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
