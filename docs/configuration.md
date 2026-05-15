# Configuration

## Resolution Order

The MCP runtime resolves configuration in this order:

1. Environment variables
2. Config file
3. Code defaults

`agentyc.config.load_agentyc_config()` loads the effective config used by the MCP server.

## Config File Location

Default config path:

```text
~/.config/agentyc/config.json
```

Overrides:

- `AGENTYC_CONFIG_PATH` points to a specific config file.
- `AGENTYC_CONFIG_DIR` changes the config directory.
- `XDG_CONFIG_HOME` changes the XDG base directory.

## Config File Shape

The current config file is a DB-style JSON document with top-level sections such as:

- `browser_profile`
- `llm`
- `agent`

The MCP server primarily consumes the default `browser_profile` entry and selected `llm` fields from that document.

## CLI Configuration

### MCP Server

```bash
agentyc --session-timeout-minutes 10
agentyc mcp --cdp-url ws://127.0.0.1:9222/devtools/browser/...
```

| Option | Description |
|--------|-------------|
| `--session-timeout-minutes` | Idle timeout for the tracked browser session |
| `--cdp-url` | Attach to an existing browser instead of launching a local one |

### Shared Browser Launcher

```bash
agentyc browser --port 9222 --detach
```

| Option | Description |
|--------|-------------|
| `--port` | Remote debugging port |
| `--headless` | Start the shared browser headless |
| `--detach` | Leave the shared browser running in the background |

## Environment Variables

### Browser Runtime

| Variable | Description |
|----------|-------------|
| `AGENTYC_HEADLESS` | Override `headless` in the default browser profile |
| `AGENTYC_ALLOWED_DOMAINS` | Comma-separated allowlist override |
| `AGENTYC_DISABLE_EXTENSIONS` | Disable bundled browser extensions |
| `AGENTYC_ACTION_TIMEOUT_S` | Per-action timeout used by `agentyc.tools.service` |

### Proxy

| Variable | Description |
|----------|-------------|
| `AGENTYC_PROXY_URL` | Chromium proxy server URL |
| `AGENTYC_NO_PROXY` | Comma-separated proxy bypass list |
| `AGENTYC_PROXY_USERNAME` | Proxy username |
| `AGENTYC_PROXY_PASSWORD` | Proxy password |

### Logging And Telemetry

| Variable | Description |
|----------|-------------|
| `AGENTYC_LOGGING_LEVEL` | agentyc log level |
| `CDP_LOGGING_LEVEL` | CDP log level |
| `AGENTYC_DEBUG_LOG_FILE` | Optional debug log file path |
| `AGENTYC_INFO_LOG_FILE` | Optional info log file path |
| `ANONYMIZED_TELEMETRY` | Enable or disable anonymized telemetry |
| `AGENTYC_VERSION_CHECK` | Enable or disable version checks |

### Optional LLM Configuration

These settings affect optional LLM integrations in the package. They do not enable an MCP extraction fallback.

| Variable | Description |
|----------|-------------|
| `AGENTYC_LLM_MODEL` | Default model string in shared config |
| `OPENAI_API_KEY` | OpenAI API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `GOOGLE_API_KEY` | Google API key |
| `AGENTYC_API_KEY` | API key for the optional hosted agentyc LLM provider integration |

`AGENTYC_API_KEY` should not be treated as part of the default browser runtime contract.

## MCP Server Browser Defaults

When the MCP server launches a local browser, it sets these public defaults in `agentyc.mcp.server`:

- `downloads_path=~/Downloads/agentyc-mcp`
- `keep_alive=False`
- `user_data_dir=~/.config/agentyc/profiles/default`
- `device_scale_factor=1.0`
- `disable_security=False`
- `headless=False` unless overridden by config or env

When attaching through `--cdp-url`, the server instead sets `keep_alive=True` and creates a fresh tab in the shared browser.

## Security-Relevant Controls

- `allowed_domains` from config or `AGENTYC_ALLOWED_DOMAINS`
- `disable_security=False` by default in the public MCP server
- IP and domain checks enforced by the browser security layer

## Deterministic Extraction Note

`browser_extract_content` in the public MCP server is deterministic-only. Supplying LLM environment variables does not change that behavior.

## Shared Browser Guidance

Current shared-browser behavior should be understood operationally:

- The MCP server attaches to an existing CDP endpoint.
- It opens a fresh tab for its own work.
- Tab focus remains explicit and mutable.
- Chrome does not expose reliable per-tab ownership coloring.

For stronger visual separation, separate windows remain more dependable than assuming tab-level ownership cues.
