# Agentyc Tool Playbook

Use this as a routing table, not as a checklist. The direct CLI and SDK is the primary interface; MCP is compatibility-only. Scope work to a logical task space and page with the host-backed API. Raw browser/tab/target identifiers and current-tab selection are not canonical identity or authority. Pick the narrowest deterministic operation available.

## Canonical direct routing (CLI and SDK)

Use logical space/page operations for primary workflows. Create or select a task space, then address a logical page for snapshots, actions, and verification.

| Goal                | CLI command           | SDK method                          | Required parameters / Notes                                                           |
| ------------------- | --------------------- | ----------------------------------- | ------------------------------------------------------------------------------------- |
| Create task space   | `space create`        | `client.createSpace(label, opts)`   | Requires `--accept-shared-profile-disclosure` / `acceptSharedProfileDisclosure: true` |
| List task spaces    | `space list`          | `client.listSpaces()`               | Returns active/retained spaces for principal                                          |
| Lease task space    | `space claim`         | `space.claim(opts)`                 | `--space-id <id>` (establishes lease epoch)                                           |
| Renew space lease   | `space renew`         | `space.renew(opts)`                 | `--space-id <id> --lease-epoch <epoch>`                                               |
| Return to user      | `space return`        | `space.returnControl(opts)`         | `--space-id <id> --lease-epoch <epoch>`                                               |
| Finish space        | `space finish`        | `space.finish(opts)`                | `--space-id <id> --lease-epoch <epoch>` (host lifecycle)                              |
| Release space       | `space release`       | `space.release(opts)`               | `--space-id <id> --lease-epoch <epoch>` (host lifecycle)                              |
| Create logical page | `page create`         | `space.newPage(label, opts)`        | `--space-id <id> --lease-epoch <epoch> --label <label>`                               |
| Create managed page | `page create-managed` | `space.newManagedPage(label, opts)` | Optional `--url <url>`, `--title <title>`                                             |
| Close logical page  | `page close`          | `page.close(opts)`                  | `--space-id <id> --page-id <id> --lease-epoch <epoch>`                                |
| Read page snapshot  | `snapshot`            | `page.snapshot(opts)`               | Returns compact normalized DOM with `ref_` handles                                    |
| Execute action      | `action execute`      | `page.action(op, payload, opts)`    | `--operation <op> [--payload <json>]` (supported ops below)                           |
| Read action receipt | `action status`       | `client.actionStatus(actionId)`     | Reads durable receipt status                                                          |
| Reconcile outcome   | `action reconcile`    | `space.reconcileAction(actionId)`   | Used when outcome is unknown; never replays action                                    |
| Resume host events  | `events`              | `client.events(opts)`               | `--after-epoch <epoch> --after-sequence <seq>`                                        |
| Host status         | `host status`         | `client.hostStatus()`               | Checks broker epoch and bridge capabilities                                           |
| Extension status    | `extension status`    | `client.hostStatus()`               | Checks observed extension connection                                                  |

### Supported operations vs. planned methods

Direct actions strictly execute using `action execute --operation <OPERATION>` (CLI) or `page.action(operation, payload)` (SDK). Currently available operations are `navigate`, `click`, `input`, `scroll`, `wait`, `screenshot`, and `close`. `evaluate`, `storage_write`, `cookie_write`, and `upload` fail locally with `permission_denied`; the direct interfaces do not expose a host-issued user-intent-ticket flow.

In the CLI, mutations use `agentyc action execute --operation <OPERATION>`; convenience subcommands such as `agentyc wait url` and `agentyc action click` are not implemented. The Node SDK provides helpers such as `Page.goto()`, `Page.click()`, and `Page.waitForURL()` over available operations. Do not infer CLI commands from SDK methods.

### Boundaries and execution limits

- **Shared-profile disclosure:** Task spaces share the user's existing Chrome profile. Space creation strictly requires explicit acknowledgement (`--accept-shared-profile-disclosure` in CLI; `acceptSharedProfileDisclosure: true` in SDK).
- **No implicit Chrome launch/download:** Agentyc never launches Chrome or downloads Chromium automatically.
- **Current CLI per-invocation nature:** CLI commands run one process per invocation. In offline mode, each process invocation starts a fresh broker, which fences active leases from previous runs. Use the SDK for long-lived session continuity.
- **Live validation limits:** Live browser control requires an enrolled Chrome extension and Native Messaging host on macOS; `--offline` provides a deterministic fake-host seam for testing and CI.

## Primary sequences

### 1. Space creation, snapshot, and action

```bash
# 1. Create a task space with disclosure acknowledgement
agentyc --state-dir /tmp/test-space --offline --json space create \
  --label research-task \
  --accept-shared-profile-disclosure

# 2. Inspect space list and host status
agentyc --state-dir /tmp/test-space --offline --json space list
agentyc --state-dir /tmp/test-space --offline --json host status
```

In Node SDK:

```js
import { connect, createLocalTransport } from "@agentyc/browser";

const client = await connect({ transport });
const space = await client.createSpace("research-task", {
  acceptSharedProfileDisclosure: true,
});
const page = space.page("main");
await page.create();
const snapshot = await page.snapshot();
const receipt = await page.action("click", { ref: "ref_button" });
```

### 2. Unknown outcome reconciliation

When a network blip occurs or an action returns `unknown_outcome`:

```js
try {
  await page.action("click", { ref: "ref_submit" });
} catch (error) {
  if (error.code === "unknown_outcome") {
    // Reconcile the existing receipt; do NOT re-dispatch the click
    const receipt = await space.reconcileAction(error.details?.action_id);
  }
}
```

## MCP compatibility boundary

Agentyc MCP exposes host-backed logical operations over stdio only. Do not route work to the removed `browser_*` tool names. The offline server lists 29 routes; the connected remote catalog declares 30, with 19 supported and 11 returning `capability_unavailable`. A limited existing-profile run passed stdio and extension fence/rebind, but snapshot/action reconciliation and full release workflows remain incomplete, so MCP is not distribution-ready. `host_lease_acknowledge_fence` retries a pending takeover fence without allocating a new epoch. Use [MCP compatibility](../../../../docs/mcp-compatibility.md) for current route details and blockers.

## Removed standalone direct-CDP CLI paths

The standalone `browser`, `run --cdp-url`, and `repl --cdp-url` CLI commands have been removed. Use the host-backed commands in the routing table above or the Node SDK at `packages/agentyc-browser`. The extension's `chrome.debugger` backend remains; direct-CDP test or installation harnesses are internal and are not user interfaces.
