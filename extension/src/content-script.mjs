import {
  ProtocolError,
  createLogicalId,
  createNonce,
  redactBrowserIdentifiers,
} from "./protocol.mjs";
import {
  PAGE_BRIDGE_VERSION,
  PAGE_OPERATIONS,
  createPageMessage,
  validatePageMessage,
} from "./page-bridge.mjs";

function chromeApiOrGlobal(chromeApi) {
  return chromeApi ?? globalThis.chrome;
}

function originFor(windowLike) {
  return windowLike?.location?.origin || globalThis.location?.origin;
}

const CONTENT_MESSAGE_TTL_MS = 60 * 1000;

function sameExtensionSender(sender, chromeApi) {
  const extensionId = chromeApi?.runtime?.id;
  return Boolean(extensionId && sender?.id === extensionId);
}

/**
 * Isolated-world relay. The content script has runtime messaging only; it
 * never calls connectNative and never becomes an authority or policy store.
 */
export function installContentScript({
  chromeApi,
  windowLike = globalThis.window,
  nonce = createNonce(),
  documentId = createLogicalId("document"),
  origin = originFor(windowLike),
  onResult = () => {},
} = {}) {
  const chrome = chromeApiOrGlobal(chromeApi);
  if (!windowLike?.addEventListener)
    throw new ProtocolError(
      "capability_unavailable",
      "content script window is unavailable",
    );
  const pending = new Map();

  const handleWorkerMessage = (message, sender) => {
    try {
      if (!sameExtensionSender(sender, chrome)) return undefined;
      if (
        !message ||
        message.type !== "agentyc.content.request" ||
        message.version !== PAGE_BRIDGE_VERSION
      )
        return undefined;
      if (message.nonce !== nonce || message.document_id !== documentId) {
        throw new ProtocolError(
          "stale_generation",
          "content request is for another document",
        );
      }
      if (
        typeof message.request_id !== "string" ||
        message.request_id.length < 8 ||
        message.request_id.length > 128 ||
        !Number.isSafeInteger(message.expires_at) ||
        message.expires_at <= Date.now() ||
        message.expires_at > Date.now() + CONTENT_MESSAGE_TTL_MS
      ) {
        throw new ProtocolError("proof_expired", "content request is expired");
      }
      if (!PAGE_OPERATIONS.has(message.operation)) {
        throw new ProtocolError(
          "capability_unavailable",
          "content operation is not allowlisted",
        );
      }
      if (pending.size >= 128)
        throw new ProtocolError(
          "resource_exhausted",
          "content bridge request bound reached",
        );
      const pageMessage = createPageMessage({
        nonce,
        documentId,
        direction: "extension_to_page",
        operation: message.operation,
        payload: message.payload ?? {},
        requestId: message.request_id,
        origin,
        expiresAt: message.expires_at,
      });
      pending.set(pageMessage.request_id, {
        expiresAt: message.expires_at,
        operation: message.operation,
      });
      windowLike.postMessage?.(pageMessage, origin || "*");
      return { accepted: true };
    } catch (error) {
      const result = {
        type: "agentyc.content.result",
        version: PAGE_BRIDGE_VERSION,
        request_id: message?.request_id,
        nonce,
        document_id: documentId,
        operation: message?.operation,
        expires_at: Date.now() + 1000,
        ok: false,
        error: {
          code:
            error instanceof ProtocolError
              ? error.code
              : "content_bridge_error",
          message: error instanceof Error ? error.message : String(error),
        },
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
        direction: "page_to_extension",
        origin,
      });
      const pendingEntry = pending.get(message.request_id);
      if (!pendingEntry || pendingEntry.expiresAt <= Date.now()) {
        pending.delete(message.request_id);
        return;
      }
      if (pendingEntry.operation !== message.operation) return;
      pending.delete(message.request_id);
      const payload = message.payload ?? {};
      const result = redactBrowserIdentifiers({
        type: "agentyc.content.result",
        version: PAGE_BRIDGE_VERSION,
        request_id: message.request_id,
        nonce,
        document_id: documentId,
        operation: message.operation,
        expires_at: pendingEntry.expiresAt,
        ok: payload.ok === true,
        ...(payload.ok === true
          ? { result: payload.result ?? {} }
          : { error: payload.error }),
      });
      onResult(result);
      void chrome?.runtime?.sendMessage?.(result);
    } catch {
      // Page messages are untrusted data. Invalid messages are dropped.
    }
  };

  const cleanup = () => {
    pending.clear();
    void chrome?.runtime?.sendMessage?.({
      type: "agentyc.content.closed",
      version: PAGE_BRIDGE_VERSION,
      nonce,
      document_id: documentId,
    });
  };
  windowLike.addEventListener("message", handlePageMessage);
  windowLike.addEventListener("pagehide", cleanup, { once: true });
  const runtimeEvent = chrome?.runtime?.onMessage;
  runtimeEvent?.addListener?.(handleWorkerMessage);
  void chrome?.runtime?.sendMessage?.({
    type: "agentyc.content.ready",
    version: PAGE_BRIDGE_VERSION,
    nonce,
    document_id: documentId,
    expires_at: Date.now() + CONTENT_MESSAGE_TTL_MS,
  });
  return {
    nonce,
    documentId,
    requestCount() {
      return pending.size;
    },
    handleWorkerMessage,
    handlePageMessage,
    stop() {
      windowLike.removeEventListener?.("message", handlePageMessage);
      windowLike.removeEventListener?.("pagehide", cleanup);
      runtimeEvent?.removeListener?.(handleWorkerMessage);
      cleanup();
    },
  };
}

export function makeContentRequest({
  nonce,
  documentId,
  operation,
  payload = {},
  requestId = createLogicalId("req"),
  expiresAt = Date.now() + CONTENT_MESSAGE_TTL_MS,
} = {}) {
  if (!PAGE_OPERATIONS.has(operation))
    throw new ProtocolError(
      "capability_unavailable",
      "content operation is not allowlisted",
    );
  if (
    !Number.isSafeInteger(expiresAt) ||
    expiresAt <= Date.now() ||
    expiresAt > Date.now() + CONTENT_MESSAGE_TTL_MS
  )
    throw new ProtocolError(
      "proof_expired",
      "content request expiry is invalid",
    );
  return {
    type: "agentyc.content.request",
    version: PAGE_BRIDGE_VERSION,
    nonce,
    document_id: documentId,
    request_id: requestId,
    operation,
    payload,
    expires_at: expiresAt,
  };
}
