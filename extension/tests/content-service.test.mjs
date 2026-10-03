import test from "node:test";
import assert from "node:assert/strict";
import { createServiceWorker } from "../src/service-worker.mjs";
import { FakeChrome, makeHostHelloOk } from "./fake-chrome.mjs";

const wait = () => new Promise((resolve) => setImmediate(resolve));

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
  return { worker, hello };
}

test("service worker content routing validates sender, document, operation, nonce, expiry, and generation", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, url: "https://user.test/" }],
  });
  const { worker } = await boot(chrome);
  const proof = {
    issued_by_host: true,
    proof_id: "claim-content-proof",
    kind: "claim",
    space_id: "space_content",
    page_id: "page_content",
    lease_epoch: 1,
  };
  await worker.tabs.createAgentPage({
    spaceId: "space_content",
    pageId: "page_content",
    leaseEpoch: 1,
    url: "https://content.test/",
    ownershipProof: proof,
  });
  const record = worker.tabs.getInternalByPage("page_content");
  const sender = {
    id: chrome.runtime.id,
    tab: { id: record.rawTabId },
    url: record.url,
    origin: new URL(record.url).origin,
  };
  const ready = await worker.handleRuntimeMessage(
    {
      type: "agentyc.content.ready",
      version: 1,
      nonce: "nonce_content_1",
      document_id: "document_content_1",
      expires_at: Date.now() + 10_000,
    },
    sender,
  );
  assert.equal(ready.ok, true);
  const accepted = await worker.sendContentRequest({
    spaceId: "space_content",
    pageId: "page_content",
    leaseEpoch: 1,
    requestId: "request_content_1",
    params: { operation: "document.title", payload: {} },
  });
  assert.equal(accepted.accepted, true);
  const request = chrome.lastContentMessage.message;
  const invalidSender = await worker.handleRuntimeMessage(
    {
      ...request,
      type: "agentyc.content.result",
      ok: true,
      result: { title: "bad sender" },
    },
    { id: "other-extension", tab: { id: record.rawTabId }, url: record.url },
  );
  assert.equal(invalidSender, undefined);
  const wrongOrigin = await worker.handleRuntimeMessage(
    {
      ...request,
      type: "agentyc.content.result",
      ok: true,
      result: { title: "wrong origin" },
    },
    { ...sender, origin: "https://other.test" },
  );
  assert.equal(wrongOrigin.ok, false);
  assert.equal(wrongOrigin.error.code, "permission_denied");
  const result = await worker.handleRuntimeMessage(
    {
      type: "agentyc.content.result",
      version: 1,
      request_id: request.request_id,
      nonce: request.nonce,
      document_id: request.document_id,
      operation: request.operation,
      expires_at: request.expires_at,
      ok: true,
      result: { title: "safe" },
    },
    sender,
  );
  assert.equal(result.ok, true);
  assert.equal(worker.contentPending.size, 0);
  await assert.rejects(
    () =>
      worker.sendContentRequest({
        spaceId: "space_content",
        pageId: "page_content",
        leaseEpoch: 1,
        requestId: "request_content_2",
        params: { operation: "unsafe.eval", payload: {} },
      }),
    (error) => error.code === "capability_unavailable",
  );
  worker.stop();
});
