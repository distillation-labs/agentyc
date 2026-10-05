import test from "node:test";
import assert from "node:assert/strict";

import { NativeMessagingClient } from "../src/native-messaging.mjs";
import { FakeChrome, makeHostHelloOk } from "./fake-chrome.mjs";

const wait = () => new Promise((resolve) => setImmediate(resolve));

test("a live Native Messaging disconnect reconnects in its event handler", async () => {
  const chrome = new FakeChrome();
  const scheduled = [];
  const client = new NativeMessagingClient({
    chromeApi: chrome,
    workerInstanceEpoch: 1,
    browserSessionEpoch: 1,
    profileInstanceId: "profile_fake",
    setTimeoutFn: (callback, delay) => {
      const timer = { callback, delay };
      scheduled.push(timer);
      return timer;
    },
    clearTimeoutFn: (timer) => {
      const index = scheduled.indexOf(timer);
      if (index >= 0) scheduled.splice(index, 1);
    },
  });

  await client.connect();
  const firstPort = chrome.lastPort;
  const firstHello = firstPort.sent.find((message) => message.kind === "hello");
  firstPort.receive(makeHostHelloOk(firstHello));
  await wait();
  assert.equal(client.connected, true);

  firstPort.disconnect();
  await wait();
  await wait();
  assert.equal(chrome.ports.length, 2);
  assert.equal(scheduled.length, 0);

  const retryPort = chrome.lastPort;
  retryPort.disconnect();
  await wait();
  assert.equal(chrome.ports.length, 2);
  assert.equal(scheduled.length, 1);
  assert.equal(scheduled[0].delay, 1000);

  client.stop();
  assert.equal(scheduled.length, 0);
});
