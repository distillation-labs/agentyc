# Configuration

`agentyc` is configured entirely through CLI flags and environment variables.
There is no config file, no API key, and no LLM — the only external requirement
is a Chrome/Chromium install.

## CLI

### `agentyc` / `agentyc mcp` — stdio MCP server

```bash
agentyc
agentyc mcp --cdp-url ws://127.0.0.1:9222/devtools/browser/...
```

| Option | Description |
|--------|-------------|
| `--cdp-url` | Attach to an existing browser over CDP (a `ws://`/`wss://` debugger URL or an HTTP endpoint) instead of launching a local one. |

### `agentyc serve` — Streamable HTTP MCP server

```bash
agentyc serve --host 127.0.0.1 --port 8765
```

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `127.0.0.1` | Bind address. |
| `--port` | `8765` | Bind port (server mounted at `/mcp`). |
| `--cdp-url` | — | Attach to an existing browser over CDP. |

### `agentyc init` — write the skills guide

| Option | Default | Description |
|--------|---------|-------------|
| `--output` | `agentyc-skill.md` | Destination path. |
| `--print` | — | Print to stdout instead of writing a file. |
| `--force` | — | Overwrite the destination if it exists. |

### `agentyc browser` — launch a shared browser

```bash
agentyc browser --port 9222 --detach
```

| Option | Default | Description |
|--------|---------|-------------|
| `--port` | `9222` | Remote debugging port. |
| `--headless` | — | Launch headless (`--headless=new`). |
| `--detach` | — | Print the CDP URL and exit without waiting on the process. |

## Environment Variables

Every variable below is read directly by the Rust binary. All are optional.

### Browser Runtime

| Variable | Description |
|----------|-------------|
| `AGENTYC_HEADLESS` | `1` runs Chrome headless. Default: visible browser. |
| `AGENTYC_ALLOWED_DOMAINS` | Comma-separated domain allowlist; navigation outside it is blocked. |

### Timeouts

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTYC_ACTION_TIMEOUT_S` | `180` | Per-action CDP timeout, in seconds. |
| `AGENTYC_CDP_TIMEOUT_S` | `60` | CDP response timeout, in seconds. |

### Proxy

| Variable | Description |
|----------|-------------|
| `AGENTYC_PROXY_URL` | Chromium proxy server URL. |
| `AGENTYC_PROXY_BYPASS` | Comma-separated proxy bypass list. |
| `AGENTYC_PROXY_USERNAME` | Proxy username. |
| `AGENTYC_PROXY_PASSWORD` | Proxy password. |

### Logging

| Variable | Description |
|----------|-------------|
| `AGENTYC_LOGGING_LEVEL` | tracing `EnvFilter` directive (e.g. `warn`, `info`, `debug`). Logs go to stderr. |

### Chrome Discovery

| Variable | Description |
|----------|-------------|
| `PLAYWRIGHT_BROWSERS_PATH` | Override where agentyc looks for a Chromium binary (e.g. an existing cache). |

## Browser Defaults

When the MCP server launches a local browser:

- `headless=false` (a visible browser) unless `AGENTYC_HEADLESS=1`.
- Per-session isolated temporary profile (`--user-data-dir` is a fresh temp dir).
- Downloads path under `~/Downloads/agentyc-mcp`.

When attaching through `--cdp-url`, the server reuses the running browser and does
not tear it down when the session ends.

## Security-Relevant Controls

- `AGENTYC_ALLOWED_DOMAINS` restricts which hosts `browser_navigate` may reach;
  blocked navigations return a `[domain_blocked]` tool error.
- The MCP server only exposes tools — no resources or prompts — and makes no
  network calls of its own beyond what the controlled browser does.

## Deterministic Extraction Note

`browser_extract_content` is deterministic-only (native HTML parsing). There is
no LLM fallback and no API key to configure.
