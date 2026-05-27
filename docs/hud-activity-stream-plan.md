# Live Activity HUD — Implementation Plan

This document describes the full three-phase implementation plan for the live activity HUD feature. It is written to be directly executable: every file, every change, every wiring point is listed explicitly with line-level context from the current codebase.

---

## Context: What exists today

### MCP server tool lifecycle
`agentyc/mcp/server.py` — class `AgentycServer`

- `handle_call_tool` (line ~395): dispatches every MCP tool call. Already emits:
  - a start log notification before execution
  - a completion log notification with `duration_ms` and tool metadata after execution
  - an error log notification on failure
- `_tool_phase_message` (line ~484): returns a human-readable phase label for each tool name, e.g. `"Navigating"`, `"Clicking page element"`, `"Typing into focused field"`, `"Waiting for network idle"`.
- `_attach_tool_result_metadata` (line ~572): attaches `duration_ms`, tool name, and success flag to every result.
- `_send_log_notification` (line ~561): sends an MCP `notifications/message` to the client.

These are the primary source of the activity stream for all MCP tool calls.

### Browser demo mode (existing in-browser panel foundation)
`agentyc/browser/demo_mode.py` — class `DemoMode`

- `__init__`: holds a reference to a `BrowserSession` and a `DemoMode` config flag.
- `inject_panel`: injects a `<div id="__agentyc_hud">` into all open pages via CDP `Page.addScriptToEvaluateOnNewDocument` and immediate eval.
- `send_log`: calls `page.evaluate(f"window.__agentycHudLog({json.dumps(entry)})")` to push a log entry to the already-injected panel.
- Currently called from `session_connection.py` when `profile.demo_mode` is True.

`agentyc/browser/demo_mode_script.py` — `DEMO_PANEL_SCRIPT`

- A large JS string that creates the panel DOM, styles it, and registers `window.__agentycHudLog(entry)`.
- Currently uses heavy `border-radius` styling that the user wants replaced with `rounded-sm` / square borders.
- Needs to be replaced with the new minimal HUD script (see Phase 1).

### Browser profile flag
`agentyc/browser/_profile_models.py` — class `BrowserProfile`, line 246–249:
```python
demo_mode: bool = Field(
    default=False,
    description='Enable demo mode side panel...',
)
```
A new `hud_overlay: bool` field needs to be added alongside this.

### Config / env
`agentyc/config.py` — class `FlatEnvConfig`, lines 207–220:
- `AGENTYC_HEADLESS`, `AGENTYC_ALLOWED_DOMAINS`, etc. already exist.
- Needs a new `AGENTYC_HUD_OVERLAY: bool` field added at line ~221.

### CLI
`agentyc/mcp/cli.py` — function `main` (line ~43–216):
- Parses `--headless`, `--allowed-domains`, etc. from argv.
- Needs a new `--hud-overlay` flag and wiring to start the overlay process.

### Browser session connection
`agentyc/browser/session_connection.py` — `_setup_demo_mode` (line ~175–190):
- Constructs a `DemoMode` and calls `inject_panel` when `profile.demo_mode` is True.
- The HUD stream wire-in goes here for the browser panel path.

---

## Phase 1 — Shared activity stream + upgraded in-browser HUD

### Goal
- Build `agentyc/browser/hud_stream.py` — a minimal, in-process pub/sub activity stream.
- Replace the existing `DEMO_PANEL_SCRIPT` with a new minimal square-border HUD.
- Wire the MCP server's tool lifecycle into `HudStream.publish()`.
- Wire browser-side `DemoMode.send_log()` to consume events from `HudStream`.

### New file: `agentyc/browser/hud_stream.py`

```python
"""Shared in-process activity stream for the live HUD.

Published events are forwarded to all registered subscribers (browser panel,
desktop overlay, operator log, etc.) in a fire-and-forget async manner.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

EventKind = Literal["tool_start", "tool_done", "tool_error", "browser_action", "intent"]


@dataclass(slots=True)
class HudEvent:
    kind: EventKind
    label: str                          # short human-readable one-liner
    tool_name: str | None = None        # raw MCP tool name (advanced mode only)
    duration_ms: float | None = None    # set on tool_done
    error: str | None = None            # set on tool_error
    url: str | None = None              # current page URL if known
    tab_title: str | None = None        # current tab title if known
    session_id: str | None = None
    ts: float = field(default_factory=time.monotonic)


SubscriberFn = Callable[[HudEvent], None]


class HudStream:
    """Singleton activity-stream broadcaster.

    Usage:
        stream = HudStream.get()
        stream.subscribe(my_handler)
        stream.publish(HudEvent(kind="tool_start", label="Navigating to GitHub"))
    """

    _instance: HudStream | None = None

    def __init__(self) -> None:
        self._subscribers: list[SubscriberFn] = []

    @classmethod
    def get(cls) -> HudStream:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def subscribe(self, fn: SubscriberFn) -> None:
        if fn not in self._subscribers:
            self._subscribers.append(fn)

    def unsubscribe(self, fn: SubscriberFn) -> None:
        self._subscribers = [s for s in self._subscribers if s is not fn]

    def publish(self, event: HudEvent) -> None:
        """Publish synchronously (cheap — all subscribers must be fast/non-blocking)."""
        for sub in self._subscribers:
            try:
                sub(event)
            except Exception:
                pass  # never break caller due to HUD subscriber error

    async def publish_async(self, event: HudEvent) -> None:
        """Publish on the running event loop without blocking the caller."""
        loop = asyncio.get_event_loop()
        loop.call_soon(self.publish, event)
```

### Changes to `agentyc/mcp/server.py`

**In `handle_call_tool`** — add two lines, one before execution and one after:

```python
# BEFORE execution (after the phase message is computed):
from agentyc.browser.hud_stream import HudStream, HudEvent
HudStream.get().publish(HudEvent(
    kind="tool_start",
    label=phase_msg,          # already computed via _tool_phase_message
    tool_name=tool_name,
    session_id=getattr(self._session, "id", None),
))

# AFTER execution (after duration_ms is known):
HudStream.get().publish(HudEvent(
    kind="tool_done",
    label=phase_msg,
    tool_name=tool_name,
    duration_ms=duration_ms,
    session_id=getattr(self._session, "id", None),
))

# ON ERROR (in except block):
HudStream.get().publish(HudEvent(
    kind="tool_error",
    label=f"Error: {tool_name}",
    tool_name=tool_name,
    error=str(exc),
    session_id=getattr(self._session, "id", None),
))
```

### Changes to `agentyc/browser/demo_mode.py`

Subscribe to `HudStream` on `DemoMode.__init__` and forward to `send_log`:

```python
from agentyc.browser.hud_stream import HudStream, HudEvent

class DemoMode:
    def __init__(self, session: BrowserSession) -> None:
        self._session = session
        HudStream.get().subscribe(self._on_hud_event)

    def _on_hud_event(self, event: HudEvent) -> None:
        """Forward HUD events to the browser-injected panel."""
        import asyncio, json
        entry = {
            "kind": event.kind,
            "label": event.label,
            "tool": event.tool_name,
            "ms": event.duration_ms,
            "error": event.error,
            "ts": event.ts,
        }
        # Fire-and-forget onto the running event loop
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._send_to_pages(entry))
            )
        except RuntimeError:
            pass

    async def _send_to_pages(self, entry: dict) -> None:
        import json
        for page in (self._session.pages or []):
            try:
                await page.evaluate(f"window.__agentycHudLog && window.__agentycHudLog({json.dumps(entry)})")
            except Exception:
                pass

    def cleanup(self) -> None:
        HudStream.get().unsubscribe(self._on_hud_event)
```

### New `DEMO_PANEL_SCRIPT` in `agentyc/browser/demo_mode_script.py`

Replace the existing large JS string with this minimal, square-border HUD:

```js
(function () {
  if (document.getElementById('__agentyc_hud')) return;

  /* ── panel shell ── */
  const panel = document.createElement('div');
  panel.id = '__agentyc_hud';
  Object.assign(panel.style, {
    position: 'fixed',
    top: '12px',
    right: '12px',
    width: '320px',
    maxHeight: '480px',
    background: 'rgba(15,15,20,0.92)',
    color: '#e8e8e8',
    fontFamily: 'ui-monospace, "Cascadia Code", monospace',
    fontSize: '11px',
    lineHeight: '1.4',
    borderRadius: '2px',   /* rounded-sm / nearly square */
    border: '1px solid rgba(255,255,255,0.12)',
    boxShadow: '0 4px 24px rgba(0,0,0,0.5)',
    zIndex: '2147483647',
    overflow: 'hidden',
    display: 'flex',
    flexDirection: 'column',
    userSelect: 'none',
    pointerEvents: 'none',
  });

  /* ── header row ── */
  const header = document.createElement('div');
  Object.assign(header.style, {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '6px 10px',
    borderBottom: '1px solid rgba(255,255,255,0.10)',
    background: 'rgba(255,255,255,0.04)',
  });
  const dot = document.createElement('span');
  dot.id = '__agentyc_hud_dot';
  Object.assign(dot.style, {
    width: '7px',
    height: '7px',
    borderRadius: '2px',   /* square dot */
    background: '#3ecf8e',
    display: 'inline-block',
    flexShrink: '0',
  });
  const title = document.createElement('span');
  title.textContent = 'agentyc';
  Object.assign(title.style, { fontWeight: '700', fontSize: '10px', opacity: '0.7', textTransform: 'uppercase', letterSpacing: '0.08em' });
  const statusText = document.createElement('span');
  statusText.id = '__agentyc_hud_status';
  statusText.textContent = 'Ready';
  Object.assign(statusText.style, { marginLeft: 'auto', fontSize: '10px', opacity: '0.6' });
  header.append(dot, title, statusText);

  /* ── current step ── */
  const current = document.createElement('div');
  current.id = '__agentyc_hud_current';
  Object.assign(current.style, {
    padding: '6px 10px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    fontSize: '11px',
    minHeight: '28px',
    color: '#fff',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  });
  current.textContent = '—';

  /* ── timeline list ── */
  const list = document.createElement('div');
  list.id = '__agentyc_hud_list';
  Object.assign(list.style, {
    overflowY: 'auto',
    flex: '1',
    padding: '4px 0',
    maxHeight: '260px',
  });

  panel.append(header, current, list);
  document.documentElement.appendChild(panel);

  /* ── colour map ── */
  const kindColor = {
    tool_start:    '#60a5fa',
    tool_done:     '#3ecf8e',
    tool_error:    '#f87171',
    browser_action:'#facc15',
    intent:        '#c084fc',
  };

  const MAX_ROWS = 40;
  const rows = [];

  window.__agentycHudLog = function (entry) {
    const kind = entry.kind || 'tool_start';
    const label = entry.label || '';

    /* update status dot color */
    dot.style.background = kindColor[kind] || '#60a5fa';

    /* update header status text */
    const statusMap = {
      tool_start: 'Working',
      tool_done:  'Done',
      tool_error: 'Error',
      browser_action: 'Acting',
      intent: 'Thinking',
    };
    statusText.textContent = statusMap[kind] || 'Working';

    /* update current step */
    current.textContent = label;

    /* add timeline row */
    const row = document.createElement('div');
    const chip = document.createElement('span');
    chip.textContent = kind === 'tool_error' ? '✕' : kind === 'tool_done' ? '✓' : '›';
    Object.assign(chip.style, {
      color: kindColor[kind] || '#60a5fa',
      marginRight: '6px',
      fontWeight: '700',
      display: 'inline-block',
      width: '10px',
      textAlign: 'center',
    });
    const rowText = document.createElement('span');
    rowText.textContent = label;
    Object.assign(rowText.style, {
      opacity: kind === 'tool_done' ? '0.5' : '0.85',
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      whiteSpace: 'nowrap',
      flex: '1',
    });

    /* optional duration chip */
    if (entry.ms != null) {
      const ms = document.createElement('span');
      ms.textContent = entry.ms < 1000 ? `${Math.round(entry.ms)}ms` : `${(entry.ms / 1000).toFixed(1)}s`;
      Object.assign(ms.style, { color: '#6b7280', marginLeft: '6px', fontSize: '10px', flexShrink: '0' });
      row.append(chip, rowText, ms);
    } else {
      row.append(chip, rowText);
    }

    Object.assign(row.style, {
      display: 'flex',
      alignItems: 'center',
      padding: '3px 10px',
      borderRadius: '1px',
    });

    list.insertBefore(row, list.firstChild);
    rows.unshift(row);

    if (rows.length > MAX_ROWS) {
      const removed = rows.pop();
      removed.remove();
    }

    /* error background flash */
    if (kind === 'tool_error') {
      panel.style.borderColor = 'rgba(248,113,113,0.5)';
      setTimeout(() => { panel.style.borderColor = 'rgba(255,255,255,0.12)'; }, 1500);
    }
  };
})();
```

Key design choices:
- `border-radius: 2px` everywhere — square / `rounded-sm`, never pill-shaped
- monospace compact font
- dark translucent background
- timeline rows prepend newest at top
- duration chip appended inline
- error flashes the panel border red briefly

### Changes to `agentyc/browser/_profile_models.py`

Add after the `demo_mode` field (line ~249):

```python
hud_overlay: bool = Field(
    default=False,
    description='Enable the floating transparent desktop overlay HUD (works in both headless and visible modes). Launches a local tkinter overlay window that streams live tool/action activity.',
)
```

### Changes to `agentyc/config.py`

Add to `FlatEnvConfig` after line ~220 (`AGENTYC_DISABLE_EXTENSIONS`):

```python
AGENTYC_HUD_OVERLAY: bool = Field(
    default=False,
    description='Enable the floating transparent desktop overlay HUD.',
)
```

And wire into `load_agentyc_config` / profile construction so `AGENTYC_HUD_OVERLAY=1` sets `profile.hud_overlay = True`.

---

## Phase 2 — Desktop overlay transport and local viewer

### Goal
Build a small always-on-top transparent desktop window (tkinter on macOS/Linux, pure Python, no extra deps) that consumes the same `HudStream` and renders the same activity events.

### New file: `agentyc/browser/hud_overlay.py`

```python
"""Optional floating transparent desktop overlay for the live activity HUD.

Runs a tkinter window in a background thread. Subscribes to HudStream and
renders events in a minimal always-on-top panel.

Only imported/started when `profile.hud_overlay` is True.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentyc.browser.hud_stream import HudEvent

_MAX_ROWS = 12
_POLL_MS = 80        # tkinter after() interval
_WIDTH = 360
_ROW_HEIGHT = 22
_HEADER_HEIGHT = 36

KIND_COLOR = {
    "tool_start":    "#60a5fa",
    "tool_done":     "#3ecf8e",
    "tool_error":    "#f87171",
    "browser_action":"#facc15",
    "intent":        "#c084fc",
}


class HudOverlay:
    """Background-thread tkinter overlay window."""

    def __init__(self) -> None:
        self._queue: list[HudEvent] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._root = None  # set inside _run()

    def start(self) -> None:
        from agentyc.browser.hud_stream import HudStream
        HudStream.get().subscribe(self._on_event)
        self._thread = threading.Thread(target=self._run, daemon=True, name="agentyc-hud-overlay")
        self._thread.start()

    def stop(self) -> None:
        from agentyc.browser.hud_stream import HudStream
        HudStream.get().unsubscribe(self._on_event)
        if self._root is not None:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass

    def _on_event(self, event: HudEvent) -> None:
        with self._lock:
            self._queue.append(event)

    def _run(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            return  # tkinter not available — silently skip

        root = tk.Tk()
        self._root = root

        # Window setup: small, top-right, always on top, no decorations
        screen_w = root.winfo_screenwidth()
        x_pos = screen_w - _WIDTH - 16
        height = _HEADER_HEIGHT + _MAX_ROWS * _ROW_HEIGHT + 8

        root.title("")
        root.geometry(f"{_WIDTH}x{height}+{x_pos}+12")
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.92)
        root.overrideredirect(True)  # no title bar / decorations
        root.configure(bg="#0f0f14")

        # Header
        header_frame = tk.Frame(root, bg="#0f0f14", height=_HEADER_HEIGHT)
        header_frame.pack(fill="x", padx=0, pady=0)
        header_frame.pack_propagate(False)

        status_dot = tk.Label(header_frame, text="■", fg="#3ecf8e", bg="#0f0f14", font=("Courier", 9))
        status_dot.pack(side="left", padx=(10, 4), pady=8)

        tk.Label(header_frame, text="AGENTYC", fg="#888", bg="#0f0f14",
                 font=("Courier", 9, "bold")).pack(side="left")

        status_label = tk.Label(header_frame, text="Ready", fg="#555", bg="#0f0f14",
                                font=("Courier", 9))
        status_label.pack(side="right", padx=10)

        # Separator
        sep = tk.Frame(root, bg="#222", height=1)
        sep.pack(fill="x")

        # Current step
        current_label = tk.Label(root, text="—", fg="#fff", bg="#0f0f14",
                                  font=("Courier", 10), anchor="w",
                                  wraplength=_WIDTH - 20)
        current_label.pack(fill="x", padx=10, pady=(4, 2))

        sep2 = tk.Frame(root, bg="#1a1a22", height=1)
        sep2.pack(fill="x")

        # Timeline frame
        timeline_frame = tk.Frame(root, bg="#0f0f14")
        timeline_frame.pack(fill="both", expand=True, pady=2)

        row_labels: list[tk.Label] = []
        for _ in range(_MAX_ROWS):
            lbl = tk.Label(timeline_frame, text="", fg="#555", bg="#0f0f14",
                            font=("Courier", 9), anchor="w")
            lbl.pack(fill="x", padx=10, pady=0)
            row_labels.append(lbl)

        rows: list[str] = []

        STATUS_MAP = {
            "tool_start":    "Working",
            "tool_done":     "Done",
            "tool_error":    "Error",
            "browser_action":"Acting",
            "intent":        "Thinking",
        }

        def poll() -> None:
            with self._lock:
                pending = self._queue[:]
                self._queue.clear()

            for evt in pending:
                color = KIND_COLOR.get(evt.kind, "#60a5fa")
                status_dot.configure(fg=color)
                status_label.configure(text=STATUS_MAP.get(evt.kind, "Working"))
                current_label.configure(text=evt.label)

                suffix = ""
                if evt.duration_ms is not None:
                    if evt.duration_ms < 1000:
                        suffix = f"  {int(evt.duration_ms)}ms"
                    else:
                        suffix = f"  {evt.duration_ms / 1000:.1f}s"

                icon = "✕" if evt.kind == "tool_error" else ("✓" if evt.kind == "tool_done" else "›")
                rows.insert(0, f"{icon} {evt.label}{suffix}")
                if len(rows) > _MAX_ROWS:
                    rows.pop()

                for i, row_lbl in enumerate(row_labels):
                    if i < len(rows):
                        age_alpha = max(0.3, 1.0 - i * 0.07)
                        gray = int(170 * age_alpha)
                        hex_gray = f"#{gray:02x}{gray:02x}{gray:02x}"
                        row_lbl.configure(text=rows[i], fg=hex_gray)
                    else:
                        row_lbl.configure(text="")

            root.after(_POLL_MS, poll)

        root.after(_POLL_MS, poll)
        root.mainloop()
```

### Changes to `agentyc/browser/session_connection.py`

In `_setup_demo_mode` (line ~175–190), add an `elif` branch for `hud_overlay`:

```python
async def _setup_demo_mode(self) -> None:
    if self._session.profile.demo_mode:
        self._demo_mode = DemoMode(self._session)
        await self._demo_mode.inject_panel()

    if self._session.profile.hud_overlay:
        from agentyc.browser.hud_overlay import HudOverlay
        self._hud_overlay = HudOverlay()
        self._hud_overlay.start()
```

And in `close()`, call `self._hud_overlay.stop()` if it was started.

---

## Phase 3 — Advanced operator mode and UI polish

### Goal
- Add an operator/advanced toggle to the in-browser HUD.
- When advanced mode is ON, show raw tool name + args summary + session ID.
- Implement per-tool noise reduction (skip timeline entries for chatty polling tools like `browser_get_state` unless they take >2s or error).
- Add `intent` event support so agents can narrate what they're doing.
- Add a new MCP tool `browser_set_intent` that lets the coding agent explicitly set the current intent text.

### Changes to `DEMO_PANEL_SCRIPT` (Phase 3 additions)

Extend the Phase 1 JS to include:

```js
/* ── Advanced toggle ── */
let advancedMode = false;
const toggleBtn = document.createElement('button');
toggleBtn.textContent = '···';
Object.assign(toggleBtn.style, {
  pointerEvents: 'all',
  background: 'none',
  border: 'none',
  color: '#555',
  cursor: 'pointer',
  fontSize: '12px',
  padding: '0 4px',
  marginLeft: '4px',
});
toggleBtn.onclick = () => {
  advancedMode = !advancedMode;
  toggleBtn.style.color = advancedMode ? '#60a5fa' : '#555';
  advancedPanel.style.display = advancedMode ? 'block' : 'none';
};
header.appendChild(toggleBtn);

/* ── Advanced detail panel ── */
const advancedPanel = document.createElement('div');
advancedPanel.id = '__agentyc_hud_advanced';
Object.assign(advancedPanel.style, {
  display: 'none',
  padding: '6px 10px',
  borderTop: '1px solid rgba(255,255,255,0.06)',
  fontSize: '10px',
  color: '#6b7280',
  fontFamily: 'ui-monospace, monospace',
  background: 'rgba(0,0,0,0.2)',
});
panel.appendChild(advancedPanel);

/* ── Noise filter list ── */
const CHATTY_TOOLS = new Set([
  'browser_get_state', 'browser_snapshot', 'browser_get_html',
  'browser_get_text', 'browser_evaluate',
]);

/* extend __agentycHudLog to support advanced mode */
const _origLog = window.__agentycHudLog;
window.__agentycHudLog = function (entry) {
  // Noise filter: skip chatty tool_start + tool_done under 500ms
  if (
    CHATTY_TOOLS.has(entry.tool) &&
    (entry.kind === 'tool_start' || (entry.kind === 'tool_done' && (entry.ms || 0) < 500))
  ) return;

  _origLog(entry);

  // Advanced panel update
  if (advancedMode && entry.tool) {
    const parts = [`tool: ${entry.tool}`];
    if (entry.ms != null) parts.push(`${Math.round(entry.ms)}ms`);
    if (entry.session) parts.push(`session: ${entry.session}`);
    advancedPanel.textContent = parts.join('  ·  ');
  }
};
```

### New MCP tool: `browser_set_intent`

Register in `agentyc/mcp/tool_dispatch.py` (or the appropriate tool registry file):

```python
@server.tool()
async def browser_set_intent(intent: str) -> ToolResult:
    """Set a short human-readable description of what the agent is currently trying to do.
    
    This text is shown in the live activity HUD. Call this before starting a
    multi-step sequence to give users context about the agent's current goal.
    
    Args:
        intent: One-line description, e.g. "Looking for the failed CI log"
    
    Returns:
        Confirmation that the intent was set.
    """
    from agentyc.browser.hud_stream import HudStream, HudEvent
    HudStream.get().publish(HudEvent(kind="intent", label=intent))
    return ToolResult(content=[TextContent(type="text", text=f"Intent set: {intent}")])
```

### Changes to `agentyc/mcp/cli.py`

Add `--hud-overlay` flag:

```python
parser.add_argument(
    "--hud-overlay",
    action="store_true",
    default=False,
    help="Enable the floating transparent desktop overlay HUD window.",
)
```

Wire into profile construction:

```python
if args.hud_overlay or env_config.AGENTYC_HUD_OVERLAY:
    profile.hud_overlay = True
```

---

## File change summary

| File | Change |
|------|--------|
| `agentyc/browser/hud_stream.py` | **NEW** — `HudEvent` dataclass + `HudStream` broadcaster |
| `agentyc/browser/hud_overlay.py` | **NEW** — tkinter desktop overlay window |
| `agentyc/browser/demo_mode_script.py` | **REPLACE** `DEMO_PANEL_SCRIPT` with new minimal square-border JS |
| `agentyc/browser/demo_mode.py` | Subscribe to `HudStream`, forward events to browser pages |
| `agentyc/browser/_profile_models.py` | Add `hud_overlay: bool` field |
| `agentyc/browser/session_connection.py` | Start `HudOverlay` when `profile.hud_overlay` is True |
| `agentyc/config.py` | Add `AGENTYC_HUD_OVERLAY: bool` to `FlatEnvConfig` |
| `agentyc/mcp/server.py` | Publish `HudEvent` on tool start, done, error |
| `agentyc/mcp/cli.py` | Add `--hud-overlay` CLI flag |
| `agentyc/mcp/tool_dispatch.py` | Add `browser_set_intent` MCP tool |

---

## Test plan

### `tests/ci/browser/test_hud.py`

```python
"""Tests for the live activity HUD stream and panel wiring."""

import pytest
from agentyc.browser.hud_stream import HudStream, HudEvent


def test_hud_stream_publish_and_subscribe():
    """Basic pub/sub round-trip."""
    stream = HudStream()
    received = []
    stream.subscribe(received.append)
    evt = HudEvent(kind="tool_start", label="Testing")
    stream.publish(evt)
    assert len(received) == 1
    assert received[0].label == "Testing"


def test_hud_stream_unsubscribe():
    stream = HudStream()
    received = []
    stream.subscribe(received.append)
    stream.unsubscribe(received.append)
    stream.publish(HudEvent(kind="tool_done", label="Done"))
    assert received == []


def test_hud_stream_subscriber_error_does_not_propagate():
    """A bad subscriber must not crash the publisher."""
    stream = HudStream()
    def bad_sub(evt):
        raise RuntimeError("boom")
    stream.subscribe(bad_sub)
    # Must not raise
    stream.publish(HudEvent(kind="tool_error", label="Err"))


def test_hud_event_fields():
    evt = HudEvent(
        kind="tool_done",
        label="Navigating",
        tool_name="browser_navigate",
        duration_ms=342.1,
        session_id="sess-abc",
    )
    assert evt.kind == "tool_done"
    assert evt.tool_name == "browser_navigate"
    assert evt.duration_ms == pytest.approx(342.1, abs=0.01)


@pytest.mark.asyncio
async def test_hud_stream_singleton():
    s1 = HudStream.get()
    s2 = HudStream.get()
    assert s1 is s2
```

These tests do not require a browser, Chrome, or the MCP server — they are pure Python unit tests for the stream layer.

---

## Activation

### Browser panel (visible mode)
```python
from agentyc.browser.profile import BrowserProfile
profile = BrowserProfile(headless=False, demo_mode=True)
```
Or set `AGENTYC_HEADLESS=false` and `AGENTYC_DEMO_MODE=true` in `.env`.

### Desktop overlay (headless or visible)
```python
profile = BrowserProfile(hud_overlay=True)
```
Or run the MCP server with `--hud-overlay`:
```bash
agentyc --hud-overlay
```
Or set `AGENTYC_HUD_OVERLAY=1` in the environment.

### Agent intent narration
```python
await session.call_tool("browser_set_intent", {"intent": "Inspecting the CI failure for PR 184"})
```
