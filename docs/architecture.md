# Architecture

## High-Level Diagram

```
┌─────────────────────────────────────────────────────────┐
│                     MCP Client / Agent                  │
│             (Claude Desktop, custom agent)              │
└─────────────────────┬───────────────────────────────────┘
                      │  MCP stdio / Python API
┌─────────────────────▼───────────────────────────────────┐
│                  AgentycServer (MCP)                 │
│               agentyc/mcp/server.py                 │
└─────────────────────┬───────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────┐
│                  Tools / Controller                     │
│               agentyc/tools/service.py              │
│  - Action schema registry                               │
│  - Timeout guards (default 180s)                        │
│  - Extraction router (deterministic paths)              │
└──────────┬──────────────────────┬───────────────────────┘
           │ Events               │ LLM invocation
           │                      │ (structured extraction)
┌──────────▼──────────┐  ┌───────▼──────────────────────┐
│   BrowserSession    │  │       LLM Providers           │
│  browser/session.py │  │  agentyc/llm/             │
│  - CDP connections  │  │  15+ providers                │
│  - Event bus        │  └──────────────────────────────┘
│  - Tab management   │
│  - Watchdogs        │
└──────────┬──────────┘
           │  CDP WebSocket
┌──────────▼──────────┐
│   Chrome/Chromium   │
│   (cdp-use wrapper) │
└─────────────────────┘
```

## Component Breakdown

### BrowserSession (`agentyc/browser/session.py`)

The core session manager. One instance = one browser process (or remote cloud browser connection).

Responsibilities:
- Launch or connect to a Chrome process
- Hold the `cdp-use` client and manage target (tab) lifecycle
- Own the `bubus` event bus and register all watchdogs on it
- Provide `get_state()` → `BrowserStateSummary` for agent context
- Handle cross-origin iframes by proxying CDP to inner sessions

**Lifecycle**: `async with BrowserSession(profile=...) as session` — session cleans up on exit.

### Tools / Controller (`agentyc/tools/service.py`)

The action layer between the agent and the browser. Takes an `ActionModel` (validated Pydantic), emits the right event on the session bus, waits for the result, returns an `ActionResult`.

Key behaviors:
- Validates all action inputs via Pydantic schema before touching the browser
- Wraps every operation in a per-action timeout (configurable, default 180s)
- Routes extraction requests: deterministic extractor first, LLM fallback only when needed
- Token cost accounting per action via `agentyc/tokens/`

### DomService (`agentyc/dom/service.py`)

Converts a live browser page into a structured, indexable DOM representation.

Pipeline:
1. Fetch raw DOM via CDP `DOMSnapshot.captureSnapshot`
2. Build `EnhancedDOMTreeNode` tree with bounding boxes, AX data, visibility flags
3. Assign sequential element indices to interactive nodes
4. Filter by viewport threshold (configurable) and paint order
5. Serialize to `SerializedDOMState` (JSON-safe, includes element index map)

The element index is stable within a single page load — agents reference elements by integer index (e.g. `click element 42`).

### BrowserProfile (`agentyc/browser/profile.py`)

Encapsulates everything needed to launch a Chrome instance:
- Chrome binary path and user data directory
- Window geometry (detected via `AppKit.NSScreen` on macOS, `screeninfo` on Linux/Windows)
- Headless mode, sandbox flags, site-isolation settings
- Proxy (server, bypass, credentials)
- Extensions: uBlock Origin, cookie handlers — with per-domain whitelisting
- Persistent profile support across sessions

All values are Pydantic-validated with `ConfigDict(extra='forbid')`.

---

## Event-Driven Architecture

### Event Bus (`bubus`)

`BrowserSession` owns a `bubus.EventBus`. All internal coordination happens by publishing typed events and awaiting their results. No shared mutable state passed between watchdogs.

```python
# Publish and wait
result = await session.bus.emit(ClickElementEvent(index=5))

# Register a handler (done by watchdogs at startup)
session.bus.on(ClickElementEvent, self._handle_click)
```

### Events (`agentyc/browser/events.py`)

**Action events** (emitted by Tools, handled by watchdogs):
| Event | Trigger |
|-------|---------|
| `NavigateToUrlEvent` | `navigate` action |
| `ClickElementEvent` | `click` action |
| `TypeTextEvent` | `type` action |
| `ScrollEvent` | `scroll` action |
| `SendKeysEvent` | `send_keys` action |
| `UploadFileEvent` | `upload_file` action |
| `SwitchTabEvent` | `switch_tab` action |
| `CloseTabEvent` | `close_tab` action |
| `GoBackEvent` | `go_back` action |
| `WaitEvent` | `wait` action |

**State events**:
| Event | Purpose |
|-------|---------|
| `BrowserStateRequestEvent` | Trigger full state snapshot |
| `ScreenshotEvent` | Capture screenshot only |

**Lifecycle events**:
| Event | When |
|-------|------|
| `BrowserStartEvent` | Before browser launch |
| `BrowserConnectedEvent` | After CDP connection established |
| `BrowserStoppedEvent` | On shutdown |
| `NavigationStartedEvent` | On page navigation begin |
| `NavigationCompleteEvent` | On page load complete |
| `TabCreatedEvent` | New tab opened |
| `TabClosedEvent` | Tab closed |
| `FileDownloadedEvent` | Download completed |

---

## Watchdog Services (`agentyc/browser/watchdogs/`)

Each watchdog is an async service that registers event handlers and monitors a specific concern. They are initialized and torn down with `BrowserSession`.

| Watchdog | Responsibility |
|----------|---------------|
| `DOMWatchdog` | DOM snapshot capture, screenshot, element highlighting |
| `DownloadsWatchdog` | PDF auto-download, file save paths, download state |
| `PopupsWatchdog` | JavaScript `alert()`, `confirm()`, `prompt()` auto-dismissal |
| `SecurityWatchdog` | Domain allowlist/denylist enforcement, IP blocking |
| `AboutBlankWatchdog` | Redirect `about:blank` navigations |
| `CrashWatchdog` | Detects renderer crashes, optionally recovers |
| `CaptchaWatchdog` | CAPTCHA detection; optional solver integration |
| `PermissionsWatchdog` | Auto-grant geolocation, camera, microphone permissions |
| `StorageStateWatchdog` | Save/restore cookies and localStorage across sessions |
| `RecordingWatchdog` | Session video recording via `imageio[ffmpeg]` |
| `HARRecordingWatchdog` | Network HAR file recording for traffic analysis |
| `LocalBrowserWatchdog` | Local browser process management |
| `DefaultActionWatchdog` | Fallback handlers for unrecognized actions |
| `ScreenshotWatchdog` | Centralized screenshot capture and caching |

---

## DOM Processing Pipeline

```
CDP DOMSnapshot.captureSnapshot()
         │
         ▼
EnhancedDOMTreeNode tree
  (bounding box, AX role, visibility, iframe depth)
         │
    Filters applied:
    - viewport threshold (hidden elements dropped)
    - paint order (occluded elements dropped)
    - depth/count limits on iframes
         │
         ▼
Element index assignment
  (sequential integers on interactive nodes)
         │
         ▼
SerializedDOMState
  (JSON: element_map, html_string, AX tree)
```

### Extractors (`agentyc/tools/extraction/router.py`)

Six deterministic extractors run without LLM:

| Extractor | What it returns |
|-----------|----------------|
| `deterministic-links` | All `<a>` hrefs with text |
| `deterministic-link-collections` | Grouped nav/pagination/search result links |
| `deterministic-tables` | `<table>` → rows of dicts |
| `deterministic-lists` | `<ul>`/`<ol>`/checklists → items |
| `deterministic-form-fields` | All form inputs with labels, values, options |
| `deterministic-key-values` | Definition lists, property panels → key-value pairs |

If no deterministic extractor matches, the content is passed to the configured LLM with a structured output schema.

---

## Data Flow: Action Execution

```
Agent calls Tools.act(ActionModel)
  │
  ├─ Pydantic validation
  ├─ Timeout context started
  │
  ├─ ClickElementEvent emitted to bus
  │     │
  │     ├─ DOMWatchdog: resolves element index → CDP coordinates
  │     ├─ CDP Input.dispatchMouseEvent (click)
  │     ├─ Wait for navigation/network idle
  │     └─ DOMWatchdog: captures new DOM snapshot
  │
  └─ ActionResult(success=True, extracted_content=...) returned
```

## Data Flow: State Request

```
Agent calls session.get_state()
  │
  ├─ BrowserStateRequestEvent emitted
  │
  ├─ DOMWatchdog: DOMSnapshot → SerializedDOMState
  ├─ ScreenshotWatchdog: screenshot → base64 PNG
  ├─ Tab info: target list via CDP Target.getTargets
  │
  └─ BrowserStateSummary returned
       (url, title, dom, screenshot, tabs, viewport, scroll_pos)
```

## MCP Integration

```
MCP Client (Claude Desktop)
  │  stdio
  ▼
AgentycServer.handle_tool_call(name, args)
  │
  ├─ Parses args → ActionModel
  ├─ Looks up or creates BrowserSession for session_id
  ├─ Calls Tools.act(action)
  └─ Returns result as MCP ToolResult JSON
```

Multiple sessions are supported simultaneously — each `session_id` gets its own `BrowserSession` and browser process.
