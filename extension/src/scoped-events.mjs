import {
  ProtocolError,
  assertLogicalScope,
  assertNoRawBrowserIdentifiers,
  redactBrowserIdentifiers,
} from "./protocol.mjs";

export const SENSITIVE_BOUNDARIES = Object.freeze(
  new Set([
    "login_challenge",
    "payment",
    "destructive_submit",
    "permission",
    "upload",
    "cookies",
    "evaluate",
  ]),
);

const PROTECTED_EVENT_PREFIXES = Object.freeze([
  "log.",
  "dialog.",
  "network.",
  "download.",
  "trace.",
  "mock.",
]);
const WAIT_EVENT_PREFIXES = Object.freeze([
  "debugger.",
  "page.",
  "tab.",
  "native.",
  "browser.",
  "space.",
  "action.",
  "host.",
  ...PROTECTED_EVENT_PREFIXES,
]);
const MAX_EVENT_RECORDS_PER_SCOPE = 256;
const MAX_EVENT_TEXT = 4096;
const MAX_TICKET_LIFETIME_MS = 60 * 1000;
const TICKET_ID_RE = /^[A-Za-z0-9._:-]{8,128}$/;
const SECRET_KEYS = new Set([
  "authorization",
  "cookie",
  "set_cookie",
  "set-cookie",
  "password",
  "passwd",
  "secret",
  "token",
  "access_token",
  "refresh_token",
  "api_key",
  "apikey",
  "request_body",
  "response_body",
  "post_data",
  "postdata",
  "file_path",
  "filepath",
]);
const PAGE_AUTHORITY_KEYS = new Set([
  "approved",
  "authorization",
  "authorized",
  "click",
  "clicked",
  "confirm",
  "confirmed",
  "focus",
  "focused",
  "intent_ticket",
  "user_intent",
]);

function isPlainObject(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (Object.getPrototypeOf(value) === Object.prototype ||
      Object.getPrototypeOf(value) === null)
  );
}

function boundedText(value) {
  if (typeof value !== "string") return value;
  return value
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(
      /(authorization|cookie|set-cookie|password|passwd|secret|token|api[_-]?key|request[_-]?body|response[_-]?body|post[_-]?data)\s*[:=]\s*[^\s,;&]+/gi,
      "$1=[redacted]",
    )
    .slice(0, MAX_EVENT_TEXT);
}

function normalizedKey(key) {
  return String(key).toLowerCase().replaceAll("-", "_");
}

function secretKey(key) {
  const normalized = normalizedKey(key);
  return (
    SECRET_KEYS.has(normalized) ||
    normalized === "body" ||
    normalized.includes("secret") ||
    normalized.includes("authorization") ||
    normalized.includes("api_key") ||
    normalized.includes("apikey") ||
    normalized.includes("cookie") ||
    normalized.endsWith("_token") ||
    normalized.endsWith("_body")
  );
}

function sanitizeValue(value, { pageSource = false } = {}) {
  if (typeof value === "string") return boundedText(value);
  if (value === null || typeof value !== "object") return value;
  if (Array.isArray(value))
    return value
      .slice(0, 256)
      .map((child) => sanitizeValue(child, { pageSource }));
  if (!isPlainObject(value)) return undefined;
  const withoutBrowserIds = redactBrowserIdentifiers(value);
  const output = {};
  for (const [key, child] of Object.entries(withoutBrowserIds)) {
    const normalized = normalizedKey(key);
    if (secretKey(key)) {
      // Bodies and secret values are never forwarded, even as event data.
      continue;
    }
    if (pageSource && PAGE_AUTHORITY_KEYS.has(normalized)) continue;
    const sanitized = sanitizeValue(child, { pageSource });
    if (sanitized !== undefined) output[key] = sanitized;
  }
  return output;
}

export function isProtectedScopedEvent(event) {
  return (
    typeof event === "string" &&
    PROTECTED_EVENT_PREFIXES.some((prefix) => event.startsWith(prefix))
  );
}

export function requiresPageScope(event) {
  return isProtectedScopedEvent(event);
}

export function normalizeScopedEvent(
  event,
  payload = {},
  { source = "host" } = {},
) {
  if (typeof event !== "string" || event.length === 0 || event.length > 128)
    throw new ProtocolError("schema_invalid", "event name is invalid");
  const pageSource = source === "page";
  const safePayload = sanitizeValue(payload, { pageSource });
  if (!isPlainObject(safePayload))
    throw new ProtocolError(
      "schema_invalid",
      "event payload must be an object",
    );
  const spaceId = safePayload.space_id;
  const pageId = safePayload.page_id;
  if (isProtectedScopedEvent(event)) {
    try {
      assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    } catch {
      throw new ProtocolError(
        "permission_denied",
        "observability event requires a logical space and page scope",
      );
    }
  } else if (spaceId !== undefined || pageId !== undefined) {
    assertLogicalScope({ spaceId, pageId });
  }
  if (pageSource) safePayload.untrusted_source = "page";
  try {
    assertNoRawBrowserIdentifiers(safePayload);
  } catch {
    throw new ProtocolError(
      "schema_invalid",
      "scoped event contains a browser-only identifier",
    );
  }
  return {
    event,
    payload: safePayload,
    ...(typeof spaceId === "string" ? { space_id: spaceId } : {}),
    ...(typeof pageId === "string" ? { page_id: pageId } : {}),
  };
}

function scopeKey(spaceId, pageId) {
  return `${spaceId}\u0000${pageId ?? ""}`;
}

function retainedEvent(event) {
  return WAIT_EVENT_PREFIXES.some((prefix) => event.startsWith(prefix));
}

function eventScopeMatches(record, spaceId, pageId) {
  return (
    record.space_id === spaceId &&
    (pageId === undefined || record.page_id === pageId)
  );
}

function textMatcherMatches(matcher, value) {
  if (!matcher || typeof value !== "string") return false;
  if (typeof matcher === "string") return value === matcher;
  if (typeof matcher !== "object") return false;
  if (typeof matcher.value !== "string") return false;
  switch (matcher.kind) {
    case "exact":
      return value === matcher.value;
    case "contains":
      return value.includes(matcher.value);
    case "prefix":
      return value.startsWith(matcher.value);
    case "suffix":
      return value.endsWith(matcher.value);
    default:
      return false;
  }
}

function matchesWaitCondition(condition, record) {
  if (!condition || typeof condition !== "object") return false;
  const payload = record.payload ?? {};
  switch (condition.kind) {
    case "event_kind":
      return record.event === condition.event;
    case "event":
      return (
        (condition.event === undefined || record.event === condition.event) &&
        Object.entries(condition.payload ?? {}).every(
          ([key, value]) => payload[key] === value,
        )
      );
    case "payload":
      return payload[condition.key] === condition.value;
    case "url":
      return (
        textMatcherMatches(condition.matcher, payload.url ?? payload.href) &&
        (condition.navigation === undefined ||
          payload.navigation === condition.navigation ||
          payload.navigation_kind === condition.navigation ||
          payload.transition === condition.navigation)
      );
    case "request":
    case "response": {
      const isResponse = condition.kind === "response";
      if (
        (isResponse &&
          !/(response|loading|finished|failed)/i.test(record.event)) ||
        (!isResponse && !/(request|loading)/i.test(record.event))
      )
        return false;
      return (
        (condition.url === undefined ||
          textMatcherMatches(condition.url, payload.url ?? payload.href)) &&
        (condition.method === undefined ||
          payload.method?.toLowerCase() === condition.method.toLowerCase()) &&
        (condition.resource_type === undefined ||
          (payload.resource_type ?? payload.type)?.toLowerCase() ===
            condition.resource_type.toLowerCase()) &&
        (condition.status === undefined ||
          Number(payload.status ?? payload.response_status) ===
            condition.status)
      );
    }
    case "element":
      return (
        (condition.selector === undefined ||
          payload.selector === condition.selector) &&
        (condition.text === undefined ||
          textMatcherMatches(
            condition.text,
            payload.text ?? payload.inner_text ?? payload.content,
          )) &&
        payload.state === condition.state
      );
    case "page":
      return (
        (condition.page_id === undefined ||
          payload.page_id === condition.page_id) &&
        (condition.lifecycle === undefined ||
          (payload.lifecycle ?? payload.state) === condition.lifecycle)
      );
    case "download":
      return (
        (condition.name === undefined ||
          textMatcherMatches(
            condition.name,
            payload.name ?? payload.filename ?? payload.file_name,
          )) &&
        (payload.state ?? payload.download_state) === condition.state
      );
    case "any":
      return (condition.conditions ?? []).some((child) =>
        matchesWaitCondition(child, record),
      );
    case "all":
      return (condition.conditions ?? []).every((child) =>
        matchesWaitCondition(child, record),
      );
    default:
      return false;
  }
}

/**
 * Adapter for event ingress and scoped event reads. It is deliberately
 * independent of Chrome tab/session handles and treats page-origin messages as
 * untrusted data.
 */
export class ScopedEventAdapter {
  constructor({ maxRecordsPerScope = MAX_EVENT_RECORDS_PER_SCOPE } = {}) {
    this.maxRecordsPerScope = Math.max(
      1,
      Math.min(MAX_EVENT_RECORDS_PER_SCOPE, maxRecordsPerScope),
    );
    this.records = new Map();
    this.waiters = new Map();
    this.nextWaiterId = 1;
  }

  admit(event, payload = {}, options = {}) {
    const normalized = normalizeScopedEvent(event, payload, options);
    if (
      retainedEvent(event) &&
      typeof normalized.space_id === "string" &&
      typeof normalized.page_id === "string"
    ) {
      const key = scopeKey(normalized.space_id, normalized.page_id);
      const records = this.records.get(key) ?? [];
      records.push(normalized);
      while (records.length > this.maxRecordsPerScope) records.shift();
      this.records.set(key, records);
      this.notifyWaiters(normalized);
    }
    return normalized;
  }

  waitFor({ requestId, spaceId, pageId, condition, timeoutMs = 60000 } = {}) {
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    if (
      typeof requestId !== "string" ||
      requestId.length < 8 ||
      requestId.length > 128
    )
      throw new ProtocolError("schema_invalid", "wait request id is invalid");
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60000)
      throw new ProtocolError("schema_invalid", "wait timeout is invalid");
    const existing = this.read({ spaceId, pageId }).find((record) =>
      matchesWaitCondition(condition, record),
    );
    if (existing) return Promise.resolve(existing);
    return new Promise((resolve, reject) => {
      const waiter = {
        requestId,
        spaceId,
        pageId,
        condition,
        resolve,
        reject,
        timer: setTimeout(() => {
          this.waiters.delete(requestId);
          reject(new ProtocolError("timeout", "event wait deadline elapsed"));
        }, timeoutMs),
      };
      this.waiters.set(requestId, waiter);
    });
  }

  cancelWait(requestId) {
    const waiter = this.waiters.get(requestId);
    if (!waiter) return false;
    clearTimeout(waiter.timer);
    this.waiters.delete(requestId);
    waiter.reject(new ProtocolError("cancelled", "event wait was cancelled"));
    return true;
  }

  notifyWaiters(record) {
    for (const [requestId, waiter] of this.waiters) {
      if (
        eventScopeMatches(record, waiter.spaceId, waiter.pageId) &&
        matchesWaitCondition(waiter.condition, record)
      ) {
        clearTimeout(waiter.timer);
        this.waiters.delete(requestId);
        waiter.resolve(record);
      }
    }
  }

  read({ spaceId, pageId } = {}) {
    assertLogicalScope({ spaceId, pageId });
    const output = [];
    for (const [key, records] of this.records) {
      const [recordSpace, recordPage] = key.split("\u0000");
      if (recordSpace !== spaceId) continue;
      if (pageId !== undefined && recordPage !== pageId) continue;
      output.push(...records);
    }
    return output;
  }

  clear({ spaceId, pageId } = {}) {
    assertLogicalScope({ spaceId, pageId });
    if (pageId !== undefined) this.records.delete(scopeKey(spaceId, pageId));
    else {
      for (const key of this.records.keys())
        if (key.startsWith(`${spaceId}\u0000`)) this.records.delete(key);
    }
    for (const [requestId, waiter] of this.waiters) {
      if (
        eventScopeMatches(
          { space_id: spaceId, page_id: pageId },
          waiter.spaceId,
          waiter.pageId,
        )
      )
        this.cancelWait(requestId);
    }
  }
}

function positiveEpoch(value, name) {
  if (!Number.isSafeInteger(value) || value < 1)
    throw new ProtocolError(
      "schema_invalid",
      `${name} must be a positive integer`,
    );
  return value;
}

function expectedTicketField(
  ticket,
  key,
  expected,
  code = "permission_denied",
) {
  if (expected === undefined || expected === null) return;
  if (ticket[key] !== expected)
    throw new ProtocolError(code, "intent ticket scope is not current");
}

function exactOptionalTicketField(
  ticket,
  key,
  expected,
  code = "permission_denied",
) {
  const actual = ticket[key] ?? undefined;
  if (actual !== expected)
    throw new ProtocolError(code, "intent ticket scope is not current");
}

/**
 * Side-panel/host ticket adapter for sensitive page operations. Lifecycle
 * tickets from the existing panel remain a separate compatibility path; this
 * adapter is strict about action hash, document, lease, and connection scope.
 */
export class SidePanelConfirmationAdapter {
  constructor({ now = () => Date.now(), maxUsedTickets = 1024 } = {}) {
    this.now = now;
    this.maxUsedTickets = Math.max(1, Math.min(1024, maxUsedTickets));
    this.used = new Map();
    this.cancelled = new Set();
    this.pausedSpaces = new Set();
  }

  setPaused(spaceId, paused = true) {
    assertLogicalScope({ spaceId });
    if (paused) this.pausedSpaces.add(spaceId);
    else this.pausedSpaces.delete(spaceId);
  }

  cancel(ticket) {
    this.assertTicketShape(ticket);
    this.cancelled.add(ticket.ticket_id);
    this.used.delete(ticket.ticket_id);
  }

  authorize({
    boundary,
    ticket,
    spaceId,
    pageId,
    leaseEpoch,
    documentGeneration,
    actionHash,
    profileInstanceId,
    connectionEpoch,
    connectionNonce,
  } = {}) {
    if (!SENSITIVE_BOUNDARIES.has(boundary))
      throw new ProtocolError(
        "schema_invalid",
        "sensitive boundary is not allowlisted",
      );
    assertLogicalScope(
      { spaceId, pageId },
      { pageRequired: pageId !== undefined },
    );
    positiveEpoch(leaseEpoch, "lease_epoch");
    if (this.pausedSpaces.has(spaceId)) return { mode: "paused" };
    if (!ticket)
      throw new ProtocolError(
        "user_confirmation_required",
        "sensitive boundary requires a host intent ticket",
      );
    this.assertTicketShape(ticket);
    if (this.cancelled.has(ticket.ticket_id))
      throw new ProtocolError("cancelled", "intent ticket was cancelled");
    const expiresAt = ticket.expires_at;
    if (
      !Number.isSafeInteger(expiresAt) ||
      expiresAt <= this.now() ||
      expiresAt > this.now() + MAX_TICKET_LIFETIME_MS
    )
      throw new ProtocolError("proof_expired", "intent ticket is expired");
    if (ticket.boundary !== undefined && ticket.boundary !== boundary)
      throw new ProtocolError(
        "permission_denied",
        "intent ticket boundary is not current",
      );
    if (typeof actionHash !== "string" || actionHash.length === 0)
      throw new ProtocolError(
        "user_confirmation_required",
        "sensitive boundary requires a canonical action hash",
      );
    expectedTicketField(ticket, "space_id", spaceId);
    exactOptionalTicketField(ticket, "page_id", pageId);
    expectedTicketField(ticket, "lease_epoch", leaseEpoch, "stale_lease");
    exactOptionalTicketField(
      ticket,
      "document_generation",
      documentGeneration,
      "stale_generation",
    );
    expectedTicketField(ticket, "action_hash", actionHash);
    expectedTicketField(ticket, "profile_instance_id", profileInstanceId);
    expectedTicketField(
      ticket,
      "connection_epoch",
      connectionEpoch,
      "stale_epoch",
    );
    expectedTicketField(
      ticket,
      "connection_nonce",
      connectionNonce,
      "stale_epoch",
    );
    if (ticket.state !== undefined && ticket.state !== "issued")
      throw new ProtocolError(
        "permission_denied",
        "intent ticket is not available",
      );
    this.prune();
    if (this.used.has(ticket.ticket_id))
      throw new ProtocolError(
        "replay_rejected",
        "intent ticket was already consumed",
      );
    if (this.used.size >= this.maxUsedTickets)
      throw new ProtocolError(
        "resource_exhausted",
        "intent ticket replay cache is full",
      );
    this.used.set(ticket.ticket_id, expiresAt);
    return { mode: "ticket", ticket_id: ticket.ticket_id };
  }

  assertTicketShape(ticket) {
    if (
      !ticket ||
      typeof ticket !== "object" ||
      ticket.issued_by_host !== true ||
      typeof ticket.ticket_id !== "string" ||
      !TICKET_ID_RE.test(ticket.ticket_id)
    )
      throw new ProtocolError(
        "user_confirmation_required",
        "ticket must be host-issued",
      );
  }

  prune() {
    const now = this.now();
    for (const [ticketId, expiresAt] of this.used)
      if (expiresAt <= now) this.used.delete(ticketId);
  }
}

export function boundaryForHostMethod(method, params = {}) {
  if (method === "cookies.write") return "cookies";
  if (method === "storage.write") return "permission";
  if (method === "page.upload") return "upload";
  if (method === "debugger.command" && params.method === "Runtime.evaluate")
    return "evaluate";
  if (
    method === "debugger.command" &&
    params.method === "Page.handleJavaScriptDialog"
  ) {
    const kind =
      params.dialog_kind ??
      params.payload?.dialog_kind ??
      params.sensitive_boundary ??
      params.boundary;
    if (typeof kind === "string" && SENSITIVE_BOUNDARIES.has(kind)) return kind;
    return "destructive_submit";
  }
  if (method === "action.execute") {
    const operation =
      params.operation ?? params.action ?? params.payload?.operation;
    if (operation === "evaluate") return "evaluate";
    if (operation === "upload") return "upload";
  }
  const explicit =
    params.sensitive_boundary ??
    params.boundary ??
    params.payload?.sensitive_boundary ??
    params.payload?.boundary;
  return typeof explicit === "string" && SENSITIVE_BOUNDARIES.has(explicit)
    ? explicit
    : undefined;
}

export function redactScopedPayload(payload, options = {}) {
  return sanitizeValue(payload, options);
}

export const SCOPED_EVENT_MAX_RECORDS = MAX_EVENT_RECORDS_PER_SCOPE;
