import test from "node:test";
import assert from "node:assert/strict";
import { createServiceWorker } from "../src/service-worker.mjs";
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

function proof(kind, spaceId, pageId, leaseEpoch, suffix = kind) {
  return {
    issued_by_host: true,
    proof_id: `${kind}-${suffix}-proof`,
    kind,
    space_id: spaceId,
    page_id: pageId,
    lease_epoch: leaseEpoch,
  };
}

async function sendRequest(port, hello, sequence, fields) {
  port.receive(
    hostEnvelope(
      port,
      {
        ...fields,
        broker_epoch: 1,
        connection_epoch: 1,
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

test("two logical spaces map conservatively and preserve the user tab", async () => {
  const chrome = new FakeChrome({
    tabs: [
      { id: 1, active: true, url: "https://user.test/", title: "User tab" },
    ],
  });
  const { worker, port, hello } = await boot(chrome);
  const first = await sendRequest(port, hello, 2, {
    kind: "request",
    request_id: "req_create_one",
    action_id: "action_create_one",
    method: "page.create",
    space_id: "space_one",
    page_id: "page_one",
    lease_epoch: 1,
    params: {
      url: "https://agent-one.test/",
      ownership_proof: proof("claim", "space_one", "page_one", 1, "one"),
    },
  });
  const second = await sendRequest(port, hello, 3, {
    kind: "request",
    request_id: "req_create_two",
    action_id: "action_create_two",
    method: "page.create",
    space_id: "space_two",
    page_id: "page_two",
    lease_epoch: 1,
    params: {
      url: "https://agent-two.test/",
      ownership_proof: proof("claim", "space_two", "page_two", 1, "two"),
    },
  });
  assert.equal(first.ok, true);
  assert.equal(second.ok, true);
  assert.equal(chrome.tabsData.has(1), true);
  assert.equal(chrome.removedTabIds.length, 0);
  assert.equal(worker.tabs.pagesForSpace("space_one").length, 1);
  assert.equal(worker.tabs.pagesForSpace("space_two").length, 1);
  assert.equal(chrome.tabsData.get(1).active, true);
  const firstRawTabId = worker.tabs.getInternalByPage("page_one").rawTabId;
  const secondRawTabId = worker.tabs.getInternalByPage("page_two").rawTabId;
  assert.equal(chrome.tabsData.get(firstRawTabId).active, false);
  assert.equal(chrome.tabsData.get(secondRawTabId).active, false);
  for (const message of chrome.hostMessages()) {
    assert.equal("tabId" in message, false);
    assert.equal("targetId" in message, false);
    assert.equal("sessionId" in message, false);
    assert.equal("groupId" in message, false);
  }
  worker.stop();
});

test("cleanup requires host proof and group drift never destroys a logical page", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker, port, hello } = await boot(chrome);
  await sendRequest(port, hello, 2, {
    kind: "request",
    request_id: "req_create",
    action_id: "action_create",
    method: "page.create",
    space_id: "space_one",
    page_id: "page_one",
    lease_epoch: 1,
    params: {
      url: "https://agent.test/",
      ownership_proof: proof("claim", "space_one", "page_one", 1),
    },
  });
  const rawAgentTab = worker.tabs.getInternalByPage("page_one").rawTabId;
  const rawGroupId = chrome.tabsData.get(rawAgentTab).groupId;
  chrome.tabGroups.onUpdated.emit({
    id: rawGroupId,
    title: "User renamed this group",
    color: "red",
    collapsed: true,
  });
  await wait();
  assert.equal(worker.groups.listHints()[0].drift, true);
  chrome.tabGroups.onRemoved.emit(rawGroupId);
  await wait();
  assert.equal(worker.tabs.getInternalByPage("page_one") !== undefined, true);

  const denied = await sendRequest(port, hello, 3, {
    kind: "request",
    request_id: "req_close_denied",
    action_id: "action_close_denied",
    method: "page.close",
    space_id: "space_one",
    page_id: "page_one",
    lease_epoch: 1,
    params: { expected_generation: 1 },
  });
  assert.equal(denied.ok, false);
  assert.equal(denied.error.code, "permission_denied");
  assert.equal(chrome.removedTabIds.length, 0);

  const closed = await sendRequest(port, hello, 4, {
    kind: "request",
    request_id: "req_close",
    action_id: "action_close",
    method: "page.close",
    space_id: "space_one",
    page_id: "page_one",
    lease_epoch: 1,
    params: {
      expected_generation: 1,
      cleanup_proof: {
        ...proof("cleanup", "space_one", "page_one", 1),
        ownership: "agent",
        generation: 1,
      },
    },
  });
  assert.equal(closed.ok, true);
  assert.deepEqual(chrome.removedTabIds, [rawAgentTab]);
  assert.equal(chrome.tabsData.has(1), true);
  worker.stop();
});

test("stale fence rejects old mutations before debugger dispatch", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const { worker, port, hello } = await boot(chrome);
  await sendRequest(port, hello, 2, {
    kind: "request",
    request_id: "req_create",
    action_id: "action_create",
    method: "page.create",
    space_id: "space_one",
    page_id: "page_one",
    lease_epoch: 1,
    params: {
      url: "https://agent.test/",
      ownership_proof: proof("claim", "space_one", "page_one", 1),
    },
  });
  const fence = await sendRequest(port, hello, 3, {
    kind: "request",
    request_id: "req_fence",
    action_id: "action_fence",
    method: "fence.barrier",
    space_id: "space_one",
    lease_epoch: 2,
    params: { fence_epoch: 2 },
  });
  assert.equal(fence.kind, "fence_ack");
  const stale = await sendRequest(port, hello, 4, {
    kind: "request",
    request_id: "req_stale",
    action_id: "action_stale",
    method: "debugger.command",
    space_id: "space_one",
    page_id: "page_one",
    lease_epoch: 1,
    params: { method: "Page.navigate", params: { url: "https://stale.test/" } },
  });
  assert.equal(stale.ok, false);
  assert.equal(stale.error.code, "stale_lease");
  assert.equal(chrome.debuggerCommands.length, 0);
  worker.stop();
});

test("worker restart rehydrates metadata but does not replay browser mutations", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://user.test/" }],
  });
  const first = await boot(chrome);
  const firstWorkerEpoch = first.hello.worker_instance_epoch;
  first.worker.stop();
  const second = await boot(chrome);
  assert.equal(second.hello.worker_instance_epoch, firstWorkerEpoch + 1);
  assert.equal(
    second.worker.tabs.inventory().some((tab) => tab.ownership === "unmanaged"),
    true,
  );
  assert.equal(
    second.port.sent.filter((message) => message.kind === "request").length,
    0,
  );
  second.worker.stop();
});
