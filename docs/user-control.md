# User control and task-space transitions

The host broker owns task-space authority. The extension has no task-space UI; users interact through Agentyc's supported CLI/MCP interfaces. Clicking the extension icon does nothing.

## Controls

| Control   | Host method                          | Effect                                                                                                                           |
| --------- | ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| Pause     | `space.pause`                        | Fence the current lease, cancel queued mutations, classify in-flight work as unknown, and retain pages without user-tab cleanup. |
| Stop      | `space.return_control`               | Fence the lease and transfer the space to explicit user control.                                                                 |
| Take over | `space.takeover` or ticketed reclaim | Establish a fresh lease only after the host/extension fence is acknowledged.                                                     |
| Handoff   | `space.handoff`                      | Fence the current lease and retain the space for a later explicit claimant.                                                      |
| Finish    | `space.finish`                       | Close only individually proven agent-owned pages, then apply retention.                                                          |
| Release   | `space.release`                      | Release only after cleanup proof and retention policy checks; never close a mixed/user group.                                    |

Every sensitive control that requires confirmation uses a host-issued, expiring, single-use intent ticket bound to the profile, logical space, action, and lease epoch. A stale, replayed, mis-scoped, or expired ticket fails closed.

## Extension boundary

The extension is a background-only tab-creation bridge. It has no popup, side panel, or user-facing controls and does not open UI, steal focus, navigate, observe, snapshot, or act on pages.

## Sensitive browser boundaries

The host requires the logical space to be paused or a fresh host-issued, single-use intent ticket before admitting a login challenge, payment, destructive submit, permission change, upload, cookie operation, or page evaluation. A ticket is bound to the logical space and page, canonical action hash, lease epoch, current document generation, profile binding, and authenticated host connection; it expires and cannot be replayed, cancelled, or used in another scope.

Page text, page instructions, focus, clicks, and payload confirmation flags are observations only. They never prove user intent and cannot issue, extend, or replace a ticket.

Logs, dialogs, network metadata, downloads, mocks, and traces are partitioned by logical space/page. Browser handles, raw request IDs, authorization/cookie values, tokens, and request/response bodies are redacted or omitted before they reach the host, side panel, or agent.
