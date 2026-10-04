import test from "node:test";
import assert from "node:assert/strict";
import {
  MAX_CONTROL_BYTES,
  ProtocolError,
  SequenceValidator,
  assertBoundedEnvelope,
  assertNoRawBrowserIdentifiers,
  createNonce,
  makeEnvelope,
  redactBrowserIdentifiers,
  validateEnvelope,
} from "../src/protocol.mjs";
import { NativeMessagingClient } from "../src/native-messaging.mjs";
import { FakeChrome, makeHostHelloOk } from "./fake-chrome.mjs";

function basicEnvelope(fields = {}) {
  return {
    protocol: 1,
    kind: "event",
    nonce: createNonce(),
    sequence: 1,
    event: "connection.changed",
    payload: {},
    ...fields,
  };
}

test("bounded envelopes reject malformed, raw-id, and oversized messages", () => {
  assert.throws(() => assertBoundedEnvelope(null), /plain object/);
  assert.throws(
    () => assertBoundedEnvelope(basicEnvelope({ tabId: 42 })),
    /raw browser identifiers/,
  );
  assert.throws(
    () =>
      assertBoundedEnvelope(
        basicEnvelope({ payload: { text: "x".repeat(MAX_CONTROL_BYTES) } }),
      ),
    (error) => {
      assert.equal(error.code, "message_too_large");
      return true;
    },
  );
  assert.throws(
    () => assertNoRawBrowserIdentifiers({ nested: { session_id: "secret" } }),
    /raw browser identifiers/,
  );
  assert.throws(
    () => assertNoRawBrowserIdentifiers({ params: { frameId: "secret" } }),
    /raw browser identifiers/,
  );
  assert.throws(
    () => assertNoRawBrowserIdentifiers({ params: { backendNodeId: 4 } }),
    /raw browser identifiers/,
  );
  assert.throws(
    () =>
      assertNoRawBrowserIdentifiers({ params: { frame: { id: "secret" } } }),
    /raw browser identifiers/,
  );
  assert.equal(
    redactBrowserIdentifiers({ frame: { id: "secret" }, loaderId: "loader" })
      .frame.id,
    undefined,
  );
  assert.deepEqual(
    makeEnvelope("event", {
      nonce: createNonce(),
      sequence: 1,
      event: "connection.changed",
      payload: {},
      optional: undefined,
    }).optional,
    undefined,
  );
});

test("post-handshake envelopes require every live epoch", () => {
  const envelope = basicEnvelope({
    broker_epoch: 1,
    connection_epoch: 1,
    worker_instance_epoch: 1,
    browser_session_epoch: 1,
  });
  assert.doesNotThrow(() =>
    validateEnvelope(envelope, {
      expectedBrokerEpoch: 1,
      expectedConnectionEpoch: 1,
      expectedWorkerInstanceEpoch: 1,
      expectedBrowserSessionEpoch: 1,
      requireEpochs: true,
    }),
  );
  assert.throws(
    () =>
      validateEnvelope(
        { ...envelope, browser_session_epoch: undefined },
        {
          expectedBrokerEpoch: 1,
          expectedConnectionEpoch: 1,
          expectedWorkerInstanceEpoch: 1,
          expectedBrowserSessionEpoch: 1,
          requireEpochs: true,
        },
      ),
    (error) => error.code === "schema_invalid",
  );
});

test("sequence validation rejects gaps and replay", () => {
  const sequence = new SequenceValidator();
  assert.equal(sequence.accept(1), 1);
  assert.throws(
    () => sequence.accept(1),
    (error) =>
      error instanceof ProtocolError && error.code === "sequence_replayed",
  );
  assert.throws(
    () => sequence.accept(3),
    (error) => error instanceof ProtocolError && error.code === "sequence_gap",
  );
});

test("logical request correlation fields are allowed on Native Messaging envelopes", async () => {
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
  const request = client.sendRequest({
    method: "tab.inventory",
    requestId: "req_logical_1",
    actionId: "action_logical_1",
  });
  assert.equal(request.request_id, "req_logical_1");
  assert.equal(request.action_id, "action_logical_1");
  assert.equal(port.sent.at(-1).kind, "request");
});

test("hello_ok negotiates only requested capabilities and bounded profile limits", async () => {
  const chrome = new FakeChrome();
  const client = new NativeMessagingClient({
    chromeApi: chrome,
    profileInstanceId: "profile_test",
    workerInstanceEpoch: 1,
    browserSessionEpoch: 1,
    autoReconnect: false,
  });
  await client.connect();
  const port = chrome.lastPort;
  const hello = port.sent[0];
  port.receive({
    ...makeHostHelloOk(hello),
    capabilities: ["not_requested"],
  });
  assert.equal(client.state, "rejected");
  assert.equal(port.disconnected, true);
});

test("Native Messaging artifact helpers enforce begin/chunk/end order and digest", async () => {
  const chrome = new FakeChrome();
  const client = new NativeMessagingClient({
    chromeApi: chrome,
    profileInstanceId: "profile_artifact",
    workerInstanceEpoch: 1,
    browserSessionEpoch: 1,
    autoReconnect: false,
  });
  await client.connect();
  const port = chrome.lastPort;
  const hello = port.sent[0];
  port.receive(makeHostHelloOk(hello));
  assert.throws(
    () =>
      client.sendArtifactChunk({
        artifactId: "artifact_missing",
        chunkSequence: 0,
        bytes: [1],
      }),
    (error) => error.code === "schema_invalid",
  );
  client.sendArtifactBegin({
    artifactId: "artifact_ordered",
    artifactKind: "binary",
    totalBytes: 2,
    chunkSize: 2,
    chunkCount: 1,
    digest: "fnv1a64:082f2407b4e8902a",
  });
  client.sendArtifactChunk({
    artifactId: "artifact_ordered",
    chunkSequence: 0,
    bytes: [1, 2],
  });
  client.sendArtifactEnd({
    artifactId: "artifact_ordered",
    totalBytes: 2,
    chunkCount: 1,
    digest: "fnv1a64:082f2407b4e8902a",
  });
  assert.deepEqual(
    port.sent.slice(-3).map((message) => message.kind),
    ["artifact_begin", "artifact_chunk", "artifact_end"],
  );
});

test("Native Messaging handshake validates nonce and sequence independently", async () => {
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
  port.receive({ ...makeHostHelloOk(hello), nonce: createNonce() });
  assert.equal(client.state, "rejected");
  assert.equal(port.disconnected, true);

  const second = new NativeMessagingClient({
    chromeApi: chrome,
    workerInstanceEpoch: 2,
    browserSessionEpoch: 1,
    autoReconnect: false,
  });
  await second.connect();
  const secondPort = chrome.lastPort;
  const secondHello = secondPort.sent[0];
  secondPort.receive(makeHostHelloOk(secondHello, { workerInstanceEpoch: 2 }));
  assert.equal(second.connected, true);
  secondPort.receive({
    ...makeHostHelloOk(secondHello, { workerInstanceEpoch: 2 }),
    kind: "event",
    sequence: 1,
  });
  assert.equal(second.state, "rejected");
});
