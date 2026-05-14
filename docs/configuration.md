# Configuration

## Configuration Sources

Configuration for the MCP server is resolved in this order:

1. Environment variables
2. Config file at `~/.config/traverse/config.json`
3. Code defaults in the browser profile and config models

The MCP server loads config through `traverse.config.load_traverse_config()` and then starts browser sessions from the default browser profile plus any environment overrides.

## Config File

Default path:

```text
~/.config/traverse/config.json
```

The MCP runtime reads the default browser profile from that file when present.

## MCP CLI

Public CLI entrypoint:

```bash
traverse --session-timeout-minutes 10
```

Supported CLI options in the public release:

| Option | Description |
|--------|-------------|
| `--session-timeout-minutes` | Idle timeout for tracked browser sessions |

There is no separate `--mcp` switch in the current public CLI. The `traverse` command itself starts the stdio MCP server.

## Environment Variables

The current MCP runtime honors these documented overrides directly or through the shared config layer.

### Browser Behavior

| Variable | Description |
|----------|-------------|
| `TRAVERSE_HEADLESS` | Override the default profile's `headless` value |
| `TRAVERSE_ALLOWED_DOMAINS` | Comma-separated domain allowlist override |
| `TRAVERSE_PROXY_URL` | Chromium proxy server URL |
| `TRAVERSE_NO_PROXY` | Comma-separated proxy bypass list |
| `TRAVERSE_PROXY_USERNAME` | Proxy username |
| `TRAVERSE_PROXY_PASSWORD` | Proxy password |
| `TRAVERSE_DISABLE_EXTENSIONS` | Disable default bundled extensions |

### Logging And Runtime

| Variable | Description |
|----------|-------------|
| `TRAVERSE_LOGGING_LEVEL` | Shared traverse log level outside MCP stdio mode |
| `TRAVERSE_ACTION_TIMEOUT_S` | Per-action timeout used by the tool service |
| `ANONYMIZED_TELEMETRY` | Enable or disable anonymized telemetry |
| `TRAVERSE_CLOUD_SYNC` | Enable or disable cloud sync behavior in shared config |

### Optional LLM Config

These values may still exist in shared config because the package exposes Python LLM integrations, but the public MCP extraction path does not use them.

| Variable | Description |
|----------|-------------|
| `TRAVERSE_LLM_MODEL` | Default model string in shared config |
| `OPENAI_API_KEY` | OpenAI API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `GOOGLE_API_KEY` | Google API key |

## Browser Profile Notes

The MCP server creates `BrowserProfile` instances from the default profile plus overrides. Publicly relevant defaults in `traverse.mcp.server` include:

- `downloads_path`: `~/Downloads/traverse-mcp`
- `keep_alive`: `False`
- `user_data_dir`: `~/.config/traverse/profiles/default`
- `device_scale_factor`: `1.0`
- `disable_security`: `False`
- `headless`: `False`, unless overridden

## Security-Relevant Settings

The current public MCP runtime documents these security-related controls:

- `allowed_domains` via config or `TRAVERSE_ALLOWED_DOMAINS`
- Private/reserved IP blocking in the browser security watchdog
- `disable_security=False` by default in the MCP server

## Deterministic Extraction Caveat

`browser_extract_content` in the public MCP server is deterministic-only for `0.1.0`. Setting LLM-related environment variables does not enable an LLM fallback for this MCP tool.
