import {
  ProtocolError,
  assertLogicalScope,
  assertNoRawBrowserIdentifiers,
  createLogicalId,
  errorResult,
  isFenceMethod,
  isMutationMethod,
  publicError,
} from "./protocol.mjs";
import { NativeMessagingClient } from "./native-messaging.mjs";
import { DebuggerBridge, debuggerOperationError } from "./debugger-bridge.mjs";
import { TabsRegistry, unknownDispatch } from "./tabs-registry.mjs";
import { GroupsRegistry } from "./groups.mjs";
import { FramesRegistry } from "./frames.mjs";
import { PAGE_OPERATIONS } from "./page-bridge.mjs";

const METADATA_KEY = "agentyc_extension_metadata";
const FENCE_KEY = "agentyc_space_fences";
const VERSION = (() => {
  try {
    const version = globalThis.chrome?.runtime?.getManifest?.().version;
    return typeof version === "string" && version.length > 0
      ? version
      : "0.1.0";
  } catch {
    return "0.1.0";
  }
})();
const CONTENT_DOCUMENT_TTL_MS = 60 * 1000;
const CONTENT_REQUEST_TTL_MS = 30 * 1000;
const MAX_SIDE_PANEL_TICKET_LIFETIME_MS = 15 * 60 * 1000;
const MAX_MUTATION_QUEUES = 256;
const MAX_CONTENT_PENDING = 256;
const MAX_SIDE_PANEL_TICKETS = 1024;
const MAX_PERSISTED_FENCES = 1024;
const MAX_INVENTORY_PAGES = 200;
const MAX_INVENTORY_GROUPS = 64;
const MAX_INVENTORY_BYTES = 512 * 1024;
const MAX_UNREPORTED_UNKNOWN_ACTIONS = 128;
const DESTRUCTIVE_SIDE_PANEL_ACTIONS = new Set([
  "stop",
  "takeover",
  "return_control",
  "handoff",
  "finish",
  "release",
]);

function chromeApiOrGlobal(chromeApi) {
  return chromeApi ?? globalThis.chrome;
}

async function storageGet(area, key) {
  if (!area?.get) return {};
  const result = area.get(key);
  return result?.then ? result : {};
}

async function storageSet(area, value) {
  if (!area?.set) return;
  const result = area.set(value);
  if (result?.then) await result;
}

function positiveEpoch(value, field) {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new ProtocolError(
      "schema_invalid",
      `${field} must be a positive integer`,
    );
  }
  return value;
}

function normalizedParams(message) {
  const params =
    message.params && typeof message.params === "object" ? message.params : {};
  assertNoRawBrowserIdentifiers(params);
  return params;
}

function valueOf(message, params, snake, camel = undefined) {
  return (
    message[snake] ??
    (camel ? message[camel] : undefined) ??
    params[snake] ??
    (camel ? params[camel] : undefined)
  );
}

/**
 * MV3 service-worker adapter. It owns only live routing and browser handles;
 * no lease, action journal, logical identity, or cleanup decision is persisted.
 */
export class ServiceWorkerController {
  constructor({
    chromeApi,
    nativeClient,
    storageArea,
    hostName,
    profileInstanceId,
    workerInstanceEpoch,
    browserSessionEpoch,
    autoReconnect = true,
    now = () => Date.now(),
  } = {}) {
    this.chrome = chromeApiOrGlobal(chromeApi);
    this.storage = storageArea ?? this.chrome?.storage?.local;
    this.hostName = hostName;
    this.now = now;
    this.metadata = {
      profileInstanceId,
      workerInstanceEpoch,
      browserSessionEpoch,
    };
    this.started = false;
    this.startPromise = null;
    this.runtimeListenersInstalled = false;
    this.pending = new Map();
    this.inflight = new Map();
    this.fences = new Map();
    this.mutationTails = new Map();
    this.contentPending = new Map();
    this.contentDocuments = new Map();
    this.usedSidePanelTickets = new Map();
    this.sessionAdvancePromise = null;
    this.sidePanelListeners = [];
    this.unreportedUnknownActions = new Set();
    this.unknownActionsOverflow = false;

    const hintSalt = `worker:${workerInstanceEpoch ?? 0}`;
    this.groups = new GroupsRegistry({
      chromeApi: this.chrome,
      hintSalt,
      onEvent: (event, payload) => this.handleExtensionEvent(event, payload),
    });
    this.tabs = new TabsRegistry({
      chromeApi: this.chrome,
      groups: this.groups,
      hintSalt,
      now,
      profileInstanceId,
      browserSessionEpoch,
      assertFence: ({ spaceId, leaseEpoch }) =>
        this.assertFence(spaceId, leaseEpoch),
      onLifecycle: (kind, tabId, record) =>
        this.handleTabLifecycle(kind, tabId, record),
      onEvent: (event, payload) => this.handleExtensionEvent(event, payload),
    });
    this.frames = new FramesRegistry({
      hintSalt,
      onEvent: (payload) => this.handleDebuggerEvent(payload),
      onRoute: () => {},
    });
    this.debugger = new DebuggerBridge({
      chromeApi: this.chrome,
      tabs: this.tabs,
      frames: this.frames,
      onEvent: (payload) => this.handleDebuggerEvent(payload),
      onStateChange: (state, payload) =>
        this.handleExtensionEvent(`debugger.${state}`, payload),
      now,
      profileInstanceId,
      browserSessionEpoch,
    });
    this.native =
      nativeClient ??
      new NativeMessagingClient({
        chromeApi: this.chrome,
        hostName,
        profileInstanceId,
        workerInstanceEpoch,
        browserSessionEpoch,
        extensionVersion: VERSION,
        autoReconnect,
        onMessage: (message) => void this.handleHostEnvelope(message),
        onStateChange: (state, detail) => this.handleNativeState(state, detail),
        onUnknownActions: (actionIds, reason) =>
          this.handleLostDispatch(actionIds, reason),
      });
  }

  async start() {
    if (this.started) return this;
    if (this.startPromise) return this.startPromise;
    this.installRuntimeListener();
    this.startPromise = (async () => {
      await this.loadMetadata();
      this.loadFences();
      this.tabs.setIdentity(this.metadata);
      this.debugger.setIdentity(this.metadata);
      this.started = true;
      this.groups.start();
      await this.tabs.start();
      this.debugger.start();
      await this.configureSidePanel();
      try {
        await this.native.connect();
      } catch {
        // Reconnect is handled by NativeMessagingClient; pages remain retained.
      }
      return this;
    })().finally(() => {
      this.startPromise = null;
    });
    return this.startPromise;
  }

  async configureSidePanel() {
    const setPanelBehavior = this.chrome?.sidePanel?.setPanelBehavior;
    if (typeof setPanelBehavior !== "function") return;
    try {
      await setPanelBehavior.call(this.chrome.sidePanel, {
        openPanelOnActionClick: true,
      });
    } catch (error) {
      this.handleExtensionEvent("side_panel.unavailable", {
        code: "capability_unavailable",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async loadMetadata() {
    const storedValues = await storageGet(this.storage, [
      METADATA_KEY,
      FENCE_KEY,
    ]);
    this.storedFences = storedValues[FENCE_KEY];
    const stored = storedValues[METADATA_KEY] ?? {};
    const profileInstanceId =
      this.metadata.profileInstanceId ??
      (typeof stored.profile_instance_id === "string"
        ? stored.profile_instance_id
        : createLogicalId("profile"));
    const previousWorker = Number.isSafeInteger(stored.worker_instance_epoch)
      ? stored.worker_instance_epoch
      : 0;
    const workerInstanceEpoch =
      this.metadata.workerInstanceEpoch ?? Math.max(1, previousWorker + 1);
    const previousBrowserSession =
      Number.isSafeInteger(stored.browser_session_epoch) &&
      stored.browser_session_epoch >= 1
        ? stored.browser_session_epoch
        : 1;
    const browserSessionEpoch =
      Number.isSafeInteger(this.metadata.browserSessionEpoch) &&
      this.metadata.browserSessionEpoch >= 1
        ? this.metadata.browserSessionEpoch
        : previousBrowserSession;
    this.metadata = {
      profileInstanceId,
      workerInstanceEpoch,
      browserSessionEpoch,
    };
    if (this.native) {
      this.native.profileInstanceId = profileInstanceId;
      this.native.workerInstanceEpoch = workerInstanceEpoch;
      this.native.browserSessionEpoch = browserSessionEpoch;
    }
    await storageSet(this.storage, {
      [METADATA_KEY]: {
        profile_instance_id: profileInstanceId,
        worker_instance_epoch: workerInstanceEpoch,
        browser_session_epoch: browserSessionEpoch,
        ui_version: VERSION,
      },
    });
  }

  /**
   * Fence floors survive a worker restart inside the same browser session so a
   * stale lease cannot bind or dispatch after Chrome terminates the worker.
   * Floors only ever restrict; they never grant ownership or leases.
   */
  loadFences() {
    const stored = this.storedFences;
    this.storedFences = undefined;
    if (
      !stored ||
      stored.profile_instance_id !== this.metadata.profileInstanceId ||
      stored.browser_session_epoch !== this.metadata.browserSessionEpoch ||
      !Array.isArray(stored.fences)
    )
      return;
    for (const entry of stored.fences.slice(0, MAX_PERSISTED_FENCES)) {
      if (!Array.isArray(entry)) continue;
      const [spaceId, epoch] = entry;
      try {
        assertLogicalScope({ spaceId });
        positiveEpoch(epoch, "fence_epoch");
      } catch {
        continue;
      }
      this.fences.set(spaceId, Math.max(this.fences.get(spaceId) ?? 0, epoch));
      this.tabs.restoreFence(spaceId, epoch);
    }
  }

  async persistFences() {
    await storageSet(this.storage, {
      [FENCE_KEY]: {
        profile_instance_id: this.metadata.profileInstanceId,
        browser_session_epoch: this.metadata.browserSessionEpoch,
        fences: [...this.fences.entries()].slice(0, MAX_PERSISTED_FENCES),
      },
    });
  }

  installRuntimeListener() {
    if (this.runtimeListenersInstalled) return;
    this.runtimeListenersInstalled = true;
    const event = this.chrome?.runtime?.onMessage;
    if (event?.addListener) {
      const listener = (message, sender, sendResponse) => {
        const respond = (value) => {
          try {
            sendResponse?.(value);
          } catch {
            // Message channels are best effort.
          }
        };
        void Promise.resolve()
          .then(async () => {
            if (!this.started && this.startPromise) {
              await this.startPromise.catch(() => {});
            }
            return this.handleRuntimeMessage(message, sender);
          })
          .then((response) => respond(response))
          .catch((error) => respond({ ok: false, error: publicError(error) }));
        return true;
      };
      event.addListener(listener);
      this.sidePanelListeners.push(() => event.removeListener?.(listener));
    }
    const startup = this.chrome?.runtime?.onStartup;
    if (startup?.addListener) {
      const listener = () => {
        void Promise.resolve()
          .then(() =>
            this.started ? undefined : (this.startPromise ?? this.start()),
          )
          .then(() => this.advanceBrowserSession("browser_startup"))
          .catch(() => {});
      };
      startup.addListener(listener);
      this.sidePanelListeners.push(() => startup.removeListener?.(listener));
    }
  }

  stop() {
    for (const remove of this.sidePanelListeners.splice(0)) remove();
    this.runtimeListenersInstalled = false;
    this.startPromise = null;
    this.debugger.stop();
    this.tabs.stop();
    this.groups.stop();
    this.native.stop();
    this.pending.clear();
    this.inflight.clear();
    this.mutationTails.clear();
    this.contentPending.clear();
    this.contentDocuments.clear();
    this.usedSidePanelTickets.clear();
    this.started = false;
  }

  handleTabLifecycle(kind, tabId, record) {
    if (kind === "document_changed") {
      if (Number.isInteger(tabId))
        this.debugger?.handleDocumentChange(tabId, record);
    } else if (Number.isInteger(tabId)) {
      this.debugger?.invalidateTab(tabId, kind);
    }
    if (Number.isInteger(tabId)) {
      this.contentDocuments.delete(tabId);
      for (const [requestId, pending] of this.contentPending) {
        if (pending.rawTabId === tabId) this.contentPending.delete(requestId);
      }
    }
  }

  async advanceBrowserSession(reason = "extension_lifecycle") {
    if (this.sessionAdvancePromise) return this.sessionAdvancePromise;
    this.sessionAdvancePromise = (async () => {
      const nextEpoch =
        (Number.isSafeInteger(this.metadata.browserSessionEpoch)
          ? this.metadata.browserSessionEpoch
          : 0) + 1;
      const unknownActions = [...this.inflight.keys()];
      this.inflight.clear();
      for (const actionId of unknownActions) {
        this.reportUnknownAction(actionId, "browser session changed");
      }
      this.fences.clear();
      this.mutationTails.clear();
      this.contentPending.clear();
      this.contentDocuments.clear();
      this.usedSidePanelTickets.clear();
      this.debugger.resetSession(nextEpoch);
      this.tabs.resetSession(nextEpoch);
      this.metadata.browserSessionEpoch = nextEpoch;
      this.native.browserSessionEpoch = nextEpoch;
      await storageSet(this.storage, {
        [METADATA_KEY]: {
          profile_instance_id: this.metadata.profileInstanceId,
          worker_instance_epoch: this.metadata.workerInstanceEpoch,
          browser_session_epoch: nextEpoch,
          ui_version: VERSION,
        },
      });
      await this.persistFences().catch(() => {});
      this.handleExtensionEvent("browser.session_changed", {
        browser_session_epoch: nextEpoch,
        reason,
      });
      if (this.started && this.native?.stop && this.native?.connect) {
        this.native.stop();
        this.native.stopped = false;
        await this.native.connect().catch(() => {});
      }
      await this.tabs.refreshSession();
      return nextEpoch;
    })().finally(() => {
      this.sessionAdvancePromise = null;
    });
    return this.sessionAdvancePromise;
  }

  enqueueMutation(spaceId, operation) {
    if (
      !this.mutationTails.has(spaceId) &&
      this.mutationTails.size >= MAX_MUTATION_QUEUES
    )
      return Promise.reject(
        new ProtocolError("resource_exhausted", "mutation queue bound reached"),
      );
    const previous = this.mutationTails.get(spaceId) ?? Promise.resolve();
    const current = previous.catch(() => {}).then(operation);
    this.mutationTails.set(spaceId, current);
    void current
      .finally(() => {
        if (this.mutationTails.get(spaceId) === current)
          this.mutationTails.delete(spaceId);
      })
      .catch(() => {});
    return current;
  }

  markActionDispatched(actionId) {
    if (!actionId) return;
    const pending = this.inflight.get(actionId);
    if (pending) pending.dispatched = true;
  }

  handleNativeState(state, detail) {
    this.handleExtensionEvent(`native.${state}`, {
      state,
      ...(detail?.code ? { code: detail.code } : {}),
    });
  }

  handleLostDispatch(actionIds, reason) {
    for (const actionId of actionIds) {
      const pending = this.inflight.get(actionId);
      this.inflight.delete(actionId);
      this.reportUnknownAction(
        actionId,
        reason instanceof Error ? reason.message : String(reason),
      );
      if (pending?.requestId) this.pending.delete(pending.requestId);
    }
  }

  /**
   * Unknown outcomes are never replayed. When the host cannot hear the event
   * (the transport is the thing that was lost), the id is held, bounded, and
   * reported in the next inventory so the host can reconcile it.
   */
  reportUnknownAction(actionId, reason) {
    if (!this.native.connected) {
      if (this.unreportedUnknownActions.size < MAX_UNREPORTED_UNKNOWN_ACTIONS)
        this.unreportedUnknownActions.add(actionId);
      else this.unknownActionsOverflow = true;
    }
    this.handleExtensionEvent("action.unknown", {
      action_id: actionId,
      outcome: "unknown",
      code: "unknown_outcome",
      reason,
    });
  }

  /**
   * Inventory must stay inside the control-envelope bounds: an oversized
   * inventory would otherwise be rejected locally and tear down the transport
   * on every reconnect. Managed pages are listed first, then other bound or
   * active tabs, so omissions only ever hide ordinary unmanaged user tabs.
   */
  boundedInventory() {
    const all = this.tabs.inventory();
    const rank = (page) =>
      page.ownership !== "unmanaged" ? 0 : page.active === true ? 1 : 2;
    const ordered = all
      .map((page, index) => ({ page, index }))
      .sort((a, b) => rank(a.page) - rank(b.page) || a.index - b.index)
      .map(({ page }) => page);
    const encoder = new TextEncoder();
    const pages = [];
    let bytes = 0;
    for (const page of ordered) {
      if (pages.length >= MAX_INVENTORY_PAGES) break;
      const size = encoder.encode(JSON.stringify(page)).byteLength + 1;
      if (bytes + size > MAX_INVENTORY_BYTES) break;
      pages.push(page);
      bytes += size;
    }
    const groups = this.groups.listHints().slice(0, MAX_INVENTORY_GROUPS);
    return {
      pages,
      groups,
      total_page_count: all.length,
      omitted_page_count: all.length - pages.length,
      truncated: pages.length < all.length,
    };
  }

  async sendInventory() {
    if (!this.native.connected) return;
    const unknownActionIds = [...this.unreportedUnknownActions];
    const overflow = this.unknownActionsOverflow;
    try {
      this.native.send("inventory", {
        payload: {
          profile_instance_id: this.metadata.profileInstanceId,
          ...this.boundedInventory(),
          unknown_action_ids: unknownActionIds,
          unknown_actions_overflow: overflow,
        },
      });
      for (const actionId of unknownActionIds)
        this.unreportedUnknownActions.delete(actionId);
      if (overflow) this.unknownActionsOverflow = false;
    } catch {
      // A disconnect between state notification and post is handled as a loss.
    }
  }

  async handleHostEnvelope(message) {
    try {
      if (message.kind === "hello_ok") {
        await this.sendInventory();
        return { ok: true };
      }
      if (message.kind === "event") {
        this.forwardHostEvent(message);
        return { ok: true };
      }
      if (message.kind === "request" || message.kind === "fence") {
        return await this.handleHostRequest(message);
      }
      if (message.kind === "response" || message.kind === "action_result") {
        this.native.markActionComplete(message.action_id);
        this.forwardHostEvent({
          event: "host.response",
          payload: message,
        });
        return { ok: true };
      }
      throw new ProtocolError(
        "schema_invalid",
        "host envelope kind is not executable",
      );
    } catch (error) {
      const safeError = publicError(error);
      this.handleExtensionEvent("host.message_rejected", {
        code: safeError.code,
      });
      if (
        (message?.kind === "request" || message?.kind === "fence") &&
        typeof message.request_id === "string"
      ) {
        this.reply({
          requestId: message.request_id,
          actionId: message.action_id,
          ok: false,
          error: safeError,
          mutation:
            isMutationMethod(message.method) ||
            isFenceMethod(
              message.method ??
                (message.kind === "fence" ? "fence" : undefined),
            ),
        });
      }
      return { ok: false, error: safeError };
    }
  }

  async handleHostRequest(message) {
    const params = normalizedParams(message);
    const method =
      message.method ?? (message.kind === "fence" ? "fence" : undefined);
    if (typeof method !== "string" || method.length === 0)
      throw new ProtocolError(
        "schema_invalid",
        "host request method is required",
      );
    const requestId = message.request_id ?? createLogicalId("req");
    const actionId = message.action_id;
    const mutation = isMutationMethod(method) || isFenceMethod(method);
    const spaceId = valueOf(message, params, "space_id", "spaceId");
    const pageId = valueOf(message, params, "page_id", "pageId");
    const leaseEpoch = valueOf(message, params, "lease_epoch", "leaseEpoch");
    if (mutation) {
      assertLogicalScope({ spaceId });
      return this.enqueueMutation(spaceId, () =>
        this.handleHostRequestSerialized({
          message,
          params,
          method,
          requestId,
          actionId,
          mutation: isMutationMethod(method),
          spaceId,
          pageId,
          leaseEpoch,
        }),
      );
    }
    return this.handleHostRequestSerialized({
      message,
      params,
      method,
      requestId,
      actionId,
      mutation: false,
      spaceId,
      pageId,
      leaseEpoch,
    });
  }

  async handleHostRequestSerialized({
    message,
    params,
    method,
    requestId,
    actionId,
    mutation,
    spaceId,
    pageId,
    leaseEpoch,
  }) {
    if (isFenceMethod(method)) {
      return this.handleFence({
        message,
        params,
        requestId,
        actionId,
        spaceId,
        leaseEpoch,
      });
    }
    if (mutation) {
      assertLogicalScope(
        { spaceId, pageId },
        {
          pageRequired:
            method !== "space.pause" &&
            method !== "space.finish" &&
            method !== "space.release",
        },
      );
      positiveEpoch(leaseEpoch, "lease_epoch");
      this.assertFence(spaceId, leaseEpoch);
    }

    if (actionId)
      this.inflight.set(actionId, {
        requestId,
        spaceId,
        method,
        dispatched: false,
      });
    try {
      const result = await this.executeHostMethod({
        method,
        params,
        spaceId,
        pageId,
        leaseEpoch,
        requestId,
        actionId,
        message,
      });
      if (actionId) this.inflight.delete(actionId);
      this.reply({ requestId, actionId, ok: true, result, mutation });
      return { ok: true, result };
    } catch (error) {
      if (actionId) this.inflight.delete(actionId);
      const safeError = publicError(error);
      this.reply({
        requestId,
        actionId,
        ok: false,
        error: safeError,
        mutation,
      });
      return { ok: false, error: safeError };
    }
  }

  async executeHostMethod({
    method,
    params,
    spaceId,
    pageId,
    leaseEpoch,
    requestId,
    actionId,
  }) {
    switch (method) {
      case "tab.inventory":
      case "page.list":
        return this.boundedInventory();
      case "page.create":
        return this.tabs.createAgentPage({
          spaceId,
          pageId,
          leaseEpoch,
          url: params.url,
          title: params.title,
          ownershipProof: params.ownership_proof,
          windowHint: params.window_hint,
          onDispatch: () => this.markActionDispatched(actionId),
        });
      case "page.adopt":
        return this.tabs.adoptExistingTab({
          tabHint: params.tab_hint,
          spaceId,
          pageId,
          leaseEpoch,
          ownershipProof: params.ownership_proof,
          intentTicket: params.intent_ticket,
          onDispatch: () => this.markActionDispatched(actionId),
        });
      case "page.close":
        return this.tabs.closeManagedPage({
          spaceId,
          pageId,
          leaseEpoch,
          expectedGeneration: params.expected_generation,
          expectedNavigationGeneration: params.expected_navigation_generation,
          expectedDocumentGeneration: params.expected_document_generation,
          cleanupProof: params.cleanup_proof,
          onDispatch: () => this.markActionDispatched(actionId),
        });
      case "debugger.attach":
        return this.debugger.attach({
          spaceId,
          pageId,
          leaseEpoch,
          onDispatch: () => this.markActionDispatched(actionId),
        });
      case "debugger.detach":
        return this.debugger.detach({
          spaceId,
          pageId,
          leaseEpoch,
          reason: params.reason,
          onDispatch: () => this.markActionDispatched(actionId),
        });
      case "debugger.command": {
        const debuggerMethod = params.method;
        this.assertFence(spaceId, leaseEpoch);
        return this.debugger.sendCommand({
          spaceId,
          pageId,
          leaseEpoch,
          method: debuggerMethod,
          params: params.params ?? {},
          expectedGeneration: params.expected_generation,
          expectedTargetGeneration: params.expected_target_generation,
          expectedNavigationGeneration: params.expected_navigation_generation,
          expectedDocumentGeneration: params.expected_document_generation,
          commandId: params.command_id ?? actionId ?? requestId,
          capability: params.capability,
          approval: params.approval,
          onDispatch: () => this.markActionDispatched(actionId),
        });
      }
      case "group.present": {
        const record = this.tabs.assertPageDispatch({
          spaceId,
          pageId,
          leaseEpoch,
          mutation: false,
        });
        return this.groups.presentSpace({
          spaceId,
          tabId: record.rawTabId,
          title: params.title,
        });
      }
      case "content.request":
        this.assertFence(spaceId, leaseEpoch);
        return this.sendContentRequest({
          spaceId,
          pageId,
          leaseEpoch,
          params,
          requestId,
          onDispatch: () => this.markActionDispatched(actionId),
        });
      default:
        throw new ProtocolError(
          "capability_unavailable",
          `extension method is not implemented: ${method}`,
        );
    }
  }

  async sendContentRequest({
    spaceId,
    pageId,
    leaseEpoch,
    params,
    requestId,
    onDispatch = () => {},
  }) {
    const record = this.tabs.assertPageDispatch({
      spaceId,
      pageId,
      leaseEpoch,
      mutation: true,
    });
    if (!this.chrome?.tabs?.sendMessage)
      throw new ProtocolError(
        "capability_unavailable",
        "content messaging is unavailable",
      );
    if (!PAGE_OPERATIONS.has(params.operation))
      throw new ProtocolError(
        "capability_unavailable",
        "content operation is not allowlisted",
      );
    const document = this.contentDocuments.get(record.rawTabId);
    if (
      !document ||
      document.spaceId !== spaceId ||
      document.pageId !== pageId ||
      document.sessionEpoch !== this.metadata.browserSessionEpoch ||
      document.targetGeneration !== record.targetGeneration ||
      document.navigationGeneration !== record.navigationGeneration ||
      document.documentGeneration !== record.documentGeneration ||
      document.expiresAt <= this.now()
    ) {
      throw new ProtocolError(
        "capability_unavailable",
        "content bridge is not ready for the current document",
      );
    }
    if (
      (params.nonce !== undefined && params.nonce !== document.nonce) ||
      (params.document_id !== undefined &&
        params.document_id !== document.documentId)
    ) {
      throw new ProtocolError(
        "stale_generation",
        "content document scope does not match the live page",
      );
    }
    const expiresAt =
      params.expires_at === undefined
        ? this.now() + CONTENT_REQUEST_TTL_MS
        : params.expires_at;
    if (
      !Number.isSafeInteger(expiresAt) ||
      expiresAt <= this.now() ||
      expiresAt > this.now() + CONTENT_REQUEST_TTL_MS
    ) {
      throw new ProtocolError(
        "proof_expired",
        "content request expiry is invalid",
      );
    }
    const message = {
      type: "agentyc.content.request",
      version: 1,
      nonce: document.nonce,
      document_id: document.documentId,
      request_id: requestId,
      operation: params.operation,
      payload: params.payload ?? {},
      expires_at: expiresAt,
    };
    assertNoRawBrowserIdentifiers(message.payload);
    if (
      !this.contentPending.has(requestId) &&
      this.contentPending.size >= MAX_CONTENT_PENDING
    )
      throw new ProtocolError(
        "resource_exhausted",
        "content request bound reached",
      );
    this.contentPending.set(requestId, {
      spaceId,
      pageId,
      rawTabId: record.rawTabId,
      nonce: document.nonce,
      documentId: document.documentId,
      operation: params.operation,
      expiresAt,
      sessionEpoch: this.metadata.browserSessionEpoch,
      targetGeneration: record.targetGeneration,
      navigationGeneration: record.navigationGeneration,
      documentGeneration: record.documentGeneration,
    });
    try {
      onDispatch();
      await this.chrome.tabs.sendMessage(record.rawTabId, message);
    } catch (error) {
      this.contentPending.delete(requestId);
      throw unknownDispatch("content bridge dispatch result was lost", {
        cause: error instanceof Error ? error.message : String(error),
      });
    }
    return { accepted: true, request_id: requestId, expires_at: expiresAt };
  }

  async handleFence({
    message,
    params,
    requestId,
    actionId,
    spaceId,
    leaseEpoch,
  }) {
    assertLogicalScope({ spaceId });
    positiveEpoch(leaseEpoch, "lease_epoch");
    const fenceEpoch = positiveEpoch(
      params.fence_epoch ?? message.fence_epoch ?? leaseEpoch,
      "fence_epoch",
    );
    const current = this.fences.get(spaceId) ?? 0;
    if (fenceEpoch <= current) {
      const error = errorResult(
        "stale_fence",
        "fence barrier is older than or equal to the live barrier",
      );
      this.reply({ requestId, actionId, ok: false, error, mutation: false });
      return { ok: false, error };
    }
    const drain = this.tabs.beginFence(spaceId, fenceEpoch);
    const unknownActionIds = [];
    for (const [id, pending] of this.inflight) {
      if (pending.spaceId === spaceId && pending.dispatched) {
        unknownActionIds.push(id);
        this.inflight.delete(id);
      }
    }
    this.fences.set(spaceId, fenceEpoch);
    let durable = true;
    try {
      await this.persistFences();
    } catch {
      // The in-memory barrier is already active; the host is told it will not
      // survive a worker restart so it can re-fence after the next handshake.
      durable = false;
    }
    const result = {
      space_id: spaceId,
      fence_epoch: fenceEpoch,
      drained_request_ids: [],
      unknown_action_ids: unknownActionIds,
      affected_page_count: drain.affected_page_ids.length,
      durable,
    };
    try {
      this.native.send("fence_ack", {
        request_id: requestId,
        action_id: actionId,
        space_id: spaceId,
        fence_epoch: fenceEpoch,
        result,
        ok: true,
      });
    } catch {
      // Host reconciliation sees the missing acknowledgement and keeps the space paused.
    }
    return { ok: true, result };
  }

  assertFence(spaceId, leaseEpoch) {
    const fence = this.fences.get(spaceId) ?? 0;
    if (leaseEpoch < fence)
      throw new ProtocolError(
        "stale_lease",
        "command is below the current fence barrier",
      );
  }

  reply({ requestId, actionId, ok, result, error, mutation }) {
    try {
      this.native.sendResponse({
        requestId,
        actionId,
        ok,
        result,
        error,
        mutation,
      });
    } catch (sendError) {
      if (actionId && mutation) {
        this.handleLostDispatch([actionId], sendError);
      }
    }
  }

  handleExtensionEvent(event, payload = {}) {
    const safePayload =
      payload && typeof payload === "object" ? payload : { value: payload };
    try {
      assertNoRawBrowserIdentifiers(safePayload);
    } catch {
      return;
    }
    this.forwardToSidePanel({
      type: "agentyc.event",
      event,
      payload: safePayload,
    });
    if (this.native.connected) {
      try {
        this.native.sendEvent(event, safePayload, {
          space_id: safePayload.space_id,
          page_id: safePayload.page_id,
        });
      } catch {
        // Transport loss is handled by NativeMessagingClient without replay.
      }
    }
  }

  handleDebuggerEvent(payload) {
    if (!payload) return;
    this.handleExtensionEvent(payload.event ?? "debugger.event", payload);
  }

  forwardHostEvent(message) {
    const payload = message.payload ?? message;
    try {
      assertNoRawBrowserIdentifiers(payload);
    } catch {
      return;
    }
    this.forwardToSidePanel({
      type: "agentyc.host_event",
      event: message.event ?? "host.event",
      payload,
    });
  }

  forwardToSidePanel(message) {
    try {
      this.chrome?.runtime?.sendMessage?.(message);
    } catch {
      // The side panel is optional and may not be open.
    }
  }

  async handleRuntimeMessage(message, sender) {
    if (!message || typeof message !== "object") return undefined;
    if (sender?.id !== this.chrome?.runtime?.id) return undefined;
    if (message.type === "agentyc.content.ready")
      return this.handleContentReady(message, sender);
    if (message.type === "agentyc.content.result")
      return this.handleContentResult(message, sender);
    if (message.type === "agentyc.content.closed")
      return this.handleContentClosed(message, sender);
    if (message.type === "agentyc.sidepanel.request")
      return this.handleSidePanelRequest(message);
    return undefined;
  }

  senderTabRecord(sender) {
    const rawTabId = sender?.tab?.id;
    if (!Number.isInteger(rawTabId)) return undefined;
    const record = this.tabs.getInternalByTab(rawTabId);
    if (!record || record.ownership !== "agent" || !record.pageId)
      return undefined;
    const expectedOrigin = (() => {
      try {
        return record.url ? new URL(record.url).origin : undefined;
      } catch {
        return undefined;
      }
    })();
    const senderOrigin =
      typeof sender?.origin === "string" ? sender.origin : undefined;
    const senderUrl = typeof sender?.url === "string" ? sender.url : undefined;
    if (!expectedOrigin || (!senderOrigin && !senderUrl)) return undefined;
    try {
      if (
        senderOrigin !== undefined &&
        new URL(senderOrigin).origin !== expectedOrigin
      )
        return undefined;
      if (
        senderUrl !== undefined &&
        new URL(senderUrl).origin !== expectedOrigin
      )
        return undefined;
    } catch {
      return undefined;
    }
    return record;
  }

  pruneContentState() {
    const now = this.now();
    for (const [tabId, document] of this.contentDocuments) {
      if (document.expiresAt <= now) this.contentDocuments.delete(tabId);
    }
    for (const [requestId, pending] of this.contentPending) {
      if (pending.expiresAt <= now) this.contentPending.delete(requestId);
    }
  }

  handleContentReady(message, sender) {
    this.pruneContentState();
    const record = this.senderTabRecord(sender);
    if (!record)
      return {
        ok: false,
        error: errorResult(
          "permission_denied",
          "content sender is not a managed page",
        ),
      };
    if (
      message.version !== 1 ||
      typeof message.nonce !== "string" ||
      message.nonce.length < 8 ||
      message.nonce.length > 128 ||
      typeof message.document_id !== "string" ||
      message.document_id.length < 8 ||
      message.document_id.length > 128
    ) {
      return {
        ok: false,
        error: errorResult(
          "schema_invalid",
          "content document identity is invalid",
        ),
      };
    }
    const expiresAt =
      message.expires_at === undefined
        ? this.now() + CONTENT_DOCUMENT_TTL_MS
        : message.expires_at;
    if (
      !Number.isSafeInteger(expiresAt) ||
      expiresAt <= this.now() ||
      expiresAt > this.now() + CONTENT_DOCUMENT_TTL_MS
    ) {
      return {
        ok: false,
        error: errorResult(
          "proof_expired",
          "content document registration is expired",
        ),
      };
    }
    this.contentDocuments.set(record.rawTabId, {
      spaceId: record.spaceId,
      pageId: record.pageId,
      rawTabId: record.rawTabId,
      nonce: message.nonce,
      documentId: message.document_id,
      expiresAt,
      sessionEpoch: this.metadata.browserSessionEpoch,
      targetGeneration: record.targetGeneration,
      navigationGeneration: record.navigationGeneration,
      documentGeneration: record.documentGeneration,
    });
    return { ok: true };
  }

  handleContentClosed(message, sender) {
    const record = this.senderTabRecord(sender);
    if (!record)
      return {
        ok: false,
        error: errorResult("permission_denied", "content sender is invalid"),
      };
    const document = this.contentDocuments.get(record.rawTabId);
    if (
      document &&
      document.nonce === message.nonce &&
      document.documentId === message.document_id
    ) {
      this.contentDocuments.delete(record.rawTabId);
      for (const [requestId, pending] of this.contentPending) {
        if (pending.rawTabId === record.rawTabId)
          this.contentPending.delete(requestId);
      }
    }
    return { ok: true };
  }

  handleContentResult(message, sender) {
    this.pruneContentState();
    const pending = this.contentPending.get(message.request_id);
    if (!pending)
      return {
        ok: false,
        error: errorResult("stale_request", "content result is not pending"),
      };
    const record = this.senderTabRecord(sender);
    if (!record) {
      return {
        ok: false,
        error: errorResult(
          "permission_denied",
          "content sender is not authorized for the managed page",
        ),
      };
    }
    if (
      record.rawTabId !== pending.rawTabId ||
      record.spaceId !== pending.spaceId ||
      record.pageId !== pending.pageId ||
      record.targetGeneration !== pending.targetGeneration ||
      record.navigationGeneration !== pending.navigationGeneration ||
      record.documentGeneration !== pending.documentGeneration ||
      pending.sessionEpoch !== this.metadata.browserSessionEpoch ||
      message.version !== 1 ||
      message.nonce !== pending.nonce ||
      message.document_id !== pending.documentId ||
      message.operation !== pending.operation ||
      !Number.isSafeInteger(message.expires_at) ||
      message.expires_at <= this.now() ||
      message.expires_at > pending.expiresAt
    ) {
      return {
        ok: false,
        error: errorResult(
          "stale_generation",
          "content result scope is not current",
        ),
      };
    }
    try {
      if (message.ok) assertNoRawBrowserIdentifiers(message.result ?? {});
      else assertNoRawBrowserIdentifiers(message.error ?? {});
    } catch (error) {
      return { ok: false, error: publicError(error) };
    }
    this.contentPending.delete(message.request_id);
    this.handleExtensionEvent("content.result", {
      space_id: pending.spaceId,
      page_id: pending.pageId,
      request_id: message.request_id,
      ok: Boolean(message.ok),
      ...(message.ok
        ? { result: message.result ?? {} }
        : { error: message.error }),
    });
    return { ok: true };
  }

  pruneSidePanelTickets() {
    const now = this.now();
    for (const [ticketId, expiresAt] of this.usedSidePanelTickets) {
      if (expiresAt <= now) this.usedSidePanelTickets.delete(ticketId);
    }
  }

  validateSidePanelTicket(ticket, action, params) {
    if (
      !ticket ||
      ticket.issued_by_host !== true ||
      typeof ticket.ticket_id !== "string" ||
      !/^[A-Za-z0-9._:-]{8,128}$/.test(ticket.ticket_id) ||
      ticket.purpose !== "sidepanel" ||
      ticket.action !== action
    ) {
      throw new ProtocolError(
        "user_confirmation_required",
        "destructive side-panel action requires a host intent ticket",
      );
    }
    const expiresAt = ticket.expires_at ?? ticket.expires_at_ms;
    if (
      !Number.isSafeInteger(expiresAt) ||
      expiresAt <= this.now() ||
      expiresAt > this.now() + MAX_SIDE_PANEL_TICKET_LIFETIME_MS
    ) {
      throw new ProtocolError(
        "proof_expired",
        "side-panel intent ticket is expired",
      );
    }
    const spaceId = params.space_id ?? params.spaceId;
    if (
      ticket.space_id !== spaceId ||
      (ticket.profile_instance_id !== undefined &&
        ticket.profile_instance_id !== this.metadata.profileInstanceId) ||
      (ticket.browser_session_epoch !== undefined &&
        ticket.browser_session_epoch !== this.metadata.browserSessionEpoch)
    ) {
      throw new ProtocolError(
        "permission_denied",
        "side-panel intent ticket scope is not current",
      );
    }
    this.pruneSidePanelTickets();
    if (this.usedSidePanelTickets.has(ticket.ticket_id))
      throw new ProtocolError(
        "replay_rejected",
        "side-panel intent ticket was already consumed",
      );
    if (this.usedSidePanelTickets.size >= MAX_SIDE_PANEL_TICKETS)
      throw new ProtocolError(
        "resource_exhausted",
        "side-panel ticket cache is full",
      );
    this.usedSidePanelTickets.set(ticket.ticket_id, expiresAt);
  }

  handleSidePanelRequest(message) {
    const action = message.action;
    const allowed = new Set([
      "create",
      "pause",
      "stop",
      "takeover",
      "return_control",
      "handoff",
      "finish",
      "retain",
      "release",
    ]);
    if (!allowed.has(action))
      return {
        ok: false,
        error: errorResult(
          "capability_unavailable",
          "side-panel action is not allowlisted",
        ),
      };
    const params =
      message.params && typeof message.params === "object"
        ? message.params
        : {};
    try {
      assertNoRawBrowserIdentifiers(params);
      if (DESTRUCTIVE_SIDE_PANEL_ACTIONS.has(action))
        this.validateSidePanelTicket(message.intent_ticket, action, params);
      const requestId = createLogicalId("req");
      const method = `space.${action}`;
      const actionId = createLogicalId("action");
      this.native.sendRequest({
        method,
        params,
        requestId,
        actionId,
        mutation: true,
      });
      return { ok: true, request_id: requestId };
    } catch (error) {
      return { ok: false, error: publicError(error) };
    }
  }
}

export function createServiceWorker(options = {}) {
  return new ServiceWorkerController(options);
}

export async function installServiceWorker(chromeApi = globalThis.chrome) {
  const controller = createServiceWorker({ chromeApi });
  await controller.start();
  return controller;
}

// Chrome loads this module as the MV3 service worker. Node tests import the
// factory instead; the protocol guard avoids side effects in those tests.
if (
  globalThis.location?.protocol === "chrome-extension:" &&
  globalThis.chrome?.runtime?.connectNative
) {
  const controller = createServiceWorker({ chromeApi: globalThis.chrome });
  globalThis.__agentycServiceWorker = controller;
  void controller.start();
}
