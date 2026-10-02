import {
  ProtocolError,
  createLogicalId,
  createNonce,
  redactBrowserIdentifiers
} from './protocol.mjs';
import {
  PAGE_BRIDGE_VERSION,
  PAGE_OPERATIONS,
  createPageMessage,
  validatePageMessage
} from './page-bridge.mjs';

function chromeApiOrGlobal(chromeApi) {
  return chromeApi ?? globalThis.chrome;
}

function originFor(windowLike) {
  return windowLike?.location?.origin || globalThis.location?.origin;
}

function sameExtensionSender(sender, chromeApi) {
  const extensionId = chromeApi?.runtime?.id;
  return !sender?.id || !extensionId || sender.id === extensionId;
}

/**
 * Isolated-world relay. The content script has runtime messaging only; it
 * never calls connectNative and never becomes an authority or policy store.
 */
export function installContentScript({
  chromeApi,
  windowLike = globalThis.window,
  nonce = createNonce(),
  documentId = createLogicalId('document'),
  origin = originFor(windowLike),
  onResult = () => {}
} = {}) {
  const chrome = chromeApiOrGlobal(chromeApi);
  if (!windowLike?.addEventListener) throw new ProtocolError('capability_unavailable', 'content script window is unavailable');
  const pending = new Map();

  const handleWorkerMessage = (message, sender) => {
    try {
      if (!sameExtensionSender(sender, chrome)) return undefined;
      if (!message || message.type !== 'agentyc.content.request' || message.version !== PAGE_BRIDGE_VERSION) return undefined;
      if (message.nonce !== nonce || message.document_id !== documentId) {
        throw new ProtocolError('stale_generation', 'content request is for another document');
      }
      if (!PAGE_OPERATIONS.has(message.operation)) {
        throw new ProtocolError('capability_unavailable', 'content operation is not allowlisted');
      }
      const pageMessage = createPageMessage({
        nonce,
        documentId,
        direction: 'extension_to_page',
        operation: message.operation,
        payload: message.payload ?? {},
        requestId: message.request_id,
        origin
      });
      pending.set(pageMessage.request_id, true);
      windowLike.postMessage?.(pageMessage, origin || '*');
      return { accepted: true };
    } catch (error) {
      const result = {
        type: 'agentyc.content.result',
        version: PAGE_BRIDGE_VERSION,
        request_id: message?.request_id,
        ok: false,
        error: {
          code: error instanceof ProtocolError ? error.code : 'content_bridge_error',
          message: error instanceof Error ? error.message : String(error)
        }
      };
      onResult(result);
      void chrome?.runtime?.sendMessage?.(result);
      return result;
    }
  };

  const handlePageMessage = (event) => {
    try {
      if (event.source !== windowLike) return;
      if (origin && event.origin !== origin) return;
      const message = validatePageMessage(event.data, {
        nonce,
        documentId,
        direction: 'page_to_extension',
        origin
      });
      if (!pending.has(message.request_id)) return;
      pending.delete(message.request_id);
      const payload = message.payload ?? {};
      const result = redactBrowserIdentifiers({
        type: 'agentyc.content.result',
        version: PAGE_BRIDGE_VERSION,
        request_id: message.request_id,
        operation: message.operation,
        ok: payload.ok === true,
        ...(payload.ok === true ? { result: payload.result ?? {} } : { error: payload.error })
      });
      onResult(result);
      void chrome?.runtime?.sendMessage?.(result);
    } catch {
      // Page messages are untrusted data. Invalid messages are dropped.
    }
  };

  windowLike.addEventListener('message', handlePageMessage);
  const runtimeEvent = chrome?.runtime?.onMessage;
  runtimeEvent?.addListener?.(handleWorkerMessage);
  return {
    nonce,
    documentId,
    requestCount() { return pending.size; },
    handleWorkerMessage,
    handlePageMessage,
    stop() {
      windowLike.removeEventListener?.('message', handlePageMessage);
      runtimeEvent?.removeListener?.(handleWorkerMessage);
      pending.clear();
    }
  };
}

export function makeContentRequest({ nonce, documentId, operation, payload = {}, requestId = createLogicalId('req') } = {}) {
  if (!PAGE_OPERATIONS.has(operation)) throw new ProtocolError('capability_unavailable', 'content operation is not allowlisted');
  return {
    type: 'agentyc.content.request',
    version: PAGE_BRIDGE_VERSION,
    nonce,
    document_id: documentId,
    request_id: requestId,
    operation,
    payload
  };
}
