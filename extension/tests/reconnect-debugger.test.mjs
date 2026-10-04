import test from "node:test";
import assert from "node:assert/strict";
import { NativeMessagingClient } from "../src/native-messaging.mjs";
import {
  DebuggerBridge,
  isAllowedDebuggerCommand,
} from "../src/debugger-bridge.mjs";
import { GroupsRegistry } from "../src/groups.mjs";
import { TabsRegistry } from "../src/tabs-registry.mjs";
import { FramesRegistry } from "../src/frames.mjs";
import { FakeChrome, makeHostHelloOk } from "./fake-chrome.mjs";

const wait = () => new Promise((resolve) => setImmediate(resolve));
const proof = (spaceId = "space_one", pageId = "page_one") => ({
  issued_by_host: true,
  proof_id: "claim-proof-123",
  space_id: spaceId,
  page_id: pageId,
  lease_epoch: 1,
});

test("Native Messaging reconnect starts a fresh nonce/sequence and never replays a mutation", async () => {
  const chrome = new FakeChrome();
  const unknown = [];
  const client = new NativeMessagingClient({
    chromeApi: chrome,
    workerInstanceEpoch: 1,
    browserSessionEpoch: 1,
    autoReconnect: false,
    onUnknownActions: (actions) => unknown.push(...actions),
  });
  await client.connect();
  const firstPort = chrome.lastPort;
  const firstHello = firstPort.sent[0];
  firstPort.receive(makeHostHelloOk(firstHello));
  client.sendRequest({
    method: "page.navigate",
    params: { space_id: "space_one", page_id: "page_one", lease_epoch: 1 },
    actionId: "action_lost",
    mutation: true,
  });
  firstPort.disconnect();
  assert.deepEqual(unknown, ["action_lost"]);

  await client.reconnect();
  const secondPort = chrome.lastPort;
  const secondHello = secondPort.sent[0];
  assert.equal(secondPort.sent.length, 1);
  assert.notEqual(secondHello.nonce, firstHello.nonce);
  assert.equal(secondHello.sequence, 1);
  secondPort.receive(makeHostHelloOk(secondHello, { connectionEpoch: 2 }));
  assert.equal(client.connected, true);
  assert.equal(
    secondPort.sent.some((message) => message.method === "page.navigate"),
    false,
  );
  client.stop();
});

test("Native Messaging ignores late messages from a previous connection", async () => {
  const chrome = new FakeChrome();
  const client = new NativeMessagingClient({
    chromeApi: chrome,
    workerInstanceEpoch: 1,
    browserSessionEpoch: 1,
    autoReconnect: false,
  });
  await client.connect();
  const firstPort = chrome.lastPort;
  const firstHello = firstPort.sent[0];
  firstPort.receive(makeHostHelloOk(firstHello));
  firstPort.disconnect();
  await client.reconnect();
  const secondPort = chrome.lastPort;
  const secondHello = secondPort.sent[0];
  secondPort.receive(makeHostHelloOk(secondHello, { connectionEpoch: 2 }));
  assert.equal(client.connected, true);

  client.handleIncoming(makeHostHelloOk(firstHello), 1, firstPort);
  assert.equal(client.connected, true);
  assert.equal(client.connectionEpoch, 2);
  client.stop();
});

test("Native Messaging disconnect requests an immediate reconnect before backoff", async () => {
  const chrome = new FakeChrome();
  const timers = [];
  const client = new NativeMessagingClient({
    chromeApi: chrome,
    workerInstanceEpoch: 1,
    browserSessionEpoch: 1,
    autoReconnect: true,
    setTimeoutFn: (callback, delay) => {
      const timer = { callback, delay, unref() {} };
      timers.push(timer);
      return timer;
    },
    clearTimeoutFn: (timer) => {
      const index = timers.indexOf(timer);
      if (index >= 0) timers.splice(index, 1);
    },
  });
  await client.connect();
  const firstPort = chrome.lastPort;
  firstPort.receive(makeHostHelloOk(firstPort.sent[0]));
  await wait();

  firstPort.disconnect();
  assert.equal(timers.length, 1);
  assert.equal(timers[0].delay, 0);
  timers.shift().callback();
  await wait();
  assert.notEqual(chrome.lastPort, firstPort);
  client.stop();
});

test("default reconnect timers preserve the platform receiver", async () => {
  const chrome = new FakeChrome();
  const timers = [];
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  globalThis.setTimeout = function strictSetTimeout(callback, delay) {
    assert.equal(this, globalThis);
    const timer = { callback, delay, unref() {} };
    timers.push(timer);
    return timer;
  };
  globalThis.clearTimeout = function strictClearTimeout(timer) {
    assert.equal(this, globalThis);
    const index = timers.indexOf(timer);
    if (index >= 0) timers.splice(index, 1);
  };

  try {
    const client = new NativeMessagingClient({
      chromeApi: chrome,
      workerInstanceEpoch: 1,
      browserSessionEpoch: 1,
      autoReconnect: true,
    });
    await client.connect();
    const port = chrome.lastPort;
    port.receive(makeHostHelloOk(port.sent[0]));
    port.disconnect();
    assert.equal(timers.length, 1);
    client.stop();
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});

test("debugger bridge routes only attributed events and returns unknown for lost mutation dispatch", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://agent.test/" }],
  });
  const groups = new GroupsRegistry({ chromeApi: chrome, hintSalt: "test" });
  const tabs = new TabsRegistry({
    chromeApi: chrome,
    groups,
    hintSalt: "test",
  });
  const frameEvents = [];
  const frames = new FramesRegistry({
    hintSalt: "test",
    onEvent: (event) => frameEvents.push(event),
  });
  const routedEvents = [];
  const bridge = new DebuggerBridge({
    chromeApi: chrome,
    tabs,
    frames,
    onEvent: (event) => routedEvents.push(event),
  });
  await tabs.start();
  tabs.bindManagedTab({
    tabId: 1,
    spaceId: "space_one",
    pageId: "page_one",
    leaseEpoch: 1,
    ownershipProof: proof(),
  });
  bridge.start();
  await bridge.attach({
    spaceId: "space_one",
    pageId: "page_one",
    leaseEpoch: 1,
  });
  assert.equal(isAllowedDebuggerCommand("Page.navigate"), true);
  assert.equal(isAllowedDebuggerCommand("Browser.getVersion"), false);
  const deniedMethods = [
    "DOM.setFileInputFiles",
    "Runtime.callFunctionOn",
    "Runtime.compileScript",
    "Runtime.runScript",
    "Runtime.addBinding",
    "Runtime.removeBinding",
    "Page.addScriptToEvaluateOnLoad",
    "Page.addScriptToEvaluateOnNewDocument",
    "Page.removeScriptToEvaluateOnLoad",
    "Page.removeScriptToEvaluateOnNewDocument",
    "Page.bringToFront",
    "Target.activateTarget",
    "Target.setAutoAttach",
  ];
  for (const deniedMethod of deniedMethods) {
    assert.equal(isAllowedDebuggerCommand(deniedMethod), false);
    await assert.rejects(
      () =>
        bridge.sendCommand({
          spaceId: "space_one",
          pageId: "page_one",
          leaseEpoch: 1,
          method: deniedMethod,
          params: {},
        }),
      (error) => error.code === "capability_unavailable",
    );
  }

  chrome.emitDebuggerEvent(1, "Network.requestWillBeSent", {
    requestId: "request-1",
    targetId: "raw-target",
    sessionId: "raw-session",
    objectId: "raw-object",
    scriptId: "raw-script",
    url: "https://agent.test/data",
  });
  await wait();
  assert.equal(routedEvents.at(-1).space_id, "space_one");
  assert.equal(routedEvents.at(-1).page_id, "page_one");
  assert.equal("targetId" in routedEvents.at(-1).params, false);
  assert.equal("sessionId" in routedEvents.at(-1).params, false);
  assert.equal("requestId" in routedEvents.at(-1).params, false);
  assert.equal("objectId" in routedEvents.at(-1).params, false);
  assert.equal("scriptId" in routedEvents.at(-1).params, false);
  assert.equal(frameEvents.length > 0, true);
  assert.equal(
    bridge.handleEvent({ tabId: 999 }, "Page.loadEventFired", {}),
    null,
  );

  chrome.debuggerFailures.set(
    "Page.navigate",
    new Error("debugger disconnected"),
  );
  await assert.rejects(
    () =>
      bridge.sendCommand({
        spaceId: "space_one",
        pageId: "page_one",
        leaseEpoch: 1,
        method: "Page.navigate",
        params: { url: "https://agent.test/next" },
        commandId: "command-1",
      }),
    (error) =>
      error.code === "unknown_outcome" &&
      error.outcome === "unknown" &&
      error.details?.chrome_error === "unknown" &&
      error.details?.cause === undefined,
  );
  await assert.rejects(
    () =>
      bridge.sendCommand({
        spaceId: "space_one",
        pageId: "page_one",
        leaseEpoch: 1,
        method: "Browser.getVersion",
      }),
    (error) => error.code === "capability_unavailable",
  );
  bridge.stop();
});

test("Chrome 125 related-target sessions are attached recursively and routed logically", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://agent.test/" }],
  });
  const groups = new GroupsRegistry({ chromeApi: chrome });
  const tabs = new TabsRegistry({ chromeApi: chrome, groups });
  const frames = new FramesRegistry();
  const routed = [];
  const bridge = new DebuggerBridge({
    chromeApi: chrome,
    tabs,
    frames,
    onEvent: (event) => routed.push(event),
  });
  await tabs.start();
  tabs.bindManagedTab({
    tabId: 1,
    spaceId: "space_one",
    pageId: "page_one",
    leaseEpoch: 1,
    ownershipProof: proof(),
  });
  bridge.start();
  await bridge.attach({
    spaceId: "space_one",
    pageId: "page_one",
    leaseEpoch: 1,
  });
  assert.equal(chrome.debuggerInternalCommands.length, 1);
  chrome.emitDebuggerEvent(1, "Target.attachedToTarget", {
    sessionId: "child-session",
    targetInfo: { type: "iframe", url: "https://child.agent.test/" },
  });
  await wait();
  await wait();
  assert.equal(Boolean(frames.getInternalBinding(1, "child-session")), true);
  assert.equal(chrome.debuggerInternalCommands.length, 2);
  assert.equal(
    chrome.debuggerInternalCommands[1].source.sessionId,
    "child-session",
  );
  frames.bindFrame({
    tabId: 1,
    sessionId: "child-session",
    frameId: "child-frame",
    logicalFrameId: "child-logical-frame",
  });
  await bridge.sendCommand({
    spaceId: "space_one",
    pageId: "page_one",
    leaseEpoch: 1,
    method: "Page.getFrameTree",
    frameScope: "child-logical-frame",
  });
  assert.equal(
    chrome.debuggerCommands.at(-1).source.sessionId,
    "child-session",
  );

  chrome.emitDebuggerEvent(
    1,
    "Runtime.executionContextCreated",
    { context: { id: 7, auxData: { frameId: "child-frame" } } },
    "child-session",
  );
  assert.equal(routed.at(-1)?.space_id, "space_one");
  assert.equal("sessionId" in (routed.at(-1)?.params ?? {}), false);
  chrome.emitDebuggerEvent(1, "Target.detachedFromTarget", {
    sessionId: "child-session",
    targetId: "raw-child-target",
  });
  assert.equal(frames.getInternalBinding(1, "child-session"), undefined);
  bridge.stop();
});

test("debugger detach is a loss event and does not trigger an automatic reattach", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://agent.test/" }],
  });
  const groups = new GroupsRegistry({ chromeApi: chrome });
  const tabs = new TabsRegistry({ chromeApi: chrome, groups });
  const frames = new FramesRegistry();
  const states = [];
  const bridge = new DebuggerBridge({
    chromeApi: chrome,
    tabs,
    frames,
    onStateChange: (state) => states.push(state),
  });
  await tabs.start();
  tabs.bindManagedTab({
    tabId: 1,
    spaceId: "space_one",
    pageId: "page_one",
    leaseEpoch: 1,
    ownershipProof: proof(),
  });
  bridge.start();
  await bridge.attach({
    spaceId: "space_one",
    pageId: "page_one",
    leaseEpoch: 1,
  });
  chrome.emitDebuggerDetach(1, "devtools_open");
  assert.equal(bridge.isAttached("page_one"), false);
  assert.equal(states.at(-1), "detached");
  assert.equal(chrome.debugger.attached.has(1), true);
  bridge.stop();
});
