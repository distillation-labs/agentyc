# Phase 4: CrashWatchdog Safe Integration

## Goal

Attach `CrashWatchdog` to the real browser session lifecycle so page crashes and renderer failures are surfaced explicitly, handled safely, and cleaned up correctly across reconnects and teardown.

## Why This Phase Exists Now

The repository already contains `CrashWatchdog`, but the roadmap context is explicit that it exists without being fully attached. After non-goal layers are removed, watchdog integration becomes less risky because the active session path is narrower and easier to reason about. This is the right point to convert an inert safety mechanism into a real runtime guarantee.

## Repo-Specific Context

- Safe-attach work needs to wire `CrashWatchdog` into `BrowserSession.attach_all_watchdogs()`.
- The watchdog must attach to both existing page targets and future page targets.
- Its scope should stay limited to crash and renderer health, not duplicate unrelated monitoring such as network logging.
- Listener and task cleanup on reconnect, detach, and teardown is a required part of the phase, not optional polish.
- The main files involved are `agentyc/browser/session.py`, `agentyc/browser/watchdogs/crash_watchdog.py`, and likely `agentyc/browser/session_manager.py`.

## In Scope

- Wire `CrashWatchdog` into the active browser session lifecycle.
- Attach to all current page targets at startup and to future targets as they appear.
- Define clear ownership for watchdog-created listeners, subscriptions, and background tasks.
- Cleanly tear down and reattach across reconnects and shutdown.
- Keep the watchdog focused on crash/renderer health signals.

## Out Of Scope

- General-purpose event logging or analytics.
- Duplicating monitoring responsibilities already implemented elsewhere.
- Broad session architecture refactors beyond what is needed for safe watchdog attachment.
- Shared-browser UX affordances unrelated to crash behavior.

## Dependencies / Prerequisites

- Phase 3 deletion work complete enough that the active session lifecycle is not split across retired layers.
- Clear understanding of how `BrowserSession` enumerates targets and reacts to new pages.
- Test coverage or reproducible validation hooks for crash and reconnect behavior.

## Key Modules / Files To Touch

- `agentyc/browser/session.py`
- `agentyc/browser/watchdogs/crash_watchdog.py`
- `agentyc/browser/session_manager.py`
- Related browser event/subscription utilities used by attach/detach logic
- Tests covering crash, reconnect, and teardown behavior

## Implementation Workstreams

### Session attach wiring

Modify `BrowserSession.attach_all_watchdogs()` so `CrashWatchdog` is instantiated and attached as part of normal session setup.

### Target coverage

Ensure the watchdog attaches to already-open page targets and subscribes to future targets created after the session starts.

### Cleanup correctness

Track and dispose listeners, tasks, and target bindings during reconnect, detach, and shutdown. Prevent duplicate subscription buildup after repeated attach cycles.

### Responsibility narrowing

Review `crash_watchdog.py` so it focuses on crash and renderer health semantics rather than drifting into unrelated observability concerns.

## Task Checklist

- [ ] Wire `CrashWatchdog` into `BrowserSession.attach_all_watchdogs()`.
- [ ] Attach the watchdog to existing page targets during session startup.
- [ ] Attach the watchdog to future page targets as they are created.
- [ ] Ensure non-page targets are ignored unless explicitly justified.
- [ ] Define where watchdog instances and per-target listener handles are stored.
- [ ] Ensure reconnect logic tears down old watchdog listeners before reattaching.
- [ ] Ensure session teardown cleans up watchdog listeners and background tasks.
- [ ] Ensure partial target detaches do not leave orphan task or subscription state.
- [ ] Keep watchdog logic scoped to crash/renderer health rather than duplicating unrelated monitoring.
- [ ] Add or update tests that simulate crash, reconnect, and teardown scenarios.

## Validation / Verification Checklist

- [ ] A normal browser session activates `CrashWatchdog` without extra manual steps.
- [ ] Existing open pages are covered, not just newly created ones.
- [ ] A reconnect cycle does not accumulate duplicate listeners or tasks.
- [ ] Shutdown leaves no lingering watchdog activity.
- [ ] Crash or renderer-failure scenarios surface explicit failure or recovery behavior rather than silent hangs.
- [ ] Watchdog scope remains narrow and does not overlap unrelated instrumentation.

## Deliverables / Artifacts

- Active `CrashWatchdog` integration in the browser session lifecycle.
- Tests or reproducible validation scripts for crash and reconnect safety.
- Cleaner watchdog responsibility boundaries.
- Session lifecycle notes that explain attach, detach, reconnect, and teardown semantics.

## Risks / Tradeoffs

- Aggressive health checks can create false positives on heavy or slow pages.
- Under-scoped cleanup can produce subtle leaks that only appear after repeated reconnect cycles.
- Folding too much unrelated logic into the watchdog would make future debugging harder.

## Exit Criteria

- `CrashWatchdog` is attached during normal session operation.
- Crash and renderer-failure behavior is explicit and testable.
- Reconnect and teardown paths are free of duplicate-listener or orphan-task accumulation.

## Notes For Docs / Public Communication

- Public docs should describe the user-visible effect in practical terms: browser crashes fail explicitly and recover predictably.
- Internal architecture docs should note that crash monitoring is attached and scoped narrowly, not presented as a generic telemetry layer.
