# Tech Stack

## Runtime Requirements

| Requirement | Value |
|-------------|-------|
| Python | ≥ 3.11 |
| Package manager | `uv` (not pip) |
| Browser | Chrome / Chromium (installed by Playwright) |
| Protocol | CDP (Chrome DevTools Protocol) |

---

## Core Dependencies

### Browser / CDP

| Package | Role |
|---------|------|
| `cdp-use` | Typed async CDP client; wraps WebSocket CDP calls into Python method calls |
| `playwright` | Manages Chromium installation; **not** used for automation (cdp-use handles that) |

### Event System

| Package | Role |
|---------|------|
| `bubus` | Lightweight async event bus; core coordination mechanism for watchdogs |

### Data Validation

| Package | Role |
|---------|------|
| `pydantic` v2 | All action models, config, browser state, DOM models; strict validation with `ConfigDict(extra='forbid')` |

### HTTP

| Package | Role |
|---------|------|
| `aiohttp` | Async HTTP for CDP WebSocket and general requests |
| `httpx` | Sync/async HTTP client for LLM provider calls |

### MCP

| Package | Role |
|---------|------|
| `mcp` | Model Context Protocol SDK; server and client implementation |

### Content Processing

| Package | Role |
|---------|------|
| `pillow` | Screenshot image manipulation and encoding |
| `pypdf` | Reading PDF file content |
| `reportlab` | Generating PDF output from page content |
| `markdownify` | HTML → Markdown conversion for LLM context |

### Utilities

| Package | Role |
|---------|------|
| `uuid-extensions` | UUID v7 generation for all entity IDs |

---

## LLM Provider Packages

Each is an optional extra dependency loaded lazily:

| Package | Provider |
|---------|---------|
| `openai` | OpenAI and Azure OpenAI |
| `anthropic` | Anthropic Claude |
| `google-genai` | Google Gemini |
| `groq` | Groq |
| `mistralai` | Mistral |
| `ollama` | Ollama (local) |
| `litellm` | LiteLLM multi-provider proxy |
| `cerebras-cloud-sdk` | Cerebras |
| `github-copilot-sdk` | GitHub Copilot (optional) |
| `boto3` | AWS Bedrock (optional) |
| `oci` | Oracle Cloud Infrastructure (optional) |

---

## Platform-Specific Dependencies

| Package | Platform | Purpose |
|---------|----------|---------|
| `pyobjc` | macOS | `AppKit.NSScreen` for display size detection |
| `screeninfo` | Linux / Windows | Display size detection |

---

## Optional / Feature Dependencies

| Package | Feature | Extra |
|---------|---------|-------|
| `lmnr` | Observability tracing | `[observability]` |
| `imageio[ffmpeg]` | Session video recording | `[recording]` |
| `numpy` | Image processing for recording | `[recording]` |
| `pytest-httpserver` | Test HTTP server for unit tests | dev |
| `pytest-asyncio` | Async test support | dev |
| `pyright` | Static type checking | dev |
| `ruff` | Linting and formatting | dev |
| `pre-commit` | Pre-commit hook runner | dev |

---

## Language and Style

| Attribute | Choice |
|-----------|--------|
| Python version target | 3.11+ |
| Type hints | Modern union syntax (`str \| None`, `list[str]`, `dict[str, Any]`) |
| Indentation | Tabs (not spaces) |
| Async | `async`/`await` throughout — no sync blocking APIs |
| Data models | Pydantic v2 with `ConfigDict(extra='forbid', validate_by_name=True)` |
| IDs | UUID v7 via `uuid_extensions.uuid7str` |
| Validation | `Annotated[..., AfterValidator(...)]` patterns in Pydantic models |

---

## Testing Stack

| Tool | Role |
|------|------|
| `pytest` | Test runner |
| `pytest-asyncio` | Async test support (no manual event loop setup needed) |
| `pytest-httpserver` | Local HTTP server for test HTML pages |
| Conftest fixtures | Scripted LLM response injection (the only thing mocked) |

Test philosophy: real browser objects, real CDP, real DOM — only the LLM is replaced with fixture-driven responses. All CI tests live in `tests/ci/` and run on every commit.

---

## Infrastructure

| Tool | Role |
|------|------|
| `uv` | Virtual environment, dependency resolution, running scripts |
| GitHub Actions | CI/CD (`.github/workflows/`) |
| PyPI | Package distribution |

---

## CDP Protocol Details

CDP is accessed via `cdp-use` which provides typed Python interfaces generated from the CDP protocol schema:

```python
# Typed CDP call
await cdp_client.send.DOMSnapshot.captureSnapshot(params=...)

# Event registration (not cdp_client.on — that doesn't exist in cdp-use)
cdp_client.register.Browser.downloadWillBegin(callback)

# With typed params
from cdp_use.cdp.target import ActivateTargetParameters
await cdp_client.send.Target.attachToTarget(
    params=ActivateTargetParameters(targetId=target_id, flatten=True)
)
```

All CDP session management, target tracking, and sub-session handling for cross-origin iframes lives in `agentyc/browser/session.py` — `cdp-use` only provides the typed protocol layer.
