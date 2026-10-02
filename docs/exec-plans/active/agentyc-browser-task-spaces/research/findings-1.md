# Findings — current agentyc gaps and ego-lite patterns worth transferring

## 1. Current control-plane shape

The current path is:

```text
MCP request
  -> BrowserServer
  -> Arc<Mutex<ServerState>>
  -> BrowserRuntime
  -> BrowserSession
  -> one mutable active PageSession
  -> CDP
```

Evidence:

- `crates/agentyc-mcp/src/lib.rs` constructs one `SharedState` per server.
- `crates/agentyc-mcp/src/tools/mod.rs:74-169` stores one runtime plus server-global dialog, logs, mocks, downloads, and trace buffers.
- `crates/agentyc-browser/src/session.rs:54-60` stores one `active_page` and one lifecycle mutex.
- `crates/agentyc/src/main.rs:186-204` creates a fresh `BrowserServer` per legacy HTTP session.

This is adequate for one serialized agent, but not for multiple task spaces. A “tab group” implemented only as a label would not solve ownership because all tools resolve the same mutable page and event buffers.

## 2. Current tab/session hazards

- Public tab identity is the last four characters of a CDP target ID (`agentyc-browser/src/session.rs:35-46`). Collision detection reduces ambiguity but does not make the alias stable across target recreation.
- `switch_tab` detaches the old active session before fully attaching and enabling domains for the new target (`session.rs:328-389`). A failed attach loses the previous page.
- `browser_list_sessions` returns a synthetic `default`; `browser_close_session` ignores its ID and delegates to global close (`agentyc-mcp/src/tools/tabs_session.rs`).
- `close_all` closes every visible page target, including attached-browser tabs not owned by agentyc (`session.rs:297-313`).
- Current docs claim tab-scoped logs and isolated subagent tabs, but the implementation has global/method-only capture paths (`docs/architecture.md:182-191`; `tools/mod.rs:409-560`).

## 3. Current context path and its limit

`browser_get_state` supports `auto`, `full`, `min`, `focus`, and `since_hash` (`crates/agentyc-mcp/src/lib.rs:313-360`; `state.rs:289-407`). It already has useful compaction, but:

- `since_hash` is checked after `get_interactive_elements` performs a fresh page scan.
- The hash includes URL/title/viewport/scroll and backend node/tag/text, but omits role/name, value, disabled state, geometry, attributes, frames, page generation, and space/page topology.
- The output includes all live tabs/current selection, which is unrelated context for a space-owned operation.
- Refs are `e<backend_node_id>` without frame, session, document, navigation, or snapshot provenance.
- A stale or duplicated backend node ID can route to the wrong element; some fallback code expects an attribute that discovery never stamps.

The refactor should make “unchanged” a cache result, not merely a smaller response after a scan. The output should carry snapshot version/hash, space/page identity, token estimate, budget/truncation, and a compact delta or resync marker.

## 4. Reliability and speed hazards

### CDP transport/events

`CdpClient` has pending oneshots and session-aware event subscriptions, but method-only subscriptions remain common and broadcast capacity is 64. `Lagged` is easy to confuse with closed/empty. Pending calls are cleared without typed transport/cancellation errors. Network entries are keyed only by request ID even though request IDs are session-scoped.

### Navigation/waits

`agentyc-runtime/src/lib.rs` and `agentyc-mcp/src/tools/navigation.rs` use short sleeps for navigation/history completion. Waits construct durations directly from user floats and inject page promises/observers without a shared cancellation registry. Request/response waits can miss or misorder metadata.

### Actionability

Discovery records visibility/disabled state, but actions do not revalidate just before dispatch. Overlay/hit-target checks and postconditions are missing. Retries may reuse a stale backend node ID.

### Cleanup/observability

Background capture tasks outlive runtime replacement; dialog fallback can route an event without a session ID to the current active page. Process cleanup uses best-effort process scanning and synchronous Drop behavior. The scenario runner discards results for many steps, creating false-green tests.

## 5. Ego-lite mechanisms to transfer

Transfer the mechanism, not the implementation language or arbitrary execution surface:

1. **Spaces:** one task workspace per goal, one isolated BrowserContext where supported, tabs retained for audit.
2. **Logical page labels:** durable public handles resolve to current target/session state; raw target IDs stay internal.
3. **Atomic ledger:** complete-document replacement, browser-instance identity, released labels, unmanaged targets, explicit user-control boundary, and reconciliation.
4. **Target/session graph:** separate target and session maps, parent/child OOPIF tracking, per-target event queues, bounded event memory, retryable frame lifecycle loss.
5. **Snapshot discipline:** compact semantic output, temporary refs, refresh after mutation, provenance-bearing internal ref registry, invalidation on navigation/raw CDP/DOM changes.
6. **Action gates:** actionability immediately before dispatch, modal/dialog/file chooser boundaries, bounded operation gate, postcondition verification.
7. **Context boundaries:** stop for user confirmation at login challenge, payment, destructive submit, authorization, and similar high-risk states; do not silently continue.

Do not transfer wholesale:

- ego-lite’s Node/native callback execution model;
- its arbitrary browser tool execution capability;
- its marketing token/latency numbers as agentyc acceptance criteria;
- raw target IDs as agent-visible durable handles.

## 6. Recommended invariants

- Every page operation resolves `(space_id, page_id, target_id, session_id, frame_id, navigation_generation)` immediately before use; raw target/session IDs remain internal.
- Every mutation has one space queue and one lease epoch; no stale epoch may mutate.
- Every event is attributed to a browser instance and target/session; missing attribution is not routed to the active page.
- Every close operation proves the page/context is broker-owned; global browser shutdown is separate and explicit.
- Every state response is space/page scoped unless a legacy compatibility request explicitly asks for broader inventory.
- Every ref either validates provenance or returns a typed stale-ref error with a compact current-state hint.
- Every disconnect after command dispatch produces `unknown` until reconciliation; non-idempotent actions are never replayed automatically.

## 7. Superseding target architecture: existing Chrome without MCP as the primary surface

The previous sections describe reusable reliability mechanisms. They do not define the primary integration after the user's clarification. The new path is:

```text
Agent CLI / persistent SDK
  -> owner-readable local agentyc-host protocol
  -> host broker and ledger
  -> Native Messaging bridge
  -> Chrome MV3 extension
       -> chrome.tabs / tabGroups / sidePanel
       -> chrome.debugger CDP transport
       -> content-script/page bridge where needed
  -> user's already-running Chrome

Legacy MCP stdio/HTTP
  -> compatibility adapter
  -> same host broker
```

### Browser boundary

The extension is the browser integration. `chrome.debugger` provides tab-scoped CDP commands/events and flat related-target sessions on the supported Chrome floor; `chrome.tabs` and `chrome.tabGroups` create and present task-space pages; the side panel exposes user control. Native Messaging only carries validated envelopes between the extension and the local host. The host is authoritative because MV3 service workers can terminate and Native Messaging processes can reconnect.

### Profile guarantees

A logical task space is not a Chrome `BrowserContext`. In ordinary installed Chrome, spaces share the Chrome profile's cookies, local/session storage, installed extensions, history, permissions, and download environment. The product must state this plainly. New agent pages are created and placed in a visual tab group by the extension. Existing/user tabs are not adopted automatically; adoption needs an explicit request and user confirmation. Cleanup can remove only pages proven claimed by the broker.

### Primary agent experience

The canonical agent object is a task space, not an active tab. A persistent CLI/SDK connection can create/resume a space, address durable page labels, request compact snapshots, run actions/waits, subscribe to events, hand off/take over, finish, and resume later. Multi-step scripts batch through one connection; the host does not embed arbitrary agent code. New output is structured and contains no `[id] name`, Chrome tab ID, or CDP target ID.

### Extension capability limits that must be explicit

- `chrome.debugger` requires a sensitive permission and exposes only an allowlisted CDP domain set; unsupported browser-level commands return a capability error.
- Debugger detachment caused by tab closure or DevTools is target loss, not permission to replay an action.
- Content scripts cannot call Native Messaging directly; they relay through the service worker, and page messages/DOM text are untrusted.
- Native Messaging host-to-extension messages are limited to 1 MiB, so screenshots, PDFs, and large snapshots use chunking or host-side artifact handles.
- Chrome tab/group IDs are session-scoped; they are internal hints behind durable `page_id`/`space_id` records.
- A stable production extension ID and exact `allowed_origins` are required for Native Messaging; unpacked development uses a separate development identity.

### Current-code migration implications

- `BrowserRuntime::open` currently launches when no `cdp_url` is given; the new default must discover the local host/extension and fail with `extension_not_connected`, never launch Chrome.
- `BrowserSession.active_page`, `PageSession.tab_id`, `TabInfo.target_id`, and `close_all` cannot remain authoritative. They move behind an explicit legacy/direct adapter.
- `crates/agentyc-mcp` must stop owning browser state and call `BrowserBroker` through the host/runtime adapter.
- `crates/agentyc/src/main.rs::run_action` currently opens/closes a runtime per command; the new CLI/SDK uses a persistent host connection.
- `crates/agentyc-browser/src/launcher.rs` and `profile.rs` become explicit legacy/test surfaces; no default path may download, discover, or launch a second browser.
- A new `agentyc-core` crate owns transport-neutral types; a new `agentyc-host` crate owns the local broker, Native Messaging bridge, persistence, and extension adapter; a new `extension/` tree owns Chrome integration and the side panel.

### Release blockers for the new target

Any of the following blocks release of the existing-Chrome experience: a cross-space mutation, a user-tab close, stale-agent mutation after takeover, automatic browser launch/download, a Native Messaging origin bypass, unbounded payload allocation, a lost mutation silently reported as success, or a primary output containing raw IDs/`[id] name`.
