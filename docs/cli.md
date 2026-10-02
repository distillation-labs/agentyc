# Direct CLI

The direct CLI is the host-backed logical interface. It owns durable state through the core/host ledger and emits exactly one JSON value on stdout for each accepted direct command.

## Command shape

```sh
agentyc --state-dir ~/.agentyc/state space create --label research
agentyc --state-dir ~/.agentyc/state space list
agentyc --state-dir ~/.agentyc/state host status
```

The direct command tree is:

- `space create --label LABEL`
- `space list`
- `space claim --space-id SPACE_ID [--ttl MS] [--now MS]`
- `space renew --space-id SPACE_ID --lease-epoch EPOCH [--ttl MS] [--now MS]`
- `space takeover --space-id SPACE_ID [--ttl MS] [--now MS]`
- `space return --space-id SPACE_ID --lease-epoch EPOCH [--now MS]`
- `space finish --space-id SPACE_ID --lease-epoch EPOCH [--now MS]`
- `space release --space-id SPACE_ID --lease-epoch EPOCH [--now MS]`
- `page create --space-id SPACE_ID --lease-epoch EPOCH --label LABEL [--now MS]`
- `page list --space-id SPACE_ID`
- `snapshot --space-id SPACE_ID --page-id PAGE_ID --lease-epoch EPOCH [--now MS]`
- `action status --action-id ACTION_ID`
- `action reconcile --action-id ACTION_ID --lease-epoch EPOCH [--now MS]`
- `events [--after-epoch EPOCH] [--after-sequence SEQUENCE] [--space-id SPACE_ID] [--page-id PAGE_ID]`
- `host status`

Global direct options are `--state-dir PATH`, `--principal PRINCIPAL`, and the explicit `--offline` fake-host seam. `AGENTYC_STATE_DIR`, `AGENTYC_PRINCIPAL`, and `AGENTYC_FAKE_HOST=1` are equivalent environment configuration where applicable.

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

## Browser and extension behavior

The direct path does **not** launch Chrome, download Chrome, discover a browser debugging endpoint, or accept a copied debugging URL. A real extension bridge is required for bridge capabilities such as lease acquisition, page creation, snapshots, and actions. Until that bridge is connected, the CLI returns `capability_unavailable` or `extension_not_connected`; it does not fall back to the legacy runtime.

`--offline` is an explicit deterministic fake-host seam for tests and local contract development. It is not a browser connection and does not grant access to a user's profile.

## State and recovery

The default state directory is `${AGENTYC_STATE_DIR:-~/.agentyc/state}`. Pass `--state-dir` in CI or tests. The host ledger owns the directory, lock, schema, broker epoch, leases, action receipts, snapshots, and events. State files are created with restrictive permissions.

The current CLI opens and closes a broker for each invocation. A restart fences active leases and marks agent-owned spaces orphaned. Consequently, a lease returned by one process is not silently treated as a warm lease by the next process. Use the explicit claim/takeover/reconciliation lifecycle and inspect `host status`/`events` when recovering. A long-lived local host transport is the continuity mechanism for the SDK.

`finish` and `release` are host-owned lifecycle transitions. Both require the current `--lease-epoch`; `--now` can pin the host timestamp for deterministic callers. `finish` completes proven page cleanup and returns a finished space, while `release` releases a finished space without performing implicit browser cleanup.

## Compatibility paths

The existing compatibility commands remain explicit:

- `agentyc mcp [--cdp-url ...]`
- `agentyc serve [--cdp-url ...]`
- `agentyc browser`
- `agentyc run [--cdp-url ...] ...`
- `agentyc repl [--cdp-url ...]`

Those commands retain their legacy CDP/runtime behavior. The direct commands above are separate and do not accept `--cdp-url`.
