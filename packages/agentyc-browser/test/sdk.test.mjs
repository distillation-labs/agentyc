import test from "node:test";
import assert from "node:assert/strict";

import {
  AgentycError,
  UnknownOutcomeError,
  connect,
  isAgentycError,
} from "../src/index.mjs";

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
  const space = await client.createSpace("alpha");
  const page = space.page("main");
  assert.equal(space.id, "space_alpha");
  assert.equal(page.id, undefined);
  await page.create();
  assert.equal(page.id, "page_main");
  assert.equal(Object.hasOwn(page, "targetId"), false);
  assert.equal(Object.hasOwn(space, "browserId"), false);
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
        { operation: "click" },
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
    () => client.request("action.execute", {}, { mayHaveSideEffects: true }),
    (error) =>
      error instanceof AgentycError &&
      error.code === "reconciliation_required" &&
      error.guidance === "reconcile",
  );
});
