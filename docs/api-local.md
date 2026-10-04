# Local browser SDK

`packages/agentyc-browser` is the typed Node SDK for the logical host interface. The direct CLI and SDK is the primary interface; MCP is compatibility-only. It does not contain a browser launcher, evaluator, CDP client, or copied browser endpoint path.

## Connect through a local transport

The SDK accepts an injected transport or a real framed local host socket. An injected transport can be a Native Messaging/local IPC adapter, a test handler, or another local host adapter that implements the same request-batch shape. Without an injected transport, pass `socketPath` or set `AGENTYC_HOST_SOCKET`; `connect({ profile })` fails with `native_host_unavailable` when no socket is configured rather than pretending to be connected.

The built-in `LocalProtocolTransport` uses the Rust envelope contract: one UTF-8 JSON `Envelope` per four-byte big-endian length frame, with the bounded one MiB control-payload default. It performs `hello`/`hello_ok`, request/response correlation, cancellation, event delivery, and cursor-based resume. The Rust host dispatcher remains the authority for method support and canonical errors.

No browser is implicitly launched or downloaded. Live automation requires an enrolled extension and Native Messaging host. For deterministic testing and CI, an injected transport provides an offline test seam.

### Runnable tested example (using fake transport seam)

The SDK can be tested deterministically without live Chrome by providing an injected transport handler (as verified by the package test suite):

```js
import { connect, createLocalTransport } from "@agentyc/browser";

// Runnable test harness with simulated host responses:
const transport = createLocalTransport({
  async request(batch) {
    return {
      responses: batch.requests.map((entry) => {
        if (entry.method === "space.create") {
          return {
            request_id: entry.request_id,
            ok: true,
            result: {
              space: { space_id: "space_research", label: entry.params.label },
            },
          };
        }
        if (entry.method === "page.create") {
          return {
            request_id: entry.request_id,
            ok: true,
            result: {
              page: {
                page_id: "page_main",
                space_id: "space_research",
                label: entry.params.label,
              },
            },
          };
        }
        return { request_id: entry.request_id, ok: true, result: {} };
      }),
    };
  },
});

const client = await connect({ transport });
// Create a space with explicit shared-profile disclosure:
const space = await client.createSpace("research", {
  acceptSharedProfileDisclosure: true,
});
const page = space.page("main"); // lazy logical handle
await page.create(); // sends logical page.create
```

The public handles are `TaskSpace` and `Page`. Their public identities are logical values only. There are no browser target, tab, session, debugger, or process IDs in the SDK API.

## Creating and leasing spaces

```js
const space = await client.createSpace("research", {
  acceptSharedProfileDisclosure: true,
});
await space.claim({ ttl: 60_000 });
const page = await space.newPage("main");
await space.renew({ ttl: 60_000 });
await space.returnControl();
```

### Explicit shared-profile disclosure

Task spaces share the user's existing browser profile state. Calling `client.createSpace(label, options)` requires `{ acceptSharedProfileDisclosure: true }`. Omission throws an `AgentycError` with code `invalid_argument`.

`TaskSpace.page(label)` is lazy. `TaskSpace.newPage(label)` sends a logical page-create request immediately. Lease epochs are retained on the handle after claim/renew/takeover and can be supplied explicitly when a caller is recovering state.

The SDK exposes host-backed `finish(options?)` and `release(options?)` transitions. Both accept `{ leaseEpoch, now }` and send those authorization values to the host; the SDK never simulates lifecycle transitions locally.

## Snapshots, actions, waits, and events

```js
const snapshot = await page.snapshot();
const receipt = await page.action("click", { ref: "ref_button" });
const events = await space.events({ afterSequence: 0 });
await space.waitFor({ kind: "page_changed" }, { timeoutMs: 10_000 });
```

### Supported operations vs. planned convenience methods

Actions are dispatched via `page.action(operation, payload?, options?)`. The supported operations are:
`navigate`, `click`, `input`, `evaluate`, `scroll`, `wait`, `screenshot`, `storage_write`, `cookie_write`, `upload`, `close`.

Planned convenience helper methods (such as `page.goto()`, `page.click()`, or `page.type()`) are planned contract wrappers and are not currently implemented on `Page`. Always use `page.action(operation, payload)` directly.

Snapshots and actions are requested through logical `space_id`/`page_id` values. Unknown action outcomes must be reconciled; the SDK does not replay raw browser commands. Event cursors are broker-epoch scoped. `client.subscribeEvents(listener, { afterEpoch, afterSequence })` resumes retained events and preserves the latest cursor across reconnects; callers must resync when the host reports a lagged or invalid cursor.

## Batching

Every request crosses the injected transport as a bounded batch. Callers can send several logical requests in one transport call:

```js
const [spaces, status] = await client.batch([
  { method: "space.list", params: {} },
  { method: "host.status", params: {} },
]);
```

The local transport is generic and injectable, so unit tests can use a deterministic fake without installing packages or starting a browser. Batched responses must contain exactly one matching `request_id` per request; missing, duplicate, or unexpected IDs are protocol failures, not positional fallbacks.

## Reconnect and outcome safety

Read-only transport failures may invoke `transport.reconnect()` once and retry. Lifecycle and mutation methods—including space create/claim/renew/takeover/return/finish/release, page create/close, and action execution—are never blindly retried after transport loss. They become a typed `UnknownOutcomeError` with code `unknown_outcome`, request/action identity, and reconciliation guidance. `AbortSignal` cancels queued/read work; a dispatched mutation cancelled before its response is also `unknown_outcome` because its result must be reconciled.

```js
try {
  await page.action("click", { ref: "ref_button" });
} catch (error) {
  if (error.code === "unknown_outcome") {
    await space.reconcileAction(error.details?.action_id);
  }
}
```

Host error codes map to typed errors, including `ExtensionNotConnectedError`, `CapabilityUnavailableError`, `UnknownOutcomeError`, `ReconciliationRequiredError`, `StaleLeaseError`, and `StaleReferenceError`. Error objects retain `code`, `retryable`, `guidance`, and wire `details`.

## Phase 2 wire contract mapping

The transport-neutral source of truth is `crates/agentyc-core/src/`. The following mappings describe current core records and local host dispatch; they do not assert that a live host, extension, or browser produced the examples. The JSON files under `crates/agentyc-core/tests/fixtures/` are deterministic contract examples, not live evidence.

### Logical identities

`crates/agentyc-core/src/ids.rs` defines validated string identities. Each identity has its own prefix, a non-empty suffix, a maximum encoded length of 128 bytes, and a suffix alphabet of lowercase ASCII letters, digits, `_`, and `-` (the first suffix character is a lowercase letter or digit):

| Logical value        | Wire prefix   |
| -------------------- | ------------- |
| principal            | `principal_`  |
| client               | `client_`     |
| connection nonce     | `nonce_`      |
| profile binding      | `profile_`    |
| task space           | `space_`      |
| page                 | `page_`       |
| document             | `document_`   |
| navigation           | `navigation_` |
| snapshot             | `snapshot_`   |
| frame                | `frame_`      |
| request              | `req_`        |
| action               | `action_`     |
| event                | `evt_`        |
| element ref          | `ref_`        |
| idempotency key      | `idem_`       |
| reconciliation token | `reconcile_`  |
| artifact             | `artifact_`   |
| snapshot element key | `element_`    |

These values identify logical records, not browser handles. Generations, epochs, versions, sequences, and timestamps serialize as unsigned integers. `ContentHash` is the fixed-width `fnv1a64:` plus 16 hexadecimal digits; it is a deterministic corruption/fingerprint check, not an authentication signature. Labels and visual-group hints are descriptive only and confer no authority.

### Local envelopes and dispatch

`protocol.rs` defines the tagged `Envelope` variants `hello`, `hello_ok`, `request`, `response`, `event`, `artifact`, `artifact_begin`, `artifact_chunk`, `artifact_end`, `cancel`, and `resume`. Protocol version is currently `1`. The local frame is a 4-byte big-endian payload length followed by UTF-8 JSON; the default control payload bound is 1 MiB. A request carries `protocol`, `request_id`, dotted `method`, `params`, optional `deadline_ms`, and optional `idempotency_key`. Responses correlate by `request_id` and carry `ok`, `result` or structured `error`, and bounded warnings. The host dispatcher maps methods such as `snapshot.read`, `action.execute`, `action.status`, `action.reconcile`, `events.read`, `events.resume`, and `wait.for` to the core/host records. These method names are dispatch mappings, not additional core enum variants.

### Snapshot and ref contract

`snapshots.rs` defines `SnapshotEnvelope`, `SnapshotDocument`, `SnapshotElement`, `SnapshotDelta`, `SnapshotProvenance`, and `ElementRef`. A snapshot is scoped by logical `space_id` and `page_id` and records snapshot version/hash, topology and document/navigation generations, frame versions, cache state, coverage, coherence, truncation, ref epoch, operation and size/token metrics, and its body. The body is tagged as `elements`, `delta`, or `resync`; the envelope mode is `full`, `compact`, `delta`, or `resync` and must agree with body metadata.

Elements use logical `element_` keys, optional logical parents, bounded element kinds, sorted attributes, and document order. Full/compact bodies must be normalized, ordered, and hash-valid. Delta metadata must agree with the base/result versions and hashes, sequence, and operation count. Delta operations are deterministically ordered and unique by target; the default limits are 1,024 operations and chain depth 8. A missing or mismatched base requires resync rather than blind patch application. FNV-1a-64 hashes the canonical UTF-8 JSON representation of the normalized element list.

Refs are bound to space, page, frame, snapshot version, document generation, navigation generation, and ref epoch. They can be issued or validated only from a complete, coherent, non-truncated snapshot that does not require resync. Scope or provenance mismatch is `stale_ref`; navigation/rebind generation changes fence old refs. The fixtures `snapshot-full.json`, `snapshot-delta.json`, `snapshot-resync.json`, and `ref.json` show valid and resync-oriented representations.

### Action, wait, and event contract

`actions.rs` models an admitted mutation as an `ActionRequest` with request/action/idempotency identities, request hash, logical scope, lease epoch, operation, typed payload, and optional postcondition. `ActionReceipt` separates lifecycle `status` from `dispatch_state`. Queued work may be cancelled before dispatch. Once dispatched, loss of its completion becomes `unknown`, `retryable: false`, `reconciliation_state: required`, with an `unknown_reason`, `reconcile_token`, and `next_action: reconcile`. Reconciliation queries the existing receipt and must not replay the operation. Definitive results record their completion source; some reconciliation failures require explicit confirmation.

`protocol.rs::WaitCondition` represents a page-generation threshold, snapshot hash, or scoped event condition. `wait.for` in `crates/agentyc-host/src/protocol.rs` additionally bounds timeout to 1–60,000 ms and bounds composite condition depth/nodes where those forms are accepted. Wait cancellation uses a versioned `cancel` envelope keyed by `request_id`; a cancellation reason, if present, is 1–256 UTF-8 bytes. Wait cancellation is not action replay or action reconciliation.

`events.rs` defines the stable event names `space.changed`, `page.changed`, `lease.changed`, `action.changed`, `snapshot.changed`, `broker.draining`, and `connection.changed`. Each record has an `evt_` identity, broker epoch, monotonically increasing sequence within that epoch, logical optional space/page scope, generation watermark, coalescing and resync flags, and payload. A cursor is the broker-epoch/sequence pair; it accepts only later events from the same epoch. A lagged or invalid cursor requires a fresh logical snapshot/watermark. `action-unknown.json`, `wait-condition.json`, `wait-cancel.json`, and `event-record.json` are representative records.

### Artifact transfer

`protocol.rs` supports a single bounded `artifact` chunk envelope and an explicit `artifact_begin` → ordered `artifact_chunk` → `artifact_end` transfer. The explicit declaration fixes artifact ID, kind, total bytes, chunk size/count, digest algorithm/hash, and redaction metadata. Chunks are zero-based, connection-epoch scoped, exact-sized (including the final chunk), and must arrive in order. Completion verifies declared byte count, chunk count, and FNV-1a-64 digest. Current bounds are 256 KiB per chunk, 32 MiB per artifact, 256 chunks, 4 MiB in-flight per connection, and 64 MiB cumulative per connection before reset. `artifact-begin.json`, `artifact-chunk-0.json`, `artifact-chunk-1.json`, and `artifact-end.json` form a small deterministic transfer example.

### Errors and explicit user control

`errors.rs::CoreError` has stable `code`, `retryable`, `guidance`, and bounded `message` fields. Codes serialize in snake case. Retryability and guidance come from the code policy; stale lease/expiry directs the client to refresh its lease, stale ref/target replacement/event lag directs it to resync, unknown outcome/reconciliation required directs it to reconcile, and user-control-required directs it to await explicit user control. Transport framing/JSON errors and operation errors retain their own stable codes; callers must not classify them by matching message text. `error-stale-ref.json` shows a core error value.

The user-control flow is host-authoritative. `space.return_control` takes logical `space_id`, current `lease_epoch`, and optional time; it releases that lease and establishes a new fence epoch. Its result includes the logical space, released/fence epochs, lifecycle, and only opaque-safe control-ticket metadata (`space_id`, `broker_epoch`, `fence_epoch`, `opaque: true`), not the ticket token. A later user-owned takeover requires the adapter-held one-time ticket. Fence acknowledgement can retry a pending return without allocating another epoch. Agent mutations remain fenced while user control owns the space. `user-control-return.json` records metadata only; it is not a usable ticket.

These mappings are limited to the checked-in core and the cited local host/adapter paths. They do not freeze a CLI/SDK or MCP compatibility registry and do not claim live evidence.

## Compatibility boundary

This package is the direct local API. It does not accept a debugging URL and does not call the legacy MCP/runtime commands. The existing Rust `mcp`, `serve`, `browser`, `run`, and `repl` commands remain explicit compatibility paths outside this SDK.
