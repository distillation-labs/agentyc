# Phase 0 replay

`replay.py` replays a JSON trace using the deterministic harness. Replay is
offline, bounded, ordered by virtual time, and emits only redacted JSON.

```bash
python3 tests/replay/replay.py --self-test
python3 tests/replay/replay.py path/to/trace.json
```

Trace schema: `{"schema_version": 1, "seed": 0, "events": [{"at_ms": 0,
"kind": "name", "payload": {}}]}`. Event times must be non-negative and
non-decreasing. Secret fields and raw browser IDs are never emitted.
