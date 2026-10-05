import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  AgentycError,
  BrowserClient,
  CancelledError,
  PAGE_HELPER_OPERATIONS,
  UnknownOutcomeError,
  actionOperationNames,
  connect,
  createLocalProtocolTransport,
  methodMayHaveSideEffects,
  operationForAction,
} from "../src/index.mjs";

function frame(envelope) {
  const payload = Buffer.from(JSON.stringify(envelope), "utf8");
  const output = Buffer.allocUnsafe(4 + payload.length);
  output.writeUInt32BE(payload.length, 0);
  payload.copy(output, 4);
  return output;
}

function okResponse(envelope, result = {}) {
  return frame({
    kind: "response",
    protocol: 1,
    request_id: envelope.request_id,
    ok: true,
    result,
    warnings: [],
  });
}

async function until(predicate, label = "condition") {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error(`timed out waiting for ${label}`);
}

/**
 * Programmable local host. `handler(envelope, socket, fixture)` returns true to
 * take over a non-hello envelope; otherwise a default reply is sent.
 */
async function startFixture(handler = () => false) {
  const directory = await mkdtemp("/tmp/ayb-");
  const socketPath = join(directory, "host.sock");
  const sockets = new Set();
  const fixture = { socketPath, hellos: [], received: [], handler };
  const server = net.createServer((socket) => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
    socket.on("error", () => {});
    let buffer = Buffer.alloc(0);
    socket.on("data", (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      while (buffer.length >= 4) {
        const length = buffer.readUInt32BE(0);
        if (buffer.length < length + 4) return;
        const envelope = JSON.parse(buffer.subarray(4, length + 4).toString());
        buffer = buffer.subarray(length + 4);
        if (envelope.kind === "hello") {
          fixture.hellos.push(envelope);
          socket.write(
            frame({
              kind: "hello_ok",
              protocol: 1,
              broker_epoch: 4,
              connection_epoch: fixture.hellos.length,
              capabilities: [],
              resume: { kind: "accepted" },
              host_metadata: {
                host_name: "fixture",
                host_version: "1",
                connection_nonce: envelope.client_metadata.connection_nonce,
              },
            }),
          );
          continue;
        }
        fixture.received.push(envelope);
        if (fixture.handler(envelope, socket, fixture)) continue;
        if (envelope.kind === "request") {
          socket.write(okResponse(envelope, { method: envelope.method }));
        } else if (envelope.kind === "resume") {
          socket.write(
            frame({
              kind: "response",
              protocol: 1,
              request_id: "req_resume-4-7",
              ok: true,
              result: {
                resume_result: JSON.stringify({ kind: "accepted" }),
                cursor: JSON.stringify({ broker_epoch: 4, sequence: 7 }),
                events: JSON.stringify([]),
              },
              warnings: [],
            }),
          );
        }
      }
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(socketPath, resolve);
  });
  fixture.requests = () =>
    fixture.received.filter((envelope) => envelope.kind === "request");
  fixture.stop = async () => {
    for (const socket of sockets) socket.destroy();
    await new Promise((resolve) => server.close(resolve));
    await rm(directory, { recursive: true, force: true });
  };
  return fixture;
}

function holdWaits() {
  const held = [];
  const handler = (envelope, socket) => {
    if (envelope.kind === "request" && envelope.method === "wait.for") {
      held.push({ envelope, socket });
      return true;
    }
    return false;
  };
  return { held, handler };
}

class FakeTransport {
  constructor() {
    this.calls = [];
    this.reconnects = 0;
    this.handler = (request) => ({
      responses: request.requests.map((entry) => ({
        request_id: entry.request_id,
        ok: true,
        result: { method: entry.method },
      })),
    });
  }

  async request(request, options) {
    this.calls.push(request);
    return this.handler(request, options);
  }

  async reconnect() {
    this.reconnects += 1;
  }
}

// ---------------------------------------------------------------------------
// Transport: encoding, dispatch, cancellation, stale sockets, close
// ---------------------------------------------------------------------------

test("a batch is fully encoded before any member is written", async (t) => {
  const fixture = await startFixture();
  t.after(fixture.stop);
  const client = await connect({
    socketPath: fixture.socketPath,
    maxPayloadBytes: 1_000_000,
  });
  t.after(() => client.close());

  await assert.rejects(
    () =>
      client.batch([
        { method: "space.claim", params: { space_id: "space_batch" } },
        { method: "space.list", params: { unencodable: 10n } },
      ]),
    (error) =>
      error instanceof AgentycError &&
      error.code === "invalid_argument" &&
      !(error instanceof UnknownOutcomeError),
  );

  const small = await connect({
    socketPath: fixture.socketPath,
    maxPayloadBytes: 1_500,
  });
  t.after(() => small.close());
  await assert.rejects(
    () =>
      small.batch([
        { method: "space.claim", params: { space_id: "space_batch" } },
        { method: "space.list", params: { blob: "x".repeat(4_000) } },
      ]),
    (error) =>
      error instanceof AgentycError &&
      error.code === "message_too_large" &&
      !(error instanceof UnknownOutcomeError),
  );

  // Same-socket ordering: earlier frames would have reached the host first.
  await client.hostStatus();
  assert.deepEqual(
    fixture.requests().map((request) => request.method),
    ["host.status"],
  );
});

test("onDispatch fires once, only after the first successful write", async (t) => {
  const fixture = await startFixture();
  t.after(fixture.stop);
  const transport = createLocalProtocolTransport({
    socketPath: fixture.socketPath,
  });
  t.after(() => transport.close());
  await transport.connect();
  const make = (id, params = {}) => ({
    request_id: id,
    method: "host.status",
    params,
  });

  let dispatches = 0;
  const onDispatch = () => {
    dispatches += 1;
  };

  await assert.rejects(() =>
    transport.request(
      { requests: [make("req_enc_a"), make("req_enc_b", { bad: 1n })] },
      { onDispatch },
    ),
  );
  assert.equal(dispatches, 0);

  const original = transport._writeFrame.bind(transport);
  transport._writeFrame = () => {
    throw new Error("first write failed");
  };
  await assert.rejects(
    () =>
      transport.request(
        { requests: [make("req_w_a"), make("req_w_b")] },
        { onDispatch },
      ),
    /first write failed/,
  );
  assert.equal(dispatches, 0);
  assert.equal(transport.pending.size, 0);

  let writes = 0;
  transport._writeFrame = (bytes) => {
    writes += 1;
    if (writes === 2) throw new Error("second write failed");
    original(bytes);
  };
  await assert.rejects(
    () =>
      transport.request(
        { requests: [make("req_p_a"), make("req_p_b")] },
        { onDispatch },
      ),
    /second write failed/,
  );
  assert.equal(dispatches, 1);
  // The member that did reach the host is remembered; its answer is ignored.
  assert.ok(transport.ignoredRequestIds.has("req_p_a"));
  assert.ok(!transport.ignoredRequestIds.has("req_p_b"));

  transport._writeFrame = original;
  await transport.request(
    { requests: [make("req_ok_a"), make("req_ok_b")] },
    {
      onDispatch,
    },
  );
  assert.equal(dispatches, 2);
  assert.equal(transport.ignoredRequestIds.size, 0);
  assert.equal(transport.connected, true);
  assert.equal(fixture.hellos.length, 1);
});

test("connection failure before dispatch is not an unknown outcome", async () => {
  const transport = createLocalProtocolTransport({
    socketPath: join(tmpdir(), "agentyc-missing-phase6.sock"),
  });
  let dispatches = 0;
  await assert.rejects(
    () =>
      transport.request(
        {
          requests: [
            { request_id: "req_never", method: "space.claim", params: {} },
          ],
        },
        { onDispatch: () => (dispatches += 1) },
      ),
    (error) => error.code === "native_host_unavailable",
  );
  assert.equal(dispatches, 0);

  const client = new BrowserClient({ transport });
  await assert.rejects(
    () => client.request("space.claim", { space_id: "space_none" }),
    (error) =>
      error instanceof AgentycError &&
      !(error instanceof UnknownOutcomeError) &&
      error.code === "native_host_unavailable",
  );
  await client.close();
});

test("client retries a side-effecting request only when nothing was dispatched", async () => {
  const transport = new FakeTransport();
  transport.dispatchAware = true;
  let attempt = 0;
  transport.handler = (request, options) => {
    attempt += 1;
    if (attempt === 1) throw new Error("connect failed before any write");
    options.onDispatch();
    if (attempt === 2) throw new Error("socket lost after write");
    return {
      responses: request.requests.map((entry) => ({
        request_id: entry.request_id,
        ok: true,
        result: {},
      })),
    };
  };
  const client = await connect({ transport });
  await assert.rejects(
    () => client.request("space.claim", { space_id: "space_retry" }),
    (error) => error instanceof UnknownOutcomeError,
  );
  // Attempt 1 failed before dispatch and was retried (attempt 2), which wrote
  // and then failed: that is the unknown outcome, and it is not retried again.
  assert.equal(attempt, 2);
  assert.equal(transport.reconnects, 1);
});

test("cancelled request IDs stay reserved and late responses are ignored", async (t) => {
  const { held, handler } = holdWaits();
  const fixture = await startFixture((envelope, socket, self) => {
    if (envelope.kind === "resume" && held.length > 0) {
      // Late answer to a cancelled request arrives while a resume is active.
      socket.write(okResponse(held[0].envelope, { wait: "late" }));
    }
    return handler(envelope, socket, self);
  });
  t.after(fixture.stop);
  const client = await connect({ socketPath: fixture.socketPath });
  t.after(() => client.close());

  const controller = new AbortController();
  const wait = client.request(
    "wait.for",
    { condition: "{}" },
    { requestId: "req_wait_late", signal: controller.signal },
  );
  await until(() => held.length === 1, "held wait");
  controller.abort(new Error("stop waiting"));
  await assert.rejects(
    () => wait,
    (error) => error instanceof CancelledError,
  );
  await until(
    () =>
      fixture.received.some(
        (envelope) =>
          envelope.kind === "cancel" && envelope.request_id === "req_wait_late",
      ),
    "cancel envelope",
  );

  // The ID is still reserved: it is never dispatched a second time.
  const before = fixture.requests().length;
  await assert.rejects(
    () => client.request("host.status", {}, { requestId: "req_wait_late" }),
    (error) => error.code === "invalid_argument",
  );
  assert.equal(fixture.requests().length, before);

  // A late response arrives during a resume; neither fails the connection.
  const resumed = await client.resumeEvents({
    afterEpoch: 4,
    afterSequence: 6,
  });
  assert.deepEqual(resumed.cursor, { broker_epoch: 4, sequence: 7 });
  await client.hostStatus();
  assert.equal(fixture.hellos.length, 1);

  // Once the host has answered, the ID is released.
  await client.request("host.status", {}, { requestId: "req_wait_late" });
  assert.equal(fixture.hellos.length, 1);
});

test("a cancelled batch only reserves IDs that have not been answered", async (t) => {
  const fixture = await startFixture((envelope, socket) => {
    if (envelope.request_id === "req_slow") return true;
    return false;
  });
  t.after(fixture.stop);
  const transport = createLocalProtocolTransport({
    socketPath: fixture.socketPath,
  });
  t.after(() => transport.close());
  await transport.connect();
  const controller = new AbortController();
  const batch = transport.request(
    {
      requests: [
        { request_id: "req_fast", method: "host.status", params: {} },
        { request_id: "req_slow", method: "host.status", params: {} },
      ],
    },
    { signal: controller.signal },
  );
  await until(
    () => !transport.pending.has("req_fast") || transport.pending.size === 2,
  );
  await until(
    () => transport.pending.get("req_fast")?.responses.has("req_fast"),
    "fast response",
  );
  controller.abort(new Error("stop"));
  await assert.rejects(
    () => batch,
    (error) => error.cancelled === true,
  );
  assert.deepEqual([...transport.ignoredRequestIds], ["req_slow"]);
});

test("events from a replaced socket never disturb the active connection", async (t) => {
  const { held, handler } = holdWaits();
  const fixture = await startFixture(handler);
  t.after(fixture.stop);
  const transport = createLocalProtocolTransport({
    socketPath: fixture.socketPath,
  });
  t.after(() => transport.close());
  await transport.connect();
  const oldSocket = transport.socket;

  await transport.reconnect();
  assert.notEqual(transport.socket, oldSocket);
  const active = transport.socket;

  const pending = transport.request({
    requests: [
      { request_id: "req_stale_wait", method: "wait.for", params: {} },
    ],
  });
  await until(() => held.length === 1, "held request");

  // Real close of the old socket plus synthetic late events on it.
  await new Promise((resolve) =>
    oldSocket.closed ? resolve() : oldSocket.once("close", resolve),
  );
  oldSocket.emit("data", Buffer.from("garbage that is not a frame header!"));
  oldSocket.emit("error", new Error("late socket error"));
  oldSocket.emit("close");
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(transport.socket, active);
  assert.equal(transport.connected, true);
  assert.equal(transport.pending.size, 1);

  held[0].socket.write(okResponse(held[0].envelope, { wait: "matched" }));
  const response = await pending;
  assert.equal(response.responses[0].result.wait, "matched");
  assert.equal(fixture.hellos.length, 2);
});

test("frames after a connection failure in the same chunk are ignored", async (t) => {
  const fixture = await startFixture();
  t.after(fixture.stop);
  const transport = createLocalProtocolTransport({
    socketPath: fixture.socketPath,
  });
  t.after(() => transport.close());
  await transport.connect();
  const socket = transport.socket;
  const events = [];
  transport.onEvent((event) => events.push(event));
  // Unsupported protocol fails the connection; the event after it must not be
  // delivered even though it is already buffered.
  socket.emit(
    "data",
    Buffer.concat([
      frame({ kind: "event", protocol: 99 }),
      frame({
        kind: "event",
        protocol: 1,
        broker_epoch: 4,
        sequence: 1,
        event_kind: "space_created",
        scope: { kind: "all" },
        payload: {},
      }),
    ]),
  );
  assert.deepEqual(events, []);
  assert.equal(transport.connected, false);
});

test("close() never reopens the connection, reconnect() does", async (t) => {
  const { held, handler } = holdWaits();
  const fixture = await startFixture(handler);
  t.after(fixture.stop);
  const client = await connect({ socketPath: fixture.socketPath });
  t.after(() => client.close());

  const inflight = client.request("wait.for", { condition: "{}" });
  await until(() => held.length === 1, "held wait");
  await client.close();
  await assert.rejects(
    () => inflight,
    (error) => error.code === "native_host_unavailable",
  );
  assert.equal(client.closed, true);
  assert.equal(fixture.hellos.length, 1);

  await assert.rejects(
    () => client.request("host.status"),
    (error) => error.code === "native_host_unavailable",
  );
  await assert.rejects(
    () =>
      client.transport.request({
        requests: [{ request_id: "req_after_close", method: "host.status" }],
      }),
    (error) => error.code === "native_host_unavailable",
  );
  assert.equal(fixture.hellos.length, 1);
  assert.equal(fixture.requests().length, 1);

  await client.reconnect();
  assert.equal(client.closed, false);
  assert.equal(fixture.hellos.length, 2);
  assert.deepEqual(await client.hostStatus(), { method: "host.status" });
});

test("a close during an injected-transport read does not auto-reconnect", async () => {
  const transport = new FakeTransport();
  transport.close = async () => {};
  const client = await connect({ transport });
  transport.handler = async () => {
    await client.close();
    throw new Error("socket closed by close()");
  };
  await assert.rejects(() => client.request("space.list"));
  assert.equal(transport.reconnects, 0);
  await assert.rejects(
    () => client.request("space.list"),
    (error) => error.code === "native_host_unavailable",
  );
  assert.equal(transport.calls.length, 1);
});

test("snapshot, wait, and action fields reach the host as string parameters", async (t) => {
  const fixture = await startFixture();
  t.after(fixture.stop);
  const client = await connect({ socketPath: fixture.socketPath });
  t.after(() => client.close());
  const space = client.taskSpace("space_wire");
  space.leaseEpoch = 3;
  const page = space.page("page_wire");

  await page.snapshot({
    now: 10,
    mode: "focus",
    focusRef: "el_main",
    maxSerializedBytes: 1024,
    tokenBudget: { max_tokens: 50 },
    metadataOnly: true,
    sinceHash: "sha256:abc",
  });
  await page.waitForURL("https://example.test/", {
    after: { broker_epoch: 4, sequence: 7 },
    timeoutMs: 500,
  });
  await page.click({ elementRef: { ref_id: "ref_1" }, x: 1 }, { now: 11 });

  const [snapshot, wait, action] = fixture.requests();
  assert.equal(snapshot.method, "snapshot.read");
  assert.equal(snapshot.params.mode, "focus");
  assert.equal(snapshot.params.focus_ref, "el_main");
  assert.equal(snapshot.params.max_serialized_bytes, "1024");
  assert.equal(snapshot.params.token_budget, '{"max_tokens":50}');
  assert.equal(snapshot.params.metadata_only, "true");
  assert.equal(snapshot.params.since_hash, "sha256:abc");
  assert.equal(snapshot.params.lease_epoch, "3");

  assert.equal(wait.method, "wait.for");
  assert.equal(wait.params.after_epoch, "4");
  assert.equal(wait.params.after_sequence, "7");
  assert.equal("after" in wait.params, false);
  assert.deepEqual(JSON.parse(wait.params.condition), {
    kind: "url",
    matcher: { kind: "exact", value: "https://example.test/" },
  });

  assert.equal(action.method, "action.execute");
  assert.equal(action.params.operation, "click");
  const payload = JSON.parse(action.params.payload);
  assert.ok(Object.values(payload).every((value) => typeof value === "string"));
  assert.deepEqual(JSON.parse(payload.element_ref), { ref_id: "ref_1" });
  assert.equal(payload.x, "1");
});

// ---------------------------------------------------------------------------
// Client: classification, waits
// ---------------------------------------------------------------------------

test("unknown methods are side-effecting unless explicitly opted out", async () => {
  assert.equal(methodMayHaveSideEffects("custom.method"), true);
  assert.equal(methodMayHaveSideEffects("custom.method", false), false);
  assert.equal(methodMayHaveSideEffects("custom.method", true), true);
  assert.equal(methodMayHaveSideEffects("space.list"), false);
  assert.equal(methodMayHaveSideEffects("space.list", true), true);
  assert.equal(methodMayHaveSideEffects("action.execute", false), true);
  assert.equal(methodMayHaveSideEffects(undefined), true);

  const transport = new FakeTransport();
  const client = await connect({ transport });
  const original = transport.handler;
  let failNext = true;
  transport.handler = (request, options) => {
    if (failNext) {
      failNext = false;
      throw new Error("socket closed");
    }
    return original(request, options);
  };
  await assert.rejects(
    () => client.request("custom.method", {}),
    (error) => error instanceof UnknownOutcomeError,
  );
  assert.equal(transport.reconnects, 0);

  failNext = true;
  const result = await client.request(
    "custom.method",
    {},
    { mayHaveSideEffects: false },
  );
  assert.equal(result.method, "custom.method");
  assert.equal(transport.reconnects, 1);
});

test("wait `after` maps to after_epoch and after_sequence", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  const paramsOf = (index) => transport.calls[index].requests[0].params;

  await client.waitFor({ kind: "event_kind", event: "page.changed" });
  assert.equal("after_epoch" in paramsOf(0), false);
  assert.equal("after_sequence" in paramsOf(0), false);
  assert.equal("after" in paramsOf(0), false);

  await client.waitFor({}, { after: { broker_epoch: 4, sequence: 7 } });
  await client.waitFor(
    {},
    { after: { cursor: { broker_epoch: 5, sequence: 1 } } },
  );
  await client.waitFor({}, { after: { afterEpoch: 6, afterSequence: 2 } });
  await client.waitFor({}, { after: 9 });
  await client.waitFor({}, { after: { sequence: 0 } });
  const mapped = [1, 2, 3, 4, 5].map((index) => {
    const params = paramsOf(index);
    assert.equal("after" in params, false);
    return [params.after_epoch, params.after_sequence];
  });
  assert.deepEqual(mapped, [
    [4, 7],
    [5, 1],
    [6, 2],
    [undefined, 9],
    [undefined, 0],
  ]);

  for (const after of [-1, 1.5, {}, null, "7", [], { sequence: -1 }]) {
    await assert.rejects(
      () => client.waitFor({}, { after }),
      (error) => error.code === "invalid_argument",
      JSON.stringify(after),
    );
  }
  assert.equal(transport.calls.length, 6);
});

// ---------------------------------------------------------------------------
// Page helpers
// ---------------------------------------------------------------------------

async function pageFixture() {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  const space = client.taskSpace("space_helpers");
  space.leaseEpoch = 2;
  return { transport, page: space.page("page_helpers"), space };
}

function lastParams(transport) {
  return transport.calls.at(-1).requests[0].params;
}

test("page helpers map only to registered canonical action operations", () => {
  const canonical = new Set(actionOperationNames());
  for (const [helper, operation] of Object.entries(PAGE_HELPER_OPERATIONS)) {
    assert.ok(canonical.has(operation), `${helper} -> ${operation}`);
    assert.ok(operationForAction(operation), helper);
  }
  assert.deepEqual(Object.keys(PAGE_HELPER_OPERATIONS).sort(), [
    "click",
    "evaluate",
    "fill",
    "goto",
    "scroll",
    "type",
  ]);
  assert.ok(Object.isFrozen(PAGE_HELPER_OPERATIONS));
});

test("page helpers build canonical action.execute requests", async () => {
  const { transport, page } = await pageFixture();
  const cases = [
    [
      () => page.goto("https://example.test/"),
      "navigate",
      { url: "https://example.test/" },
    ],
    [() => page.click("#go"), "click", { selector: "#go" }],
    [
      () => page.click({ elementRef: { ref_id: "ref_1" }, x: 1, y: 2.5 }),
      "click",
      { element_ref: '{"ref_id":"ref_1"}', x: "1", y: "2.5" },
    ],
    [
      () => page.type("#name", "agent"),
      "input",
      { selector: "#name", text: "agent" },
    ],
    [() => page.fill("#name", ""), "input", { selector: "#name", text: "" }],
    [() => page.fill(null, "focused"), "input", { text: "focused" }],
    [
      () => page.scroll({ deltaY: 400, x: 1, y: 2 }),
      "scroll",
      { deltaY: "400", x: "1", y: "2" },
    ],
  ];
  for (const [invoke, operation, payload] of cases) {
    transport.handler = (request) => ({
      responses: request.requests.map((entry) => ({
        request_id: entry.request_id,
        ok: true,
        result: { receipt: { status: "ok" } },
      })),
    });
    await invoke();
    const request = transport.calls.at(-1).requests[0];
    assert.equal(request.method, "action.execute");
    assert.equal(request.params.operation, operation);
    assert.deepEqual(request.params.payload, payload, operation);
    assert.equal(request.params.space_id, "space_helpers");
    assert.equal(request.params.page_id, "page_helpers");
    assert.equal(request.params.lease_epoch, 2);
    assert.match(request.params.idempotency_key, /^idem_/);
    assert.ok(request.idempotency_key);
  }
});

test("page helpers pass action options through", async () => {
  const { transport, page } = await pageFixture();
  await page.goto("https://example.test/", {
    requestId: "req_goto_1",
    actionId: "action_goto_1",
    idempotencyKey: "idem_goto_1",
    deadlineMs: 5_000,
    now: 77,
    leaseEpoch: 8,
  });
  const request = transport.calls[0].requests[0];
  assert.equal(request.request_id, "req_goto_1");
  assert.equal(request.deadline_ms, 5_000);
  assert.equal(request.params.action_id, "action_goto_1");
  assert.equal(request.params.now, 77);
  assert.equal(request.params.lease_epoch, 8);
});

test("page helpers reject invalid input before creating a page or dispatching", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  const space = client.taskSpace("space_lazy");
  const lazy = space.page("lazy-label");
  const invalid = [
    () => lazy.goto(""),
    () => lazy.goto("https://example.test/" + "a".repeat(5_000)),
    () => lazy.goto(42),
    () => lazy.click({ nested: { not: "typed" } }),
    () => lazy.click({ x: Number.NaN }),
    () => lazy.type("#a", 5),
    () => lazy.fill("#a"),

    () => lazy.evaluate(""),
    () => lazy.scroll("down"),
    () => lazy.waitForURL(/example/),
    () => lazy.waitForURL({ exact: "a", prefix: "b" }),
    () => lazy.waitForURL({}),
    () => lazy.waitForURL(5),
    () => lazy.snapshot({ mode: "verbose" }),
    () => lazy.snapshot({ tokenizer: "bpe" }),
    () => lazy.snapshot({ metadataOnly: "yes" }),
    () => lazy.snapshot({ maxSerializedBytes: -1 }),
  ];
  for (const [index, invoke] of invalid.entries()) {
    await assert.rejects(
      invoke,
      (error) =>
        error instanceof AgentycError && error.code === "invalid_argument",
      `case ${index}`,
    );
  }
  assert.equal(transport.calls.length, 0);
});

test("unsupported key and select helpers fail before dispatch", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  const page = client.taskSpace("space_unsupported").page("page_unsupported");

  for (const invoke of [
    () => page.press("Enter"),
    () => page.select("#choice", "b"),
  ]) {
    await assert.rejects(
      invoke,
      (error) => error.code === "capability_unavailable",
    );
  }
  assert.equal(transport.calls.length, 0);
});

test("sensitive action helpers fail closed before resolving a lazy page", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  const page = client.taskSpace("space_sensitive").page("lazy-sensitive");
  const operations = ["evaluate", "storage_write", "cookie_write", "upload"];
  const invokes = [
    () => page.evaluate("document.title"),
    () => page.action("click", { sensitive_boundary: "payment" }),
    ...operations
      .slice(1)
      .map((operation) => () => page.action(operation, { value: "x" })),
    () => page.upload("#file", { path: "report.txt" }),
  ];

  for (const invoke of invokes) {
    await assert.rejects(
      invoke,
      (error) =>
        error.code === "permission_denied" &&
        error.message.includes("host-issued user-intent ticket"),
    );
  }
  assert.equal(transport.calls.length, 0);
});

test("page labels resolve existing records and do not create duplicates", async () => {
  const transport = new FakeTransport();
  transport.handler = (request) => ({
    responses: request.requests.map((entry) => ({
      request_id: entry.request_id,
      ok: true,
      result:
        entry.method === "page.list"
          ? {
              pages: [
                {
                  page_id: "page_existing",
                  space_id: "space_lookup",
                  label: "main",
                },
              ],
            }
          : { snapshot: { ok: true } },
    })),
  });
  const client = await connect({ transport });
  const space = client.taskSpace("space_lookup");
  space.leaseEpoch = 2;
  const page = space.page("main");

  const [snapshot] = await Promise.all([
    page.snapshot(),
    page.click({ elementRef: { ref_id: "ref_existing" } }),
  ]);
  assert.deepEqual(snapshot, { snapshot: { ok: true } });
  assert.equal(page.id, "page_existing");
  assert.equal(
    transport.calls.filter((call) => call.requests[0].method === "page.list")
      .length,
    1,
  );
  assert.equal(
    transport.calls.some((call) => call.requests[0].method === "page.create"),
    false,
  );
});

test("page creation is single-flight and requires a claimed lease", async () => {
  const transport = new FakeTransport();
  transport.handler = (request) => ({
    responses: request.requests.map((entry) => ({
      request_id: entry.request_id,
      ok: true,
      result: {
        page: {
          page_id: "page_created",
          space_id: "space_claimed",
          label: "main",
        },
      },
    })),
  });
  const client = await connect({ transport });
  const unclaimed = client.taskSpace("space_unclaimed").page("main");
  await assert.rejects(
    () => unclaimed.create(),
    (error) => error.code === "invalid_argument",
  );
  assert.equal(transport.calls.length, 0);

  const space = client.taskSpace("space_claimed");
  space.leaseEpoch = 3;
  const page = space.page("main");
  const [left, right] = await Promise.all([page.create(), page.create()]);
  assert.equal(left, page);
  assert.equal(right, page);
  assert.equal(transport.calls.length, 1);
  assert.equal(transport.calls[0].requests[0].method, "page.create");
});

test("waitForURL builds a url wait condition scoped to the page", async () => {
  const { transport, page } = await pageFixture();
  await page.waitForURL("https://example.test/done", { timeoutMs: 2_000 });
  let params = lastParams(transport);
  assert.deepEqual(params.condition, {
    kind: "url",
    matcher: { kind: "exact", value: "https://example.test/done" },
  });
  assert.equal(params.timeout_ms, 2_000);
  assert.equal(params.space_id, "space_helpers");
  assert.equal(params.page_id, "page_helpers");

  for (const kind of ["contains", "prefix", "suffix"]) {
    await page.waitForURL({ [kind]: "example" }, { after: 3 });
    params = lastParams(transport);
    assert.deepEqual(params.condition.matcher, { kind, value: "example" });
    assert.equal(params.after_sequence, 3);
  }
});

test("snapshot options are sent to the host with canonical names", async () => {
  const { transport, page } = await pageFixture();
  await page.snapshot({
    now: 5,
    mode: "delta",
    focus: { frame_id: "frame_main" },
    focusRef: "el_1",
    focusElement: "el_2",
    frameId: "frame_x",
    elementKey: "key_1",
    maxSerializedBytes: 2048,
    tokenBudget: { max_tokens: 10 },
    base: { snapshot_hash: "sha256:0" },
    tokenizer: "unicode_scalars",
    metadataOnly: false,
    sinceHash: "sha256:1",
  });
  assert.deepEqual(lastParams(transport), {
    space_id: "space_helpers",
    page_id: "page_helpers",
    lease_epoch: 2,
    now: 5,
    mode: "delta",
    focus: { frame_id: "frame_main" },
    focus_ref: "el_1",
    focus_element: "el_2",
    frame_id: "frame_x",
    element_key: "key_1",
    max_serialized_bytes: 2048,
    token_budget: { max_tokens: 10 },
    base: { snapshot_hash: "sha256:0" },
    tokenizer: "unicode_scalars",
    metadata_only: false,
    since_hash: "sha256:1",
  });

  await page.snapshot({ now: 6 });
  assert.deepEqual(Object.keys(lastParams(transport)).sort(), [
    "lease_epoch",
    "now",
    "page_id",
    "space_id",
  ]);
});
