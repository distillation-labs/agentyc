# Direct CLI

The direct CLI is the host-backed logical interface. The direct CLI and SDK is the primary interface; MCP is compatibility-only. It owns durable state through the core/host ledger and emits exactly one JSON value on stdout for each accepted direct command.

## Command shape

```sh
agentyc --state-dir /tmp/agentyc-state --offline --json space create --label research --accept-shared-profile-disclosure
agentyc --state-dir /tmp/agentyc-state --offline --json space list
agentyc --state-dir /tmp/agentyc-state --offline --json host status
```

The supported direct command tree is:

- `space create --label LABEL --accept-shared-profile-disclosure`
- `space list`
- `space prune [--max-count COUNT]`
- `space claim --space-id SPACE_ID [--ttl MS] [--now MS]`
- `space renew --space-id SPACE_ID --lease-epoch EPOCH [--ttl MS] [--now MS]`
- `space takeover --space-id SPACE_ID [--ttl MS] [--now MS]`
- `space reclaim --space-id SPACE_ID [--control-ticket JSON] [--ttl MS] [--now MS]`
- `space return --space-id SPACE_ID --lease-epoch EPOCH [--now MS]`
- `space pause --space-id SPACE_ID [--ttl MS] [--now MS]`
- `space handoff --space-id SPACE_ID [--ttl MS] [--now MS]`
- `space finish --space-id SPACE_ID --lease-epoch EPOCH [--now MS]`
- `space release --space-id SPACE_ID --lease-epoch EPOCH [--now MS]`
- `page create --space-id SPACE_ID --lease-epoch EPOCH --label LABEL [--now MS]`
- `page create-managed --space-id SPACE_ID --lease-epoch EPOCH --label LABEL [--url URL] [--title TITLE] [--now MS]`
- `page close --space-id SPACE_ID --page-id PAGE_ID --lease-epoch EPOCH [--now MS]`
- `page list --space-id SPACE_ID`
- `page inventory --space-id SPACE_ID`
- `snapshot --space-id SPACE_ID --page-id PAGE_ID --lease-epoch EPOCH [--now MS]`
- `action execute --request-id REQ_ID --action-id ACT_ID --idempotency-key KEY --space-id SPACE_ID [--page-id PAGE_ID] --lease-epoch EPOCH --operation OPERATION [--payload JSON] [--postcondition JSON] [--now MS]`
- `action status --action-id ACTION_ID`
- `action reconcile --action-id ACTION_ID --lease-epoch EPOCH [--now MS]`
- `events [--after-epoch EPOCH] [--after-sequence SEQUENCE] [--space-id SPACE_ID] [--page-id PAGE_ID] [--limit LIMIT]`
- `host status`
- `extension status`
- `wait --condition JSON [--timeout-ms MS] [--after-epoch EPOCH] [--after-sequence SEQUENCE] [--space-id SPACE_ID] [--page-id PAGE_ID]`

Global direct options are `--state-dir PATH`, `--principal PRINCIPAL`, `--profile-binding-id BINDING`, `--offline`, and `--json`. `--json` emits the same structured record in compact form; without it the record is pretty-printed. `AGENTYC_STATE_DIR`, `AGENTYC_PRINCIPAL`, and `AGENTYC_FAKE_HOST=1` are equivalent environment configuration where applicable. Stdout contains one JSON value; diagnostics go to stderr.

### Supported operations vs. planned methods

The CLI strictly dispatches mutations through `action execute --operation <OPERATION>`. Currently available operations are `navigate`, `click`, `input`, `scroll`, `wait`, `screenshot`, and `close`. `evaluate`, `storage_write`, `cookie_write`, and `upload` fail locally with `permission_denied` because the direct CLI has no host-issued user-intent-ticket flow; uploads also require an enabled extension capability.

Planned convenience subcommands (such as `agentyc wait url|network-idle`, `agentyc page navigate`, `agentyc page adopt`, `agentyc host start|stop`, or direct verb subcommands like `agentyc action click`) are not implemented in the CLI. Do not treat planned convenience methods as implemented commands.

### Explicit shared-profile disclosure

Task spaces operate in the user's shared existing Chrome profile (sharing cookies, sessions, and storage), not an isolated container. Creating a space strictly requires explicit acknowledgement with the `--accept-shared-profile-disclosure` flag. Omission returns `permission_denied` with exit code `4`.

### Runnable tested example (offline test seam)

Because Agentyc never launches or downloads Chrome implicitly, testing can be performed deterministically via the in-process fake-host seam (`--offline`):

```bash
# 1. Create a logical space with explicit disclosure acknowledgement
agentyc --state-dir /tmp/test-space --offline --json space create \
  --label research-task \
  --accept-shared-profile-disclosure

# Output:
# {"ok":true,"result":{"lifecycle":"created","space":{"capabilities":["snapshot","action","wait","reconcile"],"label":"research-task","lease":null,"lifecycle":"created","owner":"principal_cli","pages":[],"profile_binding":"bound","retention":{"kind":"retain"},"space_id":"space_space-1","visual_group_hint":null,"warnings":[]},"space_id":"space_space-1"}}

# 2. List spaces visible to the principal
agentyc --state-dir /tmp/test-space --offline --json space list

# 3. Inspect host bridge capabilities
agentyc --state-dir /tmp/test-space --offline --json host status
```

The Phase 2 mapping source is the shared operation registry in `packages/agentyc-browser/src/operations.mjs`; the deterministic evidence index is `tests/phase-2-manifest.yaml`. The direct CLI and SDK use the same logical wire methods and error model.

## Structured results

Success values have this shape:

```json
{
  "ok": true,
  "result": {
    "space_id": "space_space-1",
    "lifecycle": "created"
  }
}
```

Failures have stable typed fields:

```json
{
  "ok": false,
  "error": {
    "code": "extension_not_connected",
    "message": "the browser extension bridge is not connected",
    "retryable": true,
    "guidance": "retry"
  }
}
```

The direct output contains logical `space_id`, `page_id`, and `action_id` values only. It does not print browser target, tab, session, debugger, or process identities, and it never uses `[id] name` formatting.

Direct command failures retain the JSON error record and use stable exit codes: `2` usage/invalid argument, `3` host/protocol/transport unavailable, `4` permission or capability, `5` other runtime failure, `6` timeout/cancelled, and `7` unknown outcome.

## Browser and extension behavior

The direct path does **not** launch Chrome, download Chrome, discover a browser debugging endpoint, or accept a copied debugging URL. A real extension bridge is required for bridge capabilities such as lease acquisition, managed page creation, snapshots, and actions in live mode. Until that bridge is connected, the CLI returns `capability_unavailable` or `extension_not_connected`; it does not fall back to the legacy runtime.

`--offline` is an explicit deterministic fake-host seam for tests and local contract development. It is not a browser connection and does not grant access to a user's profile.

## State and recovery

The default state directory is `${AGENTYC_STATE_DIR:-~/.agentyc/state}`. Pass `--state-dir` in CI or tests. The host ledger owns the directory, lock, schema, broker epoch, leases, action receipts, snapshots, and events. State files are created with restrictive permissions.

The current CLI opens and closes a broker for each invocation. A restart fences active leases and marks agent-owned spaces orphaned. Consequently, a lease returned by one process is not silently treated as a warm lease by the next process. Use the explicit claim/takeover/reconciliation lifecycle and inspect `host status`/`events` when recovering. A long-lived local host transport is the continuity mechanism for the SDK.

`finish` and `release` are host-owned lifecycle transitions. Both require the current `--lease-epoch`; `--now` can pin the host timestamp for deterministic callers. `finish` completes proven page cleanup and returns a finished space, while `release` releases a finished space without performing implicit browser cleanup.

## Phase evidence and live validation limits

The direct local CLI/SDK boundary is primary. MCP is compatibility-only and maps through the host adapter; it does not add a second semantic contract or become a live-browser claim. Live validation requires an enrolled Native Messaging host and extension on macOS connected to a running Chrome instance. Deterministic repository evidence validates broker, ledger, and protocol contracts offline; it does not assert that live Chrome or Web Store extension distribution is configured.

## MCP compatibility boundary

`agentyc mcp` (or `agentyc` with no subcommand) runs the host-backed logical MCP service over stdio only. There is no `agentyc serve` MCP HTTP route, direct-CDP MCP mode, or `browser_*` MCP tool surface. The offline server lists 29 routes; the connected remote catalog declares 30, and 12 currently return typed `capability_unavailable` errors. Headed live Chrome validation has not yet run, so MCP is not distribution-ready. See [MCP compatibility](mcp-compatibility.md).

## Removed legacy CLI paths

The standalone direct-CDP `browser`, `run --cdp-url`, and `repl --cdp-url` commands are not part of the shipped CLI command tree above. The shipped CLI uses host-backed logical subcommands; internal installation and test harnesses that use CDP do not provide a user-facing CLI. This does not remove the Node SDK at `packages/agentyc-browser` or the extension's `chrome.debugger` backend.
