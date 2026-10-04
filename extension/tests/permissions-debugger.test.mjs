import test from "node:test";
import assert from "node:assert/strict";
import { createServiceWorker } from "../src/service-worker.mjs";
import { DebuggerBridge, hashRuntimeScript } from "../src/debugger-bridge.mjs";
import { FramesRegistry } from "../src/frames.mjs";
import { GroupsRegistry } from "../src/groups.mjs";
import { TabsRegistry } from "../src/tabs-registry.mjs";
import { classifyChromeError } from "../src/protocol.mjs";
import { FakeChrome } from "./fake-chrome.mjs";

const proof = (spaceId, pageId, leaseEpoch = 1) => ({
  issued_by_host: true,
  proof_id: `claim-${pageId}-proof`,
  kind: "claim",
  space_id: spaceId,
  page_id: pageId,
  lease_epoch: leaseEpoch,
});

async function makeBridge({ url = "https://agent.test/" } = {}) {
  const chrome = new FakeChrome({ tabs: [{ id: 1, url }] });
  const groups = new GroupsRegistry({ chromeApi: chrome });
  const tabs = new TabsRegistry({ chromeApi: chrome, groups });
  const frames = new FramesRegistry();
  await tabs.start();
  tabs.bindManagedTab({
    tabId: 1,
    spaceId: "space_permission",
    pageId: "page_permission",
    leaseEpoch: 1,
    ownershipProof: proof("space_permission", "page_permission"),
  });
  const bridge = new DebuggerBridge({ chromeApi: chrome, tabs, frames });
  return { chrome, tabs, frames, bridge };
}

function artifactApproval(
  record,
  {
    approval_id = "artifact_approval_1",
    purpose = "screenshot",
    user_gesture = true,
    ...overrides
  } = {},
) {
  return {
    issued_by_host: true,
    approval_id,
    purpose,
    expires_at: Date.now() + 10_000,
    space_id: record.spaceId,
    page_id: record.pageId,
    lease_epoch: record.leaseEpoch,
    target_generation: record.targetGeneration,
    navigation_generation: record.navigationGeneration,
    document_generation: record.documentGeneration,
    origin: new URL(record.url).origin,
    frame_scope: "main",
    user_gesture,
    ...overrides,
  };
}

async function attachPage(bridge) {
  await bridge.attach({
    spaceId: "space_permission",
    pageId: "page_permission",
    leaseEpoch: 1,
  });
}

test("Chrome errors collapse to the stable bounded classifier vocabulary", () => {
  assert.deepEqual(
    [
      classifyChromeError(new Error("Cannot access contents of the page")),
      classifyChromeError(new Error("blocked by enterprise policy")),
      classifyChromeError(new Error("Host access is restricted by policy.")),
      classifyChromeError(new Error("DLP blocked screenshot"), {
        operation: "Page.captureScreenshot",
      }),
      classifyChromeError(
        new Error("Screenshot capture is restricted by policy."),
        {
          operation: "Page.captureScreenshot",
        },
      ),
      classifyChromeError(new Error("permission denied")),
      classifyChromeError(new Error("permission denied"), {
        operation: "Page.captureScreenshot",
      }),
      classifyChromeError(new Error("unrecognized browser failure")),
      classifyChromeError(new Error("anything"), { incognito: true }),
    ],
    [
      "restricted_url",
      "policy_denied",
      "policy_denied",
      "artifact_denied",
      "artifact_denied",
      "permission_denied",
      "permission_denied",
      "unknown",
      "incognito_not_supported",
    ],
  );
  assert.equal(
    classifyChromeError({ chrome_code: "artifact_denied" }),
    "artifact_denied",
  );
});

test("restricted URLs fail before debugger attachment or command dispatch", async () => {
  const { chrome, bridge } = await makeBridge({ url: "chrome://settings/" });
  await assert.rejects(
    () =>
      bridge.attach({
        spaceId: "space_permission",
        pageId: "page_permission",
        leaseEpoch: 1,
      }),
    (error) => error.code === "restricted_url",
  );
  assert.deepEqual(chrome.debuggerAttachCalls, []);
  assert.deepEqual(chrome.debuggerCommands, []);
});

test("incognito pages fail closed with a typed denial", async () => {
  const { chrome, tabs, bridge } = await makeBridge();
  tabs.getInternalByPage("page_permission").incognito = true;
  await assert.rejects(
    () =>
      bridge.attach({
        spaceId: "space_permission",
        pageId: "page_permission",
        leaseEpoch: 1,
      }),
    (error) => error.code === "incognito_not_supported",
  );
  assert.deepEqual(chrome.debuggerAttachCalls, []);
});

test("permission revocation and enterprise policy failures are typed", async () => {
  const attachDenied = await makeBridge();
  attachDenied.chrome.debuggerAttachFailure = new Error("permission denied");
  await assert.rejects(
    () =>
      attachDenied.bridge.attach({
        spaceId: "space_permission",
        pageId: "page_permission",
        leaseEpoch: 1,
      }),
    (error) => error.code === "permission_denied",
  );

  const policyDenied = await makeBridge();
  policyDenied.chrome.debuggerAttachFailure = new Error(
    "blocked by enterprise policy",
  );
  await assert.rejects(
    () =>
      policyDenied.bridge.attach({
        spaceId: "space_permission",
        pageId: "page_permission",
        leaseEpoch: 1,
      }),
    (error) => error.code === "policy_denied",
  );

  const revoked = await makeBridge();
  await attachPage(revoked.bridge);
  revoked.chrome.debuggerFailures.set(
    "Page.getNavigationHistory",
    new Error("permission denied after revocation"),
  );
  await assert.rejects(
    () =>
      revoked.bridge.sendCommand({
        spaceId: "space_permission",
        pageId: "page_permission",
        leaseEpoch: 1,
        method: "Page.getNavigationHistory",
      }),
    (error) => error.code === "permission_denied",
  );

  const detachDenied = await makeBridge();
  await attachPage(detachDenied.bridge);
  detachDenied.chrome.debuggerDetachFailure = new Error("permission denied");
  await assert.rejects(
    () =>
      detachDenied.bridge.detach({
        spaceId: "space_permission",
        pageId: "page_permission",
        leaseEpoch: 1,
      }),
    (error) => error.code === "permission_denied",
  );
});

test("unsupported upload and download flows return typed policy denials", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, url: "https://agent.test/" }],
  });
  const worker = createServiceWorker({
    chromeApi: chrome,
    autoReconnect: false,
  });
  worker.tabs.bindManagedTab({
    tabId: 1,
    spaceId: "space_file_policy",
    pageId: "page_file_policy",
    leaseEpoch: 1,
    ownershipProof: proof("space_file_policy", "page_file_policy"),
    url: "https://agent.test/",
  });
  await assert.rejects(
    () =>
      worker.executeAllowlistedAction({
        spaceId: "space_file_policy",
        pageId: "page_file_policy",
        leaseEpoch: 1,
        params: { operation: "upload" },
      }),
    (error) => error.code === "upload_denied",
  );
  await assert.rejects(
    () =>
      worker.executeAllowlistedAction({
        spaceId: "space_file_policy",
        pageId: "page_file_policy",
        leaseEpoch: 1,
        params: { operation: "download" },
      }),
    (error) => error.code === "download_denied",
  );
  assert.deepEqual(chrome.debuggerCommands, []);
  worker.stop();
});

test("service worker denies artifact commands before attachment without approval", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, url: "https://agent.test/" }],
  });
  const worker = createServiceWorker({
    chromeApi: chrome,
    autoReconnect: false,
  });
  worker.tabs.bindManagedTab({
    tabId: 1,
    spaceId: "space_artifact",
    pageId: "page_artifact",
    leaseEpoch: 1,
    ownershipProof: proof("space_artifact", "page_artifact"),
    url: "https://agent.test/",
  });

  await assert.rejects(
    () =>
      worker.executeDebuggerCommand({
        spaceId: "space_artifact",
        pageId: "page_artifact",
        leaseEpoch: 1,
        params: {
          method: "Page.captureScreenshot",
          params: {},
        },
        requestId: "req_artifact_missing",
      }),
    (error) => error.code === "artifact_denied",
  );
  assert.deepEqual(chrome.debuggerAttachCalls, []);
  assert.deepEqual(chrome.debuggerCommands, []);

  const record = worker.tabs.getInternalByPage("page_artifact");
  const approval = {
    issued_by_host: true,
    approval_id: "artifact_payload_1",
    purpose: "screenshot",
    expires_at: Date.now() + 10_000,
    space_id: record.spaceId,
    page_id: record.pageId,
    lease_epoch: record.leaseEpoch,
    target_generation: record.targetGeneration,
    navigation_generation: record.navigationGeneration,
    document_generation: record.documentGeneration,
    origin: "https://agent.test",
    frame_scope: "main",
    user_gesture: true,
  };
  const result = await worker.executeDebuggerCommand({
    spaceId: record.spaceId,
    pageId: record.pageId,
    leaseEpoch: record.leaseEpoch,
    params: {
      method: "Page.captureScreenshot",
      params: {},
      payload: { artifact_approval: approval },
    },
    requestId: "req_artifact_payload",
  });
  assert.equal(result.ok, true);
  assert.equal(chrome.debuggerAttachCalls.length, 1);
  assert.equal(chrome.debuggerCommands.at(-1).method, "Page.captureScreenshot");
  worker.stop();
});

test("artifact approvals require gesture and exact scope, then allow one screenshot and one PDF", async () => {
  const { chrome, tabs, bridge } = await makeBridge();
  const record = tabs.getInternalByPage("page_permission");
  await attachPage(bridge);

  const noGesture = artifactApproval(record, {
    approval_id: "artifact_no_gesture",
    user_gesture: false,
  });
  await assert.rejects(
    () =>
      bridge.sendCommand({
        spaceId: record.spaceId,
        pageId: record.pageId,
        leaseEpoch: record.leaseEpoch,
        method: "Page.captureScreenshot",
        approval: noGesture,
      }),
    (error) => error.code === "user_confirmation_required",
  );
  assert.equal(chrome.debuggerCommands.length, 0);

  const screenshotApproval = artifactApproval(record, {
    approval_id: "artifact_screenshot_1",
  });
  const screenshot = await bridge.sendCommand({
    spaceId: record.spaceId,
    pageId: record.pageId,
    leaseEpoch: record.leaseEpoch,
    method: "Page.captureScreenshot",
    approval: screenshotApproval,
  });
  assert.equal(screenshot.ok, true);
  await assert.rejects(
    () =>
      bridge.sendCommand({
        spaceId: record.spaceId,
        pageId: record.pageId,
        leaseEpoch: record.leaseEpoch,
        method: "Page.captureScreenshot",
        approval: screenshotApproval,
      }),
    (error) => error.code === "replay_rejected",
  );

  const pdf = await bridge.sendCommand({
    spaceId: record.spaceId,
    pageId: record.pageId,
    leaseEpoch: record.leaseEpoch,
    method: "Page.printToPDF",
    approval: artifactApproval(record, {
      approval_id: "artifact_pdf_1",
      purpose: "pdf",
    }),
  });
  assert.equal(pdf.ok, true);
  assert.deepEqual(
    chrome.debuggerCommands.map((command) => command.method),
    ["Page.captureScreenshot", "Page.printToPDF"],
  );
});

test("DLP artifact failures return artifact_denied and never a successful artifact", async () => {
  const { chrome, tabs, bridge } = await makeBridge();
  const record = tabs.getInternalByPage("page_permission");
  await attachPage(bridge);
  chrome.debuggerFailures.set(
    "Page.captureScreenshot",
    new Error("DLP blocked screenshot"),
  );

  await assert.rejects(
    () =>
      bridge.sendCommand({
        spaceId: record.spaceId,
        pageId: record.pageId,
        leaseEpoch: record.leaseEpoch,
        method: "Page.captureScreenshot",
        approval: artifactApproval(record, {
          approval_id: "artifact_dlp_1",
        }),
      }),
    (error) => error.code === "artifact_denied",
  );
  assert.equal(chrome.debuggerCommands.at(-1).method, "Page.captureScreenshot");
});

test("Target and unsupported debugger events are dropped before routing", async () => {
  const { chrome, bridge } = await makeBridge();
  const events = [];
  bridge.onEvent = (event) => events.push(event);
  bridge.start();
  await attachPage(bridge);

  chrome.emitDebuggerEvent(1, "Target.targetCreated", {
    targetInfo: { targetId: "raw-target" },
  });
  chrome.emitDebuggerEvent(1, "Page.frameResized", {});
  assert.deepEqual(events, []);

  chrome.emitDebuggerEvent(1, "Page.loadEventFired", {});
  assert.equal(events.length, 1);
  assert.equal(events[0].method, "Page.loadEventFired");
  bridge.stop();
});

test("Runtime.evaluate approval is bound to purpose, navigation, document, origin, and frame", async () => {
  const { chrome, tabs, bridge } = await makeBridge();
  const record = tabs.getInternalByPage("page_permission");
  await attachPage(bridge);
  const expression = "document.title";
  const base = {
    issued_by_host: true,
    approval_id: "runtime_scope_1",
    purpose: "runtime.evaluate",
    script_hash: await hashRuntimeScript(expression),
    expires_at: Date.now() + 10_000,
    space_id: record.spaceId,
    page_id: record.pageId,
    lease_epoch: record.leaseEpoch,
    target_generation: record.targetGeneration,
    navigation_generation: record.navigationGeneration,
    document_generation: record.documentGeneration,
    origin: "https://agent.test",
    frame_scope: "main",
  };

  for (const [approval_id, change] of [
    ["runtime_nav_scope", { navigation_generation: 2 }],
    ["runtime_doc_scope", { document_generation: 2 }],
    ["runtime_origin_scope", { origin: "https://other.test" }],
    ["runtime_frame_scope", { frame_scope: "missing-frame" }],
    ["runtime_purpose_scope", { purpose: "screenshot" }],
  ]) {
    await assert.rejects(
      () =>
        bridge.sendCommand({
          spaceId: record.spaceId,
          pageId: record.pageId,
          leaseEpoch: record.leaseEpoch,
          method: "Runtime.evaluate",
          params: { expression },
          capability: "evaluate",
          approval: { ...base, approval_id, ...change },
        }),
      (error) => ["permission_denied", "stale_generation"].includes(error.code),
    );
  }
  assert.equal(chrome.debuggerCommands.length, 0);
});
