# Local browser SDK

`packages/agentyc-browser` is the typed Node SDK for the logical host interface. It does not contain a browser launcher, evaluator, CDP client, or copied browser endpoint path.

## Connect through a local transport

The SDK accepts an injected transport or a real framed local host socket. An injected transport can be a Native Messaging/local IPC adapter, a test handler, or another local host adapter that implements the same request-batch shape. Without an injected transport, pass `socketPath` or set `AGENTYC_HOST_SOCKET`; `connect({ profile })` fails with `native_host_unavailable` when no socket is configured rather than pretending to be connected.

The built-in `LocalProtocolTransport` uses the Rust envelope contract: one UTF-8 JSON `Envelope` per four-byte big-endian length frame, with the bounded one MiB control-payload default. It performs `hello`/`hello_ok`, request/response correlation, cancellation, event delivery, and cursor-based resume. The Rust host dispatcher remains the authority for method support and canonical errors.

```js
import { connect, createLocalTransport } from "@agentyc/browser";

const transport = createLocalTransport({
  async request(batch) {
    return localHost.send(batch);
  },
  async reconnect() {
    await localHost.reconnect();
  },
});

const client = await connect({ transport });
// Or: const client = await connect({ profile: "default", socketPath });
const space = client.taskSpace("space_research");
const page = space.page("main"); // lazy logical handle
const snapshot = await page.snapshot(); // creates the logical page on first use
```

The public handles are `TaskSpace` and `Page`. Their public identities are logical values only. There are no browser target, tab, session, debugger, or process IDs in the SDK API.

## Creating and leasing spaces

```js
const space = await client.createSpace("research");
await space.claim({ ttl: 60_000 });
const page = await space.newPage("main");
await space.renew({ ttl: 60_000 });
await space.returnControl();
```

`TaskSpace.page(label)` is lazy. `TaskSpace.newPage(label)` sends a logical page-create request immediately. Lease epochs are retained on the handle after claim/renew/takeover and can be supplied explicitly when a caller is recovering state.

The SDK exposes host-backed `finish(options?)` and `release(options?)` transitions. Both accept `{ leaseEpoch, now }` and send those authorization values to the host; the SDK never simulates lifecycle transitions locally.

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

## Snapshots, actions, waits, and events

```js
const snapshot = await page.snapshot();
const receipt = await page.action("click", { ref: "ref_button" });
const events = await space.events({ afterSequence: 0 });
await space.waitFor({ kind: "page_changed" }, { timeoutMs: 10_000 });
```

Snapshots and actions are requested through logical `space_id`/`page_id` values. Unknown action outcomes must be reconciled; the SDK does not replay raw browser commands. Event cursors are broker-epoch scoped. `client.subscribeEvents(listener, { afterEpoch, afterSequence })` resumes retained events and preserves the latest cursor across reconnects; callers must resync when the host reports a lagged or invalid cursor.

## Compatibility boundary

This package is the direct local API. It does not accept a debugging URL and does not call the legacy MCP/runtime commands. The existing Rust `mcp`, `serve`, `browser`, `run`, and `repl` commands remain explicit compatibility paths outside this SDK.
