# Host-Backed MCP Interface

## Current surface

The MCP package exposes only the host-backed logical task-space service. The legacy direct-CDP `BrowserServer`, its 61/76-tool profiles, the `mcp --legacy-cdp` mode, the `mcp --cdp-url` option, and the direct-CDP Streamable HTTP `serve` route have been removed. MCP runs over stdio through `agentyc mcp` (or `agentyc` with no subcommand); no MCP HTTP transport is currently shipped.

The service is an adapter, not a state owner. In connected mode, `RemoteHostBrowserServer` forwards logical requests to the owner-only local host socket. The in-process `HostBrowserServer` is used by the deterministic offline seam and exposes 29 logical routes. The connected remote catalog declares 30 routes; 18 are supported by the current local protocol and the remaining routes fail closed with `capability_unavailable` before forwarding.

## Identity, profile, and authorization

MCP operations use logical `space_id`, `page_id`, action, lease, and event identities. Chrome tab, target, debugger, session, and frame identifiers are not accepted as authority. Host authorization, lease fencing, cleanup ownership, extension capability checks, and action reconciliation remain canonical.

`host_space_create` requires `profile_scope="shared_existing_profile"`, `shared_state_notice="shared_profile_state"`, `isolation_claim=false`, and `profile_disclosure_acknowledged=true`. A task space is a logical scope, not a browser-profile isolation boundary; cookies, sessions, and storage are shared with the existing Chrome profile.

MCP does not expose per-tab rename. Chrome's [`tabs.update`](https://developer.chrome.com/docs/extensions/reference/api/tabs#method-update) API has no title property; [`tabGroups.update`](https://developer.chrome.com/docs/extensions/reference/api/tabGroups#method-update) changes a visual group title, not an individual tab title. Page titles remain document-controlled.

## Remote routes unavailable in the current local protocol

The following tools are declared for the MCP contract but return a typed `capability_unavailable` result in connected mode: `host_space_describe`, `host_lease_takeover_with_control_ticket`, `host_lease_control_ticket`, `host_lease_acknowledge_return_control`, `host_lease_acknowledge_fence`, `host_page_bind`, `host_page_mark_lost`, `host_snapshot_put`, `host_snapshot_mark_dirty`, `host_action_enqueue`, `host_action_dispatch`, and `host_event_publish`.

A declared route is not proof of live Chrome support. Live distribution evidence must exercise the stdio process, local host socket, Native Messaging bridge, extension, and headed Chrome. The static/offline tests do not establish that evidence. MCP is **not distribution-ready** until the unsupported-route decisions, live workflows, and Phase 8 release gates are closed.

## Result and error boundary

Host-backed tool failures are MCP tool results with `isError=true` and structured canonical metadata. Transport, malformed JSON-RPC, protocol, and unknown-tool failures remain protocol/transport errors. An ambiguous dispatched action returns reconciliation metadata and must not be blindly replayed.

## Migration

Use the host-backed `host_*` MCP tools for logical task-space operations, or use the direct CLI/Node SDK as the primary interface. Existing MCP clients using `browser_*` tools, raw tab IDs, CDP URLs, or `agentyc serve` must migrate; those interfaces are no longer present.

## Evidence

- `crates/agentyc-mcp/src/host_adapter.rs`
- `crates/agentyc-mcp/src/host_server.rs`
- `crates/agentyc-mcp/src/remote_host_server.rs`
- `crates/agentyc/src/main.rs`
- `crates/agentyc-mcp/tests/host_phase8.rs`
- `tests/mcp_protocol.rs`
- [Phase 8 plan](exec-plans/active/agentyc-browser-task-spaces/plans/phase-8-mcp-compatibility.md)
