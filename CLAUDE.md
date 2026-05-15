# CLAUDE.md

This file gives repository-specific guidance to coding agents working in this repo.

## Repository Identity

agentyc is a pure MCP-first browser automation runtime for coding agents.

The public contract should be read from these modules first:

- `agentyc/mcp/server.py`
- `agentyc/mcp/cli.py`
- `agentyc/mcp/state.py`
- `agentyc/tools/extraction/router.py`
- `agentyc/tools/service.py`
- `agentyc/config.py`
- `agentyc/browser/session.py`
- `agentyc/browser/session_manager.py`

When docs or metadata are updated, keep them aligned to those modules rather than older product framing that may still exist elsewhere in the tree.

## Scope Boundaries

- Keep the public story centered on the MCP server and browser runtime.
- Do not reintroduce autonomous agent loops, cloud-first product framing, sync-first narratives, or Browser-Use branding into public docs.
- Treat deterministic extraction as the public default. The MCP server does not use an LLM fallback for `browser_extract_content`.
- Be cautious when describing shared-browser collaboration. `--cdp-url` support is real, but Chrome does not offer reliable per-tab ownership cues.

## Architecture Notes

### MCP Layer

- `AgentycServer` is the public stdio MCP server.
- The server exposes tools only, not MCP resources or prompts.
- The server lazily creates a browser session on first browser tool use.

### Browser Layer

- `BrowserSession` manages browser lifecycle, CDP connectivity, tab focus, and watchdog coordination.
- `SessionManager` is the source of truth for target and session tracking.
- Public state targeting uses stable element refs generated from backend node ids.

### Tool Layer

- `Tools` validates action input, dispatches typed events, and enforces bounded action timeouts.
- `agentyc.tools.extraction.router` contains the deterministic extraction routes used by the MCP server.

## Development Commands

Setup:

```bash
uv venv --python 3.11
source .venv/bin/activate
uv sync --dev
```

Quality checks:

- `./scripts/lint.sh`
- `uv run pyright`
- `uv run ruff check --fix`
- `uv run ruff format`

Tests:

- `./scripts/test.sh`
- `uv run pytest -vxs tests/ci`

Package build:

- `uv build`

## CLI Notes

- `agentyc` and `agentyc mcp` start the stdio MCP server.
- `agentyc browser` launches a Chrome or Chromium instance with remote debugging and prints a CDP URL.
- `--cdp-url` attaches the MCP server to an existing browser and opens a fresh tab for that server instance.

## Code Style

- Use async Python.
- Use tabs for indentation in Python files.
- Prefer modern type syntax such as `str | None` and `list[str]`.
- Use Pydantic v2 models for validated runtime-facing data.
- Keep the smallest correct change. Do not add compatibility layers unless there is a real public-contract need.

## Testing Guidance

- Prefer real browser behavior over mocking.
- Use `pytest-httpserver` for local HTML fixtures instead of real external websites.
- Keep tests in `tests/ci` when they belong in the default CI surface.

## Documentation Guidance

- Keep public docs factual and implementation-backed.
- Do not publish unsupported benchmark or performance claims.
- Avoid version-specific marketing language unless it is directly backed by current package metadata.
- If there is a mismatch between code and docs that should not be papered over, call it out explicitly.

## Important Reminders

- Use `uv`, not `pip`, for normal repo workflows.
- Prefer editing existing files over creating new ones.
- Keep the runtime story narrow, deterministic, and MCP-first.
