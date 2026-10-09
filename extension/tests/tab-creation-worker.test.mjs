import test from "node:test";
import assert from "node:assert/strict";

import {
  TabCreationWorker,
  handleTabCreationRequest,
} from "../src/tab-creation-worker.mjs";
import { createGroupsRegistry } from "../src/groups.mjs";

const bootstrapUrl = "about:blank#agentyc-tab=page_worker-test-3";

function nativeCapture() {
  return {
    responses: [],
    sendResponse(response) {
      this.responses.push(response);
    },
  };
}

test("tab.create only creates an inactive bootstrap tab and returns no browser ID", async () => {
  const calls = [];
  const nativeClient = nativeCapture();
  await handleTabCreationRequest(
    {
      kind: "request",
      request_id: "req_create_tab",
      method: "tab.create",
      params: { bootstrap_url: bootstrapUrl },
    },
    {
      nativeClient,
      chromeApi: {
        tabs: {
          async create(options) {
            calls.push(options);
            return { id: 73, active: false };
          },
        },
      },
    },
  );

  assert.deepEqual(calls, [{ url: bootstrapUrl, active: false }]);
  assert.deepEqual(nativeClient.responses, [
    {
      requestId: "req_create_tab",
      actionId: undefined,
      ok: true,
      result: { created: true },
      error: undefined,
    },
  ]);
  assert.equal(JSON.stringify(nativeClient.responses).includes("73"), false);
});

test("group.present groups the created page without returning Chrome IDs", async () => {
  const nativeClient = nativeCapture();
  const groups = createGroupsRegistry({
    chromeApi: {
      tabs: {
        async group({ tabIds }) {
          assert.deepEqual(tabIds, [73]);
          return 11;
        },
      },
      tabGroups: {
        async update(groupId, updateInfo) {
          assert.equal(groupId, 11);
          assert.deepEqual(updateInfo, { title: "research" });
          return { id: groupId, ...updateInfo };
        },
      },
    },
  });
  groups.start();
  const pageTabs = new Map([["page_worker-test", 73]]);

  await handleTabCreationRequest(
    {
      kind: "request",
      request_id: "req_group_page",
      method: "group.present",
      params: {
        space_id: "space_research",
        page_id: "page_worker-test",
        lease_epoch: 1,
        title: "research",
      },
    },
    { nativeClient, pageTabs, groups },
  );

  assert.deepEqual(nativeClient.responses.at(-1), {
    requestId: "req_group_page",
    actionId: undefined,
    ok: true,
    result: { grouped: true },
    error: undefined,
  });
  assert.equal(JSON.stringify(nativeClient.responses).includes("11"), false);
  groups.stop();
});

test("invalid tab.create payloads and other host methods never reach Chrome", async () => {
  const calls = [];
  const nativeClient = nativeCapture();
  const chromeApi = {
    tabs: {
      async create(options) {
        calls.push(options);
      },
    },
  };

  await handleTabCreationRequest(
    {
      kind: "request",
      request_id: "req_bad_create",
      method: "tab.create",
      params: { bootstrap_url: "https://example.com", tab_id: 73 },
    },
    { nativeClient, chromeApi },
  );
  await handleTabCreationRequest(
    {
      kind: "request",
      request_id: "req_other_method",
      method: "space.create",
      params: {},
    },
    { nativeClient, chromeApi },
  );

  assert.deepEqual(calls, []);
  assert.deepEqual(
    nativeClient.responses.map(({ ok, error }) => [ok, error.code]),
    [
      [false, "invalid_argument"],
      [false, "capability_unavailable"],
    ],
  );
});

test("worker identity advances per worker and browser session without extra capabilities", async () => {
  let metadata;
  let startupListener;
  const stored = {
    async get() {
      return { agentyc_extension_metadata: metadata };
    },
    async set(values) {
      metadata = values.agentyc_extension_metadata;
    },
  };
  const native = {
    connects: 0,
    requestedCapabilities: ["unexpected"],
    async connect() {
      this.connects += 1;
    },
    stop() {
      this.stopped = true;
    },
  };
  const chromeApi = {
    storage: { local: stored },
    runtime: {
      getManifest: () => ({ version: "1.0.0" }),
      onStartup: {
        addListener(listener) {
          startupListener = listener;
        },
        removeListener() {},
      },
    },
  };
  const worker = new TabCreationWorker({ chromeApi, nativeClient: native });

  await worker.start();
  assert.match(worker.identity.profileInstanceId, /^profile_/);
  assert.equal(worker.identity.workerInstanceEpoch, 1);
  assert.equal(native.browserSessionEpoch, 1);
  assert.deepEqual(native.requestedCapabilities, ["visual_groups"]);
  assert.equal(native.connects, 1);

  startupListener();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(worker.identity.workerInstanceEpoch, 1);
  assert.equal(worker.identity.browserSessionEpoch, 2);
  assert.equal(metadata.browser_session_epoch, 2);
  assert.equal(native.connects, 2);
});

test("worker restores internal group mappings and clears them on browser-session advance", async () => {
  let metadata;
  let startupListener;
  const sessionState = {
    agentyc_tab_group_state: {
      pages: { page_restore: 73 },
      groups: {
        space_restore: {
          group_id: 11,
          tab_ids: [73],
          title: "restore",
        },
      },
    },
  };
  const storageArea = (data) => ({
    async get(keys) {
      return Object.fromEntries(keys.map((key) => [key, data[key]]));
    },
    async set(values) {
      Object.assign(data, structuredClone(values));
    },
  });
  const native = {
    requestedCapabilities: [],
    async connect() {},
    stop() {},
  };
  const chromeApi = {
    storage: {
      local: storageArea({}),
      session: storageArea(sessionState),
    },
    runtime: {
      getManifest: () => ({ version: "1.0.0" }),
      onStartup: {
        addListener(listener) {
          startupListener = listener;
        },
        removeListener() {},
      },
    },
  };
  const worker = new TabCreationWorker({ chromeApi, nativeClient: native });
  await worker.start();

  assert.equal(worker.pageTabs.get("page_restore"), 73);
  assert.equal(worker.groups.getInternal("space_restore").rawGroupId, 11);

  startupListener();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(worker.pageTabs.size, 0);
  assert.deepEqual(sessionState.agentyc_tab_group_state, {
    pages: {},
    groups: {},
  });
});
