import {
  ProtocolError,
  assertLogicalScope,
  browserHint,
  redactBrowserIdentifiers,
} from "./protocol.mjs";

function sessionKey(tabId, sessionId) {
  return `${tabId}:${sessionId ?? "root"}`;
}

export const DEBUGGER_EVENT_ALLOWLIST = Object.freeze({
  Accessibility: Object.freeze(["loadComplete", "nodesUpdated"]),
  DOM: Object.freeze([
    "attributeModified",
    "attributeRemoved",
    "characterDataModified",
    "childNodeCountUpdated",
    "childNodeInserted",
    "childNodeRemoved",
    "documentUpdated",
    "setChildNodes",
    "shadowRootPopped",
    "shadowRootPushed",
  ]),
  Log: Object.freeze(["entryAdded"]),
  Network: Object.freeze([
    "loadingFailed",
    "loadingFinished",
    "requestServedFromCache",
    "requestWillBeSent",
    "responseReceived",
    "webSocketClosed",
    "webSocketCreated",
    "webSocketFrameError",
    "webSocketFrameReceived",
    "webSocketFrameSent",
    "webSocketHandshakeResponseReceived",
    "webSocketWillSendHandshakeRequest",
  ]),
  Page: Object.freeze([
    "domContentEventFired",
    "frameAttached",
    "frameDetached",
    "frameNavigated",
    "frameStartedLoading",
    "frameStoppedLoading",
    "javascriptDialogClosed",
    "javascriptDialogOpening",
    "lifecycleEvent",
    "loadEventFired",
    "navigatedWithinDocument",
  ]),
  Runtime: Object.freeze([
    "consoleAPICalled",
    "exceptionRevoked",
    "exceptionThrown",
    "executionContextCreated",
    "executionContextDestroyed",
    "executionContextsCleared",
  ]),
});

export function isAllowedDebuggerEvent(method) {
  if (typeof method !== "string") return false;
  const separator = method.indexOf(".");
  if (separator < 1 || separator === method.length - 1) return false;
  const domain = method.slice(0, separator);
  const event = method.slice(separator + 1);
  return (
    Object.prototype.hasOwnProperty.call(DEBUGGER_EVENT_ALLOWLIST, domain) &&
    DEBUGGER_EVENT_ALLOWLIST[domain].includes(event)
  );
}

/**
 * Maps debugger-only target/session/frame handles to logical page scope. A
 * missing mapping is a hard routing miss; there is no active-tab fallback.
 */
export class FramesRegistry {
  constructor({ onEvent = () => {}, onRoute, hintSalt = "" } = {}) {
    this.onEvent = onEvent;
    this.onRoute = onRoute ?? onEvent;
    this.hintSalt = hintSalt;
    this.tabs = new Map();
    this.sessions = new Map();
    this.frames = new Map();
    this.contexts = new Map();
    this.nextLogicalFrameNumber = 1;
  }

  allocateLogicalFrameId() {
    const value = this.nextLogicalFrameNumber++;
    return `frame-${value.toString(36)}`;
  }

  setHintSalt(hintSalt) {
    if (typeof hintSalt === "string") this.hintSalt = hintSalt;
  }

  bindTab({
    tabId,
    spaceId,
    pageId,
    origin,
    targetGeneration = 1,
    documentGeneration = 1,
    navigationGeneration = 1,
  } = {}) {
    if (!Number.isInteger(tabId))
      throw new ProtocolError(
        "schema_invalid",
        "frame binding requires an internal tab",
      );
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    const binding = {
      rawTabId: tabId,
      spaceId,
      pageId,
      origin: typeof origin === "string" ? origin : undefined,
      targetGeneration,
      documentGeneration,
      navigationGeneration,
      frameTopologyVersion: 1,
      sessionIds: new Set(),
    };
    this.tabs.set(tabId, binding);
    this.sessions.set(sessionKey(tabId), binding);
    return this.publicBinding(binding);
  }

  bindSession({
    tabId,
    sessionId,
    spaceId,
    pageId,
    origin,
    targetGeneration = 1,
    documentGeneration = 1,
    navigationGeneration = 1,
  } = {}) {
    if (
      !Number.isInteger(tabId) ||
      typeof sessionId !== "string" ||
      sessionId.length === 0
    ) {
      throw new ProtocolError(
        "schema_invalid",
        "child session binding requires internal handles",
      );
    }
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    const tab = this.tabs.get(tabId);
    if (tab && (tab.spaceId !== spaceId || tab.pageId !== pageId)) {
      throw new ProtocolError(
        "ambiguous_binding",
        "debugger session scope conflicts with its tab",
      );
    }
    const binding = tab ?? {
      rawTabId: tabId,
      spaceId,
      pageId,
      origin: typeof origin === "string" ? origin : undefined,
      targetGeneration,
      documentGeneration,
      navigationGeneration,
      frameTopologyVersion: 1,
      sessionIds: new Set(),
    };
    binding.sessionIds.add(sessionId);
    this.tabs.set(tabId, binding);
    this.sessions.set(sessionKey(tabId, sessionId), binding);
    return this.publicBinding(binding);
  }

  bindFrame({
    tabId,
    sessionId,
    frameId,
    logicalFrameId,
    origin,
    documentGeneration,
    navigationGeneration,
  } = {}) {
    if (
      !Number.isInteger(tabId) ||
      typeof frameId !== "string" ||
      frameId.length === 0
    ) {
      throw new ProtocolError(
        "schema_invalid",
        "frame binding requires internal frame handles",
      );
    }
    const binding = this.sessions.get(sessionKey(tabId, sessionId));
    if (!binding)
      throw new ProtocolError(
        "page_not_found",
        "frame has no logical page binding",
      );
    const key = `${sessionKey(tabId, sessionId)}:${frameId}`;
    const previous = this.frames.get(key);
    const frame = {
      rawTabId: tabId,
      rawSessionId: sessionId,
      rawFrameId: frameId,
      spaceId: binding.spaceId,
      pageId: binding.pageId,
      origin:
        typeof origin === "string"
          ? origin
          : (previous?.origin ?? binding.origin),
      logicalFrameId:
        typeof logicalFrameId === "string"
          ? logicalFrameId
          : (previous?.logicalFrameId ?? this.allocateLogicalFrameId()),
      documentGeneration:
        documentGeneration ??
        previous?.documentGeneration ??
        binding.documentGeneration,
      navigationGeneration:
        navigationGeneration ??
        previous?.navigationGeneration ??
        binding.navigationGeneration,
      frameVersion: previous?.frameVersion ?? 1,
    };
    this.frames.set(key, frame);
    return this.publicFrame(frame);
  }

  bindExecutionContext({ tabId, sessionId, executionContextId, frameId } = {}) {
    if (!Number.isInteger(tabId) || !Number.isSafeInteger(executionContextId)) {
      throw new ProtocolError(
        "schema_invalid",
        "execution context binding is invalid",
      );
    }
    if (
      frameId !== undefined &&
      (typeof frameId !== "string" || frameId.length === 0)
    ) {
      throw new ProtocolError(
        "schema_invalid",
        "execution context frame handle is invalid",
      );
    }
    const session = this.sessions.get(sessionKey(tabId, sessionId));
    if (!session)
      throw new ProtocolError(
        "page_not_found",
        "execution context has no logical page binding",
      );
    this.contexts.set(`${sessionKey(tabId, sessionId)}:${executionContextId}`, {
      rawTabId: tabId,
      rawSessionId: sessionId,
      rawExecutionContextId: executionContextId,
      rawFrameId: frameId,
      spaceId: session.spaceId,
      pageId: session.pageId,
      documentGeneration: session.documentGeneration,
    });
  }

  frameFor(tabId, sessionId, frameId) {
    if (typeof frameId !== "string" || frameId.length === 0) return undefined;
    return this.frames.get(`${sessionKey(tabId, sessionId)}:${frameId}`);
  }

  rawFrameIdForEvent(tabId, sessionId, method, params = {}) {
    if (typeof params.frameId === "string") return params.frameId;
    if (typeof params.frame?.id === "string") return params.frame.id;
    if (
      method === "Runtime.executionContextCreated" &&
      typeof params.context?.auxData?.frameId === "string"
    )
      return params.context.auxData.frameId;
    if (Number.isSafeInteger(params.executionContextId)) {
      return this.contexts.get(
        `${sessionKey(tabId, sessionId)}:${params.executionContextId}`,
      )?.rawFrameId;
    }
    return undefined;
  }

  clearTab(tabId) {
    this.tabs.delete(tabId);
    for (const key of [...this.sessions.keys()]) {
      if (key.startsWith(`${tabId}:`)) this.sessions.delete(key);
    }
    for (const [key, frame] of this.frames) {
      if (frame.rawTabId === tabId) this.frames.delete(key);
    }
    for (const [key, context] of this.contexts) {
      if (context.rawTabId === tabId) this.contexts.delete(key);
    }
  }

  observeDebuggerEvent({ tabId, sessionId, method, params = {} } = {}) {
    if (!isAllowedDebuggerEvent(method)) return;
    const binding = this.sessions.get(sessionKey(tabId, sessionId));
    if (!binding) return;
    if (
      method === "Page.frameNavigated" &&
      typeof params.frame?.id === "string"
    ) {
      const frameId = params.frame.id;
      const isRoot = typeof params.frame.parentId !== "string";
      let frame = this.frameFor(tabId, sessionId, frameId);
      if (!isRoot && !frame) {
        try {
          this.bindFrame({
            tabId,
            sessionId,
            frameId,
            origin: params.frame.url,
          });
          frame = this.frameFor(tabId, sessionId, frameId);
        } catch {
          // Attribution remains conservative until the session is bound.
        }
      }
      if (frame) {
        frame.documentGeneration += 1;
        frame.navigationGeneration += 1;
        frame.frameVersion += 1;
        if (typeof params.frame.url === "string")
          frame.origin = params.frame.url;
      }
      if (isRoot) {
        binding.documentGeneration += 1;
        binding.navigationGeneration += 1;
      }
    }
    if (method === "Page.frameAttached" && typeof params.frameId === "string") {
      try {
        this.bindFrame({
          tabId,
          sessionId,
          frameId: params.frameId,
          origin: params.url,
        });
      } catch {
        // Attribution remains conservative until a logical frame binding exists.
      }
      binding.frameTopologyVersion += 1;
    }
    if (method === "Page.frameDetached" && typeof params.frameId === "string") {
      const key = `${sessionKey(tabId, sessionId)}:${params.frameId}`;
      this.frames.delete(key);
      for (const [contextKey, context] of this.contexts) {
        if (
          context.rawTabId === tabId &&
          context.rawSessionId === sessionId &&
          context.rawFrameId === params.frameId
        )
          this.contexts.delete(contextKey);
      }
      binding.frameTopologyVersion += 1;
    }
    if (method === "Runtime.executionContextCreated") {
      const executionContextId = params.context?.id;
      const frameId = params.context?.auxData?.frameId;
      if (Number.isSafeInteger(executionContextId)) {
        try {
          if (typeof frameId === "string" && frameId.length > 0) {
            const exactKey = `${sessionKey(tabId, sessionId)}:${frameId}`;
            const previous = this.frameFor(tabId, sessionId, frameId);
            if (!this.frames.has(exactKey)) {
              this.bindFrame({
                tabId,
                sessionId,
                frameId,
                logicalFrameId: previous?.logicalFrameId,
                origin: previous?.origin,
                documentGeneration: previous?.documentGeneration,
                navigationGeneration: previous?.navigationGeneration,
              });
            }
          }
          this.bindExecutionContext({
            tabId,
            sessionId,
            executionContextId,
            frameId,
          });
        } catch {
          // Ignore contexts that cannot be attributed to a live logical page.
        }
      }
    }
    if (method === "Runtime.executionContextDestroyed") {
      if (Number.isSafeInteger(params.executionContextId)) {
        this.contexts.delete(
          `${sessionKey(tabId, sessionId)}:${params.executionContextId}`,
        );
      }
    }
    if (method === "Runtime.executionContextsCleared") {
      const prefix = `${sessionKey(tabId, sessionId)}:`;
      for (const key of [...this.contexts.keys()]) {
        if (key.startsWith(prefix)) this.contexts.delete(key);
      }
    }
  }

  routeDebuggerEvent({ tabId, sessionId, method, params = {} } = {}) {
    if (!isAllowedDebuggerEvent(method)) return null;
    const binding = this.sessions.get(sessionKey(tabId, sessionId));
    if (!binding || typeof method !== "string") return null;
    const beforeFrameId = this.rawFrameIdForEvent(
      tabId,
      sessionId,
      method,
      params,
    );
    const beforeFrame = this.frameFor(tabId, sessionId, beforeFrameId);
    this.observeDebuggerEvent({ tabId, sessionId, method, params });
    const frameId =
      this.rawFrameIdForEvent(tabId, sessionId, method, params) ??
      beforeFrameId;
    const frame = this.frameFor(tabId, sessionId, frameId) ?? beforeFrame;
    const routed = {
      event: "debugger.event",
      method,
      space_id: binding.spaceId,
      page_id: binding.pageId,
      target_generation: binding.targetGeneration,
      document_generation:
        frame?.documentGeneration ?? binding.documentGeneration,
      navigation_generation:
        frame?.navigationGeneration ?? binding.navigationGeneration,
      frame_topology_version: binding.frameTopologyVersion,
      params: redactBrowserIdentifiers(params),
    };
    if (frame?.logicalFrameId) routed.frame_id = frame.logicalFrameId;
    this.onRoute(routed);
    return routed;
  }

  invalidateTab(tabId, reason = "target_lost") {
    const binding = this.tabs.get(tabId);
    if (!binding) return null;
    this.clearTab(tabId);
    const event = {
      event: "debugger.target_lost",
      reason,
      space_id: binding.spaceId,
      page_id: binding.pageId,
      target_generation: binding.targetGeneration + 1,
    };
    this.onEvent(event);
    return event;
  }

  invalidateSession(tabId, sessionId, reason = "session_lost") {
    const key = sessionKey(tabId, sessionId);
    const binding = this.sessions.get(key);
    if (!binding) return null;
    this.sessions.delete(key);
    binding.sessionIds.delete(sessionId);
    for (const frameKey of [...this.frames.keys()]) {
      if (frameKey.startsWith(`${key}:`)) this.frames.delete(frameKey);
    }
    for (const contextKey of [...this.contexts.keys()]) {
      if (contextKey.startsWith(`${key}:`)) this.contexts.delete(contextKey);
    }
    const event = {
      event: "debugger.session_lost",
      reason,
      space_id: binding.spaceId,
      page_id: binding.pageId,
      target_generation: binding.targetGeneration + 1,
    };
    this.onEvent(event);
    return event;
  }

  getInternalBinding(tabId, sessionId) {
    return this.sessions.get(sessionKey(tabId, sessionId));
  }

  resolveFrameScope({ tabId, frameScope = "main" } = {}) {
    const rootBinding = this.sessions.get(sessionKey(tabId));
    if (!rootBinding)
      throw new ProtocolError(
        "page_not_found",
        "frame scope has no logical page binding",
      );
    if (frameScope === "main")
      return {
        frameScope,
        origin: rootBinding.origin,
        sessionId: undefined,
        binding: rootBinding,
      };
    if (typeof frameScope !== "string" || frameScope.length === 0)
      throw new ProtocolError(
        "permission_denied",
        "frame scope is not allowlisted",
      );
    const frame = [...this.frames.values()].find(
      (candidate) =>
        candidate.rawTabId === tabId && candidate.logicalFrameId === frameScope,
    );
    if (!frame)
      throw new ProtocolError(
        "stale_generation",
        "requested logical frame scope is not current",
      );
    const binding = this.sessions.get(sessionKey(tabId, frame.rawSessionId));
    if (!binding)
      throw new ProtocolError(
        "stale_generation",
        "frame session binding is not current",
      );
    return {
      frameScope,
      origin: frame.origin,
      sessionId: frame.rawSessionId,
      binding,
    };
  }

  assertFrameScope({ tabId, sessionId, frameScope = "main" } = {}) {
    if (sessionId !== undefined) {
      const binding = this.sessions.get(sessionKey(tabId, sessionId));
      if (!binding)
        throw new ProtocolError(
          "page_not_found",
          "frame session has no logical page binding",
        );
    }
    const resolved = this.resolveFrameScope({ tabId, frameScope });
    return { frameScope: resolved.frameScope, origin: resolved.origin };
  }

  invalidateDocument(
    tabId,
    documentGeneration,
    navigationGeneration,
    reason = "document_changed",
  ) {
    const binding = this.tabs.get(tabId);
    if (!binding) return null;
    for (const key of [...this.sessions.keys()]) {
      if (key.startsWith(`${tabId}:`) && key !== sessionKey(tabId))
        this.sessions.delete(key);
    }
    for (const key of [...this.frames.keys()]) {
      if (key.startsWith(`${tabId}:`)) this.frames.delete(key);
    }
    for (const key of [...this.contexts.keys()]) {
      if (key.startsWith(`${tabId}:`)) this.contexts.delete(key);
    }
    binding.documentGeneration = documentGeneration;
    binding.navigationGeneration = navigationGeneration;
    binding.frameTopologyVersion += 1;
    binding.sessionIds.clear();
    const event = {
      event: "debugger.document_changed",
      reason,
      space_id: binding.spaceId,
      page_id: binding.pageId,
      target_generation: binding.targetGeneration,
      document_generation: binding.documentGeneration,
      navigation_generation: binding.navigationGeneration,
    };
    this.onEvent(event);
    return event;
  }

  reset(reason = "session_reset") {
    this.tabs.clear();
    this.sessions.clear();
    this.frames.clear();
    this.contexts.clear();
    this.onEvent({ event: "debugger.mappings_reset", reason });
  }

  publicBinding(binding) {
    return {
      space_id: binding.spaceId,
      page_id: binding.pageId,
      target_generation: binding.targetGeneration,
      document_generation: binding.documentGeneration,
      navigation_generation: binding.navigationGeneration,
      frame_topology_version: binding.frameTopologyVersion,
      session_hint: browserHint(
        [...binding.sessionIds][0] ?? "root",
        this.hintSalt,
      ),
    };
  }

  publicFrame(frame) {
    return {
      space_id: frame.spaceId,
      page_id: frame.pageId,
      frame_id: frame.logicalFrameId,
      document_generation: frame.documentGeneration,
      navigation_generation: frame.navigationGeneration,
      frame_version: frame.frameVersion,
    };
  }
}

export function createFramesRegistry(options) {
  return new FramesRegistry(options);
}
