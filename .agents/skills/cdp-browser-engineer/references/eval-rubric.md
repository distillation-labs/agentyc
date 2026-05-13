# Eval Rubric — CDP Browser Engineer

## Triggering (routing)
- Skill loads for questions about CDP commands, cdp-use client API, BrowserSession, watchdog design, DOM extraction, or element highlighting.
- Skill does not load for MCP server design, LLM prompt work, or generic Python refactors.

## CDP API Correctness
- Response uses `cdp_client.send.Domain.method(params=...)` syntax.
- Response uses `cdp_client.register.Domain.Event(callback)` for subscriptions — never `cdp_client.on(...)`.
- `session_id` scoping is correct (root vs. target-scoped).

## Watchdog Design
- New watchdog extends `BaseWatchdog`.
- `LISTENS_TO` and `EMITS` class vars are declared with concrete event types.
- Handler methods follow `on_EventTypeName` naming convention.
- Shared state placed on `BrowserSession`, not on the watchdog.

## Output Quality
- Response includes CDP domain/method choice with rationale.
- Response addresses session_id scoping.
- Anti-patterns are identified where relevant.
