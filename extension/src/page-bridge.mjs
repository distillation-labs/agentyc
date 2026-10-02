import {
  MAX_STRING_BYTES,
  ProtocolError,
  createLogicalId,
  createNonce,
  redactBrowserIdentifiers,
} from "./protocol.mjs";

export const PAGE_BRIDGE_VERSION = 1;
export const MAX_PAGE_MESSAGE_BYTES = 64 * 1024;
export const PAGE_OPERATIONS = Object.freeze(
  new Set([
    "document.title",
    "document.text",
    "aria.summary",
    "element.attributes",
  ]),
);

function bytes(value) {
  return new TextEncoder().encode(value).byteLength;
}

function pageMessageBytes(message) {
  const serialized = JSON.stringify(message);
  if (
    typeof serialized !== "string" ||
    bytes(serialized) > MAX_PAGE_MESSAGE_BYTES
  ) {
    throw new ProtocolError(
      "message_too_large",
      "page bridge message exceeds its bound",
    );
  }
  return serialized;
}

function isPlainObject(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (Object.getPrototypeOf(value) === Object.prototype ||
      Object.getPrototypeOf(value) === null)
  );
}

export function validatePageMessage(
  message,
  { nonce, documentId, direction, origin } = {},
) {
  if (!isPlainObject(message))
    throw new ProtocolError("schema_invalid", "page message must be an object");
  pageMessageBytes(message);
  if (
    message.version !== PAGE_BRIDGE_VERSION ||
    message.type !== "agentyc.page.message"
  ) {
    throw new ProtocolError(
      "schema_invalid",
      "page bridge version or type is invalid",
    );
  }
  if (typeof nonce === "string" && message.nonce !== nonce) {
    throw new ProtocolError(
      "nonce_replayed",
      "page bridge nonce does not match the document",
    );
  }
  if (typeof documentId === "string" && message.document_id !== documentId) {
    throw new ProtocolError(
      "stale_generation",
      "page bridge document does not match",
    );
  }
  if (direction && message.direction !== direction) {
    throw new ProtocolError(
      "schema_invalid",
      "page bridge message direction is invalid",
    );
  }
  if (origin && message.origin !== origin) {
    throw new ProtocolError("origin_invalid", "page bridge origin is invalid");
  }
  if (
    typeof message.request_id !== "string" ||
    message.request_id.length > 128
  ) {
    throw new ProtocolError(
      "schema_invalid",
      "page bridge request id is invalid",
    );
  }
  if (!PAGE_OPERATIONS.has(message.operation)) {
    throw new ProtocolError(
      "capability_unavailable",
      "page operation is not allowlisted",
    );
  }
  if (!isPlainObject(message.payload ?? {})) {
    throw new ProtocolError(
      "schema_invalid",
      "page bridge payload must be an object",
    );
  }
  for (const [key, value] of Object.entries(message.payload ?? {})) {
    if (typeof value === "string" && bytes(value) > MAX_STRING_BYTES) {
      throw new ProtocolError(
        "message_too_large",
        `page payload field ${key} is too large`,
      );
    }
  }
  return message;
}

export function createPageMessage({
  nonce,
  documentId,
  direction,
  operation,
  payload = {},
  requestId = createLogicalId("req"),
  origin,
} = {}) {
  const message = {
    version: PAGE_BRIDGE_VERSION,
    type: "agentyc.page.message",
    nonce,
    document_id: documentId,
    direction,
    request_id: requestId,
    operation,
    payload,
    origin,
  };
  for (const key of Object.keys(message))
    if (message[key] === undefined) delete message[key];
  validatePageMessage(message, { nonce, documentId, direction, origin });
  return message;
}

function boundedText(value, max = 16_384) {
  return typeof value === "string" ? value.slice(0, max) : "";
}

function resolveElement(documentLike, payload = {}) {
  if (
    !documentLike?.querySelector ||
    typeof payload.selector !== "string" ||
    payload.selector.length > 512
  ) {
    throw new ProtocolError(
      "schema_invalid",
      "typed DOM operation requires a bounded selector",
    );
  }
  try {
    const element = documentLike.querySelector(payload.selector);
    if (!element)
      throw new ProtocolError("element_not_found", "element was not found");
    return element;
  } catch (error) {
    if (error instanceof ProtocolError) throw error;
    throw new ProtocolError("schema_invalid", "selector is invalid");
  }
}

function ariaSummary(documentLike) {
  const root = documentLike?.body ?? documentLike?.documentElement;
  if (!root?.querySelectorAll) return [];
  const nodes = [
    ...root.querySelectorAll(
      "[role],button,a,input,select,textarea,[aria-label]",
    ),
  ].slice(0, 256);
  return nodes.map((node) => ({
    role: boundedText(
      node.getAttribute?.("role") || node.tagName?.toLowerCase(),
      64,
    ),
    name: boundedText(
      node.getAttribute?.("aria-label") || node.innerText || node.textContent,
      256,
    ),
    disabled: Boolean(
      node.disabled || node.getAttribute?.("aria-disabled") === "true",
    ),
  }));
}

export function performPageOperation({
  documentLike = globalThis.document,
  operation,
  payload = {},
} = {}) {
  switch (operation) {
    case "document.title":
      return { title: boundedText(documentLike?.title, 512) };
    case "document.text": {
      const element = payload.selector
        ? resolveElement(documentLike, payload)
        : documentLike?.body;
      return {
        text: boundedText(element?.innerText || element?.textContent, 16_384),
      };
    }
    case "aria.summary":
      return { nodes: ariaSummary(documentLike) };
    case "element.attributes": {
      const element = resolveElement(documentLike, payload);
      const attributes = {};
      for (const attribute of [...(element.attributes ?? [])].slice(0, 64)) {
        attributes[boundedText(attribute.name, 128)] = boundedText(
          attribute.value,
          1024,
        );
      }
      return { attributes };
    }
    default:
      throw new ProtocolError(
        "capability_unavailable",
        "page operation is not allowlisted",
      );
  }
}

/** Install the page-world listener. It never receives Native Messaging access. */
export function installPageBridge({
  windowLike = globalThis.window,
  documentLike = globalThis.document,
  nonce = createNonce(),
  documentId = createLogicalId("document"),
  origin = globalThis.location?.origin,
  onResult = () => {},
} = {}) {
  if (!windowLike?.addEventListener)
    throw new ProtocolError(
      "capability_unavailable",
      "page window is unavailable",
    );
  const listener = (event) => {
    try {
      if (event.source !== windowLike) return;
      if (origin && event.origin !== origin) return;
      const message = validatePageMessage(event.data, {
        nonce,
        documentId,
        direction: "extension_to_page",
        origin,
      });
      const result = performPageOperation({
        documentLike,
        operation: message.operation,
        payload: message.payload,
      });
      const response = createPageMessage({
        nonce,
        documentId,
        direction: "page_to_extension",
        operation: message.operation,
        requestId: message.request_id,
        payload: { ok: true, result },
        origin,
      });
      onResult(response);
      windowLike.postMessage?.(response, origin || "*");
    } catch (error) {
      const requestId =
        typeof event?.data?.request_id === "string"
          ? event.data.request_id
          : createLogicalId("req");
      const response = createPageMessage({
        nonce,
        documentId,
        direction: "page_to_extension",
        operation: PAGE_OPERATIONS.has(event?.data?.operation)
          ? event.data.operation
          : "document.title",
        requestId,
        payload: {
          ok: false,
          error: {
            code:
              error instanceof ProtocolError ? error.code : "page_bridge_error",
            message: error instanceof Error ? error.message : String(error),
          },
        },
        origin,
      });
      onResult(response);
      windowLike.postMessage?.(response, origin || "*");
    }
  };
  windowLike.addEventListener("message", listener);
  return {
    nonce,
    documentId,
    stop() {
      windowLike.removeEventListener?.("message", listener);
    },
  };
}

export function publicPageResult(message) {
  return redactBrowserIdentifiers(message);
}
