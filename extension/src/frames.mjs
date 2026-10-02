import {
  ProtocolError,
  assertLogicalScope,
  browserHint,
  redactBrowserIdentifiers,
} from "./protocol.mjs";

function sessionKey(tabId, sessionId) {
  return `${tabId}:${sessionId ?? "root"}`;
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
  }

  bindTab({
    tabId,
    spaceId,
    pageId,
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
    const frame = {
      rawTabId: tabId,
      rawSessionId: sessionId,
      rawFrameId: frameId,
      spaceId: binding.spaceId,
      pageId: binding.pageId,
      logicalFrameId:
        typeof logicalFrameId === "string" ? logicalFrameId : undefined,
      documentGeneration: documentGeneration ?? binding.documentGeneration,
      navigationGeneration:
        navigationGeneration ?? binding.navigationGeneration,
      frameVersion: 1,
    };
    this.frames.set(`${sessionKey(tabId, sessionId)}:${frameId}`, frame);
    return this.publicFrame(frame);
  }

  bindExecutionContext({ tabId, sessionId, executionContextId, frameId } = {}) {
    if (!Number.isInteger(tabId) || !Number.isSafeInteger(executionContextId)) {
      throw new ProtocolError(
        "schema_invalid",
        "execution context binding is invalid",
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

  observeDebuggerEvent({ tabId, sessionId, method, params = {} } = {}) {
    const binding = this.sessions.get(sessionKey(tabId, sessionId));
    if (!binding) return;
    if (
      method === "Page.frameNavigated" &&
      typeof params.frame?.id === "string"
    ) {
      const frame = this.frames.get(
        `${sessionKey(tabId, sessionId)}:${params.frame.id}`,
      );
      if (frame) {
        frame.documentGeneration += 1;
        frame.navigationGeneration += 1;
        frame.frameVersion += 1;
      }
      binding.documentGeneration += 1;
      binding.navigationGeneration += 1;
    }
    if (method === "Page.frameAttached" && typeof params.frameId === "string") {
      try {
        this.bindFrame({ tabId, sessionId, frameId: params.frameId });
      } catch {
        // Attribution remains conservative until a logical frame binding exists.
      }
      binding.frameTopologyVersion += 1;
    }
    if (method === "Page.frameDetached" && typeof params.frameId === "string") {
      this.frames.delete(`${sessionKey(tabId, sessionId)}:${params.frameId}`);
      binding.frameTopologyVersion += 1;
    }
  }

  routeDebuggerEvent({ tabId, sessionId, method, params = {} } = {}) {
    const binding = this.sessions.get(sessionKey(tabId, sessionId));
    if (!binding || typeof method !== "string") return null;
    this.observeDebuggerEvent({ tabId, sessionId, method, params });
    const frameId =
      typeof params.frameId === "string" ? params.frameId : params.frame?.id;
    const frame = frameId
      ? this.frames.get(`${sessionKey(tabId, sessionId)}:${frameId}`)
      : undefined;
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
