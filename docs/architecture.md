# Architecture

## Canonical Product Path

The default product path uses the user's already-running, enrolled Chrome
profile. Its public abstraction is a logical task space containing logical
pages. It does not launch Chrome, discover or accept a CDP URL, or treat a
Chrome tab as the task-space identity. `agentyc` and `agentyc mcp` use this
host-backed path by default.

```text
CLI / MCP client
      |
      | owner-only local host protocol (MCP stdio is the client transport)
      v
agentyc host broker
  |-- durable ledger, leases, fencing, scheduling, events, snapshots/refs
  |-- local IPC and Chrome Native Messaging bridge
      |
      | Chrome-mediated Native Messaging
      v
MV3 extension service worker
  |-- Chrome tabs, tab groups, debugger and side-panel APIs
  |-- bounded observations and execution of host-authorized operations
      |
      v
user-approved pages in existing Chrome
```

The MCP service is an adapter over host operations, not an independent owner
of task-space state. The default local MCP client connects to the owner-only
host socket. The installed Native Messaging host owns the broker and ledger;
the CLI does not open a second broker in normal connected mode. The explicit
`--offline` fake-host option is a test/contract seam and is not a Chrome
connection.

### Ownership Boundaries

- **Host:** owns logical identity, durable records, principal admission,
  leases/epochs, action receipts and ordering, policy, fencing, reconciliation,
  event watermarks, snapshot/ref provenance, and cleanup authorization.
- **Extension:** owns calls to Chrome APIs and browser observations. It maps
  logical `space_id`/`page_id` identities to ephemeral Chrome objects and
  executes only host-authorized, fenced operations. It is not authoritative
  for leases or durable action state.
- **Clients and MCP:** use logical task-space and page identities. MCP
  compatibility tools do not create a parallel state owner.

`space_id` and `page_id` are public opaque identities. Chrome tab, window,
group, debugger target/session, frame runtime, extension worker, and process IDs
are private ephemeral implementation details or bounded reconciliation hints.
They are not public handles, authorization, or proof that a logical page is
unchanged. Chrome-generated IDs must not appear in primary client results.

A Chrome profile is shared browser state, not an isolation boundary between
spaces. Cookies, origin storage, history, permissions, installed extensions,
and enterprise policy may be shared according to Chrome. Logical ownership
does not isolate those resources. Tab groups are presentation only; their
membership, title, color, or movement does not grant ownership or authorize
cleanup. Existing/user tabs remain unmanaged unless explicitly claimed under
the host's ownership rules.

### Runtime and Recovery

The Native Messaging host admits the extension through the trusted Chrome
origin/extension identity, enrolled profile binding, protocol version, nonce,
epochs, and schema checks. Agent/MCP clients use a distinct owner-only local
protocol and do not open Chrome Native Messaging or Chrome APIs directly.

The host ledger is durable and authoritative. Active connection authority is
process-local and is not persisted. On restart, the host advances its broker
epoch, resets connection/event sequence context, marks interrupted operations
for reconciliation, and requires clients/extension to reconnect. A dispatched
mutation with an uncertain result is not blindly replayed. Browser-session or
profile changes require reconciliation; ambiguous pages remain paused or
unmanaged rather than being silently rebound or closed.

The state directory defaults to `${AGENTYC_STATE_DIR:-~/.agentyc/state}` and
contains `ledger.json`, an exclusive `broker.lock` while the broker is open,
and `host.sock` for local IPC (overridable by `AGENTYC_HOST_SOCKET`). Ledger
schema and recovery details are in [Configuration](configuration.md).

## Workspace Crates

The root `Cargo.toml` is the workspace source of truth. The current split is:

| Crate | Responsibility |
| --- | --- |
| `agentyc-core` | Logical IDs, protocol envelopes, schemas, records, and state/action contracts. |
| `agentyc-host` | Broker, durable ledger, leases/fences, local IPC, Native Messaging bridge, events, and host lifecycle. |
| `agentyc` | CLI dispatch for host-backed logical commands plus explicit compatibility commands. |
| `agentyc-mcp` | Host-backed logical MCP adapter and separate legacy direct-CDP `BrowserServer`. |
| `agentyc-cdp` | Chrome DevTools Protocol client used by the legacy compatibility runtime. |
| `agentyc-browser` | Chrome discovery, launch, profile, and session lifecycle for compatibility/test use. |
| `agentyc-runtime` | Runtime wrapper for the explicit legacy browser path. |
| `agentyc-dom` | DOM serialization, clickable-element detection, and HTML-to-markdown utilities. |
| `agentyc-tools` | Deterministic extraction route selection used by legacy browser tools. |
| `agentyc-tests` | Integration-test harness and browser test support. |

## CLI Dispatch

`crates/agentyc/src/main.rs` dispatches these paths:

1. `agentyc` and `agentyc mcp` run the host-backed logical MCP service by
   default. `--state-dir`, `AGENTYC_STATE_DIR`, and the local host socket
   configure host access; no browser debugger URL is implied.
2. `agentyc mcp --legacy-cdp` explicitly selects the legacy direct-CDP MCP
   compatibility server. Its managed/test lifecycle is available only within
   this explicit mode.
3. `agentyc serve --cdp-url <url>` is legacy Streamable HTTP compatibility
   mode and requires an explicit debugger endpoint.
4. `agentyc space`, `page`, `snapshot`, `action`, `events`, and `host` are
   direct logical host-backed CLI commands.
5. `agentyc browser`, `run --cdp-url`, and `repl --cdp-url` are explicit
   managed/test or direct-CDP compatibility commands; they are not defaults.
6. `agentyc init` writes the bundled agent skills guide.

Tracing writes to stderr; stdout remains available for MCP framing or the
structured JSON emitted by direct commands.

## Legacy CDP/MCP Compatibility Path

This is a separate compatibility path, not the canonical existing-Chrome
task-space path. It retains the older active-browser/tab-oriented MCP server
for clients that explicitly select it. The default path never falls back to
this server or runtime.

`agentyc_mcp::BrowserServer` is the legacy direct-CDP server. It composes six
tool routers for navigation/waits, state/HTML/screenshot/PDF/viewport,
interaction, inspection/extraction, frames/storage, and tabs/cookies,
emulation/session control. Its tool schemas are trimmed before advertising. The
compatibility server connects to a supplied CDP endpoint or, only when
`--legacy-cdp` is selected without one, may use its managed-test lifecycle.
Legacy `serve` instead requires an explicit endpoint.

The legacy runtime uses `agentyc_cdp::CdpClient` over WebSocket or HTTP attach,
enables Network/Runtime/Page domains, and attaches to a page target. Its
`ServerState` keeps the CDP client, session/active-tab details, and any managed
browser process for that compatibility session. `agentyc_browser` can locate
and launch Chrome for explicit test/managed use; the `agentyc browser` command
launches a temporary profile with remote debugging and prints its WebSocket
URL. These facilities are not defaults and are not used by the host-backed
logical adapter.

Legacy CDP environment settings such as `AGENTYC_HEADLESS`,
`AGENTYC_CDP_TIMEOUT_S`, proxy options, `AGENTYC_ALLOWED_DOMAINS`, and
`PLAYWRIGHT_BROWSERS_PATH` apply to the legacy browser runtime where supported.
Legacy attach reuses the supplied browser and does not tear it down at session
end. In shared-CDP usage, separate MCP sessions can open separate tabs, but
cookies and storage remain shared within the browser profile; this is not
logical task-space isolation.

The legacy extraction tools choose deterministic HTML routes (links, images,
tables, lists, forms, or key/value content) and do not fall back to an LLM.
Legacy structured errors and tool behavior remain compatibility concerns and
must not be confused with host broker authority or logical-space guarantees.

## Public Logical State

Host-backed page snapshots and action results are scoped by `space_id` and
`page_id`, with lease and generation checks. Refs are valid only for their
logical page and snapshot/document context. The extension supplies bounded
page observations and capability results; host policy decides whether those
observations can support an operation. Deterministic legacy extraction remains
available only through the compatibility server.

Neither this architecture description nor the presence of code/tests is proof
of a live production deployment or of end-to-end behavior with a user's Chrome
profile. Live rollout evidence is tracked separately.
