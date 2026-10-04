import test from "node:test";
import assert from "node:assert/strict";

import {
  CancelledError,
  DEFAULT_LEASE_TTL_MS,
  DEFAULT_WAIT_TIMEOUT_MS,
  MAX_WAIT_TIMEOUT_MS,
  OPERATION_REGISTRY,
  UnknownOutcomeError,
  actionOperationNames,
  connect,
  operationForAction,
  operationForMethod,
} from "../src/index.mjs";

class FakeTransport {
  constructor() {
    this.calls = [];
    this.handler = (request) => ({
      responses: request.requests.map((entry) => ({
        request_id: entry.request_id,
        ok: true,
        result: {},
      })),
    });
  }

  async request(request) {
    this.calls.push(request);
    return this.handler(request);
  }
}

function responseFor(request, result = {}) {
  return {
    responses: request.requests.map((entry) => ({
      request_id: entry.request_id,
      ok: true,
      result,
    })),
  };
}

test("createSpace requires exact disclosure acknowledgement and sends canonical fields", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });

  await assert.rejects(
    () => client.createSpace("private-looking"),
    (error) => error.code === "permission_denied",
  );
  assert.equal(transport.calls.length, 0);

  transport.handler = (request) =>
    responseFor(request, { space: { space_id: "space_consented", label: "ok" } });
  await client.createSpace("ok", { acceptSharedProfileDisclosure: true });
  assert.deepEqual(transport.calls[0].requests[0].params, {
    label: "ok",
    profile_scope: "shared_existing_profile",
    shared_state_notice: "shared_profile_state",
    isolation_claim: false,
    profile_disclosure_acknowledged: true,
  });
});

test("waits carry logical scope, use the bounded default, and reject invalid timeouts", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  transport.handler = (request) => responseFor(request, { wait: "matched" });

  await client.waitFor({ kind: "event_kind", event: "page.changed" }, {
    spaceId: "space_wait",
    pageId: "page_wait",
  });
  assert.deepEqual(transport.calls[0].requests[0].params, {
    condition: { kind: "event_kind", event: "page.changed" },
    timeout_ms: DEFAULT_WAIT_TIMEOUT_MS,
    space_id: "space_wait",
    page_id: "page_wait",
  });

  await assert.rejects(
    () => client.waitFor({}, { timeoutMs: 0 }),
    (error) => error.code === "invalid_argument",
  );
  await assert.rejects(
    () => client.waitFor({}, { timeoutMs: MAX_WAIT_TIMEOUT_MS + 1 }),
    (error) => error.code === "invalid_argument",
  );
  await assert.rejects(
    () => client.waitFor({}, { pageId: "page_without_space" }),
    (error) => error.code === "invalid_argument",
  );
  assert.equal(transport.calls.length, 1);
});

test("claim, renew, and takeover apply the same default lease TTL", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  transport.handler = (request) => {
    const method = request.requests[0].method;
    return responseFor(request, {
      lease: { lease_epoch: method === "space.renew" ? 2 : 1 },
      lease_epoch: 3,
    });
  };
  const space = client.taskSpace("space_lease");

  await space.claim();
  await space.renew();
  await space.takeover();
  assert.deepEqual(
    transport.calls.map((call) => call.requests[0].params.ttl),
    [DEFAULT_LEASE_TTL_MS, DEFAULT_LEASE_TTL_MS, DEFAULT_LEASE_TTL_MS],
  );
  assert.ok(transport.calls.every((call) => Number.isSafeInteger(call.requests[0].params.now)));
});

test("reconciliation requires a lease epoch before dispatch", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  const space = client.taskSpace("space_reconcile");

  await assert.rejects(
    () => space.reconcileAction("action_reconcile"),
    (error) => error.code === "invalid_argument",
  );
  await assert.rejects(
    () => client.reconcileAction("action_reconcile"),
    (error) => error.code === "invalid_argument",
  );
  assert.equal(transport.calls.length, 0);

  transport.handler = (request) => responseFor(request, { receipt: {} });
  await client.reconcileAction("action_reconcile", 9, 100);
  assert.deepEqual(transport.calls[0].requests[0].params, {
    action_id: "action_reconcile",
    lease_epoch: 9,
    now: 100,
  });
});

test("actions validate the registry and carry now, idempotency, request identity, and deadline", async () => {
  const transport = new FakeTransport();
  const client = await connect({ transport });
  transport.handler = (request) => responseFor(request, { receipt: {} });

  await client.submitAction({
    request_id: "req_action_phase2",
    action_id: "action_phase2",
    idempotency_key: "idem_phase2",
    space_id: "space_action",
    page_id: "page_action",
    lease_epoch: 4,
    operation: "click",
    payload: { selector: "#submit" },
    now: 123,
    deadlineMs: 456,
  });
  const request = transport.calls[0].requests[0];
  assert.equal(request.request_id, "req_action_phase2");
  assert.equal(request.deadline_ms, 456);
  assert.equal(request.idempotency_key, "idem_phase2");
  assert.deepEqual(request.params, {
    request_id: "req_action_phase2",
    action_id: "action_phase2",
    idempotency_key: "idem_phase2",
    space_id: "space_action",
    page_id: "page_action",
    lease_epoch: 4,
    operation: "click",
    payload: { selector: "#submit" },
    now: 123,
  });

  await assert.rejects(
    () => client.submitAction({ space_id: "space_action", operation: "unknown" }),
    (error) => error.code === "invalid_argument",
  );
  assert.equal(transport.calls.length, 1);
});

test("wait cancellation is cancelled while dispatched mutation cancellation is unknown", async () => {
  const transport = new FakeTransport();
  transport.handler = () => new Promise(() => {});
  const client = await connect({ transport });

  const waitController = new AbortController();
  const wait = client.waitFor({}, { signal: waitController.signal });
  waitController.abort(new Error("stop waiting"));
  await assert.rejects(wait, (error) => error instanceof CancelledError);

  const actionController = new AbortController();
  const action = client.submitAction({
    space_id: "space_cancel",
    lease_epoch: 1,
    operation: "click",
    signal: actionController.signal,
  });
  actionController.abort(new Error("stop mutation"));
  await assert.rejects(action, (error) => error instanceof UnknownOutcomeError);
});

test("operation registry exposes every core action and explicit unsupported mappings", () => {
  assert.deepEqual(actionOperationNames(), [
    "navigate",
    "click",
    "input",
    "evaluate",
    "scroll",
    "wait",
    "screenshot",
    "storage_write",
    "cookie_write",
    "upload",
    "close",
  ]);
  for (const operation of actionOperationNames()) {
    assert.equal(operationForAction(operation)?.cli.option, "--operation");
  }
  assert.equal(operationForMethod("lease.acquire")?.sdk, "TaskSpace.claim");
  assert.equal(operationForMethod("page.adopt")?.supported, false);
  assert.ok(
    OPERATION_REGISTRY.filter((entry) => entry.supported).every(
      (entry) => entry.sdk && entry.cli?.command?.length,
    ),
  );
});
