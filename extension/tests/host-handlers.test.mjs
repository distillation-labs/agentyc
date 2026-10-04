import test from "node:test";
import assert from "node:assert/strict";
import { createServiceWorker } from "../src/service-worker.mjs";
import { assertNoRawBrowserIdentifiers } from "../src/protocol.mjs";
import { FakeChrome, makeHostHelloOk } from "./fake-chrome.mjs";

const wait = () => new Promise((resolve) => setImmediate(resolve));

function proof(spaceId, pageId, suffix = "one") {
  return {
    issued_by_host: true,
    proof_id: `claim-${suffix}-proof`,
    kind: "claim",
    space_id: spaceId,
    page_id: pageId,
    lease_epoch: 1,
    expires_at: Date.now() + 60_000,
  };
}

async function boot(chrome) {
  const worker = createServiceWorker({
    chromeApi: chrome,
    autoReconnect: false,
  });
  await worker.start();
  const port = chrome.lastPort;
  const hello = port.sent.find((message) => message.kind === "hello");
  port.receive(makeHostHelloOk(hello));
  await wait();
  return { worker, port, hello };
}

async function managedPage(
  worker,
  spaceId = "space_handlers",
  pageId = "page_handlers",
) {
  await worker.tabs.createAgentPage({
    spaceId,
    pageId,
    leaseEpoch: 1,
    url: "https://managed.test/",
    ownershipProof: proof(spaceId, pageId),
  });
  return worker.tabs.getInternalByPage(pageId);
}

function sender(chrome, record) {
  return {
    id: chrome.runtime.id,
    tab: { id: record.rawTabId },
    url: record.url,
    origin: new URL(record.url).origin,
  };
}

async function registerContent(worker, chrome, record) {
  const documentId = "document_handlers_1";
  const nonce = "nonce_handlers_1";
  const result = await worker.handleRuntimeMessage(
    {
      type: "agentyc.content.ready",
      version: 1,
      nonce,
      document_id: documentId,
      expires_at: Date.now() + 10_000,
    },
    sender(chrome, record),
  );
  assert.equal(result.ok, true);
}

async function resolvePendingContent(worker, chrome, record, values) {
  for (const [requestId, pending] of [...worker.contentPending]) {
    const value = values[pending.operation] ?? {};
    await worker.handleRuntimeMessage(
      {
        type: "agentyc.content.result",
        version: 1,
        request_id: requestId,
        nonce: pending.nonce,
        document_id: pending.documentId,
        operation: pending.operation,
        expires_at: pending.expiresAt,
        ok: true,
        result: value,
      },
      sender(chrome, record),
    );
  }
}

test("event.wait resolves from the extension event stream with logical scope", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker } = await boot(chrome);
  await managedPage(worker, "space_wait", "page_wait");
  const pending = worker.handleHostRequest({
    kind: "request",
    request_id: "req_event_wait",
    method: "event.wait",
    space_id: "space_wait",
    page_id: "page_wait",
    params: {
      condition: JSON.stringify({
        kind: "payload",
        key: "state",
        value: "ready",
      }),
      timeout_ms: 1000,
    },
  });
  await wait();
  worker.handleExtensionEvent("page.changed", {
    space_id: "space_wait",
    page_id: "page_wait",
    state: "ready",
  });
  const response = await pending;
  assert.equal(response.ok, true);
  assert.equal(response.result.payload.state, "ready");
  assert.equal(response.result.space_id, "space_wait");
  worker.stop();
});

test("snapshot.read uses the managed content bridge and returns bounded logical output", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker } = await boot(chrome);
  const record = await managedPage(worker);
  await registerContent(worker, chrome, record);

  const pending = worker.handleHostRequest({
    kind: "request",
    request_id: "req_snapshot_read",
    method: "snapshot.read",
    space_id: "space_handlers",
    page_id: "page_handlers",
    lease_epoch: 1,
    params: {},
  });
  await wait();
  await resolvePendingContent(worker, chrome, record, {
    "document.title": { title: "Managed title" },
    "document.text": { text: "bounded page text" },
    "aria.summary": {
      nodes: [{ role: "button", name: "Continue", disabled: false }],
    },
  });
  const response = await pending;

  assert.equal(response.ok, true);
  const snapshot = response.result;
  assert.equal(snapshot.space_id, "space_handlers");
  assert.equal(snapshot.page_id, "page_handlers");
  assert.equal(snapshot.delta_or_elements.kind, "elements");
  assert.ok(snapshot.delta_or_elements.elements.length >= 3);
  assert.equal(snapshot.delta_or_elements.elements.at(-1).text, "Continue");
  assert.match(snapshot.snapshot_hash, /^fnv1a64:[0-9a-f]{16}$/);
  assert.ok(
    new TextEncoder().encode(JSON.stringify(snapshot)).byteLength <= 256 * 1024,
  );
  assertNoRawBrowserIdentifiers(snapshot);
  assert.equal(chrome.debuggerCommands.length, 0);
  worker.stop();
});

test("snapshot.read can use the read-only debugger fallback without exposing browser handles", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, url: "https://managed.test/" }],
  });
  const { worker } = await boot(chrome);
  const record = await managedPage(worker, "space_debugger", "page_debugger");
  const originalSendCommand = chrome.debugger.sendCommand;
  chrome.debugger.sendCommand = async (source, method, params) => {
    if (method === "DOMSnapshot.captureSnapshot") {
      chrome.debuggerCommands.push({ source: { ...source }, method, params });
      return {
        strings: ["#document", "HTML", "Hello"],
        documents: [
          {
            nodes: {
              nodeName: [0, 1, 2],
              nodeType: [9, 1, 3],
              nodeValue: [0, 0, 2],
              parentIndex: [-1, 0, 1],
              attributes: [[], [], []],
            },
          },
        ],
      };
    }
    return originalSendCommand(source, method, params);
  };

  const response = await worker.handleHostRequest({
    kind: "request",
    request_id: "req_debugger_snapshot",
    method: "snapshot.read",
    space_id: "space_debugger",
    page_id: "page_debugger",
    lease_epoch: 1,
    params: { source: "debugger" },
  });

  assert.equal(response.ok, true);
  assert.equal(response.result.delta_or_elements.elements.at(-1).text, "Hello");
  assertNoRawBrowserIdentifiers(response.result);
  assert.equal(
    chrome.debuggerCommands.some(
      (command) => command.method === "DOMSnapshot.captureSnapshot",
    ),
    true,
  );
  worker.stop();
});

test("action routing maps the Rust debugger wire, emits a logical receipt, and rejects non-action commands", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker, port } = await boot(chrome);
  await managedPage(worker, "space_action", "page_action");

  const success = await worker.handleHostRequest({
    kind: "request",
    request_id: "req_action_execute",
    action_id: "action_execute",
    method: "debugger.command",
    space_id: "space_action",
    page_id: "page_action",
    lease_epoch: 1,
    params: {
      method: "Page.navigate",
      postcondition: {
        kind: "page_generation",
        document_generation: 1,
      },
      payload: { url: "https://next.test/" },
    },
  });
  assert.equal(success.ok, true);
  assert.equal(chrome.debuggerCommands.at(-1).method, "Page.navigate");
  assert.deepEqual(chrome.debuggerCommands.at(-1).params, {
    url: "https://next.test/",
  });
  assert.equal(success.result.receipt.action_id, "action_execute");
  assert.equal(success.result.receipt.outcome, "succeeded");
  assert.equal(success.result.receipt.postcondition_satisfied, true);
  assertNoRawBrowserIdentifiers(success.result);
  const receiptEvent = port.sent.find(
    (message) => message.kind === "event" && message.event === "action.receipt",
  );
  assert.ok(receiptEvent);
  assert.equal(receiptEvent.payload.action_id, "action_execute");
  assertNoRawBrowserIdentifiers(receiptEvent.payload);

  const routed = await worker.handleHostRequest({
    kind: "request",
    request_id: "req_action_route",
    action_id: "action_route",
    method: "action.execute",
    space_id: "space_action",
    page_id: "page_action",
    lease_epoch: 1,
    params: {
      operation: "input",
      payload: { text: "safe input" },
    },
  });
  assert.equal(routed.ok, true);
  assert.equal(chrome.debuggerCommands.at(-1).method, "Input.insertText");
  assert.deepEqual(chrome.debuggerCommands.at(-1).params, {
    text: "safe input",
  });
  assert.equal(routed.result.receipt.action_id, "action_route");

  const before = chrome.debuggerCommands.length;
  const denied = await worker.handleHostRequest({
    kind: "request",
    request_id: "req_action_denied",
    action_id: "action_denied",
    method: "action.execute",
    space_id: "space_action",
    page_id: "page_action",
    lease_epoch: 1,
    params: {
      operation: "unsupported",
      payload: {},
    },
  });
  assert.equal(denied.ok, false);
  assert.equal(denied.error.code, "capability_unavailable");
  assert.equal(chrome.debuggerCommands.length, before);
  worker.stop();
});

test("actionability evidence rejects unsafe element states before debugger dispatch", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker } = await boot(chrome);
  const record = await managedPage(
    worker,
    "space_actionability",
    "page_actionability",
  );
  const base = {
    connected: true,
    visible: true,
    disabled: false,
    readonly: false,
    covered: false,
    overlay_present: false,
    hit_target: true,
    moving: false,
    offscreen: false,
    user_control: false,
    target_generation: record.targetGeneration,
    navigation_generation: record.navigationGeneration,
    document_generation: record.documentGeneration,
  };
  const cases = [
    ["hidden", { visible: false }, "permission_denied", "click"],
    ["disabled", { disabled: true }, "permission_denied", "click"],
    ["readonly", { readonly: true }, "permission_denied", "input"],
    ["covered", { covered: true }, "permission_denied", "click"],
    ["moving", { moving: true }, "permission_denied", "click"],
    ["offscreen", { offscreen: true }, "permission_denied", "click"],
    ["wrong-hit-target", { hit_target: false }, "permission_denied", "click"],
    [
      "rerendered",
      { document_generation: record.documentGeneration + 1 },
      "stale_generation",
      "click",
    ],
    ["user-control", { user_control: true }, "user_control_required", "click"],
  ];
  for (const [suffix, change, code, operation] of cases) {
    const response = await worker.handleHostRequest({
      kind: "request",
      request_id: `req_actionability_${suffix}`,
      action_id: `action_actionability_${suffix}`,
      method: "action.execute",
      space_id: "space_actionability",
      page_id: "page_actionability",
      lease_epoch: 1,
      params: {
        operation,
        actionability_evidence: { ...base, ...change },
        payload:
          operation === "input"
            ? { text: "safe input" }
            : { type: "mousePressed", x: 1, y: 1 },
      },
    });
    assert.equal(response.ok, false, suffix);
    assert.equal(response.error.code, code, suffix);
  }
  assert.equal(chrome.debuggerCommands.length, 0);
  worker.stop();
});

test("actionability frame scope and file actions fail closed", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker } = await boot(chrome);
  const record = await managedPage(
    worker,
    "space_frame_action",
    "page_frame_action",
  );
  const frame = await worker.handleHostRequest({
    kind: "request",
    request_id: "req_oopif_actionability",
    action_id: "action_oopif_actionability",
    method: "action.execute",
    space_id: "space_frame_action",
    page_id: "page_frame_action",
    lease_epoch: 1,
    params: {
      operation: "click",
      frame_scope: "logical-oopif",
      actionability_evidence: {
        connected: true,
        visible: true,
        disabled: false,
        readonly: false,
        covered: false,
        overlay_present: false,
        hit_target: true,
        moving: false,
        offscreen: false,
        user_control: false,
        target_generation: record.targetGeneration,
        navigation_generation: record.navigationGeneration,
        document_generation: record.documentGeneration,
      },
      payload: { type: "mousePressed", x: 1, y: 1 },
    },
  });
  assert.equal(frame.ok, false);
  assert.equal(frame.error.code, "stale_generation");
  const upload = await worker.handleHostRequest({
    kind: "request",
    request_id: "req_file_actionability",
    action_id: "action_file_actionability",
    method: "action.execute",
    space_id: "space_frame_action",
    page_id: "page_frame_action",
    lease_epoch: 1,
    params: { operation: "upload", payload: { path: "file.txt" } },
  });
  assert.equal(upload.ok, false);
  assert.equal(upload.error.code, "upload_denied");
  assert.equal(chrome.debuggerCommands.length, 0);
  worker.stop();
});

test("snapshot-hash postconditions include observed hash and honest outcome", async () => {
  const chrome = new FakeChrome();
  const { worker } = await boot(chrome);
  const context = {
    action_id: "action_snapshot_check",
    request_id: "req_snapshot_check",
    space_id: "space_snapshot_check",
    page_id: "page_snapshot_check",
    lease_epoch: 1,
    postcondition: {
      kind: "snapshot_hash",
      snapshot_hash: "fnv1a64:1111111111111111",
    },
  };
  const observedHash = "fnv1a64:2222222222222222";
  worker.observedPageGeneration = () => ({
    target_generation: 1,
    navigation_generation: 1,
    document_generation: 1,
    browser_session_epoch: worker.metadata.browserSessionEpoch,
  });
  worker.readSnapshot = async () => ({ snapshot_hash: observedHash });

  const evaluation = await worker.evaluateActionPostcondition(
    context,
    context.request_id,
  );
  worker.rememberActionReceipt(context, evaluation.outcome, {
    code: evaluation.code,
    message: evaluation.code,
  });
  const receipt = worker.actionReceiptFor(context.action_id);
  assert.equal(evaluation.outcome, "failed");
  assert.equal(receipt.outcome, "failed");
  assert.equal(receipt.postcondition_satisfied, false);
  assert.equal(receipt.postcondition_observed.snapshot_hash, observedHash);
  worker.stop();
});

test("action.reconcile is read-only and reports a bounded unknown receipt", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker } = await boot(chrome);
  const record = await managedPage(worker, "space_reconcile", "page_reconcile");
  worker.actionReceipts.set("action_unknown", {
    action_id: "action_unknown",
    request_id: "req_unknown",
    space_id: "space_reconcile",
    page_id: "page_reconcile",
    lease_epoch: 1,
    method: "debugger.command",
    target_generation: record.targetGeneration,
    navigation_generation: record.navigationGeneration,
    document_generation: record.documentGeneration,
    outcome: "unknown",
  });

  const before = chrome.debuggerCommands.length;
  const response = await worker.handleHostRequest({
    kind: "request",
    request_id: "req_reconcile",
    method: "action.reconcile",
    space_id: "space_reconcile",
    page_id: "page_reconcile",
    lease_epoch: 1,
    params: { action_id: "action_unknown" },
  });

  assert.equal(response.ok, true);
  assert.equal(response.result.outcome, "unknown");
  assert.equal(chrome.debuggerCommands.length, before);
  assertNoRawBrowserIdentifiers(response.result);
  worker.stop();
});

test("handlers reject stale generations, stale leases, and cross-space ownership", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker } = await boot(chrome);
  const record = await managedPage(worker, "space_fenced", "page_fenced");

  const staleGeneration = await worker.handleHostRequest({
    kind: "request",
    request_id: "req_stale_generation",
    action_id: "action_stale_generation",
    method: "action.execute",
    space_id: "space_fenced",
    page_id: "page_fenced",
    lease_epoch: 1,
    params: {
      operation: "navigate",
      expected_target_generation: record.targetGeneration + 1,
      payload: { url: "https://stale-generation.test/" },
    },
  });
  assert.equal(staleGeneration.ok, false);
  assert.equal(staleGeneration.error.code, "stale_generation");
  assert.equal(chrome.debuggerCommands.length, 0);

  const staleLease = await worker.handleHostRequest({
    kind: "request",
    request_id: "req_stale_lease",
    action_id: "action_stale_lease",
    method: "snapshot.read",
    space_id: "space_fenced",
    page_id: "page_fenced",
    lease_epoch: 2,
    params: {},
  });
  assert.equal(staleLease.ok, false);
  assert.equal(staleLease.error.code, "stale_lease");

  const wrongSpace = await worker.handleHostRequest({
    kind: "request",
    request_id: "req_wrong_space",
    action_id: "action_wrong_space",
    method: "action.execute",
    space_id: "space_other",
    page_id: "page_fenced",
    lease_epoch: 1,
    params: {
      operation: "navigate",
      payload: { url: "https://wrong-space.test/" },
    },
  });
  assert.equal(wrongSpace.ok, false);
  assert.equal(wrongSpace.error.code, "page_not_found");
  worker.stop();
});

test("response transport loss preserves an unknown mutation receipt without replay", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker } = await boot(chrome);
  await managedPage(worker, "space_loss", "page_loss");
  const originalSendResponse = worker.native.sendResponse.bind(worker.native);
  worker.native.sendResponse = () => {
    throw new Error("response transport lost");
  };

  const response = await worker.handleHostRequest({
    kind: "request",
    request_id: "req_loss",
    action_id: "action_loss",
    method: "debugger.command",
    space_id: "space_loss",
    page_id: "page_loss",
    lease_epoch: 1,
    params: {
      method: "Page.navigate",
      payload: { url: "https://lost-response.test/" },
    },
  });

  assert.equal(response.ok, true);
  assert.equal(worker.unreportedUnknownActions.has("action_loss"), true);
  assert.equal(worker.actionReceipts.get("action_loss").outcome, "unknown");
  assert.equal(chrome.debuggerCommands.length, 1);
  worker.native.sendResponse = originalSendResponse;
  worker.stop();
});
