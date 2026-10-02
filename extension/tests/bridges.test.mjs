import test from 'node:test';
import assert from 'node:assert/strict';
import { installContentScript } from '../src/content-script.mjs';
import { installPageBridge } from '../src/page-bridge.mjs';
import { FakeChrome } from './fake-chrome.mjs';

class FakeWindow {
  constructor() {
    this.location = { origin: 'https://bridge.test' };
    this.listeners = new Map();
  }
  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) ?? new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }
  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }
  postMessage(data, targetOrigin) {
    for (const listener of [...(this.listeners.get('message') ?? [])])
      listener({ source: this, origin: targetOrigin, data });
  }
  emit(type) {
    for (const listener of [...(this.listeners.get(type) ?? [])])
      listener({ source: this, origin: this.location.origin });
  }
}

test('content/page bridge requires sender, document, operation, nonce, expiry, and cleans up', () => {
  const chrome = new FakeChrome();
  const windowLike = new FakeWindow();
  const results = [];
  const pageResults = [];
  const page = installPageBridge({
    windowLike,
    documentLike: { title: 'Bridge title', body: { innerText: 'text' } },
    nonce: 'nonce_bridge_1',
    documentId: 'document_bridge_1',
    origin: windowLike.location.origin,
    onResult: (message) => pageResults.push(message)
  });
  const content = installContentScript({
    chromeApi: chrome,
    windowLike,
    nonce: page.nonce,
    documentId: page.documentId,
    origin: windowLike.location.origin,
    onResult: (message) => results.push(message)
  });

  const request = {
    type: 'agentyc.content.request',
    version: 1,
    nonce: content.nonce,
    document_id: content.documentId,
    request_id: 'request_bridge_1',
    operation: 'document.title',
    payload: {},
    expires_at: Date.now() + 10_000
  };
  assert.equal(content.handleWorkerMessage(request, {}), undefined);
  assert.equal(content.requestCount(), 0);
  const accepted = content.handleWorkerMessage(request, { id: chrome.runtime.id });
  assert.deepEqual(accepted, { accepted: true });
  assert.equal(content.requestCount(), 0);
  assert.equal(results.at(-1).nonce, content.nonce);
  assert.equal(results.at(-1).document_id, content.documentId);
  assert.equal(results.at(-1).operation, 'document.title');
  assert.equal(results.at(-1).result.title, 'Bridge title');
  assert.equal(pageResults.at(-1).direction, 'page_to_extension');

  const invalidOperation = content.handleWorkerMessage({ ...request, request_id: 'request_bridge_2', operation: 'unsafe.eval' }, { id: chrome.runtime.id });
  assert.equal(invalidOperation.ok, false);
  assert.equal(results.at(-1).error.code, 'capability_unavailable');
  windowLike.emit('pagehide');
  assert.equal(content.requestCount(), 0);
  content.stop();
  page.stop();
});
