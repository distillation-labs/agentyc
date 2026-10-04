import test from "node:test";
import assert from "node:assert/strict";
import { createServiceWorker } from "../src/service-worker.mjs";
import { assertNoRawBrowserIdentifiers } from "../src/protocol.mjs";
import { FakeChrome, hostEnvelope, makeHostHelloOk } from "./fake-chrome.mjs";

const wait = () => new Promise((resolve) => setImmediate(resolve));

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

function proof(kind, spaceId, pageId, leaseEpoch, suffix = kind, extra = {}) {
  return {
    issued_by_host: true,
    proof_id: `${kind}-${suffix}-proof`,
    kind,
    space_id: spaceId,
    page_id: pageId,
    lease_epoch: leaseEpoch,
    expires_at: Date.now() + 60_000,
    ...extra,
  };
}

async function sendRequest(port, hello, sequence, fields, connectionEpoch = 1) {
  port.receive(
    hostEnvelope(
      port,
      {
        ...fields,
        broker_epoch: 1,
        connection_epoch: connectionEpoch,
        worker_instance_epoch: hello.worker_instance_epoch,
        browser_session_epoch: hello.browser_session_epoch,
      },
      sequence,
    ),
  );
  await wait();
  return port.sent
    .filter(
      (message) =>
        (message.kind === "response" || message.kind === "fence_ack") &&
        message.request_id === fields.request_id,
    )
    .at(-1);
}

function createPage(spaceId, pageId, leaseEpoch, suffix) {
  return {
    kind: "request",
    request_id: `req_create_${suffix}`,
    action_id: `action_create_${suffix}`,
    method: "page.create",
    space_id: spaceId,
    page_id: pageId,
    lease_epoch: leaseEpoch,
    params: {
      url: `https://agent-${suffix}.test/`,
      ownership_proof: proof("claim", spaceId, pageId, leaseEpoch, suffix),
    },
  };
}

function fenceRequest(spaceId, epoch, suffix) {
  return {
    kind: "request",
    request_id: `req_fence_${suffix}`,
    action_id: `action_fence_${suffix}`,
    method: "fence.barrier",
    space_id: spaceId,
    lease_epoch: epoch,
    params: { fence_epoch: epoch },
  };
}

test("oversized tab inventory is bounded and never tears down the transport", async () => {
  const tabs = [];
  for (let id = 1; id <= 300; id += 1) {
    tabs.push({
      id,
      active: id === 300,
      url: `https://user-${id}.test/${"p".repeat(2000)}`,
      title: `User tab ${id}`,
    });
  }
  const chrome = new FakeChrome({ tabs });
  const { worker, port, hello } = await boot(chrome);

  assert.equal(worker.native.connected, true);
  assert.equal(chrome.ports.length, 1);
  const inventory = port.sent.find((message) => message.kind === "inventory");
  assert.ok(inventory, "hello_ok must be followed by an inventory");
  assert.ok(inventory.payload.pages.length <= 200);
  assert.equal(inventory.payload.total_page_count, 300);
  assert.equal(inventory.payload.truncated, true);
  assert.equal(
    inventory.payload.omitted_page_count,
    300 - inventory.payload.pages.length,
  );
  assert.equal(
    inventory.payload.pages.some((page) => page.active === true),
    true,
  );
  assertNoRawBrowserIdentifiers(inventory);

  const created = await sendRequest(
    port,
    hello,
    2,
    createPage("space_one", "page_one", 1, "one"),
  );
  assert.equal(created.ok, true);
  const listed = await sendRequest(port, hello, 3, {
    kind: "request",
    request_id: "req_list",
    method: "tab.inventory",
    params: {},
  });
  assert.equal(listed.ok, true);
  assert.equal(listed.result.truncated, true);
  assert.equal(listed.result.total_page_count, 301);
  assert.equal(listed.result.pages[0].page_id, "page_one");
  assert.equal(listed.result.pages[0].ownership, "agent");
  assert.equal(worker.native.connected, true);
  assert.equal(chrome.ports.length, 1);
  worker.stop();
});

test("reconnect reports unknown outcomes in the next inventory and never replays", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker, port, hello } = await boot(chrome);
  let release;
  chrome.beforeTabCreate = () =>
    new Promise((resolve) => {
      release = resolve;
    });

  const pending = worker.handleHostRequest(
    createPage("space_one", "page_one", 1, "lost"),
  );
  for (let i = 0; i < 10 && !release; i += 1) await wait();
  assert.ok(release, "tab creation must be in flight");
  port.disconnect();
  assert.equal(worker.native.connected, false);
  release();
  await pending;
  assert.equal(worker.unreportedUnknownActions.has("action_create_lost"), true);

  chrome.beforeTabCreate = null;
  await worker.native.connect();
  const second = chrome.lastPort;
  assert.notEqual(second, port);
  const secondHello = second.sent.find((message) => message.kind === "hello");
  assert.equal(secondHello.worker_instance_epoch, hello.worker_instance_epoch);
  assert.equal(secondHello.browser_session_epoch, hello.browser_session_epoch);
  assert.notEqual(secondHello.nonce, hello.nonce);
  second.receive(
    makeHostHelloOk(secondHello, { brokerEpoch: 1, connectionEpoch: 2 }),
  );
  await wait();

  const inventory = second.sent.find((message) => message.kind === "inventory");
  assert.ok(inventory);
  assert.deepEqual(inventory.payload.unknown_action_ids, [
    "action_create_lost",
  ]);
  assert.equal(inventory.payload.unknown_actions_overflow, false);
  assert.equal(
    inventory.payload.pages.some(
      (page) => page.page_id === "page_one" && page.ownership === "agent",
    ),
    true,
  );
  assertNoRawBrowserIdentifiers(inventory);
  assert.equal(worker.unreportedUnknownActions.size, 0);
  assert.equal(chrome.tabsCreateCalls.length, 1);
  assert.equal(
    second.sent.filter(
      (message) => message.kind === "request" || message.kind === "response",
    ).length,
    0,
  );
  worker.stop();
});

test("worker restart reports a durable in-flight mutation as unknown without replay", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const first = await boot(chrome);
  let release;
  chrome.beforeTabCreate = () =>
    new Promise((resolve) => {
      release = resolve;
    });

  const pending = first.worker.handleHostRequest(
    createPage("space_restart", "page_restart", 1, "restart"),
  );
  for (let i = 0; i < 10 && !release; i += 1) await wait();
  assert.ok(release, "tab creation must be in flight");
  first.worker.stop();
  await wait();
  const second = await boot(chrome, { connectionEpoch: 2 });
  const inventory = second.port.sent.find(
    (message) => message.kind === "inventory",
  );
  assert.ok(inventory);
  assert.deepEqual(inventory.payload.unknown_action_ids, [
    "action_create_restart",
  ]);
  assert.equal(
    second.worker.unreportedUnknownActions.has("action_create_restart"),
    false,
  );
  assert.equal(chrome.tabsCreateCalls.length, 1);

  chrome.beforeTabCreate = null;
  release();
  await pending;
  assert.equal(chrome.tabsCreateCalls.length, 1);
  assert.equal(
    second.port.sent.filter(
      (message) => message.kind === "request" || message.kind === "response",
    ).length,
    0,
  );
  second.worker.stop();
});

test("stale or duplicate fence epochs are rejected and never lower the barrier", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker, port, hello } = await boot(chrome);
  const first = await sendRequest(
    port,
    hello,
    2,
    fenceRequest("space_one", 3, "three"),
  );
  assert.equal(first.kind, "fence_ack");
  assert.equal(first.result.durable, true);

  for (const [sequence, epoch] of [
    [3, 3],
    [4, 2],
  ]) {
    const stale = await sendRequest(
      port,
      hello,
      sequence,
      fenceRequest("space_one", epoch, `stale${epoch}_${sequence}`),
    );
    assert.equal(stale.kind, "response");
    assert.equal(stale.ok, false);
    assert.equal(stale.error.code, "stale_fence");
  }
  assert.equal(worker.fences.get("space_one"), 3);

  const belowFloor = await sendRequest(
    port,
    hello,
    5,
    createPage("space_one", "page_one", 2, "below"),
  );
  assert.equal(belowFloor.ok, false);
  assert.equal(belowFloor.error.code, "stale_lease");
  assert.equal(chrome.tabsCreateCalls.length, 0);
  worker.stop();
});

test("acknowledged lease fences can rebind one retained inactive page", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker, port, hello } = await boot(chrome);
  const created = await sendRequest(
    port,
    hello,
    2,
    createPage("space_rebind", "page_rebind", 1, "rebind"),
  );
  assert.equal(created.ok, true);
  const fenced = await sendRequest(
    port,
    hello,
    3,
    fenceRequest("space_rebind", 2, "rebind"),
  );
  assert.equal(fenced.kind, "fence_ack");
  assert.equal(
    worker.tabs.getInternalByPage("page_rebind").bindingState,
    "user_owned",
  );

  const rebound = await sendRequest(port, hello, 4, {
    kind: "request",
    request_id: "req_rebind",
    action_id: "action_rebind",
    method: "page.rebind",
    space_id: "space_rebind",
    page_id: "page_rebind",
    lease_epoch: 2,
    params: {
      target_generation: 2,
      navigation_generation: 2,
      document_generation: 2,
      ownership_proof: proof(
        "rebind",
        "space_rebind",
        "page_rebind",
        2,
        "rebind",
        {
          rebind: true,
          target_generation: 2,
          profile_instance_id: worker.metadata.profileInstanceId,
          browser_session_epoch: worker.metadata.browserSessionEpoch,
        },
      ),
    },
  });
  assert.equal(rebound.ok, true);
  assert.equal(rebound.result.binding_state, "bound");
  assert.equal(rebound.result.lease_epoch, 2);
  assert.equal(rebound.result.target_generation, 2);
  assert.equal(
    worker.tabs.getInternalByPage("page_rebind").bindingState,
    "bound",
  );
  worker.stop();
});

test("fence floor survives worker restart inside one browser session", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const first = await boot(chrome);
  const fence = await sendRequest(
    first.port,
    first.hello,
    2,
    fenceRequest("space_one", 2, "two"),
  );
  assert.equal(fence.kind, "fence_ack");
  first.worker.stop();

  const second = await boot(chrome);
  assert.equal(
    second.hello.worker_instance_epoch,
    first.hello.worker_instance_epoch + 1,
  );
  assert.equal(
    second.hello.browser_session_epoch,
    first.hello.browser_session_epoch,
  );
  assert.equal(second.worker.fences.get("space_one"), 2);

  const stale = await sendRequest(
    second.port,
    second.hello,
    2,
    createPage("space_one", "page_old", 1, "old"),
  );
  assert.equal(stale.ok, false);
  assert.equal(stale.error.code, "stale_lease");
  assert.equal(chrome.tabsCreateCalls.length, 0);

  const current = await sendRequest(
    second.port,
    second.hello,
    3,
    createPage("space_one", "page_new", 2, "new"),
  );
  assert.equal(current.ok, true);
  assert.equal(chrome.tabsCreateCalls.length, 1);
  assert.equal(chrome.tabsData.get(1).active, true);
  second.worker.stop();
});

test("persisted fence floors are ignored for another browser session and cleared on session advance", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const first = await boot(chrome);
  await sendRequest(
    first.port,
    first.hello,
    2,
    fenceRequest("space_one", 2, "two"),
  );
  await first.worker.advanceBrowserSession("test");
  assert.deepEqual(chrome.storageData.agentyc_space_fences.fences, []);
  assert.equal(
    chrome.storageData.agentyc_space_fences.browser_session_epoch,
    first.hello.browser_session_epoch + 1,
  );
  first.worker.stop();

  chrome.storageData.agentyc_space_fences = {
    profile_instance_id:
      chrome.storageData.agentyc_extension_metadata.profile_instance_id,
    browser_session_epoch: 99,
    fences: [["space_one", 9]],
  };
  const second = await boot(chrome);
  assert.equal(second.worker.fences.size, 0);
  second.worker.stop();
});

test("corrupt persisted fences are ignored instead of trusted", async () => {
  const chrome = new FakeChrome();
  chrome.storageData.agentyc_extension_metadata = {
    profile_instance_id: "profile_fixed",
    worker_instance_epoch: 1,
    browser_session_epoch: 1,
  };
  chrome.storageData.agentyc_space_fences = {
    profile_instance_id: "profile_fixed",
    browser_session_epoch: 1,
    fences: [
      ["not a space", 5],
      ["space_ok", 0],
      ["space_ok", "7"],
      "junk",
      ["space_good", 4],
    ],
  };
  const { worker } = await boot(chrome);
  assert.deepEqual([...worker.fences.entries()], [["space_good", 4]]);
  worker.stop();
});

test("agent tab creation never activates tabs and unclaimed focus theft leaves the user tab alone", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker, port, hello } = await boot(chrome);
  const originalCreate = chrome.tabs.create;
  chrome.tabs.create = (options) =>
    originalCreate({ ...options, active: true });

  const stolen = await sendRequest(
    port,
    hello,
    2,
    createPage("space_one", "page_stolen", 1, "stolen"),
  );
  assert.equal(stolen.ok, false);
  assert.equal(stolen.error.code, "focus_theft");
  assert.equal(worker.tabs.getInternalByPage("page_stolen"), undefined);
  assert.equal(chrome.removedTabIds.length, 0);
  assert.equal(chrome.tabsData.has(1), true);

  chrome.tabs.create = originalCreate;
  const normal = await sendRequest(
    port,
    hello,
    3,
    createPage("space_one", "page_normal", 1, "normal"),
  );
  assert.equal(normal.ok, true);
  assert.equal(chrome.tabsCreateCalls.at(-1).active, false);
  const bound = worker.tabs.getInternalByPage("page_normal");
  assert.equal(chrome.tabsData.get(bound.rawTabId).active, false);
  worker.stop();
});

test("agent commands cannot address or close the user's own tab", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker, port, hello } = await boot(chrome);
  const userTabHint = worker.tabs
    .inventory()
    .find((page) => page.active).tab_hint;

  const attach = await sendRequest(port, hello, 2, {
    kind: "request",
    request_id: "req_attach_user",
    action_id: "action_attach_user",
    method: "debugger.attach",
    space_id: "space_one",
    page_id: "page_user",
    lease_epoch: 1,
    params: {},
  });
  assert.equal(attach.ok, false);
  assert.equal(attach.error.code, "page_not_found");
  assert.equal(chrome.debugger.attached.size, 0);

  const close = await sendRequest(port, hello, 3, {
    kind: "request",
    request_id: "req_close_user",
    action_id: "action_close_user",
    method: "page.close",
    space_id: "space_one",
    page_id: "page_user",
    lease_epoch: 1,
    params: {
      cleanup_proof: {
        ...proof("cleanup", "space_one", "page_user", 1, "user", {
          browser_session_epoch: hello.browser_session_epoch,
          tab_hint: userTabHint,
        }),
        ownership: "agent",
      },
    },
  });
  assert.equal(close.ok, false);
  assert.equal(chrome.removedTabIds.length, 0);
  assert.equal(chrome.tabsData.has(1), true);
  worker.stop();
});

test("user focus on an agent tab blocks cleanup until the user leaves it", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker, port, hello } = await boot(chrome);
  const created = await sendRequest(
    port,
    hello,
    2,
    createPage("space_one", "page_one", 1, "focus"),
  );
  assert.equal(created.ok, true);
  const rawAgentTab = worker.tabs.getInternalByPage("page_one").rawTabId;

  chrome.tabsData.get(1).active = false;
  chrome.tabsData.get(rawAgentTab).active = true;
  chrome.tabs.onActivated.emit({ tabId: rawAgentTab, windowId: 1 });
  assert.equal(
    worker.tabs.inventory().find((page) => page.page_id === "page_one").active,
    true,
  );

  const closeRequest = (sequence, suffix) => ({
    kind: "request",
    request_id: `req_close_${suffix}`,
    action_id: `action_close_${suffix}`,
    method: "page.close",
    space_id: "space_one",
    page_id: "page_one",
    lease_epoch: 1,
    params: {
      cleanup_proof: {
        ...proof("cleanup", "space_one", "page_one", 1, suffix, {
          browser_session_epoch: hello.browser_session_epoch,
          tab_hint: worker.tabs
            .inventory()
            .find((page) => page.page_id === "page_one").tab_hint,
          target_generation: 1,
        }),
        ownership: "agent",
      },
    },
  });
  const blocked = await sendRequest(port, hello, 3, closeRequest(3, "blocked"));
  assert.equal(blocked.ok, false);
  assert.equal(blocked.error.code, "user_control_required");
  assert.equal(chrome.removedTabIds.length, 0);

  chrome.tabsData.get(rawAgentTab).active = false;
  chrome.tabsData.get(1).active = true;
  chrome.tabs.onActivated.emit({ tabId: 1, windowId: 1 });
  const closed = await sendRequest(port, hello, 4, closeRequest(4, "allowed"));
  assert.equal(closed.ok, true);
  assert.deepEqual(chrome.removedTabIds, [rawAgentTab]);
  assert.equal(chrome.tabsData.has(1), true);
  worker.stop();
});
