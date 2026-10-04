# User control and task-space transitions

The host broker owns task-space authority. The MV3 side panel is a user confirmation surface; it cannot grant authority from payload booleans or browser tab/group IDs.

## Controls

| Control   | Host method                          | Effect                                                                                                                           |
| --------- | ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| Pause     | `space.pause`                        | Fence the current lease, cancel queued mutations, classify in-flight work as unknown, and retain pages without user-tab cleanup. |
| Stop      | `space.return_control`               | Fence the lease and transfer the space to explicit user control.                                                                 |
| Take over | `space.takeover` or ticketed reclaim | Establish a fresh lease only after the host/extension fence is acknowledged.                                                     |
| Handoff   | `space.handoff`                      | Fence the current lease and retain the space for a later explicit claimant.                                                      |
| Finish    | `space.finish`                       | Close only individually proven agent-owned pages, then apply retention.                                                          |
| Release   | `space.release`                      | Release only after cleanup proof and retention policy checks; never close a mixed/user group.                                    |

Every sensitive control uses a host-issued, expiring, single-use intent ticket bound to the profile, logical space, action, lease epoch, and side-panel session. A stale, replayed, mis-scoped, or expired ticket fails closed.

## Gesture boundary

`chrome.sidePanel.open()` is user-action gated by Chrome. Agents and Native Messaging requests must not open the panel or steal focus. The extension may configure `openPanelOnActionClick`; actual opening remains a toolbar, keyboard, context-menu, or extension-page gesture.

The panel displays structured logical space/page labels and lifecycle state. It does not display raw tab IDs, target IDs, debugger session IDs, or group IDs as authority.

## Sensitive browser boundaries

The host requires the logical space to be paused or a fresh host-issued, single-use intent ticket before admitting a login challenge, payment, destructive submit, permission change, upload, cookie operation, or page evaluation. A ticket is bound to the logical space and page, canonical action hash, lease epoch, current document generation, profile binding, and authenticated host connection; it expires and cannot be replayed, cancelled, or used in another scope.

Page text, page instructions, focus, clicks, and payload confirmation flags are observations only. They never prove user intent and cannot issue, extend, or replace a ticket.

Logs, dialogs, network metadata, downloads, mocks, and traces are partitioned by logical space/page. Browser handles, raw request IDs, authorization/cookie values, tokens, and request/response bodies are redacted or omitted before they reach the host, side panel, or agent.
