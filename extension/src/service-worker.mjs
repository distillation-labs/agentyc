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
import { DebuggerBridge } from "./debugger-bridge.mjs";
import { TabsRegistry, unknownDispatch } from "./tabs-registry.mjs";
import { GroupsRegistry } from "./groups.mjs";
import { FramesRegistry } from "./frames.mjs";
import { PAGE_OPERATIONS } from "./page-bridge.mjs";

const METADATA_KEY = "agentyc_extension_metadata";
const FENCE_KEY = "agentyc_space_fences";
const ACTION_STATE_KEY = "agentyc_action_state";
const MANAGED_BINDINGS_KEY = "agentyc_managed_bindings";
const SAFETY_COUNTERS_KEY = "agentyc_safety_counters";
const SESSION_MARKER_KEY = "agentyc_browser_session_marker";
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
const MAX_PERSISTED_INFLIGHT_ACTIONS = 256;
const MAX_PERSISTED_MANAGED_BINDINGS = 256;
const MAX_ACTION_RECEIPTS = 256;
const MAX_SNAPSHOT_ELEMENTS = 256;
const MAX_SNAPSHOT_BYTES = 256 * 1024;
const CONTENT_RESPONSE_TIMEOUT_MS = 10 * 1000;
const ACTION_CONTROL_KEYS = new Set([
  "action",
  "action_id",
  "approval",
  "capability",
  "cleanup_proof",
  "command_id",
  "expected_document_generation",
  "expected_generation",
  "expected_navigation_generation",
  "expected_target_generation",
  "operation",
  "page_id",
  "lease_epoch",
  "method",
  "ownership_proof",
  "postcondition",
  "request_id",
  "space_id",
  "user_intent",
]);
const ACTION_METHODS = Object.freeze({
  navigate: "Page.navigate",
  click: "Input.dispatchMouseEvent",
  input: "Input.insertText",
  type: "Input.insertText",
  fill: "Input.insertText",
  press: "Input.dispatchKeyEvent",
  scroll: "Input.dispatchMouseEvent",
  evaluate: "Runtime.evaluate",
  screenshot: "Page.captureScreenshot",
});
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

function validActionId(value) {
  return typeof value === "string" && /^[A-Za-z0-9._:-]{8,128}$/.test(value);
}

function validRequestId(value) {
  return (
    typeof value === "string" && /^req_[a-z0-9][a-z0-9_-]{0,127}$/.test(value)
  );
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

function textBytes(value) {
  return new TextEncoder().encode(value).byteLength;
}

function boundedText(value, max) {
  return typeof value === "string" ? value.slice(0, max) : "";
}

function canonicalJsonString(value) {
  let output = '"';
  for (const character of String(value)) {
    const code = character.codePointAt(0);
    switch (character) {
      case '"':
        output += '\\\"';
        break;
      case "\\":
        output += "\\\\";
        break;
      case "\n":
        output += "\\n";
        break;
      case "\r":
        output += "\\r";
        break;
      case "\t":
        output += "\\t";
        break;
      default:
        if (code < 0x20) output += `\\u${code.toString(16).padStart(4, "0")}`;
        else output += character;
    }
  }
  return `${output}"`;
}

function snapshotElementsHash(elements) {
  const canonical = `[${elements
    .map((element) => {
      const attributes = Object.keys(element.attributes ?? {})
        .sort()
        .map(
          (key) =>
            `${canonicalJsonString(key)}:${canonicalJsonString(
              element.attributes[key],
            )}`,
        )
        .join(",");
      return `{${[
        `"key":${canonicalJsonString(element.key)}`,
        `"parent":${element.parent === null ? "null" : canonicalJsonString(element.parent)}`,
        `"kind":${canonicalJsonString(element.kind)}`,
        `"text":${element.text === null ? "null" : canonicalJsonString(element.text)}`,
        `"attributes":{${attributes}}`,
        `"order":${element.order}`,
      ].join(",")}}`;
    })
    .join(",")}]`;
  let hash = 0xcbf29ce484222325n;
  for (const byte of new TextEncoder().encode(canonical)) {
    hash ^= BigInt(byte);
    hash = BigInt.asUintN(64, hash * 0x100000001b3n);
  }
  return `fnv1a64:${hash.toString(16).padStart(16, "0")}`;
}

function parseJsonObject(value, field) {
  if (isPlainObject(value)) return value;
  if (typeof value === "string" && value.length <= 16 * 1024) {
    try {
      const parsed = JSON.parse(value);
      if (isPlainObject(parsed)) return parsed;
    } catch {
      // The caller receives a typed schema error below.
    }
  }
  throw new ProtocolError("schema_invalid", `${field} must be an object`);
}

function normalizePostcondition(value) {
  if (value === undefined || value === null) return undefined;
  const condition = parseJsonObject(value, "postcondition");
  if (condition.kind === "page_generation") {
    if (
      !Number.isSafeInteger(condition.document_generation) ||
      condition.document_generation < 1
    ) {
      throw new ProtocolError(
        "schema_invalid",
        "page-generation postcondition is invalid",
      );
    }
    return {
      kind: "page_generation",
      document_generation: condition.document_generation,
    };
  }
  if (
    condition.kind === "snapshot_hash" &&
    typeof condition.snapshot_hash === "string" &&
    /^fnv1a64:[0-9a-f]{16}$/i.test(condition.snapshot_hash)
  ) {
    return {
      kind: "snapshot_hash",
      snapshot_hash: condition.snapshot_hash.toLowerCase(),
    };
  }
  throw new ProtocolError(
    "schema_invalid",
    "postcondition kind or value is not allowlisted",
  );
}

function scalarNumber(value) {
  if (typeof value === "number")
    return Number.isFinite(value) ? value : undefined;
  if (typeof value !== "string" || value.length === 0 || value.length > 32)
    return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function stripActionControls(value) {
  if (!isPlainObject(value)) return {};
  const output = {};
  for (const [key, child] of Object.entries(value)) {
    if (!ACTION_CONTROL_KEYS.has(key)) output[key] = child;
  }
  return output;
}

function commandParams(params) {
  const payload = isPlainObject(params.payload) ? params.payload : undefined;
  let source = isPlainObject(params.params)
    ? params.params
    : payload && isPlainObject(payload.params)
      ? payload.params
      : payload;
  if (typeof source === "string") source = parseJsonObject(source, "params");
  return stripActionControls(source ?? {});
}

function coerceDebuggerParams(method, value) {
  const output = { ...value };
  if (method === "Input.dispatchMouseEvent") {
    for (const key of ["x", "y", "deltaX", "deltaY", "clickCount"]) {
      if (output[key] !== undefined) {
        const number = scalarNumber(output[key]);
        if (number !== undefined) output[key] = number;
      }
    }
  }
  if (method === "Runtime.evaluate") {
    for (const key of [
      "awaitPromise",
      "returnByValue",
      "userGesture",
      "silent",
      "throwOnSideEffect",
    ]) {
      if (output[key] === "true") output[key] = true;
      if (output[key] === "false") output[key] = false;
    }
  }
  return output;
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
 * the host remains authoritative; storage holds only bounded recovery markers
 * for outcomes that became unknown across worker lifetimes.
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
    this.sessionStorage = this.chrome?.storage?.session;
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
    this.actionReceipts = new Map();
    this.snapshotVersions = new Map();
    this.safetyCounters = { userTabCloses: 0, focusTheft: 0 };
    this.storedSafetyCounters = undefined;
    this.browserSessionFresh = false;
    this.lifecycleToken = 0;
    this.nativeConnectedOnce = false;
    this.recoveryObserved = false;
    this.storedActionState = undefined;
    this.storedManagedBindings = undefined;
    this.managedBindingsWrite = Promise.resolve();
    this.actionStateWrite = Promise.resolve();

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
      this.lifecycleToken += 1;
      await this.loadMetadata();
      await this.recoverPersistedActionState();
      this.loadFences();
      this.applyRuntimeIdentity();
      this.debugger.setIdentity(this.metadata);
      this.started = true;
      this.groups.start();
      await this.tabs.start();
      await this.rehydrateManagedBindings();
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
      ACTION_STATE_KEY,
      MANAGED_BINDINGS_KEY,
      SAFETY_COUNTERS_KEY,
    ]);
    this.storedFences = storedValues[FENCE_KEY];
    this.storedActionState = storedValues[ACTION_STATE_KEY];
    this.storedManagedBindings = storedValues[MANAGED_BINDINGS_KEY];
    this.storedSafetyCounters = storedValues[SAFETY_COUNTERS_KEY];
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
    let browserSessionEpoch =
      Number.isSafeInteger(this.metadata.browserSessionEpoch) &&
      this.metadata.browserSessionEpoch >= 1
        ? this.metadata.browserSessionEpoch
        : previousBrowserSession;
    let sessionMarker;
    if (this.sessionStorage?.get) {
      const sessionValues = await storageGet(this.sessionStorage, [
        SESSION_MARKER_KEY,
      ]);
      sessionMarker = sessionValues[SESSION_MARKER_KEY];
      this.browserSessionFresh =
        !isPlainObject(sessionMarker) ||
        sessionMarker.profile_instance_id !== profileInstanceId;
      // Keep the prior durable epoch until runtime.onStartup performs the
      // browser-session transition. Rehydration is disabled while this marker
      // is absent, so a worker wake during browser startup cannot reclaim an
      // old tab before the epoch fence is installed.
    }
    this.metadata = {
      profileInstanceId,
      workerInstanceEpoch,
      browserSessionEpoch,
    };
    const storedSafety = this.storedSafetyCounters;
    if (
      isPlainObject(storedSafety) &&
      Number.isSafeInteger(storedSafety.user_tab_closes) &&
      storedSafety.user_tab_closes >= 0 &&
      Number.isSafeInteger(storedSafety.focus_theft) &&
      storedSafety.focus_theft >= 0
    ) {
      this.safetyCounters = {
        userTabCloses: storedSafety.user_tab_closes,
        focusTheft: storedSafety.focus_theft,
      };
    }
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
      [SAFETY_COUNTERS_KEY]: {
        user_tab_closes: this.safetyCounters.userTabCloses,
        focus_theft: this.safetyCounters.focusTheft,
      },
    });
    if (this.sessionStorage?.set) {
      await storageSet(this.sessionStorage, {
        [SESSION_MARKER_KEY]: {
          profile_instance_id: profileInstanceId,
          browser_session_epoch: browserSessionEpoch,
        },
      });
    }
  }

  async persistSafetyCounters() {
    await storageSet(this.storage, {
      [SAFETY_COUNTERS_KEY]: {
        user_tab_closes: this.safetyCounters.userTabCloses,
        focus_theft: this.safetyCounters.focusTheft,
      },
    });
  }

  queueSafetyCountersPersist() {
    void this.persistSafetyCounters().catch(() => {});
  }

  isLiveToken(token) {
    return this.started && token === this.lifecycleToken;
  }

  applyRuntimeIdentity() {
    // Browser hints must survive MV3 worker replacement within one browser
    // session so persisted managed bindings can be rehydrated. The worker
    // epoch remains an authority fence, not part of the browser hint key.
    const hintSalt = [
      this.metadata.profileInstanceId,
      this.metadata.browserSessionEpoch,
    ].join(":");
    this.groups.setHintSalt(hintSalt);
    this.tabs.setIdentity({ ...this.metadata, hintSalt });
    this.frames.setHintSalt(hintSalt);
  }

  async persistManagedBindings() {
    const bindings = this.tabs
      .inventory()
      .filter(
        (record) =>
          record?.ownership === "agent" &&
          record?.lifecycle === "managed" &&
          record?.binding_state === "bound" &&
          typeof record.space_id === "string" &&
          typeof record.page_id === "string" &&
          typeof record.tab_hint === "string" &&
          Number.isSafeInteger(record.lease_epoch),
      )
      .slice(0, MAX_PERSISTED_MANAGED_BINDINGS)
      .map((record) => ({
        space_id: record.space_id,
        page_id: record.page_id,
        lease_epoch: record.lease_epoch,
        tab_hint: record.tab_hint,
        target_generation: record.target_generation,
        navigation_generation: record.navigation_generation,
        document_generation: record.document_generation,
        browser_session_epoch: record.browser_session_epoch,
        url: record.url,
        title: record.title,
      }));
    const value = {
      profile_instance_id: this.metadata.profileInstanceId,
      browser_session_epoch: this.metadata.browserSessionEpoch,
      bindings,
    };
    const write = this.managedBindingsWrite
      .catch(() => {})
      .then(() => storageSet(this.storage, { [MANAGED_BINDINGS_KEY]: value }));
    this.managedBindingsWrite = write.catch(() => {});
    return write;
  }

  queueManagedBindingsPersist() {
    void this.persistManagedBindings().catch(() => {});
  }

  async rehydrateManagedBindings() {
    // Chrome may restore tabs asynchronously during browser startup. Refresh
    // twice across one bounded yield before matching exact logical URLs.
    await this.tabs.refreshSession().catch(() => {});
    await new Promise((resolve) => globalThis.setTimeout(resolve, 250));
    await this.tabs.refreshSession().catch(() => {});
    if (this.browserSessionFresh) {
      this.handleExtensionEvent("browser.session_pending", {
        reason: "storage.session marker was reset; awaiting runtime.onStartup",
      });
      return;
    }
    const stored = this.storedManagedBindings;
    this.storedManagedBindings = undefined;
    let restored = false;
    const hadPersistedBindings =
      isPlainObject(stored) &&
      Array.isArray(stored.bindings) &&
      stored.bindings.length > 0;
    if (
      !isPlainObject(stored) ||
      stored.profile_instance_id !== this.metadata.profileInstanceId ||
      !Array.isArray(stored.bindings)
    ) {
      await this.persistManagedBindings().catch(() => {});
      return;
    }
    for (const binding of stored.bindings.slice(
      0,
      MAX_PERSISTED_MANAGED_BINDINGS,
    )) {
      if (
        !isPlainObject(binding) ||
        typeof binding.space_id !== "string" ||
        typeof binding.page_id !== "string" ||
        typeof binding.tab_hint !== "string" ||
        !Number.isSafeInteger(binding.lease_epoch) ||
        binding.lease_epoch < 1 ||
        !Number.isSafeInteger(binding.browser_session_epoch) ||
        binding.browser_session_epoch < 1 ||
        binding.browser_session_epoch !== this.metadata.browserSessionEpoch
      ) {
        if (isPlainObject(binding)) {
          this.handleExtensionEvent("page.rebind_required", {
            space_id: binding.space_id,
            page_id: binding.page_id,
            reason: "persisted binding belongs to another browser session",
          });
        }
        continue;
      }
      // A worker restart may restore an exact tab hint in the same Chrome
      // session. URL/title matching is deliberately not an authority path:
      // an unrelated user tab can have the same URL.
      const candidate = this.tabs.findByHint(binding.tab_hint);
      if (
        !candidate ||
        candidate.ownership !== "unmanaged" ||
        candidate.bindingState !== "unbound" ||
        candidate.active === true ||
        candidate.incognito === true ||
        (typeof binding.url === "string" && candidate.url !== binding.url)
      ) {
        this.handleExtensionEvent("page.rebind_required", {
          space_id: binding.space_id,
          page_id: binding.page_id,
          reason:
            "persisted managed binding has no exact same-session tab match",
        });
        continue;
      }
      try {
        this.tabs.restoreManagedBinding({
          tabId: candidate.rawTabId,
          spaceId: binding.space_id,
          pageId: binding.page_id,
          leaseEpoch: binding.lease_epoch,
          targetGeneration: binding.target_generation,
          navigationGeneration: binding.navigation_generation,
          documentGeneration: binding.document_generation,
        });
        await this.groups
          .presentSpace({
            spaceId: binding.space_id,
            tabId: candidate.rawTabId,
            title: binding.title || "agentyc",
          })
          .catch(() => {});
        restored = true;
        this.handleExtensionEvent("page.rebound", {
          space_id: binding.space_id,
          page_id: binding.page_id,
          reason: "same-session durable binding restored",
        });
      } catch {
        this.handleExtensionEvent("page.rebind_required", {
          space_id: binding.space_id,
          page_id: binding.page_id,
          reason: "same-session durable binding could not be restored",
        });
      }
    }
    if (restored) this.recoveryObserved = true;
    // Keep an unmatched prior-session binding for the next browser-start
    // refresh; overwriting it with an empty list would destroy recovery proof.
    if (restored || !hadPersistedBindings)
      await this.persistManagedBindings().catch(() => {});
  }

  async recoverPersistedActionState() {
    const stored = this.storedActionState;
    this.storedActionState = undefined;
    if (!stored || typeof stored !== "object" || Array.isArray(stored)) {
      await this.persistActionState().catch(() => {});
      return;
    }
    if (stored.profile_instance_id !== this.metadata.profileInstanceId) {
      await this.persistActionState().catch(() => {});
      return;
    }
    for (const entry of (Array.isArray(stored.receipts)
      ? stored.receipts
      : []
    ).slice(0, MAX_ACTION_RECEIPTS)) {
      const receipt = this.restoreActionReceipt(entry);
      if (receipt) this.actionReceipts.set(receipt.action_id, receipt);
    }
    const recovered = [];
    const candidates = [
      ...(Array.isArray(stored.inflight) ? stored.inflight : []),
      ...(Array.isArray(stored.unknown_action_ids)
        ? stored.unknown_action_ids.map((actionId) => ({ action_id: actionId }))
        : []),
    ];
    for (const entry of candidates.slice(0, MAX_PERSISTED_INFLIGHT_ACTIONS)) {
      const actionId = typeof entry === "string" ? entry : entry?.action_id;
      if (!validActionId(actionId)) continue;
      const prior = this.actionReceipts.get(actionId);
      if (prior?.outcome === "succeeded" || prior?.outcome === "failed")
        continue;
      const recoveredEntry = this.restoreActionReceipt({
        ...entry,
        outcome: "unknown",
        code: "unknown_outcome",
        reason: "worker restarted before mutation outcome was known",
      });
      if (recoveredEntry) this.actionReceipts.set(actionId, recoveredEntry);
      if (this.unreportedUnknownActions.has(actionId)) continue;
      if (this.unreportedUnknownActions.size < MAX_UNREPORTED_UNKNOWN_ACTIONS) {
        this.unreportedUnknownActions.add(actionId);
        recovered.push(actionId);
      } else {
        this.unknownActionsOverflow = true;
      }
    }
    if (stored.unknown_actions_overflow === true)
      this.unknownActionsOverflow = true;
    for (const actionId of recovered) {
      const receipt = this.actionReceiptFor(actionId, {
        outcome: "unknown",
        code: "unknown_outcome",
        reason: "worker restarted before mutation outcome was known",
      });
      this.handleExtensionEvent("action.receipt", receipt);
      this.handleExtensionEvent("action.unknown", receipt);
    }
    await this.persistActionState().catch(() => {});
  }

  persistActionState() {
    const inflight = [...this.inflight.entries()]
      .slice(0, MAX_PERSISTED_INFLIGHT_ACTIONS)
      .map(([actionId, pending]) => ({
        action_id: actionId,
        request_id: pending.request_id ?? pending.requestId,
        space_id: pending.space_id ?? pending.spaceId,
        ...((pending.page_id ?? pending.pageId) !== undefined
          ? { page_id: pending.page_id ?? pending.pageId }
          : {}),
        method: pending.method,
        ...((pending.lease_epoch ?? pending.leaseEpoch) !== undefined
          ? { lease_epoch: pending.lease_epoch ?? pending.leaseEpoch }
          : {}),
        ...((pending.target_generation ?? pending.targetGeneration) !==
        undefined
          ? {
              target_generation:
                pending.target_generation ?? pending.targetGeneration,
            }
          : {}),
        ...((pending.navigation_generation ?? pending.navigationGeneration) !==
        undefined
          ? {
              navigation_generation:
                pending.navigation_generation ?? pending.navigationGeneration,
            }
          : {}),
        ...((pending.document_generation ?? pending.documentGeneration) !==
        undefined
          ? {
              document_generation:
                pending.document_generation ?? pending.documentGeneration,
            }
          : {}),
        ...(pending.postcondition
          ? { postcondition: pending.postcondition }
          : {}),
        ...((pending.navigation_url ?? pending.navigationUrl) !== undefined
          ? { navigation_url: pending.navigation_url ?? pending.navigationUrl }
          : {}),
        worker_instance_epoch: this.metadata.workerInstanceEpoch,
        browser_session_epoch: this.metadata.browserSessionEpoch,
      }));
    const receipts = [...this.actionReceipts.values()]
      .slice(-MAX_ACTION_RECEIPTS)
      .map((receipt) => ({
        action_id: receipt.action_id,
        ...(receipt.request_id ? { request_id: receipt.request_id } : {}),
        ...(receipt.space_id ? { space_id: receipt.space_id } : {}),
        ...(receipt.page_id ? { page_id: receipt.page_id } : {}),
        ...(receipt.method ? { method: receipt.method } : {}),
        ...(receipt.lease_epoch !== undefined
          ? { lease_epoch: receipt.lease_epoch }
          : {}),
        ...(receipt.target_generation !== undefined
          ? { target_generation: receipt.target_generation }
          : {}),
        ...(receipt.navigation_generation !== undefined
          ? { navigation_generation: receipt.navigation_generation }
          : {}),
        ...(receipt.document_generation !== undefined
          ? { document_generation: receipt.document_generation }
          : {}),
        ...(receipt.postcondition
          ? { postcondition: receipt.postcondition }
          : {}),
        ...(receipt.navigation_url !== undefined
          ? { navigation_url: receipt.navigation_url }
          : {}),
        outcome: receipt.outcome,
        ...(receipt.code ? { code: receipt.code } : {}),
        ...(receipt.reason ? { reason: boundedText(receipt.reason, 512) } : {}),
      }));
    const value = {
      profile_instance_id: this.metadata.profileInstanceId,
      browser_session_epoch: this.metadata.browserSessionEpoch,
      inflight,
      receipts,
      unknown_action_ids: [...this.unreportedUnknownActions].slice(
        0,
        MAX_UNREPORTED_UNKNOWN_ACTIONS,
      ),
      unknown_actions_overflow: this.unknownActionsOverflow,
    };
    const write = this.actionStateWrite
      .catch(() => {})
      .then(() => storageSet(this.storage, { [ACTION_STATE_KEY]: value }));
    this.actionStateWrite = write.catch(() => {});
    return write;
  }

  queueActionStatePersist() {
    void this.persistActionState().catch(() => {});
  }

  restoreActionReceipt(entry) {
    if (!isPlainObject(entry) || !validActionId(entry.action_id))
      return undefined;
    try {
      if (entry.space_id !== undefined || entry.page_id !== undefined)
        assertLogicalScope(
          { spaceId: entry.space_id, pageId: entry.page_id },
          { pageRequired: entry.page_id !== undefined },
        );
      if (
        entry.lease_epoch !== undefined &&
        (!Number.isSafeInteger(entry.lease_epoch) || entry.lease_epoch < 1)
      )
        return undefined;
      for (const field of [
        "target_generation",
        "navigation_generation",
        "document_generation",
      ]) {
        if (
          entry[field] !== undefined &&
          (!Number.isSafeInteger(entry[field]) || entry[field] < 1)
        )
          return undefined;
      }
      if (
        entry.method !== undefined &&
        (typeof entry.method !== "string" || entry.method.length > 128)
      )
        return undefined;
      const outcome = ["succeeded", "failed", "unknown"].includes(entry.outcome)
        ? entry.outcome
        : "unknown";
      const postcondition = normalizePostcondition(entry.postcondition);
      const navigationUrl =
        typeof entry.navigation_url === "string"
          ? boundedText(entry.navigation_url, 4096)
          : undefined;
      return {
        action_id: entry.action_id,
        ...(typeof entry.request_id === "string"
          ? { request_id: entry.request_id }
          : {}),
        ...(entry.space_id !== undefined ? { space_id: entry.space_id } : {}),
        ...(entry.page_id !== undefined ? { page_id: entry.page_id } : {}),
        ...(entry.method !== undefined ? { method: entry.method } : {}),
        ...(entry.lease_epoch !== undefined
          ? { lease_epoch: entry.lease_epoch }
          : {}),
        ...(entry.target_generation !== undefined
          ? { target_generation: entry.target_generation }
          : {}),
        ...(entry.navigation_generation !== undefined
          ? { navigation_generation: entry.navigation_generation }
          : {}),
        ...(entry.document_generation !== undefined
          ? { document_generation: entry.document_generation }
          : {}),
        ...(postcondition ? { postcondition } : {}),
        ...(navigationUrl !== undefined
          ? { navigation_url: navigationUrl }
          : {}),
        outcome,
        ...(typeof entry.code === "string" ? { code: entry.code } : {}),
        ...(typeof entry.reason === "string"
          ? { reason: boundedText(entry.reason, 512) }
          : {}),
      };
    } catch {
      return undefined;
    }
  }

  actionContextFor({
    actionId,
    requestId,
    method,
    params,
    spaceId,
    pageId,
    leaseEpoch,
    message,
  }) {
    if (!validActionId(actionId)) return undefined;
    const record = pageId ? this.tabs.getInternalByPage(pageId) : undefined;
    const postcondition = normalizePostcondition(
      params.postcondition ??
        params.payload?.postcondition ??
        message?.postcondition,
    );
    const wireParams =
      method === "debugger.command"
        ? commandParams(params)
        : isPlainObject(params.payload)
          ? params.payload
          : params;
    const navigationUrl =
      params.method === "Page.navigate" ||
      (method === "action.execute" &&
        (params.operation ?? params.action) === "navigate")
        ? typeof wireParams.url === "string"
          ? boundedText(wireParams.url, 4096)
          : undefined
        : undefined;
    return {
      action_id: actionId,
      request_id: validRequestId(params.request_id)
        ? params.request_id
        : requestId,
      ...(spaceId !== undefined ? { space_id: spaceId } : {}),
      ...(pageId !== undefined ? { page_id: pageId } : {}),
      method,
      ...(Number.isSafeInteger(leaseEpoch) ? { lease_epoch: leaseEpoch } : {}),
      ...(record
        ? {
            target_generation: record.targetGeneration,
            navigation_generation: record.navigationGeneration,
            document_generation: record.documentGeneration,
          }
        : {}),
      ...(postcondition ? { postcondition } : {}),
      ...(navigationUrl !== undefined ? { navigation_url: navigationUrl } : {}),
      dispatched: false,
    };
  }

  rememberActionReceipt(context, outcome, error = undefined) {
    if (!context?.action_id) return;
    const receipt = {
      ...context,
      outcome,
      ...(error?.code ? { code: error.code } : {}),
      ...(error?.message ? { reason: boundedText(error.message, 512) } : {}),
    };
    delete receipt.dispatched;
    if (!this.actionReceipts.has(receipt.action_id)) {
      if (this.actionReceipts.size >= MAX_ACTION_RECEIPTS) {
        const oldest = this.actionReceipts.keys().next().value;
        if (oldest !== undefined) this.actionReceipts.delete(oldest);
      }
    }
    this.actionReceipts.set(receipt.action_id, receipt);
    this.queueActionStatePersist();
    return receipt;
  }

  observedPageGeneration(pageId) {
    const record = pageId ? this.tabs.getInternalByPage(pageId) : undefined;
    if (!record) return undefined;
    return {
      target_generation: record.targetGeneration,
      navigation_generation: record.navigationGeneration,
      document_generation: record.documentGeneration,
      browser_session_epoch: record.sessionEpoch,
    };
  }

  postconditionSatisfied(condition, observed, snapshotHash = undefined) {
    if (!condition) return undefined;
    if (condition.kind === "page_generation")
      return observed?.document_generation === condition.document_generation;
    if (condition.kind === "snapshot_hash")
      return snapshotHash === condition.snapshot_hash;
    return false;
  }

  actionReceiptFor(actionId, overrides = {}) {
    const context = {
      ...(this.actionReceipts.get(actionId) ?? {}),
      ...(this.inflight.get(actionId) ?? {}),
      ...overrides,
    };
    const observed = this.observedPageGeneration(
      context.page_id ?? context.pageId,
    );
    const output = {
      ...(context.action_id ? { action_id: context.action_id } : {}),
      ...((context.request_id ?? context.requestId)
        ? { request_id: context.request_id ?? context.requestId }
        : {}),
      ...((context.space_id ?? context.spaceId)
        ? { space_id: context.space_id ?? context.spaceId }
        : {}),
      ...((context.page_id ?? context.pageId)
        ? { page_id: context.page_id ?? context.pageId }
        : {}),
      ...((context.lease_epoch ?? context.leaseEpoch)
        ? { lease_epoch: context.lease_epoch ?? context.leaseEpoch }
        : {}),
      outcome: context.outcome ?? "unknown",
      browser_session_epoch: this.metadata.browserSessionEpoch,
      ...(observed ?? {}),
      ...(context.code ? { code: context.code } : {}),
      ...(context.reason ? { reason: boundedText(context.reason, 512) } : {}),
    };
    output.postcondition_observed = observed ?? null;
    if (context.postcondition) {
      output.postcondition = context.postcondition;
      output.postcondition_satisfied = this.postconditionSatisfied(
        context.postcondition,
        observed,
      );
    }
    return output;
  }

  emitActionReceipt(actionId, context, outcome, error = undefined) {
    const stored = this.rememberActionReceipt(context, outcome, error);
    const receipt = this.actionReceiptFor(actionId, {
      ...(stored ?? context ?? {}),
      outcome,
      ...(error?.code ? { code: error.code } : {}),
      ...(error?.message ? { reason: boundedText(error.message, 512) } : {}),
    });
    this.handleExtensionEvent("action.receipt", receipt);
    return receipt;
  }

  withActionReceipt(result, receipt) {
    if (isPlainObject(result)) return { ...result, receipt };
    return { value: result, receipt };
  }

  async registerInflightAction(actionId, pending) {
    if (!validActionId(actionId)) return;
    if (this.inflight.has(actionId))
      throw new ProtocolError(
        "replay_rejected",
        "mutation action is already in flight",
      );
    if (this.inflight.size >= MAX_PERSISTED_INFLIGHT_ACTIONS)
      throw new ProtocolError(
        "resource_exhausted",
        "in-flight mutation journal is full",
      );
    this.inflight.set(actionId, pending);
    try {
      await this.persistActionState();
    } catch (error) {
      this.inflight.delete(actionId);
      this.queueActionStatePersist();
      throw new ProtocolError(
        "state_unavailable",
        "mutation outcome journal is unavailable",
        { cause: error instanceof Error ? error.message : String(error) },
      );
    }
  }

  completeInflightAction(actionId) {
    if (!actionId) return;
    this.inflight.delete(actionId);
    this.queueActionStatePersist();
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
    const installed = this.chrome?.runtime?.onInstalled;
    if (installed?.addListener) {
      const listener = (details = {}) => {
        if (details.reason !== "update" && details.reason !== "chrome_update")
          return;
        this.lifecycleToken += 1;
        for (const actionId of this.inflight.keys())
          this.reportUnknownAction(
            actionId,
            "extension update interrupted the mutation",
            this.inflight.get(actionId),
          );
        this.inflight.clear();
        this.queueActionStatePersist();
        this.handleExtensionEvent("extension.updated", {
          reason: details.reason,
          extension_version: VERSION,
          recovery_required: true,
        });
      };
      installed.addListener(listener);
      this.sidePanelListeners.push(() => installed.removeListener?.(listener));
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
    for (const actionId of this.inflight.keys())
      this.reportUnknownAction(
        actionId,
        "service worker stopped",
        this.inflight.get(actionId),
      );
    this.queueActionStatePersist();
    for (const remove of this.sidePanelListeners.splice(0)) remove();
    this.runtimeListenersInstalled = false;
    this.startPromise = null;
    this.debugger.stop();
    this.tabs.stop();
    this.groups.stop();
    this.native.stop();
    this.lifecycleToken += 1;
    for (const pending of this.pending.values())
      pending.reject?.(
        new ProtocolError(
          "native_host_unavailable",
          "service worker stopped before the host response arrived",
        ),
      );
    this.pending.clear();
    this.mutationTails.clear();
    for (const pending of this.contentPending.values())
      pending.reject?.(
        new ProtocolError(
          "native_host_unavailable",
          "service worker stopped before the content result arrived",
        ),
      );
    this.contentPending.clear();
    this.contentDocuments.clear();
    this.snapshotVersions.clear();
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
        if (pending.rawTabId === tabId) {
          this.contentPending.delete(requestId);
          pending.reject?.(
            new ProtocolError(
              "stale_generation",
              "content document changed before the result arrived",
            ),
          );
        }
      }
    }
    if (["removed", "session_reset", "replaced"].includes(kind))
      this.queueManagedBindingsPersist();
  }

  async advanceBrowserSession(reason = "extension_lifecycle") {
    if (this.sessionAdvancePromise) return this.sessionAdvancePromise;
    this.sessionAdvancePromise = (async () => {
      const priorBrowserSessionEpoch = this.metadata.browserSessionEpoch;
      const retainedBindings = this.tabs
        .inventory()
        .filter(
          (record) =>
            record?.ownership === "agent" &&
            record?.lifecycle === "managed" &&
            record?.binding_state === "bound" &&
            typeof record.space_id === "string" &&
            typeof record.page_id === "string" &&
            typeof record.tab_hint === "string" &&
            Number.isSafeInteger(record.lease_epoch),
        )
        .slice(0, MAX_PERSISTED_MANAGED_BINDINGS)
        .map((record) => ({
          space_id: record.space_id,
          page_id: record.page_id,
          lease_epoch: record.lease_epoch,
          tab_hint: record.tab_hint,
          target_generation: record.target_generation,
          navigation_generation: record.navigation_generation,
          document_generation: record.document_generation,
          browser_session_epoch: record.browser_session_epoch,
          url: record.url,
          title: record.title,
        }));
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
      for (const pending of this.contentPending.values())
        pending.reject?.(
          new ProtocolError(
            "stale_epoch",
            "browser session changed before the content result arrived",
          ),
        );
      this.contentPending.clear();
      this.contentDocuments.clear();
      this.snapshotVersions.clear();
      this.usedSidePanelTickets.clear();
      this.safetyCounters = { userTabCloses: 0, focusTheft: 0 };
      this.debugger.resetSession(nextEpoch);
      this.tabs.resetSession(nextEpoch);
      this.metadata.browserSessionEpoch = nextEpoch;
      this.native.browserSessionEpoch = nextEpoch;
      this.applyRuntimeIdentity();
      await storageSet(this.storage, {
        [METADATA_KEY]: {
          profile_instance_id: this.metadata.profileInstanceId,
          worker_instance_epoch: this.metadata.workerInstanceEpoch,
          browser_session_epoch: nextEpoch,
          ui_version: VERSION,
        },
      });
      await this.persistSafetyCounters().catch(() => {});
      if (this.sessionStorage?.set)
        await storageSet(this.sessionStorage, {
          [SESSION_MARKER_KEY]: {
            profile_instance_id: this.metadata.profileInstanceId,
            browser_session_epoch: nextEpoch,
          },
        }).catch(() => {});
      this.browserSessionFresh = false;
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
      await new Promise((resolve) => globalThis.setTimeout(resolve, 250));
      await this.tabs.refreshSession();
      const storedBindings = await storageGet(this.storage, [
        MANAGED_BINDINGS_KEY,
      ]).catch(() => ({}));
      const persistedBindings = storedBindings[MANAGED_BINDINGS_KEY];
      this.storedManagedBindings =
        Array.isArray(persistedBindings?.bindings) &&
        persistedBindings.bindings.length > 0
          ? persistedBindings
          : {
              profile_instance_id: this.metadata.profileInstanceId,
              browser_session_epoch: priorBrowserSessionEpoch,
              bindings: retainedBindings,
            };
      await this.rehydrateManagedBindings();
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
    if (state === "connected") {
      if (this.nativeConnectedOnce) this.recoveryObserved = true;
      this.nativeConnectedOnce = true;
    }
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
        pending,
      );
      const pendingRequestId = pending?.request_id ?? pending?.requestId;
      if (pendingRequestId) this.pending.delete(pendingRequestId);
    }
    if (actionIds.length > 0) this.queueActionStatePersist();
  }

  /**
   * Unknown outcomes are never replayed. When the host cannot hear the event
   * (the transport is the thing that was lost), the id is held, bounded, and
   * reported in the next inventory so the host can reconcile it.
   */
  reportUnknownAction(actionId, reason, pending = undefined) {
    if (validActionId(actionId)) {
      if (this.unreportedUnknownActions.size < MAX_UNREPORTED_UNKNOWN_ACTIONS)
        this.unreportedUnknownActions.add(actionId);
      else this.unknownActionsOverflow = true;
      const context = pending ?? this.actionReceipts.get(actionId);
      this.rememberActionReceipt(
        context ?? { action_id: actionId },
        "unknown",
        {
          code: "unknown_outcome",
          message: reason instanceof Error ? reason.message : String(reason),
        },
      );
      this.queueActionStatePersist();
      const receipt = this.actionReceiptFor(actionId, {
        ...(context ?? {}),
        outcome: "unknown",
        code: "unknown_outcome",
        reason: reason instanceof Error ? reason.message : String(reason),
      });
      this.handleExtensionEvent("action.receipt", receipt);
      this.handleExtensionEvent("action.unknown", receipt);
      return receipt;
    }
    this.handleExtensionEvent("action.unknown", {
      action_id: actionId,
      outcome: "unknown",
      code: "unknown_outcome",
      reason: boundedText(
        reason instanceof Error ? reason.message : String(reason),
        512,
      ),
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
      .map((page, index) => ({
        page:
          page.ownership === "unmanaged"
            ? (() => {
                const safe = { ...page };
                delete safe.url;
                delete safe.title;
                return safe;
              })()
            : page,
        index,
      }))
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
      safety: {
        measurement_status: "measured_live",
        current_run: true,
        user_tab_closes: this.safetyCounters.userTabCloses,
        focus_theft: this.safetyCounters.focusTheft,
      },
      recovery_observed: this.recoveryObserved,
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
      this.queueActionStatePersist();
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
        if (typeof message.request_id === "string") {
          const pending = this.pending.get(message.request_id);
          if (pending) {
            this.pending.delete(message.request_id);
            if (pending.timeout) clearTimeout(pending.timeout);
            pending.resolve(
              message.ok === true
                ? { ok: true, result: message.result ?? {} }
                : {
                    ok: false,
                    error:
                      message.error ??
                      errorResult("host_error", "host request failed"),
                  },
            );
          }
        }
        const forwarded = { ...message };
        if (
          forwarded.result?.control_ticket &&
          typeof forwarded.result.control_ticket === "object"
        ) {
          forwarded.result = {
            ...forwarded.result,
            control_ticket: {
              ...forwarded.result.control_ticket,
              ...(forwarded.result.control_ticket.token !== undefined
                ? { token: "<redacted>" }
                : {}),
            },
          };
        }
        this.forwardHostEvent({
          event: "host.response",
          payload: forwarded,
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
    const actionId = message.action_id ?? params.action_id;
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

    if (actionId !== undefined && !validActionId(actionId))
      throw new ProtocolError("schema_invalid", "action_id is invalid");
    let journaled = false;
    const actionContext = actionId
      ? this.actionContextFor({
          actionId,
          requestId,
          method,
          params,
          spaceId,
          pageId,
          leaseEpoch,
          message,
        })
      : undefined;
    if (actionContext) {
      await this.registerInflightAction(actionId, actionContext);
      journaled = true;
    }
    try {
      const operationToken = this.lifecycleToken;
      const result = await this.executeHostMethod({
        method,
        params,
        spaceId,
        pageId,
        leaseEpoch,
        requestId,
        actionId,
        message,
        operationToken,
      });
      if (!this.isLiveToken(operationToken)) {
        const error = new ProtocolError(
          "unknown_outcome",
          "service worker stopped before the mutation outcome was committed",
        );
        error.outcome = "unknown";
        error.retryable = false;
        throw error;
      }
      const receipt =
        mutation && actionId
          ? this.emitActionReceipt(actionId, actionContext, "succeeded")
          : undefined;
      const resultWithReceipt =
        receipt === undefined
          ? result
          : this.withActionReceipt(result, receipt);
      const sent = this.reply({
        requestId,
        actionId,
        ok: true,
        result: resultWithReceipt,
        mutation,
      });
      if (journaled && sent) this.completeInflightAction(actionId);
      return { ok: true, result: resultWithReceipt };
    } catch (error) {
      const safeError = publicError(error);
      const outcome = safeError.outcome === "unknown" ? "unknown" : "failed";
      if (mutation && actionId)
        this.emitActionReceipt(actionId, actionContext, outcome, safeError);
      const sent = this.reply({
        requestId,
        actionId,
        ok: false,
        error: safeError,
        mutation,
      });
      if (journaled && sent) this.completeInflightAction(actionId);
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
    operationToken = this.lifecycleToken,
  }) {
    switch (method) {
      case "snapshot.read":
        return this.readSnapshot({
          spaceId,
          pageId,
          leaseEpoch,
          params,
          requestId,
        });
      case "action.reconcile":
        return this.reconcileAction({
          spaceId,
          pageId,
          leaseEpoch,
          params,
          requestId,
          actionId,
        });
      case "action.execute":
        return this.executeAllowlistedAction({
          spaceId,
          pageId,
          leaseEpoch,
          params,
          requestId,
          actionId,
        });
      case "tab.inventory":
      case "page.list":
        return this.boundedInventory();
      case "page.create": {
        const record = await this.tabs.createAgentPage({
          spaceId,
          pageId,
          leaseEpoch,
          url: params.url,
          title: params.title,
          ownershipProof: params.ownership_proof,
          windowHint: params.window_hint,
          onDispatch: () => this.markActionDispatched(actionId),
          isLive: () => this.isLiveToken(operationToken),
        });
        await this.persistManagedBindings().catch(() => {});
        return record;
      }
      case "page.adopt": {
        const record = this.tabs.adoptExistingTab({
          tabHint: params.tab_hint,
          spaceId,
          pageId,
          leaseEpoch,
          ownershipProof: params.ownership_proof,
          intentTicket: params.intent_ticket,
          onDispatch: () => this.markActionDispatched(actionId),
        });
        await this.persistManagedBindings().catch(() => {});
        return record;
      }
      case "page.rebind": {
        const record = await this.tabs.rebindManagedTab({
          spaceId,
          pageId,
          leaseEpoch,
          targetGeneration: params.target_generation,
          navigationGeneration: params.navigation_generation,
          documentGeneration: params.document_generation,
          ownershipProof: params.ownership_proof,
        });
        await this.persistManagedBindings().catch(() => {});
        return record;
      }
      case "page.close": {
        const result = await this.tabs.closeManagedPage({
          spaceId,
          pageId,
          leaseEpoch,
          expectedGeneration: params.expected_generation,
          expectedNavigationGeneration: params.expected_navigation_generation,
          expectedDocumentGeneration: params.expected_document_generation,
          cleanupProof: params.cleanup_proof,
          onDispatch: () => this.markActionDispatched(actionId),
        });
        await this.persistManagedBindings().catch(() => {});
        return result;
      }
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
      case "debugger.command":
        return this.executeDebuggerCommand({
          spaceId,
          pageId,
          leaseEpoch,
          params,
          requestId,
          actionId,
        });
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
      case "event.wait":
      case "storage.write":
      case "cookies.write":
      case "page.upload":
        throw new ProtocolError(
          "capability_unavailable",
          `extension method is not implemented: ${method}`,
        );
      default:
        throw new ProtocolError(
          "capability_unavailable",
          `extension method is not implemented: ${method}`,
        );
    }
  }

  async executeDebuggerCommand({
    spaceId,
    pageId,
    leaseEpoch,
    params,
    requestId,
    actionId,
  }) {
    const debuggerMethod = params.method;
    this.assertFence(spaceId, leaseEpoch);
    await this.debugger.attach({ spaceId, pageId, leaseEpoch });
    return this.debugger.sendCommand({
      spaceId,
      pageId,
      leaseEpoch,
      method: debuggerMethod,
      params: coerceDebuggerParams(debuggerMethod, commandParams(params)),
      expectedGeneration: params.expected_generation,
      expectedTargetGeneration: params.expected_target_generation,
      expectedNavigationGeneration: params.expected_navigation_generation,
      expectedDocumentGeneration: params.expected_document_generation,
      commandId: params.command_id ?? actionId ?? requestId,
      capability: params.capability ?? params.payload?.capability,
      approval: params.approval ?? params.payload?.approval,
      onDispatch: () => this.markActionDispatched(actionId),
    });
  }

  actionRoute(params) {
    const payload = isPlainObject(params.payload) ? params.payload : {};
    let operation =
      params.operation ?? params.action ?? payload.operation ?? payload.action;
    if (typeof operation === "string")
      operation = operation.toLowerCase().replaceAll("-", "_");
    const methodToOperation = Object.fromEntries(
      Object.entries(ACTION_METHODS).map(([name, method]) => [method, name]),
    );
    if (operation === undefined && typeof params.method === "string")
      operation = methodToOperation[params.method];
    if (operation === "close")
      return { operation, method: "page.close", commandParams: {} };
    const method = ACTION_METHODS[operation];
    if (!method || (params.method !== undefined && params.method !== method))
      throw new ProtocolError(
        "capability_unavailable",
        "action operation is not allowlisted",
      );
    const command = coerceDebuggerParams(
      method,
      commandParams({ ...params, method }),
    );
    if (method === "Page.navigate") {
      if (
        typeof command.url !== "string" ||
        command.url.length === 0 ||
        command.url.length > 4096
      )
        throw new ProtocolError(
          "schema_invalid",
          "navigate action requires a bounded URL",
        );
    }
    if (method === "Input.insertText") {
      if (typeof command.text !== "string" || command.text.length > 64 * 1024)
        throw new ProtocolError(
          "schema_invalid",
          "input action requires bounded text",
        );
    }
    if (method === "Input.dispatchMouseEvent") {
      const type = command.type;
      const x = scalarNumber(command.x);
      const y = scalarNumber(command.y);
      if (
        !["mousePressed", "mouseReleased", "mouseWheel"].includes(type) ||
        x === undefined ||
        y === undefined
      )
        throw new ProtocolError(
          "schema_invalid",
          "pointer action requires an allowlisted event and coordinates",
        );
      command.x = x;
      command.y = y;
      for (const key of ["deltaX", "deltaY", "clickCount"]) {
        if (command[key] !== undefined) {
          const value = scalarNumber(command[key]);
          if (value === undefined)
            throw new ProtocolError(
              "schema_invalid",
              `pointer action field ${key} is invalid`,
            );
          command[key] = value;
        }
      }
    }
    if (method === "Input.dispatchKeyEvent") {
      if (
        typeof command.type !== "string" ||
        !["keyDown", "keyUp", "rawKeyDown", "char"].includes(command.type)
      )
        throw new ProtocolError(
          "schema_invalid",
          "key action type is not allowlisted",
        );
    }
    return { operation, method, commandParams: command };
  }

  async executeAllowlistedAction({
    spaceId,
    pageId,
    leaseEpoch,
    params,
    requestId,
    actionId,
  }) {
    const route = this.actionRoute(params);
    if (route.method === "page.close") {
      return this.tabs.closeManagedPage({
        spaceId,
        pageId,
        leaseEpoch,
        expectedGeneration: params.expected_generation,
        expectedNavigationGeneration: params.expected_navigation_generation,
        expectedDocumentGeneration: params.expected_document_generation,
        cleanupProof: params.cleanup_proof ?? params.payload?.cleanup_proof,
        onDispatch: () => this.markActionDispatched(actionId),
      });
    }
    await this.debugger.attach({ spaceId, pageId, leaseEpoch });
    return this.debugger.sendCommand({
      spaceId,
      pageId,
      leaseEpoch,
      method: route.method,
      params: route.commandParams,
      expectedGeneration: params.expected_generation,
      expectedTargetGeneration: params.expected_target_generation,
      expectedNavigationGeneration: params.expected_navigation_generation,
      expectedDocumentGeneration: params.expected_document_generation,
      commandId: params.command_id ?? actionId ?? requestId,
      capability: params.capability ?? params.payload?.capability,
      approval: params.approval ?? params.payload?.approval,
      onDispatch: () => this.markActionDispatched(actionId),
    });
  }

  async readContentOperation({
    spaceId,
    pageId,
    leaseEpoch,
    operation,
    payload = {},
    requestId,
  }) {
    const response = await this.sendContentRequest({
      spaceId,
      pageId,
      leaseEpoch,
      params: { operation, payload },
      requestId: createLogicalId("req"),
      waitForResult: true,
    });
    return response.result ?? {};
  }

  contentSnapshotElements(content) {
    const elements = [
      {
        key: "element_root",
        parent: null,
        kind: "root",
        text: null,
        attributes: {},
        order: 0,
      },
    ];
    const title = boundedText(content.title, 512);
    const text = boundedText(content.text, 16 * 1024);
    elements.push({
      key: "element_title",
      parent: "element_root",
      kind: "element",
      text: title,
      attributes: { role: "title" },
      order: 1,
    });
    elements.push({
      key: "element_body_text",
      parent: "element_root",
      kind: "text",
      text,
      attributes: {},
      order: 2,
    });
    for (const [index, node] of (Array.isArray(content.nodes)
      ? content.nodes
      : []
    )
      .slice(0, MAX_SNAPSHOT_ELEMENTS - elements.length)
      .entries()) {
      if (!isPlainObject(node)) continue;
      const attributes = {};
      if (typeof node.role === "string")
        attributes.role = boundedText(node.role, 64);
      if (typeof node.disabled === "boolean")
        attributes.disabled = String(node.disabled);
      elements.push({
        key: `element_control_${index}`,
        parent: "element_root",
        kind: "control",
        text: boundedText(node.name, 256),
        attributes,
        order: elements.length,
      });
    }
    return {
      elements,
      truncated:
        title.length >= 512 ||
        text.length >= 16 * 1024 ||
        (Array.isArray(content.nodes) && content.nodes.length >= 256),
    };
  }

  debuggerSnapshotElements(result) {
    const document = result?.result?.documents?.[0];
    if (!document || !isPlainObject(document) || !isPlainObject(document.nodes))
      throw new ProtocolError(
        "capability_unavailable",
        "debugger did not return a logical DOM snapshot",
      );
    const strings = Array.isArray(result.result.strings)
      ? result.result.strings
      : [];
    const stringAt = (value) =>
      typeof value === "string"
        ? value
        : Number.isSafeInteger(value) && typeof strings[value] === "string"
          ? strings[value]
          : "";
    const nodes = Array.isArray(document.nodes.nodeName)
      ? document.nodes.nodeName.map((_, index) => index)
      : [];
    const elements = [];
    for (const index of nodes.slice(0, MAX_SNAPSHOT_ELEMENTS)) {
      const nodeType = document.nodes.nodeType?.[index];
      const nodeName = boundedText(
        stringAt(document.nodes.nodeName[index]),
        128,
      );
      const nodeValue = boundedText(
        stringAt(document.nodes.nodeValue?.[index]),
        16 * 1024,
      );
      const kind =
        nodeType === 9
          ? "root"
          : nodeType === 3
            ? "text"
            : nodeType === 1
              ? "element"
              : undefined;
      if (!kind) continue;
      const attributes = Object.create(null);
      const rawAttributes = document.nodes.attributes?.[index];
      if (Array.isArray(rawAttributes)) {
        for (let offset = 0; offset + 1 < rawAttributes.length; offset += 2) {
          const name = boundedText(stringAt(rawAttributes[offset]), 128);
          if (
            !name ||
            name === "__proto__" ||
            name === "constructor" ||
            name === "prototype"
          )
            continue;
          attributes[name] = boundedText(
            stringAt(rawAttributes[offset + 1]),
            1024,
          );
        }
      }
      const parentIndex = document.nodes.parentIndex?.[index];
      elements.push({
        key: `element_${index}`,
        parent:
          Number.isSafeInteger(parentIndex) && parentIndex >= 0
            ? `element_${parentIndex}`
            : null,
        kind,
        text: kind === "text" ? nodeValue : null,
        attributes,
        order: index,
      });
    }
    if (elements.length === 0)
      throw new ProtocolError(
        "capability_unavailable",
        "debugger DOM snapshot contained no logical nodes",
      );
    return {
      elements,
      truncated: nodes.length > MAX_SNAPSHOT_ELEMENTS,
    };
  }

  buildSnapshotEnvelope({ spaceId, pageId, record, elements, truncated }) {
    let boundedElements = elements
      .slice(0, MAX_SNAPSHOT_ELEMENTS)
      .sort(
        (left, right) =>
          left.order - right.order || left.key.localeCompare(right.key),
      );
    let wasTruncated = Boolean(truncated);
    const nextVersion = (this.snapshotVersions.get(pageId) ?? 0) + 1;
    this.snapshotVersions.set(pageId, nextVersion);
    const makeEnvelope = () => {
      const snapshotHash = snapshotElementsHash(boundedElements);
      return {
        schema_version: 1,
        space_id: spaceId,
        page_id: pageId,
        snapshot_version: nextVersion,
        snapshot_hash: snapshotHash,
        base_snapshot_version: null,
        base_hash: null,
        result_hash: snapshotHash,
        delta_sequence: null,
        topology_version: 1,
        navigation_generation: record.navigationGeneration,
        document_generation: record.documentGeneration,
        frame_versions: { frame_main: 1 },
        changed: [],
        delta_or_elements: { kind: "elements", elements: boundedElements },
        mode: wasTruncated ? "compact" : "full",
        operation_count: 0,
        coherent: !wasTruncated,
        coverage: wasTruncated ? "partial" : "complete",
        dirty_reasons: [],
        cache_state: "fresh",
        resync_reason: null,
        transport_bytes: 0,
        utf8_bytes: textBytes(
          `[${boundedElements.map((element) => JSON.stringify(element)).join(",")}]`,
        ),
        serialized_tokens: 0,
        model_context_tokens: 0,
        tokenizer: null,
        budget: null,
        omitted: wasTruncated ? ["bounded logical elements"] : [],
        truncated: wasTruncated,
        resync_required: false,
        refs_epoch: nextVersion,
      };
    };
    let envelope = makeEnvelope();
    while (
      textBytes(JSON.stringify(envelope)) > MAX_SNAPSHOT_BYTES &&
      boundedElements.length > 1
    ) {
      boundedElements = boundedElements.slice(0, -1);
      wasTruncated = true;
      envelope = makeEnvelope();
    }
    envelope.transport_bytes = textBytes(JSON.stringify(envelope));
    if (envelope.transport_bytes > MAX_SNAPSHOT_BYTES)
      throw new ProtocolError(
        "message_too_large",
        "logical snapshot exceeds the control bound",
      );
    assertNoRawBrowserIdentifiers(envelope);
    return envelope;
  }

  async readSnapshot({ spaceId, pageId, leaseEpoch, params, requestId }) {
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    const record = this.tabs.getInternalByPage(pageId);
    if (!record)
      throw new ProtocolError("page_not_found", "logical page is not bound");
    const effectiveLease = leaseEpoch ?? record.leaseEpoch;
    positiveEpoch(effectiveLease, "lease_epoch");
    const current = this.tabs.assertPageDispatch({
      spaceId,
      pageId,
      leaseEpoch: effectiveLease,
      expectedGeneration: params.expected_generation,
      expectedTargetGeneration: params.expected_target_generation,
      expectedNavigationGeneration: params.expected_navigation_generation,
      expectedDocumentGeneration: params.expected_document_generation,
      mutation: false,
    });
    const source = params.source;
    if (source !== undefined && source !== "content" && source !== "debugger")
      throw new ProtocolError(
        "capability_unavailable",
        "snapshot source is not allowlisted",
      );
    let logical;
    if (source !== "debugger") {
      try {
        const [title, text, aria] = await Promise.all([
          this.readContentOperation({
            spaceId,
            pageId,
            leaseEpoch: effectiveLease,
            operation: "document.title",
            requestId,
          }),
          this.readContentOperation({
            spaceId,
            pageId,
            leaseEpoch: effectiveLease,
            operation: "document.text",
            payload:
              typeof params.selector === "string"
                ? { selector: params.selector }
                : {},
            requestId,
          }),
          this.readContentOperation({
            spaceId,
            pageId,
            leaseEpoch: effectiveLease,
            operation: "aria.summary",
            requestId,
          }),
        ]);
        logical = this.contentSnapshotElements({
          title: title.title,
          text: text.text,
          nodes: aria.nodes,
        });
      } catch (error) {
        if (source === "content") throw error;
      }
    }
    if (!logical) {
      await this.debugger.attach({
        spaceId,
        pageId,
        leaseEpoch: effectiveLease,
      });
      const debuggerResult = await this.debugger.sendCommand({
        spaceId,
        pageId,
        leaseEpoch: effectiveLease,
        method: "DOMSnapshot.captureSnapshot",
        params: { computedStyles: [] },
        expectedTargetGeneration: current.targetGeneration,
        expectedNavigationGeneration: current.navigationGeneration,
        expectedDocumentGeneration: current.documentGeneration,
        commandId: requestId,
      });
      logical = this.debuggerSnapshotElements(debuggerResult);
    }
    const latest = this.tabs.assertPageDispatch({
      spaceId,
      pageId,
      leaseEpoch: effectiveLease,
      expectedTargetGeneration: current.targetGeneration,
      expectedNavigationGeneration: current.navigationGeneration,
      expectedDocumentGeneration: current.documentGeneration,
      mutation: false,
    });
    return this.buildSnapshotEnvelope({
      spaceId,
      pageId,
      record: latest,
      elements: logical.elements,
      truncated: logical.truncated,
    });
  }

  async reconcileAction({
    spaceId,
    pageId,
    leaseEpoch,
    params,
    requestId,
    actionId,
  }) {
    const reconciledActionId = params.action_id ?? params.actionId ?? actionId;
    if (!validActionId(reconciledActionId))
      throw new ProtocolError(
        "schema_invalid",
        "reconciliation action_id is invalid",
      );
    const stored = this.actionReceipts.get(reconciledActionId);
    const effectiveSpaceId = spaceId ?? stored?.space_id;
    const effectivePageId = pageId ?? stored?.page_id;
    assertLogicalScope(
      { spaceId: effectiveSpaceId, pageId: effectivePageId },
      { pageRequired: effectivePageId !== undefined },
    );
    if (stored?.space_id !== undefined && stored.space_id !== effectiveSpaceId)
      throw new ProtocolError(
        "permission_denied",
        "reconciliation space is not current",
      );
    if (stored?.page_id !== undefined && stored.page_id !== effectivePageId)
      throw new ProtocolError(
        "permission_denied",
        "reconciliation page is not current",
      );
    const record = effectivePageId
      ? this.tabs.getInternalByPage(effectivePageId)
      : undefined;
    const effectiveLease =
      leaseEpoch ?? record?.leaseEpoch ?? stored?.lease_epoch;
    if (effectiveLease !== undefined)
      positiveEpoch(effectiveLease, "lease_epoch");
    if (effectiveLease !== undefined)
      this.assertFence(effectiveSpaceId, effectiveLease);
    if (record) {
      if (
        effectiveLease === undefined ||
        record.spaceId !== effectiveSpaceId ||
        record.pageId !== effectivePageId
      )
        throw new ProtocolError(
          "stale_lease",
          "reconciliation lease is not current",
        );
      this.tabs.assertPageDispatch({
        spaceId: effectiveSpaceId,
        pageId: effectivePageId,
        leaseEpoch: effectiveLease,
        mutation: false,
      });
    }
    const context = {
      ...(stored ?? {}),
      action_id: reconciledActionId,
      request_id: stored?.request_id ?? requestId,
      ...(effectiveSpaceId !== undefined ? { space_id: effectiveSpaceId } : {}),
      ...(effectivePageId !== undefined ? { page_id: effectivePageId } : {}),
      ...(effectiveLease !== undefined ? { lease_epoch: effectiveLease } : {}),
      postcondition:
        stored?.postcondition ?? normalizePostcondition(params.postcondition),
    };
    const finish = (outcome, code = undefined, evidence = undefined) => {
      const error = code ? { code, message: code } : undefined;
      this.rememberActionReceipt(context, outcome, error);
      const receipt = this.actionReceiptFor(reconciledActionId, {
        ...context,
        outcome,
        ...(code ? { code } : {}),
      });
      this.handleExtensionEvent("action.receipt", receipt);
      this.handleExtensionEvent("action.reconciled", {
        ...receipt,
        ...(evidence ? { evidence } : {}),
      });
      return {
        outcome,
        receipt,
        ...(evidence ? { evidence } : {}),
      };
    };
    if (stored?.outcome === "succeeded" || stored?.outcome === "failed")
      return finish(stored.outcome, stored.code);
    if (!record) {
      // A missing logical mapping is not positive proof that a close reached
      // Chrome. The target may still exist after a lost response or may have
      // been replaced. Keep the outcome unknown until a later inventory or
      // host-side reconciliation proves absence.
      return finish("unknown", "unknown_outcome");
    }
    const observed = this.observedPageGeneration(effectivePageId);
    const postcondition = context.postcondition;
    if (postcondition?.kind === "page_generation") {
      return finish(
        observed?.document_generation === postcondition.document_generation
          ? "succeeded"
          : "failed",
        observed?.document_generation === postcondition.document_generation
          ? undefined
          : "target_replaced",
        { generation: observed },
      );
    }
    if (postcondition?.kind === "snapshot_hash") {
      try {
        const snapshot = await this.readSnapshot({
          spaceId: effectiveSpaceId,
          pageId: effectivePageId,
          leaseEpoch: effectiveLease,
          params: {
            expected_target_generation: record.targetGeneration,
            expected_navigation_generation: record.navigationGeneration,
            expected_document_generation: record.documentGeneration,
          },
          requestId,
        });
        const matches = snapshot.snapshot_hash === postcondition.snapshot_hash;
        return finish(
          matches ? "succeeded" : "failed",
          matches ? undefined : "target_replaced",
          {
            generation: this.observedPageGeneration(effectivePageId),
            snapshot_hash: snapshot.snapshot_hash,
          },
        );
      } catch {
        return finish("unknown", "unknown_outcome");
      }
    }
    if (
      (stored?.method === "debugger.command" ||
        stored?.method === "action.execute") &&
      stored.navigation_url &&
      stored.navigation_generation !== undefined &&
      record.navigationGeneration > stored.navigation_generation &&
      record.url === stored.navigation_url
    )
      return finish("succeeded", undefined, { generation: observed });
    return finish("unknown", "unknown_outcome", { generation: observed });
  }

  async sendContentRequest({
    spaceId,
    pageId,
    leaseEpoch,
    params,
    requestId,
    onDispatch = () => {},
    waitForResult = false,
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
    let resolveResult;
    let rejectResult;
    const resultPromise = waitForResult
      ? new Promise((resolve, reject) => {
          resolveResult = resolve;
          rejectResult = reject;
        })
      : undefined;
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
      resolve: resolveResult,
      reject: rejectResult,
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
    const accepted = {
      accepted: true,
      request_id: requestId,
      expires_at: expiresAt,
    };
    if (!waitForResult) return accepted;
    let timeoutHandle;
    try {
      const timeoutPromise = new Promise((_, reject) => {
        timeoutHandle = setTimeout(() => {
          const pendingEntry = this.contentPending.get(requestId);
          if (pendingEntry) this.contentPending.delete(requestId);
          reject(
            new ProtocolError(
              "capability_unavailable",
              "content bridge response timed out",
            ),
          );
        }, CONTENT_RESPONSE_TIMEOUT_MS);
        timeoutHandle?.unref?.();
      });
      const result = await Promise.race([resultPromise, timeoutPromise]);
      if (!result?.ok)
        throw new ProtocolError(
          result?.error?.code ?? "content_bridge_error",
          result?.error?.message ?? "content operation failed",
        );
      return { ...accepted, result: result.result ?? {} };
    } finally {
      if (timeoutHandle) clearTimeout(timeoutHandle);
    }
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
    if (fenceEpoch < current) {
      const error = errorResult(
        "stale_fence",
        "fence barrier is older than the live barrier",
      );
      this.reply({ requestId, actionId, ok: false, error, mutation: false });
      return { ok: false, error };
    }
    if (fenceEpoch === current) {
      const result = {
        space_id: spaceId,
        fence_epoch: fenceEpoch,
        drained_request_ids: [],
        unknown_action_ids: [],
        affected_page_count: this.tabs.pagesForSpace(spaceId).length,
        durable: true,
        idempotent: true,
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
        // The host keeps the fence pending when the acknowledgement is lost.
      }
      return { ok: true, result };
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
    for (const actionId of unknownActionIds)
      this.reportUnknownAction(actionId, "fence interrupted mutation");
    this.queueActionStatePersist();
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
      return true;
    } catch (sendError) {
      if (actionId && mutation && this.inflight.has(actionId)) {
        this.handleLostDispatch([actionId], sendError);
      }
      return false;
    }
  }

  handleExtensionEvent(event, payload = {}) {
    const sourcePayload =
      payload && typeof payload === "object" ? payload : { value: payload };
    if (event === "tab.closed" && sourcePayload.ownership === "unmanaged") {
      this.safetyCounters.userTabCloses += 1;
      this.queueSafetyCountersPersist();
    }
    if (
      event === "page.focus_changed" &&
      sourcePayload.ownership === "agent" &&
      sourcePayload.active === true
    ) {
      this.safetyCounters.focusTheft += 1;
      this.queueSafetyCountersPersist();
    }
    let safePayload =
      payload && typeof payload === "object"
        ? { ...payload }
        : { value: payload };
    if (safePayload.ownership === "unmanaged") {
      // User-tab URLs and titles are not needed for safety receipts and must
      // not cross the Native Messaging/UI boundary.
      delete safePayload.url;
      delete safePayload.title;
    }
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
      if (pending.expiresAt <= now) {
        this.contentPending.delete(requestId);
        pending.reject?.(
          new ProtocolError(
            "proof_expired",
            "content result expired before it arrived",
          ),
        );
      }
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
        if (pending.rawTabId === record.rawTabId) {
          this.contentPending.delete(requestId);
          pending.reject?.(
            new ProtocolError(
              "stale_generation",
              "content document was closed before the result arrived",
            ),
          );
        }
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
      this.contentPending.delete(message.request_id);
      pending.reject?.(error);
      return { ok: false, error: publicError(error) };
    }
    this.contentPending.delete(message.request_id);
    const settled = message.ok
      ? { ok: true, result: message.result ?? {} }
      : { ok: false, error: message.error };
    this.handleExtensionEvent("content.result", {
      space_id: pending.spaceId,
      page_id: pending.pageId,
      request_id: message.request_id,
      ok: Boolean(message.ok),
      ...(message.ok
        ? { result: message.result ?? {} }
        : { error: message.error }),
    });
    pending.resolve?.(settled);
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

  async handleSidePanelRequest(message) {
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
      const method =
        action === "stop" || action === "pause" || action === "handoff"
          ? "space.return_control"
          : action === "retain"
            ? "space.takeover"
            : `space.${action}`;
      const actionId = createLogicalId("action");
      const requestParams = {
        ...params,
        ...(message.intent_ticket
          ? { intent_ticket: message.intent_ticket }
          : {}),
      };
      const responsePromise = new Promise((resolve) => {
        const timeout = setTimeout(() => {
          if (!this.pending.has(requestId)) return;
          this.pending.delete(requestId);
          resolve({
            ok: false,
            error: errorResult("timeout", "host side-panel request timed out"),
          });
        }, CONTENT_RESPONSE_TIMEOUT_MS);
        timeout?.unref?.();
        this.pending.set(requestId, { resolve, timeout });
      });
      try {
        this.native.sendRequest({
          method,
          params: requestParams,
          requestId,
          actionId,
          mutation: true,
        });
      } catch (error) {
        const pending = this.pending.get(requestId);
        this.pending.delete(requestId);
        if (pending?.timeout) clearTimeout(pending.timeout);
        return { ok: false, error: publicError(error) };
      }
      return await responsePromise;
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
