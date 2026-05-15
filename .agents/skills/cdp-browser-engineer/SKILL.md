---
name: cdp-browser-engineer
description: >
  Use for building or modifying browser automation logic that touches CDP (Chrome DevTools Protocol),
  cdp-use typed client calls, BrowserSession lifecycle, Target/session management, watchdog services,
  DOM extraction, or screenshot/element-highlight flows. Trigger when the user asks how to send a CDP
  command, how to register a CDP event, how to add or modify a watchdog, how BrowserSession coordinates
  targets, or how the DOM serialization pipeline works. Do not use for MCP server design or LLM prompt work.
when_to_use: >
  Especially useful for CDP domain calls, session/target plumbing, watchdog patterns, event bus
  wiring, accessibility tree extraction, element highlighting, and browser lifecycle management.
metadata:
  version: "0.1.0"
  category: browser-automation
  tags: [cdp, cdp-use, browser-session, watchdog, bubus, dom, accessibility, targets, automation]
license: Proprietary
---

# CDP Browser Engineer

Control the browser through protocol calls, not heuristics. Every browser action is a typed CDP
command or a registered event callback. Keep session management in `BrowserSession`, not scattered
across callers.

## Core Rules

- Use `cdp_client.send.DomainName.methodName(params=...)` for all CDP commands.
- Use `cdp_client.register.DomainName.EventName(callback)` for all CDP event subscriptions — never `cdp_client.on(...)`.
- Never call CDP directly from outside `BrowserSession` or `DomService`.
- Keep watchdog logic inside a `BaseWatchdog` subclass with typed `LISTENS_TO` and `EMITS` class vars.
- Publish browser lifecycle changes through the `EventBus`; never call watchdogs directly.

## CDP-Use API Patterns

### Sending commands
```python
# Preferred: use typed parameter objects
from cdp_use.cdp.target import ActivateTargetParameters
result = await cdp_client.send.Target.activateTarget(
    params=ActivateTargetParameters(targetId=target_id)
)

# Acceptable: plain dict when no typed class exists
result = await cdp_client.send.DOMSnapshot.captureSnapshot(
    params={'computedStyles': REQUIRED_COMPUTED_STYLES},
    session_id=session_id,
)
```

### Registering events
```python
# Always use .register, not .on
cdp_client.register.Browser.downloadWillBegin(self._on_download_will_begin)
cdp_client.register.Target.targetCreated(self._on_target_created)
```

### Session IDs
- Root-level commands: omit `session_id`.
- Target-scoped commands: always pass `session_id=session_id` explicitly.
- Never guess session IDs; always obtain from `Target.session_id` or the active target on `BrowserSession`.

## BrowserSession Patterns

- `BrowserSession` owns all CDP client state, target registry, and watchdog lifecycle.
- Use `browser_session.cdp_client` as the single connection point — no separate CDPClient instances.
- Access the currently focused target via `browser_session.active_target`.
- Attach to new targets using `cdp_client.send.Target.attachToTarget(...)` and store the returned `SessionID`.
- Use `browser_session.event_bus` to publish/subscribe to lifecycle events — do not couple components directly.
- Keep `BrowserSession` thin where possible: split target management, event registration, overlays/highlights, domain-specific helpers, and lifecycle wiring by concern instead of growing one giant session file.
- Treat 700-800 lines as the general upper bound for active implementation files, scrutinize files above 800 lines for modular refactor, and treat files above 1000 lines as priority candidates.

## Watchdog Pattern

Each watchdog is a `BaseWatchdog` subclass that:
1. Declares `LISTENS_TO` and `EMITS` as class vars with concrete event types.
2. Registers handlers automatically via the naming convention `on_EventTypeName(self, event)`.
3. Receives `event_bus` and `browser_session` as injected Pydantic fields.
4. Uses `model_config = ConfigDict(arbitrary_types_allowed=True, extra='forbid', ...)`.

```python
class MyWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[list] = [NavigationCompleteEvent]
    EMITS: ClassVar[list] = [MyCustomEvent]

    async def on_NavigationCompleteEvent(self, event: NavigationCompleteEvent) -> None:
        # react to navigation, maybe emit a new event
        await self.event_bus.emit(MyCustomEvent(...))
```

Do NOT read or mutate watchdog-local state from other watchdogs. Shared state belongs on `BrowserSession`.

## DOM Extraction

- `DomService` manages DOM snapshots, accessibility trees, and element highlighting.
- Use `cdp_client.send.DOMSnapshot.captureSnapshot(...)` for full DOM+CSS snapshots.
- Use `cdp_client.send.Accessibility.getFullAXTree(...)` for the accessibility tree.
- Always pass `session_id` scoped to the active target when calling DOM APIs.
- Use `DOMTreeSerializer` for converting raw CDP output to structured `EnhancedDOMTreeNode` objects.
- Element highlight state lives in `DomService` — call its methods rather than issuing raw CDP highlight commands.
- Prefer extracting shared CDP helpers, parser/formatter modules, and feature-specific watchdog helpers instead of duplicating protocol glue across session code.

## Output Format

Return:
1. CDP domain and method choice
2. session_id scoping decision
3. event registration approach
4. watchdog placement (if side-effecting)
5. error handling and timeout considerations
6. rejected alternatives

## Anti-Patterns

- calling `cdp_client.on(...)` — use `cdp_client.register.Domain.Event(callback)` instead
- sending CDP commands with the wrong `session_id` scope (missing or root where target-scoped is needed)
- mutating BrowserSession state from inside a watchdog (use events instead)
- creating a second `CDPClient` instance instead of reusing `browser_session.cdp_client`
- accessing the active target URL directly instead of listening to navigation events
- bypassing `DomService` for DOM queries to avoid "unnecessary" abstraction
- adding more unrelated responsibilities to a giant `BrowserSession` or watchdog file instead of splitting by target plumbing, event handling, overlay logic, or domain helpers

## References

- `references/cdp-patterns.md`
- `references/eval-rubric.md`
