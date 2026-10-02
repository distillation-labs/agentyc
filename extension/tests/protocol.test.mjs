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
