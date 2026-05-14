# agentyc contributor notes

Agentyc is a lightweight MCP-first browser automation runtime for coding agents. The repository intentionally focuses on deterministic browser control over CDP and MCP stdio integration.

## Scope

- Keep: `agentyc/mcp`, `agentyc/browser`, `agentyc/dom`, `agentyc/tools`, `agentyc/filesystem`, `agentyc/llm`.
- Do not reintroduce autonomous agent loops, standalone interactive CLI flows, cloud skill wrappers, or sandbox execution surfaces.
- Prefer explicit browser actions and clear failure modes over fallback automation.

## Development

```bash
uv venv --python 3.11
source .venv/bin/activate
uv sync --dev
./scripts/lint.sh
./scripts/test.sh
```

## Code guidelines

- Use async Python and Pydantic v2 models.
- Keep MCP tool contracts deterministic and minimal.
- Reuse existing browser/session primitives instead of adding parallel abstractions.
- When changing browser behavior, preserve direct inspectability through state, HTML, screenshots, and explicit errors.
