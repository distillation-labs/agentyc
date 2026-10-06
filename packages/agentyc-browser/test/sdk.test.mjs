import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  AgentycError,
  BatchError,
  CancelledError,
  UnknownOutcomeError,
  connect,
  createLocalTransport,
  isAgentycError,
} from "../src/index.mjs";

const boundElementRefPayload = {
  element_ref: JSON.stringify({
    ref_id: "ref_submit",
    element_key: "element_submit",
  }),
};

class FakeTransport {
  constructor() {
    this.calls = [];
    this.reconnects = 0;
    this.failNext = false;
    this.handler = (request) => ({
      responses: request.requests.map((entry) => ({
        request_id: entry.request_id,
        ok: true,
        result: { method: entry.method, params: entry.params },
      })),
    });
  }

  async request(request) {
    this.calls.push(request);
    if (this.failNext) {
      this.failNext = false;
      throw new Error("socket closed");
    }
    return this.handler(request);
  }

  async reconnect() {
    this.reconnects += 1;
  }
}

test("logical space and lazy page handles never expose browser identities", async () => {
  const transport = new FakeTransport();
  transport.handler = (request) => ({
    responses: request.requests.map((entry) => {
      if (entry.method === "space.create") {
        return {
          request_id: entry.request_id,
          ok: true,
          result: { space: { space_id: "space_alpha", label: "alpha" } },
        };
      }
      if (entry.method === "page.create") {
        return {
          request_id: entry.request_id,
          ok: true,
          result: {
            page: {
              page_id: "page_main",
              space_id: "space_alpha",
              label: "main",
            },
          },
        };
      }
      return {
        request_id: entry.request_id,
        ok: true,
        result: { method: entry.method },
      };
    }),
  });
  const client = await connect({ transport });
  const space = await client.createSpace("alpha", {
    acceptSharedProfileDisclosure: true,
  });
  const page = space.page("main");
  assert.equal(space.id, "space_alpha");
  assert.equal(page.id, undefined);
  await page.create({ leaseEpoch: 1 });
  assert.equal(page.id, "page_main");
  assert.equal(Object.hasOwn(page, "targetId"), false);
  assert.equal(Object.hasOwn(space, "browserId"), false);
});

test("offline skill example completes a task-space read-act flow through the SDK", async () => {
  const results = {
    "space.create": { space: { space_id: "space_demo", label: "research" } },
    "space.claim": { space_id: "space_demo", lease: { lease_epoch: 1 } },
    "page.create": {
      page: { page_id: "page_main", space_id: "space_demo", label: "main" },
    },
    "snapshot.read": {
      refs: {
        submit: {
          ref_id: "ref_button",
          element_key: "element_submit",
        },
      },
      snapshot_hash: "demo",
    },
    "action.execute": { action_id: "action_demo", status: "succeeded" },
  };
  const methods = [];
  const transport = createLocalTransport(({ requests }) => ({
    responses: requests.map(({ request_id, method }) => {
      methods.push(method);
      return {
        request_id,
        ok: true,
        result: results[method] ?? {},
      };
    }),
  }));
  const client = await connect({ transport });
  const space = await client.createSpace("research", {
    acceptSharedProfileDisclosure: true,
  });
  await space.claim();
  const page = await space.newPage("main");
  const snapshot = await page.snapshot();
  const receipt = await page.action("click", {
    ...boundElementRefPayload,
    element_ref: JSON.stringify(snapshot.refs.submit),
  });
  await client.close();

  assert.deepEqual(methods, [
    "space.create",
    "space.claim",
    "page.create",
    "snapshot.read",
    "action.execute",
  ]);
  assert.equal(receipt.action_id, "action_demo");
  assert.equal(receipt.status, "succeeded");
});

test("space finish and release send host authorization parameters", async () => {
  const transport = new FakeTransport();
  transport.handler = (request) => ({
    responses: request.requests.map((entry) => {
      if (entry.method === "space.claim") {
        return {
          request_id: entry.request_id,
          ok: true,
          result: {
            space_id: "space_alpha",
            lease: { lease_epoch: 7 },
          },
        };
      }
      return {
        request_id: entry.request_id,
        ok: true,
        result: {
          space_id: "space_alpha",
          lifecycle: entry.method.split(".")[1],
        },
      };
    }),
  });
  const client = await connect({ transport });
  const space = client.taskSpace("space_alpha");

  await space.claim({ ttl: 100, now: 10 });
  await space.finish({ now: 20 });
  await space.release({ leaseEpoch: 7, now: 30 });

  assert.deepEqual(transport.calls[1].requests[0].params, {
    space_id: "space_alpha",
    lease_epoch: 7,
    now: 20,
  });
  assert.deepEqual(transport.calls[2].requests[0].params, {
    space_id: "space_alpha",
    lease_epoch: 7,
    now: 30,
  });
});

test("pause and handoff use host fencing transitions and clear the cached lease", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  const space = client.taskSpace("space_alpha");
  space.leaseEpoch = 7;

  await space.pause({ ttl: 250, now: 11 });
  assert.equal(transport.calls[0].requests[0].method, "space.pause");
  assert.match(transport.calls[0].requests[0].request_id, /^req_/);
  assert.deepEqual(transport.calls[0].requests[0].params, {
    space_id: "space_alpha",
    ttl: 250,
    now: 11,
  });
  assert.equal(space.leaseEpoch, undefined);

  space.leaseEpoch = 8;
  await space.handoff({ now: 12 });
  assert.equal(transport.calls[1].requests[0].method, "space.handoff");
  assert.deepEqual(transport.calls[1].requests[0].params, {
    space_id: "space_alpha",
    ttl: 60_000,
    now: 12,
  });
  assert.equal(space.leaseEpoch, undefined);
});

test("raw and batched requests cannot bypass sensitive SDK action guards", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  const blocked = [
    () => client.request("action.execute", { operation: "evaluate" }),
    () =>
      client.request("action.execute", {
        operation: "click",
        payload: {
          ...boundElementRefPayload,
          sensitive_boundary: "payment",
        },
      }),

    () =>
      client.batch([
        { method: "space.list" },
        {
          method: "action.execute",
          params: { operation: "storage_write" },
        },
      ]),
  ];

  for (const invoke of blocked) {
    await assert.rejects(invoke, (error) => error.code === "permission_denied");
  }
  assert.equal(transport.calls.length, 0);
});

test("batch sends several logical requests through one transport call", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  const result = await client.batch([
    { method: "space.list", params: {} },
    { method: "host.status", params: {} },
  ]);
  assert.equal(transport.calls.length, 1);
  assert.equal(transport.calls[0].requests.length, 2);
  assert.deepEqual(
    result.map((value) => value.method),
    ["space.list", "host.status"],
  );
});

test("read requests reconnect once, while side-effect loss becomes unknown", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  transport.failNext = true;
  const status = await client.request("space.list");
  assert.equal(status.method, "space.list");
  assert.equal(transport.reconnects, 1);

  transport.failNext = true;
  await assert.rejects(
    () =>
      client.request(
        "action.execute",
        { operation: "click", payload: boundElementRefPayload },
        { mayHaveSideEffects: true },
      ),
    (error) =>
      error instanceof UnknownOutcomeError && error.code === "unknown_outcome",
  );
});

test("wire errors map to typed extension and reconciliation errors", async () => {
  const transport = new FakeTransport();
  transport.handler = (request) => ({
    responses: [
      {
        request_id: request.requests[0].request_id,
        ok: false,
        error: {
          code: "extension_not_connected",
          message: "bridge unavailable",
          retryable: true,
          guidance: "retry",
        },
      },
    ],
  });
  const client = await connect({ transport });
  await assert.rejects(
    () => client.request("snapshot", {}),
    (error) =>
      isAgentycError(error) &&
      error.code === "extension_not_connected" &&
      error.retryable,
  );

  transport.handler = (request) => ({
    responses: [
      {
        request_id: request.requests[0].request_id,
        ok: false,
        error: { code: "reconciliation_required", message: "reconcile first" },
      },
    ],
  });
  await assert.rejects(
    () =>
      client.request(
        "action.execute",
        { operation: "click", payload: boundElementRefPayload },
        { mayHaveSideEffects: true },
      ),
    (error) =>
      error instanceof AgentycError &&
      error.code === "reconciliation_required" &&
      error.guidance === "reconcile",
  );
});

function responseFor(requestId, result = {}) {
  return { request_id: requestId, ok: true, result };
}

function responseTransport(handler) {
  const transport = new FakeTransport();
  transport.handler = handler;
  return transport;
}

test("batch correlation rejects duplicate request IDs before dispatch", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  await assert.rejects(
    () =>
      client.batch([
        { method: "space.list", requestId: "req_same" },
        { method: "host.status", requestId: "req_same" },
      ]),
    (error) => error instanceof AgentycError && error.code === "invalid_json",
  );
  assert.equal(transport.calls.length, 0);
});

test("batch correlation rejects missing, duplicate, and unexpected response IDs", async () => {
  const cases = [
    (request) => ({
      responses: [responseFor(request.requests[0].request_id)],
    }),
    (request) => ({
      responses: [
        responseFor(request.requests[0].request_id),
        responseFor(request.requests[0].request_id),
      ],
    }),
    (request) => ({
      responses: [
        responseFor(request.requests[0].request_id),
        responseFor("req_unexpected"),
      ],
    }),
  ];
  for (const handler of cases) {
    const client = await connect({ transport: responseTransport(handler) });
    await assert.rejects(
      () => client.batch([{ method: "space.list" }, { method: "host.status" }]),
      (error) => error instanceof AgentycError && error.code === "invalid_json",
    );
  }
});

test("batch failures preserve successful results and logical failure identity", async () => {
  const transport = responseTransport((request) => ({
    responses: [
      responseFor(request.requests[0].request_id, { ok: "first" }),
      {
        request_id: request.requests[1].request_id,
        ok: false,
        error: { code: "stale_lease", message: "lease changed" },
      },
    ],
  }));
  const client = await connect({ transport });
  await assert.rejects(
    () =>
      client.batch([
        { method: "space.list" },
        {
          method: "action.execute",
          params: {
            action_id: "action_demo",
            operation: "click",
            payload: boundElementRefPayload,
          },
        },
      ]),
    (error) => {
      assert.ok(error instanceof BatchError);
      assert.deepEqual(error.results[0], { ok: "first" });
      assert.equal(error.results[1], undefined);
      assert.equal(
        error.details.failures[0].request_id,
        transport.calls[0].requests[1].request_id,
      );
      assert.equal(error.details.failures[0].action_id, "action_demo");
      assert.equal(error.details.failures[0].error.code, "stale_lease");
      return true;
    },
  );
});

test("central side-effect classification never reconnects lifecycle or page mutations", async () => {
  const methods = [
    "space.create",
    "space.claim",
    "space.renew",
    "space.takeover",
    "space.acknowledge_fence",
    "space.pause",
    "space.handoff",
    "space.return",
    "space.finish",
    "space.release",
    "page.create",
    "page.close",
    "action.execute",
    "action.cancel",
    "action.reconcile",
    "page.navigate",
    "page.adopt",
  ];
  for (const method of methods) {
    const transport = new FakeTransport();
    const client = await connect({ transport });
    transport.failNext = true;
    await assert.rejects(
      () =>
        client.request(
          method,
          method === "action.execute"
            ? { operation: "click", payload: boundElementRefPayload }
            : {},
        ),
      (error) => error instanceof UnknownOutcomeError,
      method,
    );
    assert.equal(transport.reconnects, 0, method);
  }
});

test("unknown outcomes retain request and action identity", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  transport.failNext = true;
  await assert.rejects(
    () =>
      client.request("action.execute", {
        action_id: "action_lost",
        operation: "click",
        payload: boundElementRefPayload,
      }),
    (error) => {
      assert.ok(error instanceof UnknownOutcomeError);
      assert.equal(error.details.action_id, "action_lost");
      assert.equal(error.details.requests[0].action_id, "action_lost");
      assert.match(error.details.request_id, /^req_/);
      assert.equal(error.guidance, "reconcile");
      return true;
    },
  );
});

test("AbortSignal distinguishes pre-dispatch cancellation from dispatched mutation loss", async () => {
  const preDispatchTransport = new FakeTransport();
  const preDispatchClient = await connect({ transport: preDispatchTransport });
  const preDispatch = new AbortController();
  preDispatch.abort();
  await assert.rejects(
    () =>
      preDispatchClient.request(
        "space.list",
        {},
        { signal: preDispatch.signal },
      ),
    (error) => error instanceof CancelledError,
  );
  assert.equal(preDispatchTransport.calls.length, 0);

  const pendingTransport = new FakeTransport();
  pendingTransport.handler = () => new Promise(() => {});
  const pendingClient = await connect({ transport: pendingTransport });
  const mutation = new AbortController();
  const request = pendingClient.request(
    "space.claim",
    { space_id: "space_demo" },
    { signal: mutation.signal },
  );
  mutation.abort(new Error("caller stopped waiting"));
  await assert.rejects(
    () => request,
    (error) => error instanceof UnknownOutcomeError,
  );
});

test("profile-only local connection fails clearly without a configured socket", async () => {
  await assert.rejects(
    () =>
      connect({
        profile: "default",
        socketPath: join(tmpdir(), "missing-agentyc-host"),
      }),
    (error) =>
      error instanceof AgentycError && error.code === "native_host_unavailable",
  );
});

function frame(envelope) {
  const payload = Buffer.from(JSON.stringify(envelope), "utf8");
  const output = Buffer.allocUnsafe(4 + payload.length);
  output.writeUInt32BE(payload.length, 0);
  payload.copy(output, 4);
  return output;
}

function startProtocolFixture(socketPath) {
  const server = net.createServer((socket) => {
    let buffer = Buffer.alloc(0);
    const delayedRequests = [];
    socket.on("data", (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      while (buffer.length >= 4) {
        const length = buffer.readUInt32BE(0);
        if (buffer.length < length + 4) return;
        const payload = buffer.subarray(4, length + 4);
        buffer = buffer.subarray(length + 4);
        const envelope = JSON.parse(payload.toString("utf8"));
        server.received.push(envelope);
        if (envelope.kind === "hello") {
          server.hellos.push(envelope);
          socket.write(
            frame({
              kind: "hello_ok",
              protocol: 1,
              broker_epoch: 4,
              connection_epoch: 1,
              capabilities: [],
              resume: { kind: "accepted" },
              host_metadata: {
                host_name: "fixture",
                host_version: "1",
                connection_nonce: envelope.client_metadata.connection_nonce,
                ...(envelope.client_metadata.profile_binding_id
                  ? {
                      profile_binding_id:
                        envelope.client_metadata.profile_binding_id,
                    }
                  : {}),
              },
            }),
          );
        } else if (envelope.kind === "request") {
          const outOfOrder = envelope.params?.out_of_order === "yes";
          const result = outOfOrder
            ? envelope.method === "space.list"
              ? {
                  space_id: JSON.stringify("space_scalar"),
                  lease_epoch: JSON.stringify(9),
                  lifecycle: JSON.stringify("agent_owned"),
                  scan_performed: JSON.stringify(true),
                  raw_text: "plain_scalar",
                }
              : {
                  broker_epoch: JSON.stringify(4),
                  profile_bound: JSON.stringify(false),
                  raw_text: "plain_status",
                }
            : { method: envelope.method };
          const response = {
            kind: "response",
            protocol: 1,
            request_id: envelope.request_id,
            ok: true,
            result,
            warnings: [],
          };
          if (outOfOrder) {
            delayedRequests.push(response);
            if (delayedRequests.length === 2) {
              for (const delayed of delayedRequests.toReversed()) {
                socket.write(frame(delayed));
              }
              delayedRequests.length = 0;
            }
          } else {
            socket.write(frame(response));
          }
        } else if (envelope.kind === "resume") {
          socket.write(
            frame({
              kind: "event",
              protocol: 1,
              broker_epoch: 4,
              sequence: 7,
              event_kind: "space_created",
              scope: { kind: "all" },
              payload: {},
            }),
          );
          socket.write(
            frame({
              kind: "response",
              protocol: 1,
              request_id: "req_resume-4-7",
              ok: true,
              result: {
                resume_result: JSON.stringify({ kind: "accepted" }),
                cursor: JSON.stringify({ broker_epoch: 4, sequence: 7 }),
                events: JSON.stringify([
                  {
                    broker_epoch: 4,
                    sequence: 7,
                    event_kind: "space_created",
                  },
                ]),
              },
              warnings: [],
            }),
          );
        }
      }
    });
  });
  server.hellos = [];
  server.received = [];
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(socketPath, () => resolve(server));
  });
}

test("local protocol transport uses Rust-compatible framing, handshake, events, and resume", async () => {
  const directory = await mkdtemp("/tmp/ayb-");
  const socketPath = join(directory, "host.sock");
  const server = await startProtocolFixture(socketPath);
  let client;
  try {
    client = await connect({ socketPath, profile: "default" });
    assert.deepEqual(await client.hostStatus(), { method: "host.status" });

    const [scalarResult, statusResult] = await client.batch([
      { method: "space.list", params: { out_of_order: "yes" } },
      { method: "host.status", params: { out_of_order: "yes" } },
    ]);
    assert.deepEqual(scalarResult, {
      space_id: "space_scalar",
      lease_epoch: 9,
      lifecycle: "agent_owned",
      scan_performed: true,
      raw_text: "plain_scalar",
    });
    assert.deepEqual(statusResult, {
      broker_epoch: 4,
      profile_bound: false,
      raw_text: "plain_status",
    });

    const received = [];
    const unsubscribe = await client.subscribeEvents(
      (event) => received.push(event),
      { afterEpoch: 4, afterSequence: 6 },
    );
    assert.equal(received[0].sequence, 7);
    assert.deepEqual(
      await client.resumeEvents({ afterEpoch: 4, afterSequence: 6 }),
      {
        resume_result: { kind: "accepted" },
        cursor: { broker_epoch: 4, sequence: 7 },
        events: [
          {
            broker_epoch: 4,
            sequence: 7,
            event_kind: "space_created",
          },
        ],
      },
    );
    await client.reconnect();
    assert.deepEqual(server.hellos.at(-1).resume_from, {
      broker_epoch: 4,
      sequence: 7,
    });
    assert.equal(
      server.received.filter((envelope) => envelope.kind === "resume").length,
      3,
      "reconnect performs an explicit cursor resume",
    );
    assert.equal(unsubscribe(), true);
  } finally {
    await client?.close();
    server.closeAllConnections?.();
    await new Promise((resolve) => server.close(resolve));
    await rm(directory, { recursive: true, force: true });
  }
});
