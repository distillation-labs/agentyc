# Features

## Host-Backed MCP

Agentyc MCP exposes logical task-space operations over stdio. It delegates ownership and policy to the host broker and does not connect to Chrome through CDP or keep browser state in the MCP process. Use `agentyc mcp` when a client requires MCP; the direct CLI and Node SDK remain the primary interfaces.

Available operations cover:

- **Spaces:** list, create with required shared-profile acknowledgement, finish, and release.
- **Leases and control:** acquire, renew, and the supported control/fencing operations.
- **Pages:** create and list logical pages, with supported close and managed-page operations.
- **Snapshots:** read host-owned snapshot state.
- **Actions:** execute, inspect, and reconcile host-authorized actions.
- **Events:** read cursors and resume scoped event streams.

The connected remote adapter advertises 30 routes. Twelve routes are not implemented by the current local protocol and return typed `capability_unavailable` errors; see [MCP compatibility](mcp-compatibility.md). The deterministic offline server exposes the in-process host contract and does not connect to Chrome.

Task spaces do not isolate browser profiles. Cookies, sessions, and storage are shared with the enrolled existing Chrome profile. Creation requires explicit acknowledgement. Chrome does not provide an API for renaming an individual tab; page titles are document-controlled, while tab-group titles are visual group labels only.

## Task-Space Browser Automation

The host/core/extension stack provides logical spaces and pages, leased ownership, snapshots with scoped refs, action receipts, event cursors, recovery, and capability-denial results. See the [local API](api-local.md), [architecture](architecture.md), and [Phase 8 plan](exec-plans/active/agentyc-browser-task-spaces/plans/phase-8-mcp-compatibility.md) for current status and release evidence requirements.
