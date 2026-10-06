# Architecture

## Canonical Product Path

The default product path uses a dedicated Chrome profile launched by the user
with a loopback debugging endpoint. Its public abstraction is a logical task
space containing logical pages. Agentyc does not launch Chrome, create or
switch profiles, or accept a user-supplied CDP URL. `agentyc` and `agentyc
mcp` use this host-backed path by default.

```text
CLI / MCP client
      |
      | owner-only local host protocol (MCP stdio is the client transport)
      v
agentyc host broker
  |-- durable ledger, leases, fencing, scheduling, events, snapshots/refs
  |-- host-owned CDP bridge -> loopback 127.0.0.1:9222
  `-- Native Messaging request: tab.create only
      |                       |
      |                       v
      |                MV3 extension worker
      |                  `-- chrome.tabs.create only
      v
user-launched dedicated Chrome profile
```

The user starts Chrome with its loopback debugging endpoint enabled; Agentyc
does not launch Chrome or inspect the user's everyday profile. The extension
keeps the Native Messaging identity handshake and creates inactive bootstrap
tabs only. The host owns navigation, snapshots, actions, waits, and page
lifecycle through CDP. If Native Messaging disconnects, existing CDP-bound
pages remain host-controlled; creating another tab waits for reconnection.

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
- **Extension:** owns only the Native Messaging handshake and host-requested
  inactive tab creation. It does not enumerate, observe, group, navigate,
  snapshot, or act on browser pages.
- **Host CDP bridge:** owns browser discovery on loopback port 9222 and maps
  logical `space_id`/`page_id` identities to private, in-memory CDP handles.
- **Clients and MCP:** use logical task-space and page identities. MCP
  compatibility tools do not create a parallel state owner.

`space_id` and `page_id` are public opaque identities. Chrome tab, window,
debugger target/session, frame runtime, extension worker, and process IDs are
private ephemeral implementation details or bounded reconciliation hints.
They are not public handles, authorization, or proof that a logical page is
unchanged. Chrome-generated IDs must not appear in primary client results.

Spaces share browser state within the dedicated profile; logical spaces are
not isolation boundaries from one another. The dedicated profile is separate
from the user's everyday profile. Existing/user tabs remain unmanaged unless
explicitly claimed under the host's ownership rules.

### Runtime and Recovery

The Native Messaging host admits the extension through the trusted Chrome
origin/extension identity, enrolled profile binding, protocol version, nonce,
epochs, and schema checks. Agent/MCP clients use a distinct owner-only local
protocol and do not open Chrome Native Messaging or Chrome APIs directly.

The host ledger is durable and authoritative. Active connection authority and
CDP target/session mappings are process-local. On restart, the host advances
its broker epoch, resets connection/event sequence context, marks interrupted
operations for reconciliation, and requires clients to reconnect. The
extension is needed again only when a new tab must be created. A dispatched
mutation with an uncertain result is not blindly replayed. Restart recovery
of retained CDP page bindings is not yet proven; ambiguous pages remain
unavailable rather than being silently rebound or closed.

The state directory defaults to `${AGENTYC_STATE_DIR:-~/.agentyc/state}` and
contains `ledger.json`, an exclusive `broker.lock` while the broker is open,
and `host.sock` for local IPC (overridable by `AGENTYC_HOST_SOCKET`). Ledger
schema and recovery details are in [Configuration](configuration.md).

## Workspace Crates

The root `Cargo.toml` is the workspace source of truth. The current split is:

| Crate           | Responsibility                                                                                         |
| --------------- | ------------------------------------------------------------------------------------------------------ |
| `agentyc-core`  | Logical IDs, protocol envelopes, schemas, records, and state/action contracts.                         |
| `agentyc-host`  | Broker, durable ledger, leases/fences, local IPC, Native Messaging bridge, events, and host lifecycle. |
| `agentyc`       | Host-backed logical CLI dispatch, MCP stdio entry point, and skill initialization.                     |
| `agentyc-mcp`   | Host-backed logical MCP adapter.                                                                       |
| `agentyc-tests` | Integration-test harness; internal CDP use is test-only, not a user-facing CLI.                        |

## CLI Dispatch

`crates/agentyc/src/main.rs` dispatches the shipped host-backed paths:

1. `agentyc` and `agentyc mcp` run the logical MCP service over stdio only.
   No MCP HTTP route or direct-CDP MCP mode is shipped.
2. `agentyc space`, `page`, `snapshot`, `action`, `events`, `host`, `wait`,
   and `extension` are direct logical host-backed CLI commands.
3. `agentyc init` writes the bundled agent skills guide.

Standalone direct-CDP `browser`, `run --cdp-url`, and `repl --cdp-url` CLI
paths were removed. The Node SDK at `packages/agentyc-browser` remains; this is
not the removed Rust `agentyc-browser` crate. The host uses CDP internally;
the extension is only a tab-creation bridge. Internal CDP test or installation
harnesses are not CLI interfaces.

Tracing writes to stderr; stdout remains available for MCP framing or the
structured JSON emitted by direct commands.

## MCP Surface and Release Boundary

The MCP service is a host-backed logical adapter, not an independent owner of
browser or task-space state. It is exposed over stdio only. The deterministic
offline server lists 29 routes; the connected remote catalog declares 30, with
11 routes returning typed `capability_unavailable` before forwarding. A route
declaration is not proof of connected support. A limited existing-profile MCP
run passed stdio and extension fence/rebind, but snapshot/action reconciliation
and the full live workflows remain incomplete, so MCP is not
distribution-ready. See
[MCP compatibility](mcp-compatibility.md) for route-level details and release
blockers.

## Public Logical State

Host-backed page snapshots and action results are scoped by `space_id` and
`page_id`, with lease and generation checks. Refs are valid only for their
logical page and snapshot/document context. The extension supplies bounded
page observations and capability results; host policy decides whether those
observations can support an operation. The removed standalone direct-CDP CLI
paths are not part of the MCP contract.

Neither this architecture description nor the presence of code/tests is proof
of a live production deployment or of end-to-end behavior with a user's Chrome
profile. Live rollout evidence is tracked separately.
