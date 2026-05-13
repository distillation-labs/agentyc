# Features

## Browser Actions

All actions are Pydantic-validated before execution and return a typed `ActionResult`.

### Navigation
| Action | Description |
|--------|-------------|
| `navigate` | Navigate to a URL; optionally open in new tab |
| `go_back` | Browser back button |
| `wait` | Wait N seconds (rate-limiting, animation settling) |

### Element Interaction
| Action | Description |
|--------|-------------|
| `click` | Click by element index or absolute (x, y) coordinates |
| `type` | Type text into focused/specified element; optional clear-first |
| `send_keys` | Send raw keyboard sequences (Tab, Enter, Ctrl+A, etc.) |
| `scroll` | Scroll page or specific element by pixel delta |
| `upload_file` | Upload a local file to a file input element |

### Content Extraction
| Action | Description |
|--------|-------------|
| `extract` | Extract content from page; deterministic or LLM-backed |
| `search_page` | Search page text or regex; returns matching node list |
| `find_elements` | CSS selector query; returns matched element list |

### State & Output
| Action | Description |
|--------|-------------|
| `screenshot` | Capture current viewport as PNG |
| `save_as_pdf` | Export current page as PDF |
| `get_state` | Return full `BrowserStateSummary` (DOM, screenshot, tabs, URL) |

### Tab Management
| Action | Description |
|--------|-------------|
| `list_tabs` | Return all open tabs with URL/title |
| `switch_tab` | Focus a tab by target ID or index |
| `close_tab` | Close a tab |

---

## DOM State Modes

When fetching browser state, you choose a verbosity mode to balance token cost vs. context richness:

| Mode | What's included | Use when |
|------|----------------|----------|
| `full` | Complete DOM tree with all elements | Deep inspection needed |
| `min` | Minimal text content only | Quick status checks |
| `auto` | Adaptive based on page complexity | Default for most use |
| `focus` | Only the focused/active element subtree | Form filling |

---

## Deterministic Extractors

Six extraction strategies run without an LLM round-trip:

| Strategy | Handles |
|----------|---------|
| `deterministic-links` | All `<a>` hrefs with anchor text |
| `deterministic-link-collections` | Navigation menus, search results, pagination |
| `deterministic-tables` | HTML `<table>` elements → row arrays |
| `deterministic-lists` | `<ul>`, `<ol>`, checkboxes → item arrays |
| `deterministic-form-fields` | Inputs, selects, textareas with labels and current values |
| `deterministic-key-values` | Definition lists, property panels, metadata blocks |

If no deterministic pattern matches, content is passed to the configured LLM with structured output schema.

---

## LLM Providers

15+ providers are supported, each as a drop-in `BaseChatModel` implementation.

| Provider | Class | Notes |
|----------|-------|-------|
| OpenAI | `ChatOpenAI` | GPT-4o, GPT-4.1-mini, o1/o3 series |
| Anthropic | `ChatAnthropic` | Claude 3.x / 4.x models |
| Google | `ChatGoogle` | Gemini 2.0 Flash, 2.5 Pro/Flash/Flash-Lite |
| Azure OpenAI | `ChatAzureOpenAI` | GPT-4o on Azure endpoints |
| GitHub Copilot | `ChatGitHubCopilot` | Copilot-hosted models |
| Groq | `ChatGroq` | Fast inference (Llama, Mixtral) |
| LiteLLM | `ChatLiteLLM` | Multi-provider proxy |
| Mistral | `ChatMistral` | Mistral 7B / Mixtral |
| Ollama | `ChatOllama` | Self-hosted local models |
| Cerebras | `ChatCerebras` | Cerebras fast inference |
| DeepSeek | (via LiteLLM) | DeepSeek Chat/Coder |
| Vercel | `ChatVercel` | Vercel AI SDK models |
| OpenRouter | (via LiteLLM) | Aggregated model routing |
| OCI Raw | `ChatOCIRaw` | Oracle Cloud Infrastructure |
| Traverse | `ChatTraverse` | Internal Traverse cloud API |

All providers share the same message format (`SystemMessage`, `UserMessage`, `AssistantMessage`) and content types (`ContentText`, `ContentImage`, `ContentRefusal`).

Pre-configured instances are available in `traverse.models` (e.g., `models.openai_gpt_4o`, `models.google_gemini_flash`).

---

## MCP Integration

### As MCP Server

`TraverseServer` exposes 15 tools over MCP stdio:

| Tool | Description |
|------|-------------|
| `browser_navigate` | Navigate to URL |
| `browser_click` | Click element by index or coordinates |
| `browser_type` | Type text |
| `browser_get_state` | Get DOM + screenshot state |
| `browser_extract_content` | Extract structured content |
| `browser_get_html` | Get raw page HTML |
| `browser_screenshot` | Capture screenshot |
| `browser_scroll` | Scroll page |
| `browser_go_back` | Navigate back |
| `browser_send_keys` | Send keyboard input |
| `browser_upload_file` | Upload a file |
| `browser_list_tabs` | List open tabs |
| `browser_switch_tab` | Switch active tab |
| `browser_close_tab` | Close a tab |
| `browser_list_sessions` | List active browser sessions |
| `browser_close_session` | Close a session |
| `browser_close_all` | Close all sessions |

Multiple named sessions are supported simultaneously. Stable element refs (`e123`) persist across state queries within a session.

### As MCP Client

`traverse/mcp/client.py` connects to external MCP servers (filesystem, GitHub, databases, etc.) and injects their tools dynamically into the action registry. An agent using traverse can thus call external MCP tools alongside browser actions.

---

## Watchdog Services

Autonomous async monitors that handle browser-level concerns:

| Watchdog | What it handles |
|----------|----------------|
| Popups | Auto-dismiss JS `alert()`, `confirm()`, `prompt()` |
| Downloads | Auto-save PDFs; track download state |
| Security | Domain allowlist/denylist; IP address blocking |
| CAPTCHA | Detect CAPTCHAs; optional external solver integration |
| Permissions | Auto-grant geolocation/camera/microphone |
| Storage State | Persist cookies and localStorage across sessions |
| Recording | Video recording of sessions |
| HAR Recording | Network traffic capture in HAR format |
| Crash | Renderer crash detection and recovery |
| About:blank | Redirect empty-page navigations |

---

## Security Controls

- **Domain allowlist** (`allowed_domains`): block navigation to any domain not in the list
- **Domain denylist** (`blocked_domains`): prevent navigation to specific domains
- **IP blocking**: prevent resolution of private/reserved IP ranges (SSRF protection)
- **Sandbox flags**: configurable Chrome sandbox settings
- **Site isolation**: per-origin process isolation control
- **Headless mode**: no visible browser UI

---

## Browser Configuration

`BrowserProfile` exposes fine-grained control:

- **Display**: auto-detected window size; configurable width/height/scale
- **Proxy**: server URL, bypass list, username/password
- **Extensions**: uBlock Origin and cookie managers built-in; per-domain whitelist
- **Persistence**: user data directory for profile reuse across sessions
- **Remote debugging port**: configurable CDP port
- **Cloud browser**: connect to remote Chrome instances (e.g., Browserless)
- **Docker optimization**: automatic flag adjustments for containerized environments
- **Custom Chrome flags**: arbitrary extra arguments passthrough

---

## Cross-Origin Iframe Support

`BrowserSession` handles cross-origin iframes by:
1. Detecting when an element index falls inside an inner frame
2. Attaching a sub-CDP session to that frame's target
3. Proxying all CDP operations through the sub-session
4. Merging iframe element indices into the top-level index map

Configurable limits: max iframe depth and max iframe count per page.

---

## Token Efficiency Features

- **State modes** (full/min/auto/focus) reduce DOM tokens proportionally
- **Element index maps** replace verbose XPaths/selectors with small integers
- **Deterministic extractors** skip LLM calls for standard data patterns
- **Per-action token cost tracking** via `traverse/tokens/` for budget management
- **Lazy LLM imports**: providers only loaded when first used

---

## Gmail Integration

`traverse/integrations/gmail/actions.py` provides Gmail-specific actions built on top of the core browser tools: reading emails, composing, searching. Packaged as a separate integration to avoid coupling.

---

## Observability

- **lmnr integration** (`traverse/observability.py`): trace individual actions and LLM calls
- **Telemetry** (`traverse/telemetry/`): aggregated usage metrics
- **HAR recording**: full network traffic capture for debugging
- **Session recording**: video replay of browser sessions
- **Structured logging**: all console output in `_log_*` methods for easy filtering
