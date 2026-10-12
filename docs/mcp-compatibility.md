# Host-Backed MCP Interface

## Current surface

The MCP package exposes only the host-backed logical task-space service. The legacy direct-CDP `BrowserServer`, its 61/76-tool profiles, the `mcp --legacy-cdp` mode, the `mcp --cdp-url` option, and the direct-CDP Streamable HTTP `serve` route have been removed. MCP runs over stdio through `agentyc mcp` (or `agentyc` with no subcommand); no MCP HTTP transport is currently shipped.

The service is an adapter, not a state owner. In connected mode, `RemoteHostBrowserServer` forwards logical requests to the owner-only local host socket. The in-process `HostBrowserServer` is used by the deterministic offline seam and exposes 29 logical routes. The connected remote catalog declares 30 routes; 19 are supported by the current local protocol and the remaining 11 fail closed with `capability_unavailable` before forwarding.

## Identity, profile, and authorization

MCP operations use logical `space_id`, `page_id`, action, lease, and event identities. Chrome tab, target, debugger, session, and frame identifiers are not accepted as authority. The host owns browser control through its loopback CDP connection; the extension is invoked only to create tabs and present the required per-space visual Chrome tab group. Each leased space is bound to the host-issued connection that acquired it; another connection, including one with the same principal label, is rejected before browser or CDP mutation.

`host_space_create` requires `profile_scope="shared_existing_profile"`, `shared_state_notice="shared_profile_state"`, `isolation_claim=false`, and `profile_disclosure_acknowledged=true`. Spaces are logical scopes, not isolation boundaries: cookies, sessions, and storage are shared within the dedicated profile, which is separate from the user's everyday profile.

MCP does not expose per-tab rename. Page titles remain document-controlled.

`host_lease_acknowledge_fence` is supported in connected mode. It retries only the current authority's pending takeover fence at its existing epoch, renews the lease TTL, and still requires durable extension-fence acknowledgement and page-rebind proof.

## Remote routes unavailable in the current local protocol

The following 11 tools are declared for the MCP contract but return a typed `capability_unavailable` result in connected mode: `host_space_describe`, `host_lease_takeover_with_control_ticket`, `host_lease_control_ticket`, `host_lease_acknowledge_return_control`, `host_page_bind`, `host_page_mark_lost`, `host_snapshot_put`, `host_snapshot_mark_dirty`, `host_action_enqueue`, `host_action_dispatch`, and `host_event_publish`.

Historical run under the earlier extension-owned architecture (2026-10-06): MCP stdio, the owner-only host socket, Native Messaging, and an extension takeover/rebind were exercised; the managed page returned bound and an unrelated unmanaged tab remained active. The run did not complete the live workflow: `host_snapshot_read` returned `unknown_outcome` when debugger attachment could not be confirmed, and a prior navigation action remains unknown after reconciliation. No snapshot hash or logical ref was produced. The active unmanaged tab remained present with no observed focus theft; one unmanaged-tab close was counted without actor attribution. This is not live evidence for the current host-owned CDP design.

A separate disposable-profile MCP/extension E2E passed extension load, Native Messaging, logical page creation, required group presentation, `host_snapshot_read` with a snapshot hash, and `host_page_close` with the page subsequently observed as `closed`. It produced no logical element refs and does not replace existing-profile or element-ref validation. These runs are historical partial evidence, not release acceptance: the existing-profile snapshot/ref workflow, host user-control/OOPIF/restart evidence, unsupported-route decisions, and Phase 8 release gates remain open, so MCP is **not distribution-ready**.

Existing-profile live run (2026-10-09, dedicated non-everyday Chrome profile with the production extension and registered Native Messaging host): a persistent MCP session created a space, acquired a lease, created a managed page through the extension, and presented the required Chrome tab group (the extension reported the group with the space label). `host_snapshot_read` returned snapshot hash `fnv1a64:2ee5a814cbc904dc`. A second same-principal connection was denied `permission_denied` for lease renew and page close, a different-principal connection was denied `space_forbidden`, and page close, space finish, and space release completed. The run still produced no logical element refs and does not constitute release acceptance; distribution/signing evidence and the remaining release gates are open.

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
