import {
  ProtocolError,
  assertLogicalScope,
  browserHint,
  redactBrowserIdentifiers,
} from "./protocol.mjs";

function sessionKey(tabId, sessionId) {
  return `${tabId}:${sessionId ?? "root"}`;
}

function isRootSession(sessionId) {
  return sessionId === undefined || sessionId === null;
}

function frameKey(tabId, sessionId, frameId) {
  return `${sessionKey(tabId, sessionId)}:${frameId}`;
}

function contextKey(tabId, sessionId, executionContextId) {
  return `${sessionKey(tabId, sessionId)}:${executionContextId}`;
}

function hasHandle(value) {
  return typeof value === "string" && value.length > 0;
}

function isSafeGeneration(value) {
  return Number.isSafeInteger(value) && value > 0;
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
    this.sessionMetadata = new Map();
    this.sessionGenerationCounters = new Map();
    this.frameGenerationCounters = new Map();
    this.contextGenerationCounters = new Map();
    this.retiredLogicalFrameIds = new Set();
    this.nextLogicalFrameNumber = 1;
  }

  allocateLogicalFrameId() {
    let value;
    do {
      const number = this.nextLogicalFrameNumber++;
      value = `frame-${number.toString(36)}`;
    } while (
      this.retiredLogicalFrameIds.has(value) ||
      this.logicalFrameIdInUse(value)
    );
    return value;
  }

  setHintSalt(hintSalt) {
    if (typeof hintSalt === "string") this.hintSalt = hintSalt;
  }

  bindTab({
    tabId,
    spaceId,
    pageId,
    origin,
    targetGeneration,
    documentGeneration,
    navigationGeneration,
  } = {}) {
    if (!Number.isInteger(tabId))
      throw new ProtocolError(
        "schema_invalid",
        "frame binding requires an internal tab",
      );
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });

    const previous = this.tabs.get(tabId);
    const nextTargetGeneration = isSafeGeneration(targetGeneration)
      ? targetGeneration
      : previous
        ? previous.targetGeneration + 1
        : 1;
    const binding = {
      rawTabId: tabId,
      spaceId,
      pageId,
      origin: typeof origin === "string" ? origin : undefined,
      targetGeneration: nextTargetGeneration,
      documentGeneration: isSafeGeneration(documentGeneration)
        ? documentGeneration
        : previous
          ? previous.documentGeneration + 1
          : 1,
      navigationGeneration: isSafeGeneration(navigationGeneration)
        ? navigationGeneration
        : previous
          ? previous.navigationGeneration + 1
          : 1,
      frameTopologyVersion: previous ? previous.frameTopologyVersion + 1 : 1,
      sessionIds: new Set(),
    };

    if (previous) this.clearTab(tabId);
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
    targetGeneration,
    documentGeneration,
    navigationGeneration,
    targetId,
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
    if (targetId !== undefined && !hasHandle(targetId))
      throw new ProtocolError(
        "schema_invalid",
        "child session target handle is invalid",
      );
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    const key = sessionKey(tabId, sessionId);
    const tab = this.tabs.get(tabId);
    if (tab && (tab.spaceId !== spaceId || tab.pageId !== pageId)) {
      throw new ProtocolError(
        "ambiguous_binding",
        "debugger session scope conflicts with its tab",
      );
    }

    const current = this.sessions.get(key);
    const currentMetadata = this.sessionMetadata.get(key);
    if (
      current &&
      (!targetId ||
        !currentMetadata?.targetId ||
        currentMetadata.targetId === targetId)
    ) {
      if (
        targetId !== undefined &&
        currentMetadata &&
        currentMetadata.targetId === undefined
      )
        currentMetadata.targetId = targetId;
      return this.publicBinding(current);
    }

    if (current) this.retireSession(tabId, sessionId);

    const binding = tab ?? {
      rawTabId: tabId,
      spaceId,
      pageId,
      origin: typeof origin === "string" ? origin : undefined,
      targetGeneration: isSafeGeneration(targetGeneration)
        ? targetGeneration
        : 1,
      documentGeneration: isSafeGeneration(documentGeneration)
        ? documentGeneration
        : 1,
      navigationGeneration: isSafeGeneration(navigationGeneration)
        ? navigationGeneration
        : 1,
      frameTopologyVersion: 1,
      sessionIds: new Set(),
    };
    const sessionGeneration =
      (this.sessionGenerationCounters.get(key) ?? 0) + 1;
    this.sessionGenerationCounters.set(key, sessionGeneration);
    this.sessionMetadata.set(key, {
      sessionGeneration,
      targetId,
    });
    binding.sessionIds.add(sessionId);
    this.tabs.set(tabId, binding);
    this.sessions.set(key, binding);
    return this.publicBinding(binding);
  }

  bindFrame({
    tabId,
    sessionId,
    frameId,
    logicalFrameId,
    origin,
    parentFrameId,
    documentGeneration,
    navigationGeneration,
  } = {}) {
    if (!Number.isInteger(tabId) || !hasHandle(frameId)) {
      throw new ProtocolError(
        "schema_invalid",
        "frame binding requires internal frame handles",
      );
    }
    if (parentFrameId !== undefined && !hasHandle(parentFrameId))
      throw new ProtocolError(
        "schema_invalid",
        "frame parent handle is invalid",
      );
    const key = frameKey(tabId, sessionId, frameId);
    const binding = this.sessions.get(sessionKey(tabId, sessionId));
    if (!binding)
      throw new ProtocolError(
        "page_not_found",
        "frame has no logical page binding",
      );
    const sessionGeneration = this.sessionMetadata.get(
      sessionKey(tabId, sessionId),
    )?.sessionGeneration;
    const previous = this.frames.get(key);
    if (previous) {
      if (
        previous.sessionGeneration !== undefined &&
        previous.sessionGeneration !== sessionGeneration
      )
        throw new ProtocolError(
          "stale_generation",
          "frame binding belongs to an older debugger session",
        );
      if (typeof origin === "string") previous.origin = origin;
      if (parentFrameId !== undefined)
        previous.rawParentFrameId = parentFrameId;
      if (isSafeGeneration(documentGeneration))
        previous.documentGeneration = documentGeneration;
      if (isSafeGeneration(navigationGeneration))
        previous.navigationGeneration = navigationGeneration;
      return this.publicFrame(previous);
    }

    const frameGeneration = (this.frameGenerationCounters.get(key) ?? 0) + 1;
    this.frameGenerationCounters.set(key, frameGeneration);
    const requestedLogicalFrameId = hasHandle(logicalFrameId)
      ? logicalFrameId
      : undefined;
    const nextLogicalFrameId =
      requestedLogicalFrameId &&
      !this.retiredLogicalFrameIds.has(requestedLogicalFrameId) &&
      !this.logicalFrameIdInUse(requestedLogicalFrameId)
        ? requestedLogicalFrameId
        : this.allocateLogicalFrameId();
    const frame = {
      rawTabId: tabId,
      rawSessionId: sessionId,
      rawFrameId: frameId,
      rawParentFrameId: parentFrameId,
      sessionGeneration,
      spaceId: binding.spaceId,
      pageId: binding.pageId,
      origin: typeof origin === "string" ? origin : binding.origin,
      logicalFrameId: nextLogicalFrameId,
      documentGeneration: isSafeGeneration(documentGeneration)
        ? documentGeneration
        : binding.documentGeneration,
      navigationGeneration: isSafeGeneration(navigationGeneration)
        ? navigationGeneration
        : binding.navigationGeneration,
      frameVersion: frameGeneration,
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
    if (frameId !== undefined && !hasHandle(frameId))
      throw new ProtocolError(
        "schema_invalid",
        "execution context frame handle is invalid",
      );
    const sessionKeyValue = sessionKey(tabId, sessionId);
    const session = this.sessions.get(sessionKeyValue);
    if (!session)
      throw new ProtocolError(
        "page_not_found",
        "execution context has no logical page binding",
      );
    if (frameId !== undefined && !this.frameFor(tabId, sessionId, frameId))
      throw new ProtocolError(
        "stale_generation",
        "execution context frame binding is not current",
      );
    const key = contextKey(tabId, sessionId, executionContextId);
    const contextGeneration =
      (this.contextGenerationCounters.get(key) ?? 0) + 1;
    this.contextGenerationCounters.set(key, contextGeneration);
    const sessionGeneration =
      this.sessionMetadata.get(sessionKeyValue)?.sessionGeneration;
    this.contexts.set(key, {
      rawTabId: tabId,
      rawSessionId: sessionId,
      rawExecutionContextId: executionContextId,
      rawFrameId: frameId,
      sessionGeneration,
      contextGeneration,
      spaceId: session.spaceId,
      pageId: session.pageId,
      documentGeneration: session.documentGeneration,
    });
  }

  logicalFrameIdInUse(logicalFrameId) {
    for (const frame of this.frames.values()) {
      if (frame.logicalFrameId === logicalFrameId) return true;
    }
    return false;
  }

  frameFor(tabId, sessionId, frameId) {
    if (!hasHandle(frameId)) return undefined;
    const frame = this.frames.get(frameKey(tabId, sessionId, frameId));
    if (!frame) return undefined;
    const sessionGeneration = this.sessionMetadata.get(
      sessionKey(tabId, sessionId),
    )?.sessionGeneration;
    if (
      frame.sessionGeneration !== undefined &&
      frame.sessionGeneration !== sessionGeneration
    )
      return undefined;
    return frame;
  }

  contextFor(tabId, sessionId, executionContextId) {
    if (!Number.isSafeInteger(executionContextId)) return undefined;
    const key = contextKey(tabId, sessionId, executionContextId);
    const context = this.contexts.get(key);
    if (!context) return undefined;
    const sessionGeneration = this.sessionMetadata.get(
      sessionKey(tabId, sessionId),
    )?.sessionGeneration;
    if (
      context.sessionGeneration !== undefined &&
      context.sessionGeneration !== sessionGeneration
    )
      return undefined;
    if (
      context.rawFrameId !== undefined &&
      !this.frameFor(tabId, sessionId, context.rawFrameId)
    )
      return undefined;
    return context;
  }

  rawExecutionContextIdForEvent(method, params = {}) {
    if (Number.isSafeInteger(params.executionContextId))
      return params.executionContextId;
    if (Number.isSafeInteger(params.exceptionDetails?.executionContextId))
      return params.exceptionDetails.executionContextId;
    return undefined;
  }

  rawFrameIdForEvent(tabId, sessionId, method, params = {}) {
    if (hasHandle(params.frameId)) return params.frameId;
    if (hasHandle(params.frame?.id)) return params.frame.id;
    if (
      method === "Runtime.executionContextCreated" &&
      hasHandle(params.context?.auxData?.frameId)
    )
      return params.context.auxData.frameId;
    const executionContextId = this.rawExecutionContextIdForEvent(
      method,
      params,
    );
    if (executionContextId !== undefined)
      return this.contextFor(tabId, sessionId, executionContextId)?.rawFrameId;
    return undefined;
  }

  retireFrameByKey(key) {
    const frame = this.frames.get(key);
    if (!frame) return false;
    this.frameGenerationCounters.set(
      key,
      Math.max(this.frameGenerationCounters.get(key) ?? 0, frame.frameVersion),
    );
    if (hasHandle(frame.logicalFrameId))
      this.retiredLogicalFrameIds.add(frame.logicalFrameId);
    this.frames.delete(key);
    for (const [contextKeyValue, context] of this.contexts) {
      if (
        context.rawTabId === frame.rawTabId &&
        context.rawSessionId === frame.rawSessionId &&
        context.rawFrameId === frame.rawFrameId
      ) {
        this.contextGenerationCounters.set(
          contextKeyValue,
          Math.max(
            this.contextGenerationCounters.get(contextKeyValue) ?? 0,
            context.contextGeneration ?? 0,
          ),
        );
        this.contexts.delete(contextKeyValue);
      }
    }
    for (const [childKey, child] of [...this.frames]) {
      if (
        child.rawTabId === frame.rawTabId &&
        child.rawSessionId === frame.rawSessionId &&
        child.rawParentFrameId === frame.rawFrameId
      )
        this.retireFrameByKey(childKey);
    }
    return true;
  }

  retireSession(tabId, sessionId, options = {}) {
    const key = sessionKey(tabId, sessionId);
    const binding = this.sessions.get(key);
    if (!binding) return false;
    const metadata = this.sessionMetadata.get(key);
    if (
      options.expectedSessionGeneration !== undefined &&
      metadata?.sessionGeneration !== options.expectedSessionGeneration
    )
      return false;
    if (
      options.targetId !== undefined &&
      metadata?.targetId !== undefined &&
      metadata.targetId !== options.targetId
    )
      return false;

    this.sessions.delete(key);
    this.sessionMetadata.delete(key);
    binding.sessionIds.delete(sessionId);
    for (const frameKeyValue of [...this.frames.keys()]) {
      if (frameKeyValue.startsWith(`${key}:`))
        this.retireFrameByKey(frameKeyValue);
    }
    for (const [contextKeyValue, context] of this.contexts) {
      if (contextKeyValue.startsWith(`${key}:`)) {
        this.contextGenerationCounters.set(
          contextKeyValue,
          Math.max(
            this.contextGenerationCounters.get(contextKeyValue) ?? 0,
            context.contextGeneration ?? 0,
          ),
        );
        this.contexts.delete(contextKeyValue);
      }
    }
    return true;
  }

  clearTab(tabId) {
    for (const key of [...this.sessions.keys()]) {
      if (key.startsWith(`${tabId}:`) && key !== sessionKey(tabId)) {
        const sessionId = key.slice(`${tabId}:`.length);
        this.retireSession(tabId, sessionId);
      }
    }
    for (const [key, frame] of [...this.frames]) {
      if (frame.rawTabId === tabId) this.retireFrameByKey(key);
    }
    for (const [key, context] of [...this.contexts]) {
      if (context.rawTabId === tabId) {
        this.contextGenerationCounters.set(
          key,
          Math.max(
            this.contextGenerationCounters.get(key) ?? 0,
            context.contextGeneration ?? 0,
          ),
        );
        this.contexts.delete(key);
      }
    }
    for (const key of [...this.sessions.keys()]) {
      if (key.startsWith(`${tabId}:`)) this.sessions.delete(key);
    }
    for (const key of [...this.sessionMetadata.keys()]) {
      if (key.startsWith(`${tabId}:`)) this.sessionMetadata.delete(key);
    }
    this.tabs.delete(tabId);
  }

  observeDebuggerEvent({ tabId, sessionId, method, params = {} } = {}) {
    if (!isAllowedDebuggerEvent(method)) return false;
    const binding = this.sessions.get(sessionKey(tabId, sessionId));
    if (!binding) return false;

    if (method === "Page.frameNavigated" && hasHandle(params.frame?.id)) {
      const frameId = params.frame.id;
      const parentFrameId = hasHandle(params.frame.parentId)
        ? params.frame.parentId
        : undefined;
      const isRootNavigation =
        isRootSession(sessionId) && parentFrameId === undefined;
      const frame = this.frameFor(tabId, sessionId, frameId);
      if (frame) {
        if (
          frame.rawParentFrameId !== undefined &&
          parentFrameId !== frame.rawParentFrameId
        )
          return false;
        if (frame.rawParentFrameId === undefined && parentFrameId !== undefined)
          frame.rawParentFrameId = parentFrameId;
        frame.documentGeneration += 1;
        frame.navigationGeneration += 1;
        frame.frameVersion += 1;
        this.frameGenerationCounters.set(
          frameKey(tabId, sessionId, frameId),
          frame.frameVersion,
        );
        if (typeof params.frame.url === "string")
          frame.origin = params.frame.url;
        if (isRootNavigation) {
          binding.documentGeneration += 1;
          binding.navigationGeneration += 1;
        }
      } else if (!isRootNavigation) {
        // A navigation event cannot create a frame. It may be a late event for
        // a detached frame, so keep the attribution as a hard routing miss.
        return false;
      } else {
        binding.documentGeneration += 1;
        binding.navigationGeneration += 1;
      }
    }

    if (method === "Page.frameAttached" && hasHandle(params.frameId)) {
      const frame = this.frameFor(tabId, sessionId, params.frameId);
      const parentFrameId = hasHandle(params.parentFrameId)
        ? params.parentFrameId
        : undefined;
      if (
        frame &&
        frame.rawParentFrameId !== undefined &&
        parentFrameId !== frame.rawParentFrameId
      )
        return false;
      try {
        this.bindFrame({
          tabId,
          sessionId,
          frameId: params.frameId,
          origin: params.url,
          parentFrameId,
        });
      } catch {
        return false;
      }
      binding.frameTopologyVersion += 1;
    }

    if (method === "Page.frameDetached" && hasHandle(params.frameId)) {
      const key = frameKey(tabId, sessionId, params.frameId);
      if (!this.frameFor(tabId, sessionId, params.frameId)) return false;
      this.retireFrameByKey(key);
      binding.frameTopologyVersion += 1;
    }

    if (method === "Runtime.executionContextCreated") {
      const executionContextId = params.context?.id;
      const frameId = hasHandle(params.context?.auxData?.frameId)
        ? params.context.auxData.frameId
        : undefined;
      if (!Number.isSafeInteger(executionContextId)) return false;
      if (frameId !== undefined && !this.frameFor(tabId, sessionId, frameId))
        return false;
      try {
        this.bindExecutionContext({
          tabId,
          sessionId,
          executionContextId,
          frameId,
        });
      } catch {
        return false;
      }
    }

    if (method === "Runtime.executionContextDestroyed") {
      const executionContextId = this.rawExecutionContextIdForEvent(
        method,
        params,
      );
      if (executionContextId === undefined) return false;
      const context = this.contextFor(tabId, sessionId, executionContextId);
      if (!context) return false;
      this.contextGenerationCounters.set(
        contextKey(tabId, sessionId, executionContextId),
        Math.max(
          this.contextGenerationCounters.get(
            contextKey(tabId, sessionId, executionContextId),
          ) ?? 0,
          context.contextGeneration ?? 0,
        ),
      );
      this.contexts.delete(contextKey(tabId, sessionId, executionContextId));
    }

    if (method === "Runtime.executionContextsCleared") {
      const prefix = `${sessionKey(tabId, sessionId)}:`;
      for (const [key, context] of [...this.contexts]) {
        if (!key.startsWith(prefix)) continue;
        this.contextGenerationCounters.set(
          key,
          Math.max(
            this.contextGenerationCounters.get(key) ?? 0,
            context.contextGeneration ?? 0,
          ),
        );
        this.contexts.delete(key);
      }
    }
    return true;
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
    const executionContextId = this.rawExecutionContextIdForEvent(
      method,
      params,
    );
    const beforeContext = this.contextFor(tabId, sessionId, executionContextId);
    if (!this.observeDebuggerEvent({ tabId, sessionId, method, params }))
      return null;

    const frameId =
      this.rawFrameIdForEvent(tabId, sessionId, method, params) ??
      beforeFrameId;
    const isFrameDetached = method === "Page.frameDetached";
    const isContextDestroyed = method === "Runtime.executionContextDestroyed";
    const frame =
      this.frameFor(tabId, sessionId, frameId) ??
      (isFrameDetached || isContextDestroyed ? beforeFrame : undefined);
    const hasExplicitFrame =
      hasHandle(params.frameId) ||
      hasHandle(params.frame?.id) ||
      hasHandle(params.context?.auxData?.frameId);
    const isRootNavigation =
      method === "Page.frameNavigated" &&
      isRootSession(sessionId) &&
      !hasHandle(params.frame?.parentId);
    if (hasExplicitFrame && !frame && !isRootNavigation) return null;
    if (
      executionContextId !== undefined &&
      !beforeContext &&
      !this.contextFor(tabId, sessionId, executionContextId) &&
      method !== "Runtime.executionContextCreated"
    )
      return null;

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

  invalidateSession(tabId, sessionId, reason = "session_lost", options = {}) {
    const key = sessionKey(tabId, sessionId);
    const binding = this.sessions.get(key);
    if (!binding) return null;
    const metadata = this.sessionMetadata.get(key);
    if (
      options.expectedSessionGeneration !== undefined &&
      metadata?.sessionGeneration !== options.expectedSessionGeneration
    )
      return null;
    if (
      options.targetId !== undefined &&
      metadata?.targetId !== undefined &&
      metadata.targetId !== options.targetId
    )
      return null;
    if (!this.retireSession(tabId, sessionId, options)) return null;
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

  getSessionGeneration(tabId, sessionId) {
    return this.sessionMetadata.get(sessionKey(tabId, sessionId))
      ?.sessionGeneration;
  }

  isCurrentSession(tabId, sessionId, { sessionGeneration, targetId } = {}) {
    const key = sessionKey(tabId, sessionId);
    const current = this.sessions.get(key);
    const metadata = this.sessionMetadata.get(key);
    if (!current || !metadata) return false;
    if (
      sessionGeneration !== undefined &&
      metadata.sessionGeneration !== sessionGeneration
    )
      return false;
    if (
      targetId !== undefined &&
      metadata.targetId !== undefined &&
      metadata.targetId !== targetId
    )
      return false;
    return true;
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
      if (key.startsWith(`${tabId}:`) && key !== sessionKey(tabId)) {
        const sessionId = key.slice(`${tabId}:`.length);
        this.retireSession(tabId, sessionId);
      }
    }
    for (const [key, frame] of [...this.frames]) {
      if (frame.rawTabId === tabId) this.retireFrameByKey(key);
    }
    for (const [key, context] of [...this.contexts]) {
      if (context.rawTabId !== tabId) continue;
      this.contextGenerationCounters.set(
        key,
        Math.max(
          this.contextGenerationCounters.get(key) ?? 0,
          context.contextGeneration ?? 0,
        ),
      );
      this.contexts.delete(key);
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
    for (const tabId of [...this.tabs.keys()]) this.clearTab(tabId);
    this.tabs.clear();
    this.sessions.clear();
    this.sessionMetadata.clear();
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
