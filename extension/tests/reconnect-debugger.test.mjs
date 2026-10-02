import test from 'node:test';
import assert from 'node:assert/strict';
import { NativeMessagingClient } from '../src/native-messaging.mjs';
import {
  DebuggerBridge,
  isAllowedDebuggerCommand
} from '../src/debugger-bridge.mjs';
import { GroupsRegistry } from '../src/groups.mjs';
import { TabsRegistry } from '../src/tabs-registry.mjs';
import { FramesRegistry } from '../src/frames.mjs';
import { FakeChrome, makeHostHelloOk } from './fake-chrome.mjs';

const wait = () => new Promise((resolve) => setImmediate(resolve));
const proof = (spaceId = 'space_one', pageId = 'page_one') => ({
  issued_by_host: true,
  proof_id: 'claim-proof-123',
  space_id: spaceId,
  page_id: pageId,
  lease_epoch: 1
});

test('Native Messaging reconnect starts a fresh nonce/sequence and never replays a mutation', async () => {
  const chrome = new FakeChrome();
  const unknown = [];
  const client = new NativeMessagingClient({
    chromeApi: chrome,
    workerInstanceEpoch: 1,
    browserSessionEpoch: 1,
    autoReconnect: false,
    onUnknownActions: (actions) => unknown.push(...actions)
  });
  await client.connect();
  const firstPort = chrome.lastPort;
  const firstHello = firstPort.sent[0];
  firstPort.receive(makeHostHelloOk(firstHello));
  client.sendRequest({
    method: 'page.navigate',
    params: { space_id: 'space_one', page_id: 'page_one', lease_epoch: 1 },
    actionId: 'action_lost',
    mutation: true
  });
  firstPort.disconnect();
  assert.deepEqual(unknown, ['action_lost']);

  await client.reconnect();
  const secondPort = chrome.lastPort;
  const secondHello = secondPort.sent[0];
  assert.equal(secondPort.sent.length, 1);
  assert.notEqual(secondHello.nonce, firstHello.nonce);
  assert.equal(secondHello.sequence, 1);
  secondPort.receive(makeHostHelloOk(secondHello, { connectionEpoch: 2 }));
  assert.equal(client.connected, true);
  assert.equal(secondPort.sent.some((message) => message.method === 'page.navigate'), false);
  client.stop();
});

test('debugger bridge routes only attributed events and returns unknown for lost mutation dispatch', async () => {
  const chrome = new FakeChrome({ tabs: [{ id: 1, active: true, url: 'https://agent.test/' }] });
  const groups = new GroupsRegistry({ chromeApi: chrome, hintSalt: 'test' });
  const tabs = new TabsRegistry({ chromeApi: chrome, groups, hintSalt: 'test' });
  const frameEvents = [];
  const frames = new FramesRegistry({ hintSalt: 'test', onEvent: (event) => frameEvents.push(event) });
  const routedEvents = [];
  const bridge = new DebuggerBridge({
    chromeApi: chrome,
    tabs,
    frames,
    onEvent: (event) => routedEvents.push(event)
  });
  await tabs.start();
  tabs.bindManagedTab({
    tabId: 1,
    spaceId: 'space_one',
    pageId: 'page_one',
    leaseEpoch: 1,
    ownershipProof: proof()
  });
  bridge.start();
  await bridge.attach({ spaceId: 'space_one', pageId: 'page_one', leaseEpoch: 1 });
  assert.equal(isAllowedDebuggerCommand('Page.navigate'), true);
  assert.equal(isAllowedDebuggerCommand('Browser.getVersion'), false);

  chrome.emitDebuggerEvent(1, 'Network.requestWillBeSent', {
    requestId: 'request-1',
    targetId: 'raw-target',
    sessionId: 'raw-session',
    url: 'https://agent.test/data'
  });
  await wait();
  assert.equal(routedEvents.at(-1).space_id, 'space_one');
  assert.equal(routedEvents.at(-1).page_id, 'page_one');
  assert.equal('targetId' in routedEvents.at(-1).params, false);
  assert.equal('sessionId' in routedEvents.at(-1).params, false);
  assert.equal(frameEvents.length > 0, true);
  assert.equal(bridge.handleEvent({ tabId: 999 }, 'Page.loadEventFired', {}), null);

  chrome.debuggerFailures.set('Page.navigate', new Error('debugger disconnected'));
  await assert.rejects(() => bridge.sendCommand({
    spaceId: 'space_one',
    pageId: 'page_one',
    leaseEpoch: 1,
    method: 'Page.navigate',
    params: { url: 'https://agent.test/next' },
    commandId: 'command-1'
  }), (error) => error.code === 'unknown_outcome' && error.outcome === 'unknown');
  await assert.rejects(() => bridge.sendCommand({
    spaceId: 'space_one', pageId: 'page_one', leaseEpoch: 1, method: 'Browser.getVersion'
  }), (error) => error.code === 'capability_unavailable');
  bridge.stop();
});

test('debugger detach is a loss event and does not trigger an automatic reattach', async () => {
  const chrome = new FakeChrome({ tabs: [{ id: 1, active: true, url: 'https://agent.test/' }] });
  const groups = new GroupsRegistry({ chromeApi: chrome });
  const tabs = new TabsRegistry({ chromeApi: chrome, groups });
  const frames = new FramesRegistry();
  const states = [];
  const bridge = new DebuggerBridge({ chromeApi: chrome, tabs, frames, onStateChange: (state) => states.push(state) });
  await tabs.start();
  tabs.bindManagedTab({ tabId: 1, spaceId: 'space_one', pageId: 'page_one', leaseEpoch: 1, ownershipProof: proof() });
  bridge.start();
  await bridge.attach({ spaceId: 'space_one', pageId: 'page_one', leaseEpoch: 1 });
  chrome.emitDebuggerDetach(1, 'devtools_open');
  assert.equal(bridge.isAttached('page_one'), false);
  assert.equal(states.at(-1), 'detached');
  assert.equal(chrome.debugger.attached.has(1), true);
  bridge.stop();
});
