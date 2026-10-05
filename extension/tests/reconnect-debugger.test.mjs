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

function hostEvent(
  hello,
  { sequence, brokerEpoch = 1, connectionEpoch = 1, cursor },
) {
  return {
    protocol: 1,
    kind: "event",
    nonce: hello.nonce,
    sequence,
    broker_epoch: brokerEpoch,
    connection_epoch: connectionEpoch,
    worker_instance_epoch: hello.worker_instance_epoch,
    browser_session_epoch: hello.browser_session_epoch,
    event: "host.cursor_test",
    payload: { source: "host" },
    ...(cursor ? { cursor } : {}),
  };
}

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

test("Native Messaging reconnect resumes the broker cursor without reusing envelope sequence", async () => {
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
  firstPort.receive(
    hostEvent(firstHello, {
      sequence: 2,
      cursor: { broker_epoch: 1, sequence: 41 },
    }),
  );
  assert.deepEqual(client.connectionInfo.resumeCursor, {
    broker_epoch: 1,
    sequence: 41,
  });

  firstPort.disconnect();
  await client.reconnect();
  const secondPort = chrome.lastPort;
  const secondHello = secondPort.sent[0];
  assert.equal(secondHello.sequence, 1);
  assert.deepEqual(secondHello.resume_from, {
    broker_epoch: 1,
    sequence: 41,
  });
  assert.notEqual(secondHello.sequence, secondHello.resume_from.sequence);
  secondPort.receive({
    ...makeHostHelloOk(secondHello, { connectionEpoch: 2 }),
    resume: "accepted",
  });
  assert.equal(client.connected, true);
  assert.equal(client.connectionInfo.resumeStatus, "accepted");
  client.stop();
});

test("Native Messaging clears a retained cursor after a broker epoch resync", async () => {
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
  firstPort.receive(
    hostEvent(firstHello, {
      sequence: 2,
      cursor: { broker_epoch: 1, sequence: 9 },
    }),
  );
  firstPort.disconnect();

  await client.reconnect();
  const secondPort = chrome.lastPort;
  const secondHello = secondPort.sent[0];
  assert.deepEqual(secondHello.resume_from, {
    broker_epoch: 1,
    sequence: 9,
  });
  secondPort.receive({
    ...makeHostHelloOk(secondHello, {
      brokerEpoch: 2,
      connectionEpoch: 2,
    }),
    resume: "resync_required",
  });
  assert.equal(client.connected, true);
  assert.equal(client.connectionInfo.resumeCursor, undefined);
  assert.equal(client.connectionInfo.resumeStatus, "resync_required");

  secondPort.disconnect();
  await client.reconnect();
  const thirdHello = chrome.lastPort.sent[0];
  assert.equal(thirdHello.sequence, 1);
  assert.equal(Object.hasOwn(thirdHello, "resume_from"), false);
  client.stop();
});

test("Native Messaging rejects a stale broker cursor independently of envelope ordering", async () => {
  const chrome = new FakeChrome();
  const client = new NativeMessagingClient({
    chromeApi: chrome,
    workerInstanceEpoch: 1,
    browserSessionEpoch: 1,
    autoReconnect: false,
  });
  await client.connect();
  const port = chrome.lastPort;
  const hello = port.sent[0];
  port.receive(makeHostHelloOk(hello));
  port.receive(
    hostEvent(hello, {
      sequence: 2,
      cursor: { broker_epoch: 1, sequence: 10 },
    }),
  );
  port.receive(
    hostEvent(hello, {
      sequence: 3,
      cursor: { broker_epoch: 1, sequence: 9 },
    }),
  );
  assert.equal(client.state, "rejected");
  assert.equal(port.disconnected, true);
});

test("Native Messaging source event sequence is not retained as a broker cursor", async () => {
  const chrome = new FakeChrome();
  const client = new NativeMessagingClient({
    chromeApi: chrome,
    workerInstanceEpoch: 1,
    browserSessionEpoch: 1,
    autoReconnect: false,
  });
  await client.connect();
  const port = chrome.lastPort;
  const hello = port.sent[0];
  port.receive(makeHostHelloOk(hello));
  const event = client.sendEvent("extension.source_test", { value: true });
  assert.equal(event.sequence, 2);
  assert.equal(event.payload.event_sequence, 1);
  assert.equal(client.connectionInfo.resumeCursor, undefined);
  assert.equal(Object.hasOwn(hello, "resume_from"), false);
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
  await wait();
  assert.equal(chrome.ports.length, 2);
  assert.notEqual(chrome.lastPort, firstPort);
  assert.equal(timers.length, 0);

  // A disconnect before the replacement handshake completes is a failed
  // reconnect and should use backoff rather than recurse immediately.
  chrome.lastPort.disconnect();
  assert.equal(timers.length, 1);
  assert.equal(timers[0].delay, 1000);
  client.stop();
  assert.equal(timers.length, 0);
});

test("default reconnect timers preserve the platform receiver", () => {
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
    client.scheduleReconnect();
    assert.equal(timers.length, 1);
    assert.equal(timers[0].delay, 1000);
    client.stop();
    assert.equal(timers.length, 0);
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
    enableEventDomains: true,
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
  assert.equal(
    chrome.debuggerInternalCommands.filter(
      (command) => command.method === "Target.setAutoAttach",
    ).length,
    1,
  );
  chrome.emitDebuggerEvent(1, "Target.attachedToTarget", {
    sessionId: "child-session",
    targetInfo: { type: "iframe", url: "https://child.agent.test/" },
  });
  for (let index = 0; index < 8; index += 1) await wait();
  assert.equal(Boolean(frames.getInternalBinding(1, "child-session")), true);
  assert.equal(
    chrome.debuggerInternalCommands.filter(
      (command) => command.method === "Target.setAutoAttach",
    ).length,
    2,
  );
  assert.equal(
    chrome.debuggerCommands.some(
      (command) =>
        command.method === "Runtime.enable" &&
        command.source.sessionId === "child-session",
    ),
    true,
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

test("synchronous related-target events are attributed before auto-attach completes", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://agent.test/" }],
  });
  const groups = new GroupsRegistry({ chromeApi: chrome });
  const tabs = new TabsRegistry({ chromeApi: chrome, groups });
  const frames = new FramesRegistry();
  const bridge = new DebuggerBridge({
    chromeApi: chrome,
    tabs,
    frames,
    enableEventDomains: true,
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

  const sendCommand = chrome.debugger.sendCommand;
  chrome.debugger.sendCommand = async (source, method, params) => {
    const result = await sendCommand(source, method, params);
    if (method === "Target.setAutoAttach" && source.sessionId === undefined) {
      chrome.emitDebuggerEvent(1, "Target.attachedToTarget", {
        sessionId: "sync-child-session",
        targetInfo: { type: "iframe", url: "https://child.agent.test/" },
      });
    }
    return result;
  };

  await bridge.attach({
    spaceId: "space_one",
    pageId: "page_one",
    leaseEpoch: 1,
  });
  assert.equal(
    frames.getInternalBinding(1, "sync-child-session")?.pageId,
    "page_one",
  );
  for (let index = 0; index < 8; index += 1) await wait();
  assert.equal(
    chrome.debuggerInternalCommands.filter(
      (command) => command.method === "Target.setAutoAttach",
    ).length,
    2,
  );
  assert.equal(
    chrome.debuggerCommands.some(
      (command) =>
        command.method === "Runtime.enable" &&
        command.source.sessionId === "sync-child-session",
    ),
    true,
  );
  bridge.stop();
});

test("auto-attach setup failure rolls back the root debugger attachment", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, active: true, url: "https://agent.test/" }],
  });
  const groups = new GroupsRegistry({ chromeApi: chrome });
  const tabs = new TabsRegistry({ chromeApi: chrome, groups });
  const frames = new FramesRegistry();
  const bridge = new DebuggerBridge({ chromeApi: chrome, tabs, frames });
  await tabs.start();
  tabs.bindManagedTab({
    tabId: 1,
    spaceId: "space_one",
    pageId: "page_one",
    leaseEpoch: 1,
    ownershipProof: proof(),
  });
  chrome.debuggerFailures.set(
    "Target.setAutoAttach",
    new Error("auto-attach setup failed"),
  );

  await assert.rejects(
    () =>
      bridge.attach({
        spaceId: "space_one",
        pageId: "page_one",
        leaseEpoch: 1,
      }),
    (error) => error.code === "unknown_outcome",
  );
  assert.deepEqual(chrome.debuggerDetachCalls, [{ tabId: 1 }]);
  assert.equal(chrome.debugger.attached.has(1), false);
  assert.equal(bridge.isAttached("page_one"), false);
  assert.equal(frames.getInternalBinding(1), undefined);
});

test("frame and execution-context events retain logical attribution without raw handles", () => {
  const routed = [];
  const frames = new FramesRegistry({ onRoute: (event) => routed.push(event) });
  frames.bindTab({
    tabId: 1,
    spaceId: "space_one",
    pageId: "page_one",
  });
  frames.bindSession({
    tabId: 1,
    sessionId: "child-session",
    spaceId: "space_one",
    pageId: "page_one",
  });
  frames.bindFrame({
    tabId: 1,
    sessionId: "child-session",
    frameId: "raw-frame",
    logicalFrameId: "logical-frame",
  });

  const attached = frames.routeDebuggerEvent({
    tabId: 1,
    sessionId: "child-session",
    method: "Page.frameAttached",
    params: { frameId: "raw-frame", parentFrameId: "raw-root" },
  });
  assert.equal(attached.frame_id, "logical-frame");
  assert.equal(attached.params.frameId, undefined);
  assert.equal(attached.params.parentFrameId, undefined);

  const created = frames.routeDebuggerEvent({
    tabId: 1,
    sessionId: "child-session",
    method: "Runtime.executionContextCreated",
    params: {
      context: { id: 7, auxData: { frameId: "raw-frame", isDefault: true } },
    },
  });
  assert.equal(created.frame_id, "logical-frame");
  assert.equal(created.params.context.id, undefined);
  assert.equal(created.params.context.auxData.frameId, undefined);
  assert.equal(frames.contexts.size, 1);

  const consoleEvent = frames.routeDebuggerEvent({
    tabId: 1,
    sessionId: "child-session",
    method: "Runtime.consoleAPICalled",
    params: { executionContextId: 7, type: "log", args: [] },
  });
  assert.equal(consoleEvent.frame_id, "logical-frame");

  const destroyed = frames.routeDebuggerEvent({
    tabId: 1,
    sessionId: "child-session",
    method: "Runtime.executionContextDestroyed",
    params: { executionContextId: 7 },
  });
  assert.equal(destroyed.frame_id, "logical-frame");
  assert.equal(frames.contexts.size, 0);

  frames.routeDebuggerEvent({
    tabId: 1,
    sessionId: "child-session",
    method: "Runtime.executionContextCreated",
    params: { context: { id: 8, auxData: { frameId: "raw-frame" } } },
  });
  assert.equal(frames.contexts.size, 1);
  frames.routeDebuggerEvent({
    tabId: 1,
    sessionId: "child-session",
    method: "Runtime.executionContextsCleared",
    params: {},
  });
  assert.equal(frames.contexts.size, 0);
  assert.equal(routed.length, 6);
});

test("detached frame and context reuse gets fresh identities and drops late events", () => {
  const frames = new FramesRegistry();
  frames.bindTab({
    tabId: 1,
    spaceId: "space_one",
    pageId: "page_one",
  });
  frames.bindSession({
    tabId: 1,
    sessionId: "child-session",
    spaceId: "space_one",
    pageId: "page_one",
    targetId: "target-one",
  });
  const first = frames.bindFrame({
    tabId: 1,
    sessionId: "child-session",
    frameId: "raw-frame",
    logicalFrameId: "logical-frame-one",
    parentFrameId: "parent-frame",
  });
  const firstContext = frames.routeDebuggerEvent({
    tabId: 1,
    sessionId: "child-session",
    method: "Runtime.executionContextCreated",
    params: {
      context: { id: 7, auxData: { frameId: "raw-frame" } },
    },
  });
  assert.equal(firstContext.frame_id, first.frame_id);
  assert.equal(frames.contextFor(1, "child-session", 7)?.contextGeneration, 1);

  const detached = frames.routeDebuggerEvent({
    tabId: 1,
    sessionId: "child-session",
    method: "Page.frameDetached",
    params: { frameId: "raw-frame", reason: "removed" },
  });
  assert.equal(detached.frame_id, first.frame_id);
  assert.equal(frames.frameFor(1, "child-session", "raw-frame"), undefined);
  assert.equal(frames.contextFor(1, "child-session", 7), undefined);
  assert.equal(
    frames.routeDebuggerEvent({
      tabId: 1,
      sessionId: "child-session",
      method: "Page.frameNavigated",
      params: {
        frame: {
          id: "raw-frame",
          parentId: "parent-frame",
          url: "https://late.agent.test/",
        },
      },
    }),
    null,
  );
  assert.equal(
    frames.routeDebuggerEvent({
      tabId: 1,
      sessionId: "child-session",
      method: "Runtime.executionContextCreated",
      params: {
        context: { id: 7, auxData: { frameId: "raw-frame" } },
      },
    }),
    null,
  );

  const replacement = frames.bindFrame({
    tabId: 1,
    sessionId: "child-session",
    frameId: "raw-frame",
    parentFrameId: "parent-frame",
  });
  assert.notEqual(replacement.frame_id, first.frame_id);
  assert.equal(replacement.frame_version, first.frame_version + 1);
  const recreatedContext = frames.routeDebuggerEvent({
    tabId: 1,
    sessionId: "child-session",
    method: "Runtime.executionContextCreated",
    params: {
      context: { id: 7, auxData: { frameId: "raw-frame" } },
    },
  });
  assert.equal(recreatedContext.frame_id, replacement.frame_id);
  assert.equal(frames.contextFor(1, "child-session", 7)?.contextGeneration, 2);
  assert.equal(recreatedContext.params.context.id, undefined);
  assert.equal(recreatedContext.params.context.auxData.frameId, undefined);
});

test("execution context destroy and reuse gets a new context generation", () => {
  const frames = new FramesRegistry();
  frames.bindTab({
    tabId: 1,
    spaceId: "space_one",
    pageId: "page_one",
  });
  frames.bindSession({
    tabId: 1,
    sessionId: "child-session",
    spaceId: "space_one",
    pageId: "page_one",
  });
  frames.bindFrame({
    tabId: 1,
    sessionId: "child-session",
    frameId: "raw-frame",
    logicalFrameId: "logical-context-frame",
  });
  frames.routeDebuggerEvent({
    tabId: 1,
    sessionId: "child-session",
    method: "Runtime.executionContextCreated",
    params: {
      context: { id: 11, auxData: { frameId: "raw-frame" } },
    },
  });
  const destroyed = frames.routeDebuggerEvent({
    tabId: 1,
    sessionId: "child-session",
    method: "Runtime.executionContextDestroyed",
    params: { executionContextId: 11 },
  });
  assert.equal(destroyed.frame_id, "logical-context-frame");
  assert.equal(
    frames.routeDebuggerEvent({
      tabId: 1,
      sessionId: "child-session",
      method: "Runtime.consoleAPICalled",
      params: { executionContextId: 11, type: "log", args: [] },
    }),
    null,
  );

  const recreated = frames.routeDebuggerEvent({
    tabId: 1,
    sessionId: "child-session",
    method: "Runtime.executionContextCreated",
    params: {
      context: { id: 11, auxData: { frameId: "raw-frame" } },
    },
  });
  assert.equal(recreated.frame_id, "logical-context-frame");
  assert.equal(frames.contextFor(1, "child-session", 11)?.contextGeneration, 2);
  assert.equal(
    frames.routeDebuggerEvent({
      tabId: 1,
      sessionId: "child-session",
      method: "Runtime.consoleAPICalled",
      params: { executionContextId: 11, type: "log", args: [] },
    }).frame_id,
    "logical-context-frame",
  );
});

test("session invalidation retires mappings, generations, and late events", () => {
  const frames = new FramesRegistry();
  frames.bindTab({
    tabId: 1,
    spaceId: "space_one",
    pageId: "page_one",
  });
  frames.bindSession({
    tabId: 1,
    sessionId: "child-session",
    spaceId: "space_one",
    pageId: "page_one",
    targetId: "target-one",
  });
  const firstGeneration = frames.getSessionGeneration(1, "child-session");
  const first = frames.bindFrame({
    tabId: 1,
    sessionId: "child-session",
    frameId: "raw-frame",
    logicalFrameId: "logical-session-frame",
  });
  const lost = frames.invalidateSession(1, "child-session", "detached");
  assert.equal(lost.event, "debugger.session_lost");
  assert.equal(frames.getSessionGeneration(1, "child-session"), undefined);
  assert.equal(
    frames.routeDebuggerEvent({
      tabId: 1,
      sessionId: "child-session",
      method: "Network.requestWillBeSent",
      params: { requestId: "late-request", url: "https://late.agent.test/" },
    }),
    null,
  );

  frames.bindSession({
    tabId: 1,
    sessionId: "child-session",
    spaceId: "space_one",
    pageId: "page_one",
    targetId: "target-two",
  });
  assert.equal(
    frames.getSessionGeneration(1, "child-session"),
    firstGeneration + 1,
  );
  const replacement = frames.bindFrame({
    tabId: 1,
    sessionId: "child-session",
    frameId: "raw-frame",
  });
  assert.notEqual(replacement.frame_id, first.frame_id);
  assert.equal(replacement.frame_version, first.frame_version + 1);
});

test("stale related-target detach cannot invalidate a replacement session", async () => {
  const chrome = new FakeChrome();
  const frames = new FramesRegistry();
  const bridge = new DebuggerBridge({ chromeApi: chrome, frames });
  bridge.attached.set(1, {
    spaceId: "space_one",
    pageId: "page_one",
    targetGeneration: 1,
    documentGeneration: 1,
    navigationGeneration: 1,
  });

  assert.equal(
    bridge.handleRelatedTargetAttached(
      { tabId: 1 },
      {
        sessionId: "child-session",
        targetInfo: {
          type: "iframe",
          targetId: "target-one",
          url: "https://child.agent.test/",
        },
      },
    ),
    true,
  );
  const firstGeneration = frames.getSessionGeneration(1, "child-session");
  assert.equal(
    bridge.handleRelatedTargetAttached(
      { tabId: 1 },
      {
        sessionId: "child-session",
        targetInfo: {
          type: "iframe",
          targetId: "target-two",
          url: "https://replacement.agent.test/",
        },
      },
    ),
    true,
  );
  const replacementGeneration = frames.getSessionGeneration(1, "child-session");
  assert.equal(replacementGeneration, firstGeneration + 1);
  assert.equal(
    bridge.handleRelatedTargetDetached(
      { tabId: 1 },
      { sessionId: "child-session", targetId: "target-one" },
    ),
    false,
  );
  assert.equal(
    frames.getSessionGeneration(1, "child-session"),
    replacementGeneration,
  );
  assert.equal(
    bridge.handleRelatedTargetDetached(
      { tabId: 1 },
      { sessionId: "child-session", targetId: "target-two" },
    ),
    true,
  );
  assert.equal(frames.getInternalBinding(1, "child-session"), undefined);
  await wait();
});

test("detaching a parent retires dependent frame and context mappings", () => {
  const frames = new FramesRegistry();
  frames.bindTab({
    tabId: 1,
    spaceId: "space_one",
    pageId: "page_one",
  });
  frames.bindSession({
    tabId: 1,
    sessionId: "child-session",
    spaceId: "space_one",
    pageId: "page_one",
  });
  frames.bindFrame({
    tabId: 1,
    sessionId: "child-session",
    frameId: "raw-parent",
    logicalFrameId: "logical-parent",
  });
  frames.bindFrame({
    tabId: 1,
    sessionId: "child-session",
    frameId: "raw-child",
    logicalFrameId: "logical-child",
    parentFrameId: "raw-parent",
  });
  frames.routeDebuggerEvent({
    tabId: 1,
    sessionId: "child-session",
    method: "Runtime.executionContextCreated",
    params: {
      context: { id: 13, auxData: { frameId: "raw-child" } },
    },
  });

  frames.routeDebuggerEvent({
    tabId: 1,
    sessionId: "child-session",
    method: "Page.frameDetached",
    params: { frameId: "raw-parent", reason: "removed" },
  });
  assert.equal(frames.frameFor(1, "child-session", "raw-child"), undefined);
  assert.equal(frames.contextFor(1, "child-session", 13), undefined);
  assert.equal(
    frames.routeDebuggerEvent({
      tabId: 1,
      sessionId: "child-session",
      method: "Page.frameNavigated",
      params: {
        frame: { id: "raw-child", parentId: "raw-parent" },
      },
    }),
    null,
  );
});

test("mapped root navigation advances binding and frame generations", () => {
  const frames = new FramesRegistry();
  frames.bindTab({
    tabId: 1,
    spaceId: "space_one",
    pageId: "page_one",
  });
  frames.bindFrame({
    tabId: 1,
    frameId: "raw-root-frame",
    logicalFrameId: "logical-root-frame",
  });

  const navigated = frames.routeDebuggerEvent({
    tabId: 1,
    method: "Page.frameNavigated",
    params: {
      frame: { id: "raw-root-frame", url: "https://next.agent.test/" },
    },
  });
  assert.equal(navigated.frame_id, "logical-root-frame");
  assert.equal(navigated.document_generation, 2);
  assert.equal(navigated.navigation_generation, 2);
  assert.equal(frames.getInternalBinding(1).documentGeneration, 2);
  assert.equal(frames.frameFor(1, undefined, "raw-root-frame").frameVersion, 2);
});

test("parent mismatches do not route stale frame events", () => {
  const frames = new FramesRegistry();
  frames.bindTab({
    tabId: 1,
    spaceId: "space_one",
    pageId: "page_one",
  });
  frames.bindSession({
    tabId: 1,
    sessionId: "child-session",
    spaceId: "space_one",
    pageId: "page_one",
  });
  frames.bindFrame({
    tabId: 1,
    sessionId: "child-session",
    frameId: "raw-frame",
    logicalFrameId: "logical-parent-frame",
    parentFrameId: "parent-one",
  });

  assert.equal(
    frames.routeDebuggerEvent({
      tabId: 1,
      sessionId: "child-session",
      method: "Page.frameNavigated",
      params: {
        frame: { id: "orphan-frame", url: "https://orphan.agent.test/" },
      },
    }),
    null,
  );
  assert.equal(
    frames.routeDebuggerEvent({
      tabId: 1,
      sessionId: "child-session",
      method: "Page.frameNavigated",
      params: {
        frame: {
          id: "raw-frame",
          parentId: "parent-two",
          url: "https://wrong-parent.agent.test/",
        },
      },
    }),
    null,
  );
  assert.equal(
    frames.routeDebuggerEvent({
      tabId: 1,
      sessionId: "child-session",
      method: "Page.frameAttached",
      params: { frameId: "raw-frame", parentFrameId: "parent-two" },
    }),
    null,
  );
  assert.equal(
    frames.frameFor(1, "child-session", "raw-frame").rawParentFrameId,
    "parent-one",
  );
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
