# Async Python Patterns

Grounded in traverse's asyncio usage, bubus EventBus, and watchdog lifecycle.

## What Good Looks Like

- `create_task_with_error_handling(coro, ...)` wraps every fire-and-forget task with error logging.
- `asyncio.gather(*coros, return_exceptions=False)` propagates the first failure cleanly.
- `asyncio.wait_for(coro, timeout=...)` bounds every external wait.
- `asyncio.Event` or `asyncio.Queue` signals between coroutines — not polling sleep loops.
- `async with` context managers own resource acquisition and release.
- Event bus emits happen before task cancellation in shutdown sequences.
- `asyncio.TaskGroup` groups tasks that should all cancel if any one fails (Python 3.11+).
- `CancelledError` is always re-raised after cleanup — never swallowed.

## What To Avoid

- `asyncio.create_task(coro)` without a done-callback
- `await asyncio.sleep(0)` as an inter-task signaling mechanism
- catching and ignoring `CancelledError`
- calling `loop.run_until_complete(...)` inside a running event loop
- emitting bus events after CDP disconnect (cascades errors in subscribed watchdogs)
- mutable shared state accessed from multiple concurrent tasks without an `asyncio.Lock`
