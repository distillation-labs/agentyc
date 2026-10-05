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

| Crate             | Responsibility                                                                                         |
| ----------------- | ------------------------------------------------------------------------------------------------------ |
| `agentyc-core`    | Logical IDs, protocol envelopes, schemas, records, and state/action contracts.                         |
| `agentyc-host`    | Broker, durable ledger, leases/fences, local IPC, Native Messaging bridge, events, and host lifecycle. |
| `agentyc`         | Host-backed logical CLI/MCP dispatch plus separate standalone browser/CDP CLI commands.                |
| `agentyc-mcp`     | Host-backed logical MCP adapter.                                                                       |
| `agentyc-cdp`     | Chrome DevTools Protocol client for standalone direct-CDP CLI utilities.                               |
| `agentyc-browser` | Chrome discovery, launch, profile, and session lifecycle for standalone CLI/test use.                  |
| `agentyc-runtime` | Runtime wrapper used by standalone direct-CDP CLI utilities.                                           |
| `agentyc-dom`     | DOM serialization, clickable-element detection, and HTML-to-markdown utilities.                        |
| `agentyc-tools`   | Deterministic extraction utilities for standalone direct-CDP CLI use.                                  |
| `agentyc-tests`   | Integration-test harness and browser test support.                                                     |

## CLI Dispatch

`crates/agentyc/src/main.rs` dispatches these paths:

1. `agentyc` and `agentyc mcp` run the host-backed logical MCP service over
   stdio. No MCP HTTP route or direct-CDP MCP mode is shipped.
2. `agentyc space`, `page`, `snapshot`, `action`, `events`, `host`, `wait`,
   and `extension` are direct logical host-backed CLI commands.
3. `agentyc browser`, `run --cdp-url`, and `repl --cdp-url` remain separate
   standalone direct-CDP CLI utilities. They do not add MCP tools or transports.
4. `agentyc init` writes the bundled agent skills guide.

Tracing writes to stderr; stdout remains available for MCP framing or the
structured JSON emitted by direct commands.

## MCP Surface and Release Boundary

The MCP service is a host-backed logical adapter, not an independent owner of
browser or task-space state. It is exposed over stdio only. The deterministic
offline server lists 29 routes; the connected remote catalog declares 30, with
12 routes returning typed `capability_unavailable` before forwarding. A route
declaration is not proof of connected support. The real headed-Chrome MCP path
has not yet been run, and MCP is not distribution-ready. See
[MCP compatibility](mcp-compatibility.md) for route-level details and release
blockers.

## Standalone Direct-CDP CLI Utilities

`agentyc browser` launches Chrome with a temporary profile and prints a CDP
WebSocket URL. `agentyc run --cdp-url <url>` and `agentyc repl --cdp-url <url>`
operate against an explicitly supplied endpoint. These standalone CLI commands
are separate from MCP; they do not restore the removed direct-CDP `BrowserServer`,
`browser_*` tools, `--legacy-cdp` mode, or `serve` HTTP route. The host-backed
logical adapter does not fall back to these utilities.

## Public Logical State

Host-backed page snapshots and action results are scoped by `space_id` and
`page_id`, with lease and generation checks. Refs are valid only for their
logical page and snapshot/document context. The extension supplies bounded
page observations and capability results; host policy decides whether those
observations can support an operation. The standalone direct-CDP CLI utilities
are not part of the MCP contract.

Neither this architecture description nor the presence of code/tests is proof
of a live production deployment or of end-to-end behavior with a user's Chrome
profile. Live rollout evidence is tracked separately.
