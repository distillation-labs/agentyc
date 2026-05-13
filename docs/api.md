# Public API Reference

All public symbols are importable from `traverse` directly — the `__init__.py` uses lazy loading so importing the package doesn't eagerly load all provider SDKs.

## Core Classes

### `BrowserSession`

```python
from traverse import BrowserSession, BrowserProfile

async with BrowserSession(profile=BrowserProfile()) as session:
    state = await session.get_state()
```

Main browser lifecycle manager. One instance = one Chrome process (or remote CDP connection).

**Key methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_state` | `async () -> BrowserStateSummary` | Full DOM + screenshot + tabs snapshot |
| `get_screenshot` | `async () -> bytes` | PNG screenshot of current viewport |
| `get_current_url` | `async () -> str` | Current page URL |
| `get_tabs` | `async () -> list[TabInfo]` | All open tabs |
| `navigate` | `async (url: str) -> None` | Navigate to URL |
| `close` | `async () -> None` | Shutdown browser; called automatically by context manager |

---

### `BrowserProfile`

```python
from traverse import BrowserProfile

profile = BrowserProfile(
    headless=True,
    allowed_domains=["example.com"],
    proxy=ProxySettings(server="http://proxy:8080"),
)
```

All browser launch configuration. See [Configuration](./configuration.md) for full parameter list.

---

### `Tools` / `Controller`

```python
from traverse import Tools

tools = Tools(session=session, llm=llm)
result: ActionResult = await tools.act(action_model)
```

Action executor. Takes validated `ActionModel` instances, routes to the right CDP operations, returns `ActionResult`.

**`ActionResult` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `success` | `bool` | Whether the action succeeded |
| `extracted_content` | `str \| None` | Extracted text/data if applicable |
| `error` | `str \| None` | Error message if `success=False` |
| `is_done` | `bool` | Agent completion signal |
| `include_in_memory` | `bool` | Whether to include in agent memory |

---

### `DomService`

```python
from traverse import DomService

dom_service = DomService(session=session)
dom_state = await dom_service.get_dom_state()
```

DOM extraction and serialization. Usually accessed through `session.get_state()` rather than directly.

---

## Action Models

All in `traverse.tools.views` (also importable from `traverse`):

### Navigation
```python
NavigateAction(url="https://example.com", new_tab=False)
GoBackAction()
WaitAction(seconds=2.0)
```

### Interaction
```python
ClickElementAction(index=5)                        # by element index
ClickElementAction(coordinate=(640, 400))          # by pixel coords
InputTextAction(index=5, text="hello", clear=True)
SendKeysAction(keys="ctrl+a ctrl+c")
ScrollAction(direction="down", amount=300, index=None)  # page scroll
ScrollAction(direction="up", amount=200, index=5)       # element scroll
UploadFileAction(index=5, file_path="/path/to/file.pdf")
```

### Extraction
```python
ExtractAction(
    goal="Get all product prices",
    schema=None,           # None = markdown text; dict = structured JSON
    selector=None,         # Optional CSS selector to scope extraction
)

SearchPageAction(query="login button", regex=False)
FindElementsAction(selector="button[type='submit']")
```

### Output
```python
ScreenshotAction()
SaveAsPdfAction(output_path="/tmp/page.pdf")
StructuredOutputAction[MyModel](data=my_model_instance)
```

### Tab Management
```python
SwitchTabAction(target_id="<target_id>")
CloseTabAction(target_id="<target_id>")
```

---

## LLM Classes

### Instantiation

```python
from traverse import ChatOpenAI, ChatAnthropic, ChatGoogle

llm = ChatOpenAI(model="gpt-4o", api_key="sk-...")
llm = ChatAnthropic(model="claude-opus-4-7-20251101")
llm = ChatGoogle(model="gemini-2.0-flash")
```

### Pre-configured Instances

```python
from traverse import models

llm = models.openai_gpt_4o         # GPT-4o default config
llm = models.google_gemini_flash   # Gemini 2.0 Flash
llm = models.anthropic_claude      # Claude default
```

### Message Format

```python
from traverse.llm.views import SystemMessage, UserMessage, ContentText, ContentImage

messages = [
    SystemMessage(content="You are a browser automation assistant."),
    UserMessage(content=[
        ContentText(text="What is on this page?"),
        ContentImage(url="data:image/png;base64,..."),
    ]),
]

response = await llm.invoke(messages)
# response: ChatInvokeCompletion
# response.content: str
# response.usage.input_tokens / output_tokens
```

---

## MCP Server

```python
from traverse import TraverseServer

server = TraverseServer()
await server.run_stdio()   # Start as MCP stdio server
```

Or via CLI:
```bash
uvx traverse[cli] --mcp
```

---

## Data Models

### `BrowserStateSummary`

Returned by `session.get_state()`:

```python
state.url              # str — current URL
state.title            # str — page title
state.dom              # SerializedDOMState
state.screenshot       # bytes — PNG
state.tabs             # list[TabInfo]
state.viewport         # PageInfo (width, height, scroll_x, scroll_y)
state.element_map      # dict[int, EnhancedDOMTreeNode] — index → node
```

### `TabInfo`

```python
tab.target_id   # str — CDP target ID
tab.url         # str
tab.title       # str
tab.is_active   # bool
```

### `SerializedDOMState`

```python
dom.element_map     # dict[int, EnhancedDOMTreeNode]
dom.html_string     # str — serialized HTML with index annotations
dom.ax_tree         # list[EnhancedAXNode] — accessibility tree
```

### `EnhancedDOMTreeNode`

```python
node.index          # int — element index for actions
node.tag_name       # str
node.text           # str | None
node.attributes     # dict[str, str]
node.bbox           # DOMRect (x, y, width, height)
node.is_visible     # bool
node.is_interactive # bool
node.ax_role        # str | None — ARIA role
node.ax_name        # str | None — accessible name
```

---

## File Organization

```
traverse/
├── __init__.py              # Public API, lazy imports
├── actions.py               # ActionModel, ActionResult base types
├── actor/                   # Low-level CDP actor modules
│   ├── element.py           # Element-level operations
│   ├── page.py              # Page-level operations
│   └── mouse.py             # Mouse operations
├── browser/
│   ├── session.py           # BrowserSession
│   ├── profile.py           # BrowserProfile
│   ├── events.py            # Event type definitions
│   ├── views.py             # BrowserStateSummary, TabInfo, etc.
│   └── watchdogs/           # Watchdog service implementations
├── config.py                # Config file parsing, LLMEntry, AgentEntry
├── dom/
│   ├── service.py           # DomService
│   ├── views.py             # EnhancedDOMTreeNode, SerializedDOMState, etc.
│   └── serializer/          # DOM serialization pipeline
├── integrations/
│   └── gmail/               # Gmail-specific actions
├── llm/
│   ├── __init__.py          # Lazy-loaded provider registry
│   ├── models.py            # Pre-configured model instances
│   ├── views.py             # Message types, BaseChatModel Protocol
│   ├── openai/              # OpenAI provider
│   ├── anthropic/           # Anthropic provider
│   ├── google/              # Google provider
│   └── ...                  # Other providers
├── mcp/
│   ├── server.py            # TraverseServer (MCP server)
│   └── client.py            # MCP client for external tool integration
├── tools/
│   ├── service.py           # Tools / Controller
│   ├── views.py             # Action models
│   ├── extraction/          # Deterministic extractors
│   └── registry/            # Action registry
├── tokens/                  # Token counting and cost tracking
└── utils.py                 # Shared utilities
```
