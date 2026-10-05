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

Direct actions strictly execute using `action execute --operation <OPERATION>` (CLI) or `page.action(operation, payload)` (SDK).
Supported operations:

- `navigate`: `{ "url": "https://example.com" }`
- `click`: `{ "ref": "ref_button" }`
- `input`: `{ "ref": "ref_field", "value": "text" }`
- `evaluate`: `{ "expression": "document.title" }`
- `scroll`: `{ "direction": "down", "amount": "300" }`
- `wait`: `{ "condition": "network_idle" }`
- `screenshot`: viewport capture
- `storage_write`, `cookie_write`, `upload`, `close`

In the CLI, mutations use `agentyc action execute --operation <OPERATION>`; convenience subcommands such as `agentyc wait url` and `agentyc action click` are not implemented. The Node SDK does provide `Page.goto()`, `Page.click()`, and `Page.waitForURL()` helpers, which dispatch through the canonical host protocol. Do not infer CLI commands from SDK methods.

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

Agentyc MCP exposes host-backed logical operations over stdio only. Do not route work to the removed `browser_*` tool names. The offline server lists 29 routes; the connected remote catalog declares 30, with 12 returning `capability_unavailable`. Headed live Chrome validation has not run, so MCP is not distribution-ready. Use [MCP compatibility](../../../../docs/mcp-compatibility.md) for current route details and blockers.

## Standalone direct-CDP CLI utilities (not MCP)

The following commands remain separate from MCP:

```bash
agentyc browser --port 9222 --detach
agentyc run --cdp-url <CDP_WEBSOCKET_URL> navigate https://example.com
agentyc run --cdp-url <CDP_WEBSOCKET_URL> evaluate 'document.title'
agentyc repl --cdp-url <CDP_WEBSOCKET_URL>
```

`browser` launches Chrome with a temporary profile and prints its CDP URL. `run` and `repl` require an explicit endpoint at runtime. These utilities do not add an MCP transport or tool surface.
