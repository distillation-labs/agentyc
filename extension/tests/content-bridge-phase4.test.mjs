import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";

const contentBridgeSource = await readFile(
  new URL("../src/content-bridge.js", import.meta.url),
  "utf8",
);
const pageBridgeSource = await readFile(
  new URL("../src/page-bridge-content.js", import.meta.url),
  "utf8",
);

class FakeWindow {
  constructor(origin = "https://bridge.test") {
    this.location = { origin };
    this.listeners = new Map();
    this.sent = [];
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) ?? new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  dispatch(type, { source = this, origin = this.location.origin, data } = {}) {
    for (const listener of [...(this.listeners.get(type) ?? [])])
      listener({ source, origin, data });
  }

  postMessage(data, targetOrigin) {
    this.sent.push({ data, targetOrigin });
  }
}

function runScript(source, globals = {}) {
  runInNewContext(source, {
    Date,
    Math,
    TextEncoder,
    crypto: { randomUUID: () => "12345678-1234-4234-8234-123456789abc" },
    ...globals,
  });
}

function contentHarness() {
  const windowLike = new FakeWindow();
  const sent = [];
  let runtimeListener;
  let nativeMessagingReads = 0;
  const runtime = new Proxy(
    {
      id: "test-extension-id",
      onMessage: {
        addListener(listener) {
          runtimeListener = listener;
        },
      },
      get connectNative() {
        nativeMessagingReads += 1;
        return () => {
          nativeMessagingReads += 1;
        };
      },
      sendMessage(message) {
        sent.push(message);
      },
    },
    {
      get(target, property, receiver) {
        return Reflect.get(target, property, receiver);
      },
    },
  );
  runScript(contentBridgeSource, {
    window: windowLike,
    chrome: { runtime },
    document: {
      title: "Trusted title",
      body: { innerText: "Trusted document text" },
    },
  });
  const ready = sent[0];
  const request = (overrides = {}) => ({
    type: "agentyc.content.request",
    version: 1,
    nonce: ready.nonce,
    document_id: ready.document_id,
    request_id: "request_phase4_1",
    operation: "document.title",
    payload: {},
    expires_at: Date.now() + 10_000,
    ...overrides,
  });
  return {
    windowLike,
    sent,
    ready,
    request,
    runtimeListener,
    get nativeMessagingReads() {
      return nativeMessagingReads;
    },
  };
}

test("production content bridge accepts only extension requests and exposes no Native Messaging API", () => {
  const harness = contentHarness();
  const { ready, sent, runtimeListener } = harness;
  assert.equal(ready.type, "agentyc.content.ready");
  assert.equal(typeof ready.nonce, "string");
  assert.equal(typeof ready.document_id, "string");

  harness.windowLike.dispatch("message", {
    data: {
      type: "agentyc.content.request",
      request_id: "page_forged_1",
      operation: "document.title",
    },
  });
  assert.equal(
    sent.length,
    1,
    "hostile page postMessage is not a content request",
  );

  assert.equal(
    runtimeListener(harness.request(), { id: "hostile-page" }),
    undefined,
  );
  assert.equal(sent.length, 1, "wrong runtime sender is ignored");

  assert.equal(
    runtimeListener(harness.request(), { id: "test-extension-id" })?.ok,
    true,
  );
  assert.equal(sent.at(-1).type, "agentyc.content.result");
  assert.equal(sent.at(-1).result.title, "Trusted title");

  for (const invalid of [
    harness.request({ nonce: "nonce_wrong_value" }),
    harness.request({ document_id: "document_wrong_value" }),
    harness.request({ expires_at: Date.now() - 1 }),
    harness.request({ operation: "unsafe.eval" }),
    harness.request({ payload: { text: "x".repeat(65 * 1024) } }),
  ]) {
    const before = sent.length;
    assert.equal(
      runtimeListener(invalid, { id: "test-extension-id" })?.ok,
      false,
    );
    assert.equal(sent.length, before + 1);
    assert.equal(sent.at(-1).type, "agentyc.content.result");
    assert.equal(sent.at(-1).ok, false);
  }

  assert.equal(harness.nativeMessagingReads, 0);
  harness.windowLike.dispatch("pagehide");
  assert.equal(sent.at(-1).type, "agentyc.content.closed");
  assert.equal(sent.at(-1).nonce, ready.nonce);
  assert.equal(sent.at(-1).document_id, ready.document_id);
});

test("prompt-like page text is returned as data and cannot alter the typed operation boundary", () => {
  const harness = contentHarness();
  harness.runtimeListener(
    harness.request({
      operation: "document.text",
      payload: {},
    }),
    { id: "test-extension-id" },
  );
  const result = harness.sent.at(-1);
  assert.equal(result.type, "agentyc.content.result");
  assert.match(result.result.text, /Trusted document text/);
  assert.doesNotMatch(result.result.text, /execute|tool|system/i);
  assert.equal(harness.nativeMessagingReads, 0);
});

test("page-world bridge rejects hostile source, origin, nonce, document, expiry, size, and operation", () => {
  const windowLike = new FakeWindow();
  const documentLike = {
    title: "Page title",
    body: { innerText: "Page text" },
  };
  runScript(pageBridgeSource, {
    window: windowLike,
    document: documentLike,
    location: windowLike.location,
  });
  assert.equal(windowLike.sent.length, 0);

  const base = {
    type: "agentyc.page.init",
    version: 1,
    nonce: "nonce_phase4_123",
    document_id: "document_phase4_123",
    origin: windowLike.location.origin,
    expires_at: Date.now() + 10_000,
  };
  windowLike.dispatch("message", {
    source: {},
    data: base,
  });
  windowLike.dispatch("message", {
    origin: "https://attacker.test",
    data: base,
  });
  assert.equal(
    windowLike.sent.length,
    0,
    "invalid init does not establish bridge state",
  );

  windowLike.dispatch("message", { data: base });
  assert.equal(windowLike.sent.at(-1).data.type, "agentyc.page.ready");
  windowLike.sent.length = 0;

  const request = (overrides = {}) => ({
    type: "agentyc.page.message",
    version: 1,
    direction: "extension_to_page",
    nonce: base.nonce,
    document_id: base.document_id,
    origin: base.origin,
    request_id: "request_phase4_2",
    operation: "document.title",
    payload: {},
    expires_at: Date.now() + 10_000,
    ...overrides,
  });
  for (const hostile of [
    { event: { source: {}, data: request() } },
    { event: { origin: "https://attacker.test", data: request() } },
    { event: { data: request({ nonce: "nonce_wrong_value" }) } },
    { event: { data: request({ document_id: "document_wrong_value" }) } },
    { event: { data: request({ origin: "https://attacker.test" }) } },
    { event: { data: request({ expires_at: Date.now() - 1 }) } },
    { event: { data: request({ operation: "unsafe.eval" }) } },
    {
      event: {
        data: request({ payload: { text: "x".repeat(65 * 1024) } }),
      },
    },
  ]) {
    windowLike.dispatch("message", hostile.event);
    assert.equal(windowLike.sent.length, 0);
  }

  windowLike.dispatch("message", { data: request() });
  assert.equal(windowLike.sent.length, 1);
  assert.equal(windowLike.sent[0].data.direction, "page_to_extension");
  assert.equal(windowLike.sent[0].data.payload.result.title, "Page title");
  assert.equal(windowLike.sent[0].targetOrigin, windowLike.location.origin);

  windowLike.dispatch("pagehide");
  windowLike.sent.length = 0;
  windowLike.dispatch("message", { data: request() });
  assert.equal(
    windowLike.sent.length,
    0,
    "pagehide clears page-world bridge state",
  );
});
