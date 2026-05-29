# Eval Rubric — Async Python Engineer

## Pass when the skill:

- loads for asyncio task management, bubus EventBus wiring, concurrency patterns, cancellation, and browser session lifecycle
- does not load for CDP protocol specifics, Pydantic model design, or generic Python refactors
- uses `create_task_with_error_handling` for fire-and-forget tasks
- never recommends raw `asyncio.create_task` without an error callback
- propagates `CancelledError` instead of swallowing it
- uses timeout-aware waiting patterns
- identifies emit vs. subscribe points correctly
- uses the watchdog naming convention for watchdog handlers
- addresses startup and shutdown ordering when lifecycle is involved

## Fail when the skill:

- recommends silent background tasks
- uses sleep-based polling where signals or queues should be used
- ignores cancellation semantics
- mixes watchdog subscription guidance with ad hoc direct coupling
- omits lifecycle ordering on tasks that clearly need startup or teardown sequencing
