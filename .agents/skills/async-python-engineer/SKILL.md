---
name: async-python-engineer
description: >
  Use for designing or implementing async Python patterns in traverse: asyncio task management,
  event-driven architecture with bubus EventBus, structured concurrency, cancellation, error
  propagation from background tasks, and async lifecycle management. Trigger when the user asks
  how to run something concurrently, how to handle task errors without crashing the session, how
  to wire up event bus subscribers, or how to coordinate async startup/shutdown. Do not use for
  CDP protocol specifics or Pydantic model design.
when_to_use: >
  Especially useful for asyncio.Task lifecycle, bubus event bus publish/subscribe wiring,
  background task error handling, structured async startup/shutdown, and safe concurrency
  within the browser session and watchdog architecture.
metadata:
  version: "0.1.0"
  category: async-python
  tags: [asyncio, async, bubus, event-bus, tasks, concurrency, lifecycle, watchdogs, error-handling]
license: Proprietary
---

# Async Python Engineer

Write async Python that fails loudly, cleans up correctly, and never swallows errors in
background tasks. The session lifecycle is the unit of correctness.

## Core Rules

- Use `create_task_with_error_handling(coro, ...)` from `traverse.utils` for all fire-and-forget tasks.
- Never use raw `asyncio.create_task(coro)` without attaching an error callback — uncaught exceptions in tasks are silent by default.
- Use `asyncio.gather(*coros, return_exceptions=False)` when you need to fan out and want to propagate the first failure.
- Use `asyncio.gather(*coros, return_exceptions=True)` when you need all results regardless of failures.
- Prefer `async with` context managers for resource lifecycle over manual `try/finally` pairs.
- Use `asyncio.Event` or `asyncio.Queue` for inter-coroutine signaling, not bare `await asyncio.sleep(0)` polling.

## bubus EventBus Patterns

### Publishing events
```python
from bubus import BaseEvent, EventBus

class NavigationCompleteEvent(BaseEvent):
    url: str
    session_id: str

# from inside a watchdog or BrowserSession
await event_bus.emit(NavigationCompleteEvent(url=url, session_id=sid))
```

### Subscribing via watchdog naming convention
Handler methods named `on_EventTypeName` are auto-registered when a `BaseWatchdog` is
initialized with an `EventBus`. The convention replaces explicit `.subscribe(...)` calls:

```python
class MyWatchdog(BaseWatchdog):
    async def on_NavigationCompleteEvent(self, event: NavigationCompleteEvent) -> None:
        ...
```

### Subscribing manually (outside watchdog)
```python
event_bus.subscribe(NavigationCompleteEvent, handler_coroutine)
```

Use manual subscription only in `BrowserSession` itself or test fixtures. Watchdogs must use
the naming convention.

## Task Lifecycle

### Safe background tasks
```python
from traverse.utils import create_task_with_error_handling

task = create_task_with_error_handling(
    self._poll_something(),
    error_message='poll failed',
    logger=self.logger,
)
```

This wraps `asyncio.create_task` and attaches a done-callback that logs the exception and
(optionally) emits a `BrowserErrorEvent` through the bus.

### Cancellation
- Cancel tasks with `task.cancel()` and `await asyncio.shield(task)` or `try/except asyncio.CancelledError`.
- Always propagate `CancelledError` — do not swallow it.
- In `__aexit__` / cleanup paths, cancel tasks and `await` them to drain.

### Startup / shutdown ordering
```python
async def start(self) -> None:
    await self._connect_cdp()          # CDP must be up before watchdogs
    await self._init_watchdogs()       # watchdogs subscribe to bus
    await self.event_bus.emit(BrowserStartEvent(...))

async def stop(self) -> None:
    await self.event_bus.emit(BrowserStopEvent(...))
    await self._cancel_background_tasks()
    await self._disconnect_cdp()
```

Emit stop events before cancelling tasks so watchdogs can flush state.

## Structured Concurrency Patterns

- Prefer `asyncio.TaskGroup` (Python 3.11+) for grouped tasks that should all succeed or all cancel.
- Use `asyncio.timeout(seconds)` or `asyncio.wait_for(coro, timeout=seconds)` for bounded waits.
- Never use bare `asyncio.sleep(long_timeout)` as a guard — use `asyncio.wait_for`.
- For producer/consumer fan-out, use `asyncio.Queue` with explicit maxsize.

## Output Format

Return:
1. concurrency model choice (gather vs TaskGroup vs Queue vs Event)
2. task error handling strategy
3. event bus wiring (emit vs subscribe points)
4. startup/shutdown ordering
5. cancellation safety
6. rejected alternatives

## Anti-Patterns

- `asyncio.create_task(coro)` without a done-callback (silent exceptions)
- `await asyncio.sleep(0)` as an inter-coroutine signaling mechanism
- swallowing `CancelledError` in cleanup paths
- mutating shared state from multiple concurrent tasks without an asyncio Lock
- using `loop.run_until_complete(...)` inside an already-running event loop
- emitting events after CDP disconnect (triggers cascading errors in watchdogs)

## References

- `references/async-patterns.md`
- `references/eval-rubric.md`
