# CDP Browser Patterns

Grounded in cdp-use typed client, BrowserSession, and the watchdog architecture.

## What Good Looks Like

- `cdp_client.send.Domain.method(params=TypedParams(...), session_id=sid)` for all commands.
- `cdp_client.register.Domain.Event(callback)` for all event subscriptions — never `cdp_client.on(...)`.
- Session IDs are always obtained from a `Target` object, never guessed or hardcoded.
- Watchdogs declare `LISTENS_TO` and `EMITS` class vars with concrete event types.
- Watchdog handlers are named `on_EventTypeName` and receive a typed event argument.
- Shared state across watchdogs lives on `BrowserSession`, never on an individual watchdog.
- DOM extraction goes through `DomService`, not raw CDP calls from outside the service.
- `BrowserSession.event_bus` is the single pub/sub backbone; components are decoupled through it.

## What To Avoid

- calling `cdp_client.on(...)` — the method does not exist on cdp-use's client
- issuing DOM or target commands without the correct `session_id` scope
- creating a second `CDPClient` inside a watchdog instead of reusing `browser_session.cdp_client`
- mutating `BrowserSession` state directly from inside a watchdog (use events)
- calling raw CDP highlight commands instead of going through `DomService`
- registering the same event handler twice without a deregistration path
