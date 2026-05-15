# Tech Stack

## Runtime Requirements

| Requirement | Value |
|-------------|-------|
| Python | `>=3.11,<4.0` |
| Package manager | `uv` |
| Browser | Local Chrome or Chromium, or an existing browser exposed over CDP |
| Protocol | Chrome DevTools Protocol |

The public runtime does not depend on Playwright for browser automation.

## Core Dependencies

### Browser And Protocol

| Package | Role |
|---------|------|
| `cdp-use` | Typed async CDP client |
| `aiohttp` | Async HTTP and transport helpers |
| `psutil` | Process inspection used by runtime helpers |

### MCP And Validation

| Package | Role |
|---------|------|
| `mcp` | MCP server SDK |
| `pydantic` | Validation for actions, config, and browser-facing payloads |
| `pydantic-settings` | Environment-backed settings loading |
| `typing-extensions` | Typing helpers where needed |

### Browser Coordination

| Package | Role |
|---------|------|
| `bubus` | Async event bus for browser coordination |
| `anyio` | Async compatibility helpers |

### Content And Files

| Package | Role |
|---------|------|
| `pillow` | Screenshot handling |
| `markdownify` | HTML-to-Markdown conversion for extraction helpers |
| `pypdf` | PDF parsing |
| `reportlab` | PDF generation helpers |
| `python-docx` | Document parsing helpers |

### Runtime Utilities

| Package | Role |
|---------|------|
| `python-dotenv` | `.env` loading |
| `posthog` | Product telemetry |
| `uuid7` | UUID helpers |

## Optional LLM Provider Packages

The package still contains optional LLM integrations even though the public MCP extraction path is deterministic-only.

| Package | Provider Or Role |
|---------|------------------|
| `openai` | OpenAI and related integrations |
| `anthropic` | Anthropic |
| `google-genai` | Google Gemini |
| `groq` | Groq |
| `ollama` | Ollama |
| `boto3` | AWS Bedrock extra |
| `oci` | OCI extra |
| `github-copilot-sdk` | Copilot extra |

## Platform-Specific Dependencies

| Package | Platform | Purpose |
|---------|----------|---------|
| `pyobjc` | macOS | Display and windowing helpers |
| `screeninfo` | Linux and Windows | Display detection |

## Optional Feature Dependencies

| Package | Feature |
|---------|---------|
| `imageio[ffmpeg]` | Video recording extra |
| `numpy` | Recording and image-processing helpers |
| `lmnr` | Eval and observability extra |

## Testing And Quality

| Tool | Role |
|------|------|
| `pytest` | Test runner |
| `pytest-asyncio` | Async test support |
| `pytest-httpserver` | Local HTTP fixtures |
| `ruff` | Linting and formatting |
| `pyright` | Static type checking |
| `codespell` | Spelling checks |
| `pre-commit` | Hook runner |

## Language And Style

| Attribute | Choice |
|-----------|--------|
| Async model | `async` and `await` throughout the runtime |
| Validation style | Pydantic v2 models |
| Python formatting | Tabs, with Ruff configured as the formatter |
| Transport model | stdio MCP plus CDP WebSocket to Chrome or Chromium |

## Infrastructure

| Tool | Role |
|------|------|
| `uv` | Dependency resolution, virtualenvs, builds |
| GitHub Actions | CI |
| PyPI | Package distribution |
