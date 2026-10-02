# Phase 0 browser-task-space fixtures

These pages are local, deterministic, and self-contained. They do not fetch network resources, persist credentials, or require a browser launch. `manifest.json` is the fixture contract consumed by the offline benchmark.

Modes used by the Phase 0 scripts:

- `offline`: parse these files locally; safe on every machine.
- `target`/`headed`: inspect an already running, headed, user-approved Chrome; the scripts never launch Chrome and do not require a CDP URL.
- `managed`: explicit test-browser lane; a caller must provide the executable and profile directory. The scaffolding does not download or silently launch a browser.
