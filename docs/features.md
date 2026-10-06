# Features

## Host-Backed MCP

Agentyc MCP exposes logical task-space operations over stdio. It delegates ownership and policy to the host broker and does not connect to Chrome through CDP or keep browser state in the MCP process. Use `agentyc mcp` when a client requires MCP; the direct CLI and Node SDK remain the primary interfaces.

Available operations cover:

- **Spaces:** list, create with required shared-profile acknowledgement, finish, and release.
- **Leases and control:** acquire and renew leases, take over spaces, and retry a pending takeover fence without allocating another epoch.
- **Pages:** create and list logical pages, with supported close and managed-page operations.
- **Snapshots:** read host-owned snapshot state.
- **Actions:** execute, inspect, and reconcile host-authorized actions.
- **Events:** read cursors and resume scoped event streams.

The connected remote adapter advertises 30 routes. Nineteen are implemented by the current local protocol; the remaining 11 return typed `capability_unavailable` errors. The deterministic offline server exposes the in-process host contract and does not connect to Chrome. See [MCP compatibility](mcp-compatibility.md).

Task spaces share cookies, sessions, and storage within the dedicated Chrome profile; that profile is separate from the user's everyday profile. Creation requires explicit acknowledgement. Page titles are document-controlled; Agentyc does not use tab groups.

## Task-Space Browser Automation

The host/core stack provides logical spaces and pages, leased ownership, snapshots with scoped refs, action receipts, event cursors, recovery, and capability-denial results. The extension only creates tabs; the host owns browser control. See the [local API](api-local.md), [architecture](architecture.md), and [Phase 8 plan](exec-plans/active/agentyc-browser-task-spaces/plans/phase-8-mcp-compatibility.md) for current status and release evidence requirements.
