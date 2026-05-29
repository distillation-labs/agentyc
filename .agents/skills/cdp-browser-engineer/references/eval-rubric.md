# Eval Rubric — CDP Browser Engineer

## Pass when the skill:

- loads for questions about CDP commands, cdp-use client API, BrowserSession, watchdog design, DOM extraction, or element highlighting
- does not load for MCP server design, LLM prompt work, or generic Python refactors
- uses `cdp_client.send.Domain.method(params=...)` syntax
- uses `cdp_client.register.Domain.Event(callback)` for subscriptions instead of `cdp_client.on(...)`
- gets `session_id` scoping correct
- places shared state on `BrowserSession` instead of individual watchdogs
- defines watchdogs with `BaseWatchdog`, `LISTENS_TO`, `EMITS`, and `on_EventTypeName` handlers
- explains CDP domain and method choice with rationale

## Fail when the skill:

- recommends the wrong event-registration API
- ignores root vs target session boundaries
- mutates shared browser state from watchdog-local ad hoc state
- bypasses DomService for behavior the service should own
- omits the protocol-scoping explanation on target-sensitive operations
