# Configuration

The default `agentyc` and `agentyc mcp` path is host-backed and operates on
logical task spaces in the user's already-running, enrolled Chrome profile.
It does not launch Chrome, discover a browser, or require a CDP URL. The host
owns durable task state and leases; the extension owns Chrome API access.
Spaces share their Chrome profile state and are not storage-isolation
boundaries.

## Default Host-Backed Path

```sh
agentyc
agentyc mcp
agentyc --state-dir ~/.agentyc/state space list
```

`agentyc` with no subcommand and `agentyc mcp` connect to the local host via
the owner-only local socket and serve the logical host-backed MCP adapter over
stdio. Host state is owned by the installed Native Messaging host, not by each
MCP client process. When the host/extension bridge is unavailable, operations
that need Chrome return a typed unavailable/capability error; the client does
not fall back to CDP or launch a browser.

| Option / variable | Default | Description |
| --- | --- | --- |
| `--state-dir PATH` | `~/.agentyc/state` | Host state directory. Takes precedence over `AGENTYC_STATE_DIR`. |
| `AGENTYC_STATE_DIR` | `~/.agentyc/state` | Host state directory used by the CLI and Native Messaging host. |
| `AGENTYC_HOST_SOCKET` | `<state-dir>/host.sock` | Override the owner-only local host socket path. |
| `--principal PRINCIPAL` / `AGENTYC_PRINCIPAL` | `principal_cli` | Logical client principal/routing identity; not authentication against another process running as the same OS user. |
| `--offline` | off | Use the deterministic fake-host seam for local tests/contracts; it does not connect to Chrome. |
| `--json` | off | Emit compact direct-command JSON instead of pretty-printed JSON. |

The extension must already be installed/enrolled and connected for Chrome-backed
operations. The product path uses the current Chrome profile. Cookies,
origin storage, history, permissions, installed extensions, and enterprise
policy may be shared across spaces; logical spaces do not isolate them.

## Host State Directory and Ledger

The host uses `${AGENTYC_STATE_DIR:-~/.agentyc/state}` unless configured with
`--state-dir PATH` for a CLI client. The directory is private host-owned state,
not a user-editable configuration file location.

| Entry | Purpose |
| --- | --- |
| `ledger.json` | Atomically replaced durable JSON state; current `schema_version` is `2`. |
| `broker.lock` | Exclusive single-broker ownership lock, held while the broker is open and removed by its owner on clean drop. |
| `host.sock` | Owner-only Unix socket for local CLI/MCP clients; default path can be overridden by `AGENTYC_HOST_SOCKET`. |
| `ledger.json.tmp-<pid>-<sequence>` | Transient private file used for a durable replacement; removed after failed writes where possible. |
| `ledger.json.quarantine-<pid>-<sequence>` | Best-effort private preserved copy when ledger JSON is corrupt, invalid, or has an unsupported schema. |

The JSON ledger stores the schema and broker/connection epoch counters, host-
assigned identity counters, logical spaces/pages and their lifecycle/lease
state, action receipts and idempotency/index/queue records, retained events,
snapshot cache records, control/fence/takeover proofs, enrolled profile
bindings, and space generations. It does not make Chrome tab/target/session
IDs authoritative public identity. Active connection authorities, admitted
nonces, and current connection profile/principal context are process-local and
excluded from serialization, so clients must reconnect after a host restart.

Default ledger limits are 64 spaces, 128 pages per space, 4,096 action
receipts, 256 queued actions per space, 4,096 retained events, 512 cached
snapshots, 8 MiB serialized ledger size, and 2 MiB per snapshot. These are
host implementation bounds, not user configuration settings.

On Unix, the host restricts the state directory to mode `0700` and creates
state files, lock, and socket with mode `0600`; an existing ledger with broader
permissions is rejected. Symlinked state/lock/ledger endpoints fail closed.
Writes use a bounded temporary file, flush/sync, atomic replacement, and
directory sync. A second broker cannot take over an existing lock. An unclean
process exit can leave `broker.lock` behind. Verify that no broker is active
before an operator removes a stale lock; the host does not guess that another
process is dead or steal its ownership.

### Migration and Fail-Closed Recovery

The ledger reader accepts only schema version `2`; there is no automatic
cross-version migration. Before upgrading or rolling back binaries, preserve a
backup and use a host release that explicitly supports the existing schema.
Do not edit the schema version or repair records by hand to bypass validation.
An incompatible schema is copied to a quarantine file on a best-effort basis
and opening fails with `ledger_incompatible`; the old state is not silently
overwritten with a fresh ledger. Malformed JSON or invalid state likewise
fails closed and preserves the original bytes when quarantine succeeds.

After a valid ledger is reopened, the broker advances its epoch, resets
process-local connection authority and event-sequence context, reconciles
interrupted actions, and recovers space/page lifecycle conservatively. Lost
post-dispatch outcomes are not blindly replayed, leases do not silently
survive as warm authority, and ambiguous browser bindings are not adopted or
closed. If validation or persistence fails, stop and retain the ledger and
quarantine copy for a compatible recovery path; do not delete them to force
initialization. A restart requires clients and the extension to reconnect.

## Explicit Legacy CDP/MCP Compatibility

The following entry points are compatibility or test paths, not the default
existing-Chrome architecture:

| Command | Behavior |
| --- | --- |
| `agentyc mcp --legacy-cdp [--cdp-url URL]` | Legacy direct-CDP MCP `BrowserServer`; without a URL this explicitly selected mode may use its managed-test browser lifecycle. |
| `agentyc serve --cdp-url URL [--host HOST] [--port PORT]` | Legacy Streamable HTTP MCP server attached to the explicitly supplied debugger endpoint. `--cdp-url` is required; host defaults to `127.0.0.1`, port to `8765`, and the MCP route is `/mcp`. |
| `agentyc browser [--port PORT] [--headless] [--detach]` | Explicitly launches a temporary-profile Chrome with remote debugging and prints its CDP WebSocket URL. |
| `agentyc run --cdp-url URL ...` | Legacy direct-CDP command runner; URL is required. |
| `agentyc repl --cdp-url URL` | Legacy direct-CDP REPL; URL is required. |

The host-backed `agentyc`/`agentyc mcp` path never calls these commands or
falls back to their browser lifecycle. CDP URLs are debugger credentials; do
not expose them to untrusted clients.

### Legacy Environment Variables

These settings configure the legacy browser runtime where supported. They do
not switch the canonical host-backed path into CDP mode.

| Variable | Default | Description |
| --- | --- | --- |
| `AGENTYC_HEADLESS` | visible browser | Set to `1` to request headless Chrome for a legacy managed launch. |
| `AGENTYC_ALLOWED_DOMAINS` | unrestricted | Comma-separated domain allowlist for legacy `browser_navigate`. |
| `AGENTYC_ACTION_TIMEOUT_S` | `180` | Per-action timeout in seconds for legacy CDP operations. |
| `AGENTYC_CDP_TIMEOUT_S` | `60` | CDP response timeout in seconds. |
| `AGENTYC_PROXY_URL` | unset | Chromium proxy URL for legacy managed launches. |
| `AGENTYC_PROXY_BYPASS` | unset | Comma-separated proxy bypass list. |
| `AGENTYC_PROXY_USERNAME` | unset | Proxy username. |
| `AGENTYC_PROXY_PASSWORD` | unset | Proxy password. |
| `PLAYWRIGHT_BROWSERS_PATH` | platform discovery | Additional legacy Chrome/Chromium binary search location. |

An attached legacy browser is reused and is not torn down when the MCP session
ends. Managed legacy launch uses a per-session/temporary profile and downloads
under `~/Downloads/agentyc-mcp` where the older runtime supports it. Shared
CDP tabs still share cookies and storage within their Chrome profile and do
not provide logical space isolation.

## Logging

| Variable | Default | Description |
| --- | --- | --- |
| `AGENTYC_LOGGING_LEVEL` | `warn` | Rust tracing `EnvFilter` directive such as `warn`, `info`, or `debug`. Logs go to stderr. |

## Agent Skills and Plugin

Installable agent guidance and portable plugin metadata are documented in
[`docs/skills-and-plugins.md`](skills-and-plugins.md). The canonical skill is
`.agents/skills/agentyc-browser-automation/SKILL.md`; the plugin bundle is under
`plugins/agentyc-browser-automation/`.

The implementation and available tests do not by themselves establish live
production behavior. Production enrollment and rollout evidence is tracked
separately.
