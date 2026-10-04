import test from "node:test";
import assert from "node:assert/strict";
import { createServiceWorker } from "../src/service-worker.mjs";
import { DebuggerBridge, hashRuntimeScript } from "../src/debugger-bridge.mjs";
import { FramesRegistry } from "../src/frames.mjs";
import { GroupsRegistry } from "../src/groups.mjs";
import { TabsRegistry } from "../src/tabs-registry.mjs";
import { sendPanelAction } from "../src/sidepanel/controls.mjs";
import { FakeChrome, hostEnvelope, makeHostHelloOk } from "./fake-chrome.mjs";

const wait = () => new Promise((resolve) => setImmediate(resolve));
const proof = (
  kind,
  spaceId,
  pageId,
  leaseEpoch,
  suffix = kind,
  extra = {},
) => ({
  issued_by_host: true,
  proof_id: `${kind}-${suffix}-proof`,
  kind,
  space_id: spaceId,
  page_id: pageId,
  lease_epoch: leaseEpoch,
  expires_at: Date.now() + 60_000,
  ...extra,
});

async function boot(chrome, options = {}) {
  const worker = createServiceWorker({
    chromeApi: chrome,
    autoReconnect: false,
    ...options,
  });
  await worker.start();
  const port = chrome.lastPort;
  const hello = port.sent.find((message) => message.kind === "hello");
  port.receive(
    makeHostHelloOk(hello, {
      brokerEpoch: options.brokerEpoch ?? 1,
      connectionEpoch: options.connectionEpoch ?? 1,
      workerInstanceEpoch: hello.worker_instance_epoch,
      browserSessionEpoch: hello.browser_session_epoch,
    }),
  );
  await wait();
  return { worker, port, hello };
}

test("same-space page, action, and fence mutations are serialized", async () => {
  const chrome = new FakeChrome();
  const { worker } = await boot(chrome);
  const order = [];
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  worker.executeHostMethod = async ({ method }) => {
    order.push(`${method}:start`);
    if (method === "page.create") await gate;
    order.push(`${method}:end`);
    return {};
  };
  const originalFence = worker.handleFence.bind(worker);
  worker.handleFence = (value) => {
    order.push("fence:start");
    const result = originalFence(value);
    order.push("fence:end");
    return result;
  };
  const create = worker.handleHostRequest({
    kind: "request",
    request_id: "req_queue_create",
    action_id: "action_queue_create",
    method: "page.create",
    space_id: "space_queue",
    page_id: "page_queue",
    lease_epoch: 1,
    params: {
      ownership_proof: proof("claim", "space_queue", "page_queue", 1, "create"),
    },
  });
  await wait();
  const action = worker.handleHostRequest({
    kind: "request",
    request_id: "req_queue_action",
    action_id: "action_queue_action",
    method: "action.execute",
    space_id: "space_queue",
    page_id: "page_queue",
    lease_epoch: 1,
    params: {},
  });
  const fence = worker.handleHostRequest({
    kind: "request",
    request_id: "req_queue_fence",
    action_id: "action_queue_fence",
    method: "fence.barrier",
    space_id: "space_queue",
    lease_epoch: 2,
    params: { fence_epoch: 2 },
  });
  await wait();
  assert.deepEqual(order, ["page.create:start"]);
  release();
  await Promise.all([create, action, fence]);
  assert.deepEqual(order, [
    "page.create:start",
    "page.create:end",
    "action.execute:start",
    "action.execute:end",
    "fence:start",
    "fence:end",
  ]);
  worker.stop();
});

test("fence arriving during page creation prevents binding and rolls the tab back", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker } = await boot(chrome);
  const realCreate = chrome.tabs.create.bind(chrome.tabs);
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  let created;
  chrome.tabs.create = async (options) => {
    created = await realCreate(options);
    await gate;
    return created;
  };
  const request = worker.handleHostRequest({
    kind: "request",
    request_id: "req_fenced_create",
    action_id: "action_fenced_create",
    method: "page.create",
    space_id: "space_fenced",
    page_id: "page_fenced",
    lease_epoch: 1,
    params: {
      url: "https://agent.test/",
      ownership_proof: proof(
        "claim",
        "space_fenced",
        "page_fenced",
        1,
        "create",
      ),
    },
  });
  for (let i = 0; i < 10 && !created; i += 1) await wait();
  assert.ok(created?.id);
  await worker.handleFence({
    message: {
      request_id: "req_fenced_barrier",
      action_id: "action_fenced_barrier",
    },
    params: { fence_epoch: 2 },
    requestId: "req_fenced_barrier",
    actionId: "action_fenced_barrier",
    spaceId: "space_fenced",
    leaseEpoch: 2,
  });
  release();
  const result = await request;
  assert.equal(result.ok, false);
  assert.equal(result.error.code, "stale_lease");
  assert.deepEqual(chrome.removedTabIds, [created.id]);
  assert.equal(worker.tabs.getInternalByPage("page_fenced"), undefined);
  worker.stop();
});

test("browser session advancement retires old bindings and raw tab identities", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker, port, hello } = await boot(chrome);
  port.receive(
    hostEnvelope(
      port,
      {
        kind: "request",
        request_id: "req_session_create",
        action_id: "action_session_create",
        method: "page.create",
        space_id: "space_session",
        page_id: "page_session",
        lease_epoch: 1,
        params: {
          url: "https://agent.test/",
          ownership_proof: proof(
            "claim",
            "space_session",
            "page_session",
            1,
            "create",
          ),
        },
      },
      2,
    ),
  );
  await wait();
  const oldRecord = worker.tabs.getInternalByPage("page_session");
  const oldEpoch = worker.metadata.browserSessionEpoch;
  await worker.advanceBrowserSession("test");
  assert.equal(worker.metadata.browserSessionEpoch, oldEpoch + 1);
  const reboundRecord = worker.tabs.getInternalByPage("page_session");
  assert.equal(reboundRecord, undefined);
  assert.equal(chrome.tabsData.has(oldRecord.rawTabId), true);
  assert.equal(worker.tabs.retiredRawTabIds.has(oldRecord.rawTabId), true);
  assert.throws(
    () =>
      worker.tabs.bindManagedTab({
        tabId: oldRecord.rawTabId,
        spaceId: "space_session",
        pageId: "page_reused",
        leaseEpoch: 1,
        ownershipProof: proof(
          "claim",
          "space_session",
          "page_reused",
          1,
          "reuse",
        ),
      }),
    (error) => error.code === "stale_target",
  );
  worker.stop();
});

test("tab removal and replacement invalidate debugger and frame mappings", async () => {
  const chrome = new FakeChrome({
    tabs: [
      { id: 1, url: "https://agent.test/" },
      { id: 2, url: "https://replacement.test/" },
    ],
  });
  const groups = new GroupsRegistry({ chromeApi: chrome });
  let bridge;
  const tabs = new TabsRegistry({
    chromeApi: chrome,
    groups,
    hintSalt: "mapping",
    onLifecycle: (kind, tabId) => bridge?.invalidateTab(tabId, kind),
  });
  const frames = new FramesRegistry();
  bridge = new DebuggerBridge({ chromeApi: chrome, tabs, frames });
  await tabs.start();
  tabs.bindManagedTab({
    tabId: 1,
    spaceId: "space_map",
    pageId: "page_map",
    leaseEpoch: 1,
    ownershipProof: proof("claim", "space_map", "page_map", 1),
  });
  await bridge.attach({
    spaceId: "space_map",
    pageId: "page_map",
    leaseEpoch: 1,
  });
  frames.bindFrame({
    tabId: 1,
    frameId: "frame_internal",
    logicalFrameId: "frame_logical",
  });
  tabs.handleDetached(1, { oldWindowId: 1 });
  assert.equal(bridge.isAttached("page_map"), false);
  assert.equal(frames.getInternalBinding(1), undefined);
  tabs.handleRemoved(1);
  assert.equal(bridge.isAttached("page_map"), false);
  assert.equal(frames.getInternalBinding(1), undefined);

  tabs.bindManagedTab({
    tabId: 2,
    spaceId: "space_map",
    pageId: "page_map_two",
    leaseEpoch: 1,
    ownershipProof: proof("claim", "space_map", "page_map_two", 1, "two"),
  });
  await bridge.attach({
    spaceId: "space_map",
    pageId: "page_map_two",
    leaseEpoch: 1,
  });
  tabs.handleAttached(2, { newWindowId: 2 });
  assert.equal(bridge.isAttached("page_map_two"), false);
  assert.equal(frames.getInternalBinding(2), undefined);
  tabs.handleReplaced(2, 2);
  assert.equal(bridge.isAttached("page_map_two"), false);
  assert.equal(frames.getInternalBinding(2), undefined);
});

test("debugger attachment rejects mismatched scope and generations", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, url: "https://agent.test/" }],
  });
  const groups = new GroupsRegistry({ chromeApi: chrome });
  const tabs = new TabsRegistry({ chromeApi: chrome, groups });
  const frames = new FramesRegistry();
  const bridge = new DebuggerBridge({ chromeApi: chrome, tabs, frames });
  await tabs.start();
  tabs.bindManagedTab({
    tabId: 1,
    spaceId: "space_scope",
    pageId: "page_scope",
    leaseEpoch: 1,
    ownershipProof: proof("claim", "space_scope", "page_scope", 1),
  });
  await bridge.attach({
    spaceId: "space_scope",
    pageId: "page_scope",
    leaseEpoch: 1,
  });
  const attachment = bridge.attached.get(1);
  attachment.spaceId = "space_other";
  await assert.rejects(
    () =>
      bridge.sendCommand({
        spaceId: "space_scope",
        pageId: "page_scope",
        leaseEpoch: 1,
        method: "Page.getNavigationHistory",
      }),
    (error) => error.code === "stale_generation",
  );
});

test("runtime evaluation approvals are hashed, scoped, expiring, and single-use", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, url: "https://agent.test/" }],
  });
  const groups = new GroupsRegistry({ chromeApi: chrome });
  const tabs = new TabsRegistry({ chromeApi: chrome, groups });
  const frames = new FramesRegistry();
  const bridge = new DebuggerBridge({ chromeApi: chrome, tabs, frames });
  await tabs.start();
  tabs.bindManagedTab({
    tabId: 1,
    spaceId: "space_eval",
    pageId: "page_eval",
    leaseEpoch: 1,
    ownershipProof: proof("claim", "space_eval", "page_eval", 1),
  });
  await bridge.attach({
    spaceId: "space_eval",
    pageId: "page_eval",
    leaseEpoch: 1,
  });
  const expression = "document.title";
  const baseApproval = {
    issued_by_host: true,
    approval_id: "approval_eval_1",
    purpose: "runtime.evaluate",
    script_hash: await hashRuntimeScript(expression),
    space_id: "space_eval",
    page_id: "page_eval",
    lease_epoch: 1,
    target_generation: 1,
    expires_at: Date.now() + 10_000,
  };
  const result = await bridge.sendCommand({
    spaceId: "space_eval",
    pageId: "page_eval",
    leaseEpoch: 1,
    method: "Runtime.evaluate",
    params: { expression },
    capability: "evaluate",
    approval: baseApproval,
    commandId: "eval_command_1",
  });
  assert.equal(result.ok, true);
  assert.equal(chrome.debuggerCommands.length, 1);
  await assert.rejects(
    () =>
      bridge.sendCommand({
        spaceId: "space_eval",
        pageId: "page_eval",
        leaseEpoch: 1,
        method: "Runtime.evaluate",
        params: { expression },
        capability: "evaluate",
        approval: baseApproval,
      }),
    (error) => error.code === "replay_rejected",
  );
  await assert.rejects(
    () =>
      bridge.sendCommand({
        spaceId: "space_eval",
        pageId: "page_eval",
        leaseEpoch: 1,
        method: "Runtime.evaluate",
        params: { expression: "document.body" },
        capability: "evaluate",
        approval: { ...baseApproval, approval_id: "approval_eval_2" },
      }),
    (error) => error.code === "permission_denied",
  );
  await assert.rejects(
    () =>
      bridge.sendCommand({
        spaceId: "space_eval",
        pageId: "page_eval",
        leaseEpoch: 1,
        method: "Runtime.evaluate",
        params: { expression },
        capability: "evaluate",
        approval: {
          ...baseApproval,
          approval_id: "approval_eval_3",
          expires_at: Date.now() - 1,
        },
      }),
    (error) => error.code === "approval_expired",
  );
});

test("pre-dispatch capability failures stay capability errors", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, url: "https://agent.test/" }],
  });
  const groups = new GroupsRegistry({ chromeApi: chrome });
  const tabs = new TabsRegistry({ chromeApi: chrome, groups });
  await tabs.start();
  tabs.bindManagedTab({
    tabId: 1,
    spaceId: "space_cap",
    pageId: "page_cap",
    leaseEpoch: 1,
    ownershipProof: proof("claim", "space_cap", "page_cap", 1),
  });
  const frames = new FramesRegistry();
  const bridge = new DebuggerBridge({ chromeApi: chrome, tabs, frames });
  await bridge.attach({
    spaceId: "space_cap",
    pageId: "page_cap",
    leaseEpoch: 1,
  });
  const sendCommand = chrome.debugger.sendCommand;
  delete chrome.debugger.sendCommand;
  await assert.rejects(
    () =>
      bridge.sendCommand({
        spaceId: "space_cap",
        pageId: "page_cap",
        leaseEpoch: 1,
        method: "Page.reload",
      }),
    (error) => error.code === "capability_unavailable",
  );
  chrome.debugger.sendCommand = sendCommand;
  delete chrome.tabs.remove;
  await assert.rejects(
    () =>
      tabs.closeManagedPage({
        spaceId: "space_cap",
        pageId: "page_cap",
        leaseEpoch: 1,
        expectedGeneration: 1,
        cleanupProof: proof("cleanup", "space_cap", "page_cap", 1, "cap", {
          ownership: "agent",
          target_generation: 1,
          tab_hint: tabs.inventory().find((page) => page.page_id === "page_cap")
            .tab_hint,
        }),
      }),
    (error) => error.code === "capability_unavailable",
  );
});

test("side-panel destructive actions require expiring single-use intent tickets", async () => {
  const chrome = new FakeChrome();
  const { worker, port } = await boot(chrome);
  const sender = {
    id: chrome.runtime.id,
    url: "chrome-extension://fake-extension-id/src/sidepanel/index.html",
    origin: "chrome-extension://fake-extension-id",
    frameId: 0,
  };
  const untrusted = await worker.handleRuntimeMessage(
    {
      type: "agentyc.sidepanel.request",
      action: "create",
      params: { label: "not from side panel" },
    },
    { ...sender, url: "https://attacker.test/" },
  );
  assert.equal(untrusted.ok, false);
  assert.equal(untrusted.error.code, "permission_denied");
  worker.native.sendRequest = ({ requestId }) => {
    worker.pending.get(requestId)?.resolve({
      ok: true,
      result: { space_id: "space_created" },
    });
  };
  const created = await worker.handleRuntimeMessage(
    {
      type: "agentyc.sidepanel.request",
      action: "create",
      params: { label: "created without ticket" },
    },
    sender,
  );
  assert.equal(created.ok, true);
  assert.equal(created.result.space_id, "space_created");
  const denied = await worker.handleSidePanelRequest(
    {
      action: "stop",
      params: { space_id: "space_panel" },
    },
    sender,
  );
  assert.equal(denied.ok, false);
  assert.equal(denied.error.code, "user_confirmation_required");
  for (const action of ["pause", "retain"]) {
    const missingTicket = await worker.handleSidePanelRequest(
      { action, params: { space_id: "space_panel" } },
      sender,
    );
    assert.equal(missingTicket.ok, false);
    assert.equal(missingTicket.error.code, "user_confirmation_required");
  }
  const ticket = {
    issued_by_host: true,
    ticket_id: "ticket_panel_1",
    purpose: "sidepanel",
    action: "stop",
    space_id: "space_panel",
    profile_instance_id: worker.metadata.profileInstanceId,
    expires_at: Date.now() + 10_000,
    browser_session_epoch: worker.metadata.browserSessionEpoch,
  };
  let responseSequence = 2;
  const originalSendRequest = worker.native.sendRequest.bind(worker.native);
  worker.native.sendRequest = (request) => {
    const envelope = originalSendRequest(request);
    queueMicrotask(() =>
      port.receive(
        hostEnvelope(
          port,
          {
            kind: "response",
            request_id: request.requestId,
            action_id: request.actionId,
            ok: true,
            result: {},
          },
          responseSequence++,
        ),
      ),
    );
    return envelope;
  };
  const accepted = await worker.handleSidePanelRequest({
    action: "stop",
    params: { space_id: "space_panel" },
    intent_ticket: ticket,
  }, sender);
  assert.equal(accepted.ok, true);
  const replay = await worker.handleSidePanelRequest({
    action: "stop",
    params: { space_id: "space_panel" },
    intent_ticket: ticket,
  }, sender);
  assert.equal(replay.ok, false);
  assert.equal(replay.error.code, "replay_rejected");
  worker.stop();
});

test("side-panel controls keep create ticket-free and require tickets for controls", async () => {
  const messages = [];
  const chromeApi = {
    runtime: { sendMessage: async (message) => messages.push(message) },
  };
  await sendPanelAction({
    chromeApi,
    action: "create",
    params: { label: "new space" },
  });
  await assert.rejects(
    () => sendPanelAction({ chromeApi, action: "pause", params: {} }),
    /host intent ticket/,
  );
  assert.equal(messages.length, 1);
  assert.equal(messages[0].action, "create");
});
