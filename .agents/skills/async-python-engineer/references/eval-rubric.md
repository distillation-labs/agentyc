# Eval Rubric — Async Python Engineer

## Triggering (routing)
- Skill loads for asyncio task management, bubus EventBus wiring, concurrency patterns, cancellation, and browser session lifecycle.
- Skill does not load for CDP protocol specifics, Pydantic model design, or generic Python refactors.

## Async Correctness
- Response uses `create_task_with_error_handling` for fire-and-forget tasks.
- Response never recommends raw `asyncio.create_task` without an error callback.
- `CancelledError` is propagated, not swallowed.
- `asyncio.wait_for` is used for bounded waits.

## EventBus Wiring
- Response correctly identifies emit vs. subscribe points.
- Watchdog handler naming convention (`on_EventTypeName`) is used.
- Manual subscription is only recommended for non-watchdog components.

## Output Quality
- Response includes concurrency model choice with rationale.
- Startup/shutdown ordering is addressed when lifecycle is involved.
- Anti-patterns are flagged where relevant.
