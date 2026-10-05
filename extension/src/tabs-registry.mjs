import {
  ProtocolError,
  assertLogicalScope,
  browserHint,
  errorResult,
  isRestrictedUrl,
  publicError,
} from "./protocol.mjs";

function chromeApiOrGlobal(chromeApi) {
  return chromeApi ?? globalThis.chrome;
}

async function chromeCall(fn, ...args) {
  if (typeof fn !== "function")
    throw new ProtocolError(
      "capability_unavailable",
      "Chrome tabs API is unavailable",
    );
  return fn(...args);
}

const MAX_PROOF_ID = 128;
const MAX_PROOF_LIFETIME_MS = 24 * 60 * 60 * 1000;
const MAX_USED_PROOFS = 1024;

function logicalProof(
  proof,
  {
    spaceId,
    pageId,
    leaseEpoch,
    kind,
    purpose,
    requireExpiry = false,
    targetGeneration,
    tabHint,
    profileInstanceId,
    browserSessionEpoch,
    requireProfileInstanceId = false,
    requireBrowserSessionEpoch = false,
    now = Date.now(),
  } = {},
) {
  if (!proof || typeof proof !== "object" || Array.isArray(proof)) {
    throw new ProtocolError(
      "permission_denied",
      `${kind} requires a host-issued proof object`,
    );
  }
  if (
    proof.issued_by_host !== true ||
    typeof proof.proof_id !== "string" ||
    !/^[A-Za-z0-9._:-]{8,128}$/.test(proof.proof_id) ||
    proof.proof_id.length > MAX_PROOF_ID
  ) {
    throw new ProtocolError(
      "permission_denied",
      `${kind} proof is not host-issued`,
    );
  }
  if (
    proof.space_id !== spaceId ||
    (pageId !== undefined && proof.page_id !== pageId)
  ) {
    throw new ProtocolError(
      "permission_denied",
      `${kind} proof scope does not match the logical target`,
    );
  }
  if (leaseEpoch !== undefined && proof.lease_epoch !== leaseEpoch) {
    throw new ProtocolError(
      "stale_lease",
      `${kind} proof lease does not match the command`,
    );
  }
  const proofPurpose = proof.purpose ?? proof.kind;
  if (purpose !== undefined && proofPurpose !== purpose) {
    throw new ProtocolError(
      "permission_denied",
      `${kind} proof purpose is invalid`,
    );
  }
  const expiry = proof.expires_at ?? proof.expires_at_ms;
  if (expiry !== undefined) {
    if (
      !Number.isSafeInteger(expiry) ||
      expiry <= now ||
      expiry > now + MAX_PROOF_LIFETIME_MS
    ) {
      throw new ProtocolError(
        "proof_expired",
        `${kind} proof expiry is invalid`,
      );
    }
  } else if (requireExpiry) {
    throw new ProtocolError("proof_expired", `${kind} proof must expire`);
  }
  if (
    targetGeneration !== undefined &&
    (proof.target_generation ?? proof.generation) !== targetGeneration
  ) {
    throw new ProtocolError(
      "stale_generation",
      `${kind} proof generation is not current`,
    );
  }
  if (profileInstanceId !== undefined) {
    if (requireProfileInstanceId && proof.profile_instance_id === undefined) {
      throw new ProtocolError(
        "permission_denied",
        `${kind} proof profile is required`,
      );
    }
    if (
      proof.profile_instance_id !== undefined &&
      proof.profile_instance_id !== profileInstanceId
    ) {
      throw new ProtocolError(
        "permission_denied",
        `${kind} proof profile does not match the live profile`,
      );
    }
  }
  if (browserSessionEpoch !== undefined) {
    if (
      requireBrowserSessionEpoch &&
      proof.browser_session_epoch === undefined
    ) {
      throw new ProtocolError(
        "stale_epoch",
        `${kind} proof browser session is required`,
      );
    }
    if (
      proof.browser_session_epoch !== undefined &&
      proof.browser_session_epoch !== browserSessionEpoch
    ) {
      throw new ProtocolError(
        "stale_epoch",
        `${kind} proof browser session does not match`,
      );
    }
  }
  if (tabHint !== undefined && proof.tab_hint !== tabHint) {
    throw new ProtocolError(
      "permission_denied",
      `${kind} proof tab scope does not match`,
    );
  }
  return proof;
}

function safeText(value, max = 2048) {
  return typeof value === "string" ? value.slice(0, max) : undefined;
}

function unknownDispatch(message, details = undefined) {
  const error = new ProtocolError("unknown_outcome", message, details);
  error.outcome = "unknown";
  error.retryable = false;
  return error;
}

/**
 * Browser identity stays in this registry. Callers receive logical records,
 * not Chrome tab/window/group ids.
 */
export class TabsRegistry {
  constructor({
    chromeApi,
    groups,
    onEvent = () => {},
    hintSalt = "",
    now = () => Date.now(),
    profileInstanceId,
    browserSessionEpoch,
    assertFence = () => {},
    onLifecycle = () => {},
  } = {}) {
    this.chrome = chromeApiOrGlobal(chromeApi);
    this.groups = groups;
    this.onEvent = onEvent;
    this.hintSalt = hintSalt;
    this.now = now;
    this.profileInstanceId = profileInstanceId;
    this.browserSessionEpoch = browserSessionEpoch;
    this.assertFence = assertFence;
    this.onLifecycle = onLifecycle;
    this.byRawTab = new Map();
    this.byPage = new Map();
    this.windowHints = new Map();
    this.fenceEpochs = new Map();
    this.usedCleanupProofs = new Map();
    this.usedAdoptionProofs = new Map();
    this.usedIntentTickets = new Map();
    this.retiredRawTabIds = new Set();
    this.hostCreatedTabIds = new Set();
    this.removeListeners = [];
    this.listenersInstalled = false;
    this.started = false;
  }

  async start() {
    if (this.started) return;
    this.started = true;
    this.installListeners();
    if (this.chrome?.tabs?.query) {
      try {
        const tabs = await chromeCall(
          this.chrome.tabs.query.bind(this.chrome.tabs),
          {},
        );
        for (const tab of tabs ?? []) this.observeTab(tab, "startup");
      } catch (error) {
        this.onEvent("tabs.inventory_failed", {
          code: "tabs_unavailable",
          message: error instanceof Error ? error.message : String(error),
        });
      }
    }
  }

  stop() {
    for (const remove of this.removeListeners.splice(0)) remove();
    this.listenersInstalled = false;
    this.started = false;
  }

  setIdentity({ profileInstanceId, browserSessionEpoch, hintSalt } = {}) {
    if (profileInstanceId !== undefined)
      this.profileInstanceId = profileInstanceId;
    if (browserSessionEpoch !== undefined)
      this.browserSessionEpoch = browserSessionEpoch;
    if (typeof hintSalt === "string") {
      this.hintSalt = hintSalt;
      this.groups?.setHintSalt?.(hintSalt);
    }
  }

  pruneProofs() {
    const now = this.now();
    for (const used of [
      this.usedCleanupProofs,
      this.usedAdoptionProofs,
      this.usedIntentTickets,
    ]) {
      for (const [id, expiresAt] of used) {
        if (expiresAt <= now) used.delete(id);
      }
    }
  }

  assertProofCapacity(used, proofId) {
    this.pruneProofs();
    if (used.has(proofId)) {
      throw new ProtocolError("replay_rejected", "proof was already consumed");
    }
    if (used.size >= MAX_USED_PROOFS)
      throw new ProtocolError(
        "resource_exhausted",
        "proof replay cache is full",
      );
  }

  consumeProof(used, proofId, expiresAt) {
    this.assertProofCapacity(used, proofId);
    used.set(proofId, expiresAt);
  }

  resetSession(browserSessionEpoch) {
    if (browserSessionEpoch !== undefined)
      this.browserSessionEpoch = browserSessionEpoch;
    for (const record of this.byPage.values()) {
      this.retiredRawTabIds.add(record.rawTabId);
      this.onLifecycle("session_reset", record.rawTabId, record);
      record.lifecycle = "target_lost";
      record.bindingState = "lost";
      this.emit("page.lost", record, { reason: "browser_session_changed" });
    }
    this.byRawTab.clear();
    this.byPage.clear();
    this.windowHints.clear();
    this.hostCreatedTabIds.clear();
    this.usedCleanupProofs.clear();
    this.usedAdoptionProofs.clear();
    this.usedIntentTickets.clear();
  }

  async refreshSession() {
    if (!this.chrome?.tabs?.query) return;
    try {
      const tabs = await chromeCall(
        this.chrome.tabs.query.bind(this.chrome.tabs),
        {},
      );
      for (const tab of tabs ?? []) this.observeTab(tab, "session_refresh");
    } catch {
      this.onEvent("tabs.inventory_failed", {
        code: "tabs_unavailable",
        message: "Chrome tab inventory is unavailable",
      });
    }
  }

  installListeners() {
    if (this.listenersInstalled) return;
    const tabs = this.chrome?.tabs;
    if (!tabs) return;
    this.listenersInstalled = true;
    const whenStarted =
      (handler) =>
      (...args) => {
        if (!this.started) return;
        return handler(...args);
      };
    const onCreated = whenStarted((tab) => this.observeTab(tab, "created"));
    const onUpdated = whenStarted(
      (tabId, changeInfo, tab) =>
        void this.handleUpdated(tabId, changeInfo, tab),
    );
    const onRemoved = whenStarted((tabId, removeInfo) =>
      this.handleRemoved(tabId, removeInfo),
    );
    const onReplaced = whenStarted((addedTabId, removedTabId) =>
      this.handleReplaced(addedTabId, removedTabId),
    );
    const onAttached = whenStarted((tabId, attachInfo) =>
      this.handleAttached(tabId, attachInfo),
    );
    const onDetached = whenStarted((tabId, detachInfo) =>
      this.handleDetached(tabId, detachInfo),
    );
    const onActivated = whenStarted((activeInfo) =>
      this.handleActivated(activeInfo),
    );
    for (const [event, listener] of [
      [tabs.onCreated, onCreated],
      [tabs.onUpdated, onUpdated],
      [tabs.onRemoved, onRemoved],
      [tabs.onReplaced, onReplaced],
      [tabs.onAttached, onAttached],
      [tabs.onDetached, onDetached],
      [tabs.onActivated, onActivated],
    ]) {
      event?.addListener?.(listener);
      this.removeListeners.push(() => event?.removeListener?.(listener));
    }
  }

  observeTab(tab, reason = "observed") {
    if (!tab || !Number.isInteger(tab.id)) return undefined;
    let existing = this.byRawTab.get(tab.id);
    if (existing && reason === "created") {
      this.retiredRawTabIds.add(tab.id);
      this.onLifecycle("raw_id_reused", tab.id, existing);
      if (existing.pageId) this.byPage.delete(existing.pageId);
      this.byRawTab.delete(tab.id);
      existing = undefined;
    }
    if (
      existing &&
      this.browserSessionEpoch !== undefined &&
      existing.sessionEpoch !== this.browserSessionEpoch
    ) {
      this.retiredRawTabIds.add(tab.id);
      if (existing.pageId) this.byPage.delete(existing.pageId);
      this.byRawTab.delete(tab.id);
      existing = undefined;
    }
    const record = existing ?? {
      rawTabId: tab.id,
      rawWindowId: Number.isInteger(tab.windowId) ? tab.windowId : undefined,
      rawGroupId: Number.isInteger(tab.groupId) ? tab.groupId : undefined,
      spaceId: undefined,
      pageId: undefined,
      ownership: "unmanaged",
      lifecycle: "unmanaged",
      bindingState: "unbound",
      leaseEpoch: undefined,
      sessionEpoch: this.browserSessionEpoch,
      targetGeneration: 1,
      navigationGeneration: 1,
      documentGeneration: 1,
      generation: 1,
      url: undefined,
      title: undefined,
      incognito: Boolean(tab.incognito),
      discarded: Boolean(tab.discarded),
      frozen: Boolean(tab.frozen),
      active: Boolean(tab.active),
      createdAt: this.now(),
      lastObservedAt: this.now(),
      claimProofId: undefined,
    };
    record.rawWindowId = Number.isInteger(tab.windowId)
      ? tab.windowId
      : record.rawWindowId;
    record.rawGroupId = Number.isInteger(tab.groupId)
      ? tab.groupId
      : record.rawGroupId;
    record.url = safeText(tab.url, 4096) ?? record.url;
    record.title = safeText(tab.title, 512) ?? record.title;
    record.incognito = Boolean(tab.incognito ?? record.incognito);
    record.discarded = Boolean(tab.discarded ?? record.discarded);
    record.frozen = Boolean(tab.frozen ?? record.frozen);
    record.active = Boolean(tab.active ?? record.active);
    record.lastObservedAt = this.now();
    if (record.incognito && record.ownership === "agent") {
      const priorPageId = record.pageId;
      if (priorPageId) this.byPage.delete(priorPageId);
      record.ownership = "unmanaged";
      record.lifecycle = "unmanaged";
      record.bindingState = "rebind_required";
      record.spaceId = undefined;
      record.pageId = undefined;
      record.leaseEpoch = undefined;
    }
    this.byRawTab.set(tab.id, record);
    if (record.rawWindowId !== undefined) {
      this.windowHints.set(
        browserHint(record.rawWindowId, this.hintSalt),
        record.rawWindowId,
      );
    }
    this.groups?.observeTabMembership(record.rawTabId, record.rawGroupId);
    if (!existing) this.emit("tab.observed", record, { reason });
    return record;
  }

  async hydrateTab(tabId) {
    if (!this.chrome?.tabs?.get) return undefined;
    try {
      return this.observeTab(
        await chromeCall(this.chrome.tabs.get.bind(this.chrome.tabs), tabId),
        "hydrate",
      );
    } catch {
      return this.byRawTab.get(tabId);
    }
  }

  /** Bind only after a host-issued logical claim has been validated. */
  bindManagedTab({
    tab,
    tabId,
    spaceId,
    pageId,
    leaseEpoch,
    ownershipProof,
    generation = 1,
    targetGeneration,
    navigationGeneration,
    documentGeneration,
    url,
    title,
  } = {}) {
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    if (!Number.isSafeInteger(leaseEpoch) || leaseEpoch < 1) {
      throw new ProtocolError(
        "stale_lease",
        "managed tab binding requires a lease epoch",
      );
    }
    const proof = logicalProof(ownershipProof, {
      spaceId,
      pageId,
      leaseEpoch,
      kind: "page claim",
      profileInstanceId: this.profileInstanceId,
      browserSessionEpoch: this.browserSessionEpoch,
      now: this.now(),
    });
    const rawTabId = tabId ?? tab?.id;
    if (!Number.isInteger(rawTabId))
      throw new ProtocolError(
        "schema_invalid",
        "managed binding requires a tab",
      );
    if (
      this.retiredRawTabIds.has(rawTabId) &&
      !this.hostCreatedTabIds.has(rawTabId) &&
      proof.rebind !== true
    ) {
      throw new ProtocolError(
        "stale_target",
        "raw tab identity was retired and requires an explicit rebind",
      );
    }
    this.assertFence({ spaceId, leaseEpoch });
    const record = this.observeTab(
      { ...(tab ?? {}), id: rawTabId, url, title },
      "managed",
    );
    if (record.incognito)
      throw new ProtocolError(
        "incognito_not_supported",
        "incognito pages are not enrolled",
      );
    const priorPage = this.byPage.get(pageId);
    if (priorPage && priorPage.rawTabId !== rawTabId) {
      throw new ProtocolError(
        "ambiguous_binding",
        "logical page is already bound to another live tab",
      );
    }
    if (
      record.pageId !== undefined &&
      (record.pageId !== pageId || record.spaceId !== spaceId)
    ) {
      const canRebind =
        proof.rebind === true &&
        record.bindingState !== "bound" &&
        record.bindingState !== "user_owned";
      if (!canRebind) {
        throw new ProtocolError(
          "ambiguous_binding",
          "live tab is already bound to another logical page",
        );
      }
      this.byPage.delete(record.pageId);
    }
    this.byPage.set(pageId, record);
    record.spaceId = spaceId;
    record.pageId = pageId;
    record.ownership = "agent";
    record.lifecycle = "managed";
    record.bindingState = "bound";
    record.leaseEpoch = leaseEpoch;
    record.targetGeneration =
      Number.isSafeInteger(targetGeneration) && targetGeneration > 0
        ? targetGeneration
        : Number.isSafeInteger(generation) && generation > 0
          ? generation
          : record.targetGeneration;
    record.navigationGeneration =
      Number.isSafeInteger(navigationGeneration) && navigationGeneration > 0
        ? navigationGeneration
        : record.navigationGeneration;
    record.documentGeneration =
      Number.isSafeInteger(documentGeneration) && documentGeneration > 0
        ? documentGeneration
        : record.documentGeneration;
    record.generation = record.targetGeneration;
    record.sessionEpoch = this.browserSessionEpoch;
    record.claimProofId = proof.proof_id;
    this.hostCreatedTabIds.delete(rawTabId);
    this.groups?.claimTab(spaceId, rawTabId);
    this.emit("page.bound", record, { reason: "host_claim" });
    return this.publicRecord(record);
  }

  /**
   * Restore a durable binding after a service-worker restart in the same
   * browser session. This is intentionally narrower than a host rebind:
   * exact tab hints and the current session are required, and no URL match or
   * host-proof forgery is accepted here.
   */
  restoreManagedBinding({
    tabId,
    spaceId,
    pageId,
    leaseEpoch,
    targetGeneration,
    navigationGeneration,
    documentGeneration,
  } = {}) {
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    for (const [value, name] of [
      [leaseEpoch, "lease_epoch"],
      [targetGeneration, "target_generation"],
      [navigationGeneration, "navigation_generation"],
      [documentGeneration, "document_generation"],
    ]) {
      if (!Number.isSafeInteger(value) || value < 1)
        throw new ProtocolError("stale_generation", `${name} is invalid`);
    }
    const record = this.byRawTab.get(tabId);
    if (
      !record ||
      record.sessionEpoch !== this.browserSessionEpoch ||
      record.ownership !== "unmanaged" ||
      record.bindingState !== "unbound" ||
      record.active === true ||
      record.incognito === true ||
      this.retiredRawTabIds.has(tabId)
    ) {
      throw new ProtocolError(
        "stale_target",
        "same-session durable binding is not an exact inactive tab match",
      );
    }
    const priorPage = this.byPage.get(pageId);
    if (priorPage && priorPage !== record)
      throw new ProtocolError(
        "ambiguous_binding",
        "logical page is already bound to another live tab",
      );
    if (record.pageId !== undefined)
      throw new ProtocolError(
        "ambiguous_binding",
        "tab is already logically bound",
      );
    this.byPage.set(pageId, record);
    record.spaceId = spaceId;
    record.pageId = pageId;
    record.ownership = "agent";
    record.lifecycle = "managed";
    record.bindingState = "bound";
    record.leaseEpoch = leaseEpoch;
    record.targetGeneration = targetGeneration;
    record.navigationGeneration = navigationGeneration;
    record.documentGeneration = documentGeneration;
    record.generation = targetGeneration;
    this.groups?.claimTab(spaceId, tabId);
    this.emit("page.bound", record, { reason: "same_session_worker_restore" });
    return this.publicRecord(record);
  }

  /** Rebind one retained inactive tab after an acknowledged lease fence. */
  async rebindManagedTab({
    spaceId,
    pageId,
    leaseEpoch,
    targetGeneration,
    navigationGeneration,
    documentGeneration,
    ownershipProof,
  } = {}) {
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    if (!Number.isSafeInteger(leaseEpoch) || leaseEpoch < 1)
      throw new ProtocolError(
        "stale_lease",
        "managed tab rebind requires a lease epoch",
      );
    for (const [value, name] of [
      [targetGeneration, "target_generation"],
      [navigationGeneration, "navigation_generation"],
      [documentGeneration, "document_generation"],
    ]) {
      if (!Number.isSafeInteger(value) || value < 1)
        throw new ProtocolError("stale_generation", `${name} is invalid`);
    }
    const proof = logicalProof(ownershipProof, {
      spaceId,
      pageId,
      leaseEpoch,
      kind: "rebind",
      purpose: "rebind",
      requireExpiry: true,
      targetGeneration,
      profileInstanceId: this.profileInstanceId,
      browserSessionEpoch: this.browserSessionEpoch,
      requireProfileInstanceId: true,
      requireBrowserSessionEpoch: true,
      now: this.now(),
    });
    if (proof.rebind !== true)
      throw new ProtocolError(
        "permission_denied",
        "rebind proof does not authorize a retained page rebind",
      );

    let record = this.byPage.get(pageId);
    if (!record) {
      const candidate = this.findRehydrateCandidate({
        url: proof.url,
        title: proof.title,
      });
      if (!candidate)
        throw new ProtocolError(
          "page_not_found",
          "retained page is not available for rebind",
        );
      const rebound = this.bindManagedTab({
        tab: { id: candidate.rawTabId },
        spaceId,
        pageId,
        leaseEpoch,
        ownershipProof: proof,
        targetGeneration,
        navigationGeneration,
        documentGeneration,
        url: candidate.url,
        title: candidate.title,
      });
      await this.groups
        ?.presentSpace({
          spaceId,
          tabId: candidate.rawTabId,
          title: candidate.title || "agentyc",
        })
        .catch(() => {});
      return rebound;
    }
    if (record.spaceId !== spaceId)
      throw new ProtocolError("page_not_found", "logical page is not bound");
    if (
      record.ownership === "agent" &&
      record.lifecycle === "managed" &&
      record.bindingState === "bound" &&
      record.leaseEpoch === leaseEpoch &&
      record.targetGeneration === targetGeneration &&
      record.navigationGeneration === navigationGeneration &&
      record.documentGeneration === documentGeneration
    ) {
      return this.publicRecord(record);
    }
    if (
      record.ownership !== "agent" ||
      record.lifecycle !== "managed" ||
      record.bindingState !== "user_owned"
    )
      throw new ProtocolError(
        "user_control_required",
        "retained page is not awaiting an explicit lease rebind",
      );
    if (record.active === true)
      throw new ProtocolError(
        "user_control_required",
        "active retained page remains under user control",
      );
    if (record.incognito === true)
      throw new ProtocolError(
        "incognito_not_supported",
        "incognito pages are not enrolled",
      );
    if (typeof this.chrome?.tabs?.get !== "function")
      throw new ProtocolError(
        "capability_unavailable",
        "Chrome tabs.get is unavailable for rebind verification",
      );

    let liveTab;
    try {
      liveTab = await chromeCall(
        this.chrome.tabs.get.bind(this.chrome.tabs),
        record.rawTabId,
      );
    } catch (error) {
      throw new ProtocolError(
        "stale_target",
        "retained page is no longer live",
        {
          cause: error instanceof Error ? error.message : String(error),
        },
      );
    }
    if (
      !liveTab ||
      liveTab.id !== record.rawTabId ||
      liveTab.active === true ||
      Boolean(liveTab.incognito) !== Boolean(record.incognito) ||
      (record.url !== undefined &&
        liveTab.url !== undefined &&
        safeText(liveTab.url, 4096) !== record.url)
    )
      throw new ProtocolError(
        liveTab?.active === true ? "user_control_required" : "stale_target",
        "retained page identity or user-control state changed",
      );

    this.assertFence({ spaceId, leaseEpoch });
    const rebound = this.bindManagedTab({
      tab: liveTab,
      spaceId,
      pageId,
      leaseEpoch,
      ownershipProof: proof,
      targetGeneration,
      navigationGeneration,
      documentGeneration,
      url: liveTab.url,
      title: liveTab.title,
    });
    await this.groups
      ?.presentSpace({
        spaceId,
        tabId: liveTab.id,
        title: liveTab.title || "agentyc",
      })
      .catch(() => {});
    return rebound;
  }

  async rollbackCreatedTab(tab, originalError) {
    if (!tab || !Number.isInteger(tab.id)) return;
    this.hostCreatedTabIds.delete(tab.id);
    if (tab.active === true) {
      const record = this.observeTab(tab, "claim_rollback_skipped_focus");
      record.lifecycle = "unmanaged";
      record.ownership = "unmanaged";
      record.bindingState = "unbound";
      return;
    }
    if (typeof this.chrome?.tabs?.remove !== "function") {
      throw unknownDispatch(
        "created tab claim failed and cleanup is unavailable",
        {
          cause:
            originalError instanceof Error
              ? originalError.message
              : String(originalError),
        },
      );
    }
    try {
      await this.chrome.tabs.remove(tab.id);
    } catch (error) {
      throw unknownDispatch("created tab claim rollback was not confirmed", {
        cause: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async focusedTabSnapshot() {
    if (typeof this.chrome?.tabs?.query !== "function") return undefined;
    try {
      const tabs = await chromeCall(
        this.chrome.tabs.query.bind(this.chrome.tabs),
        { active: true, lastFocusedWindow: true },
      );
      const tab = Array.isArray(tabs) ? tabs[0] : undefined;
      if (!tab || !Number.isInteger(tab.id)) return undefined;
      return {
        tabId: tab.id,
        windowId: Number.isInteger(tab.windowId) ? tab.windowId : undefined,
      };
    } catch {
      return undefined;
    }
  }

  async createAgentPage({
    spaceId,
    pageId,
    leaseEpoch,
    url,
    title,
    ownershipProof,
    windowHint,
    onDispatch = () => {},
    isLive = () => true,
  } = {}) {
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    const creationSessionEpoch = this.browserSessionEpoch;
    logicalProof(ownershipProof, {
      spaceId,
      pageId,
      leaseEpoch,
      kind: "page creation",
      profileInstanceId: this.profileInstanceId,
      browserSessionEpoch: this.browserSessionEpoch,
      now: this.now(),
    });
    if (isRestrictedUrl(url))
      throw new ProtocolError(
        "restricted_url",
        "page URL is not debugger-accessible",
      );
    if (!this.chrome?.tabs?.create)
      throw new ProtocolError(
        "capability_unavailable",
        "Chrome tabs.create is unavailable",
      );
    const options = { active: false };
    const rawWindowId =
      windowHint === undefined ? undefined : this.windowHints.get(windowHint);
    if (windowHint !== undefined && rawWindowId === undefined) {
      throw new ProtocolError(
        "window_not_found",
        "approved window hint is not live",
      );
    }
    if (rawWindowId !== undefined) options.windowId = rawWindowId;
    if (typeof url === "string" && url.length > 0) options.url = url;
    const focusedBefore = await this.focusedTabSnapshot();
    this.assertFence({ spaceId, leaseEpoch });
    let tab;
    try {
      onDispatch();
      tab = await chromeCall(
        this.chrome.tabs.create.bind(this.chrome.tabs),
        options,
      );
    } catch (error) {
      throw unknownDispatch("tab creation dispatch was not confirmed", {
        cause: error instanceof Error ? error.message : String(error),
      });
    }
    if (!tab || !Number.isInteger(tab.id)) {
      throw unknownDispatch("Chrome did not return a created tab identity");
    }
    this.hostCreatedTabIds.add(tab.id);
    if (this.browserSessionEpoch !== creationSessionEpoch || !isLive()) {
      const sessionError = new ProtocolError(
        "unknown_outcome",
        "page creation completed after its worker authority ended",
      );
      sessionError.outcome = "unknown";
      sessionError.retryable = false;
      await this.rollbackCreatedTab(tab, sessionError);
      throw sessionError;
    }
    const focusedAfter = await this.focusedTabSnapshot();
    const focusChanged =
      focusedBefore &&
      focusedAfter &&
      (focusedBefore.tabId !== focusedAfter.tabId ||
        focusedBefore.windowId !== focusedAfter.windowId);
    if (tab.active === true || focusChanged) {
      const record = this.observeTab(tab, "focus_theft");
      record.lifecycle = "unmanaged";
      record.ownership = "unmanaged";
      record.bindingState = "unbound";
      this.hostCreatedTabIds.delete(tab.id);
      if (tab.active !== true)
        await this.rollbackCreatedTab(
          tab,
          new Error("focus changed during page creation"),
        );
      throw new ProtocolError(
        "focus_theft",
        "Chrome changed the focused tab or window during agent page creation",
      );
    }
    let publicRecord;
    try {
      this.assertFence({ spaceId, leaseEpoch });
      if (!isLive()) {
        const error = new ProtocolError(
          "unknown_outcome",
          "page creation authority ended before binding",
        );
        error.outcome = "unknown";
        error.retryable = false;
        throw error;
      }
      publicRecord = this.bindManagedTab({
        tab,
        spaceId,
        pageId,
        leaseEpoch,
        ownershipProof,
        url,
        title,
      });
    } catch (error) {
      await this.rollbackCreatedTab(tab, error);
      throw error;
    }
    try {
      await this.groups?.presentSpace({
        spaceId,
        tabId: tab.id,
        title: title || "agentyc",
      });
    } catch {
      // Visual grouping is best effort and never changes logical ownership.
    }
    return publicRecord;
  }

  /** Explicit adoption is separate from observation and requires a user ticket. */
  adoptExistingTab({
    tabHint,
    spaceId,
    pageId,
    leaseEpoch,
    ownershipProof,
    intentTicket,
    onDispatch = () => {},
  } = {}) {
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    logicalProof(ownershipProof, {
      spaceId,
      pageId,
      leaseEpoch,
      kind: "adoption",
      purpose: "adoption",
      requireExpiry: true,
      profileInstanceId: this.profileInstanceId,
      browserSessionEpoch: this.browserSessionEpoch,
      requireProfileInstanceId: true,
      requireBrowserSessionEpoch: true,
      now: this.now(),
    });
    if (
      !intentTicket ||
      intentTicket.issued_by_host !== true ||
      typeof intentTicket.ticket_id !== "string"
    ) {
      throw new ProtocolError(
        "user_confirmation_required",
        "adoption requires a host-issued intent ticket",
      );
    }
    const ticketProof = { ...intentTicket, proof_id: intentTicket.ticket_id };
    logicalProof(ticketProof, {
      spaceId,
      pageId,
      leaseEpoch,
      kind: "adoption intent",
      purpose: "adoption",
      requireExpiry: true,
      tabHint,
      profileInstanceId: this.profileInstanceId,
      browserSessionEpoch: this.browserSessionEpoch,
      requireProfileInstanceId: true,
      requireBrowserSessionEpoch: true,
      now: this.now(),
    });
    this.pruneProofs();
    if (
      this.usedAdoptionProofs.has(ownershipProof.proof_id) ||
      this.usedIntentTickets.has(intentTicket.ticket_id)
    ) {
      throw new ProtocolError(
        "replay_rejected",
        "adoption proof or intent ticket was already consumed",
      );
    }
    this.assertProofCapacity(this.usedAdoptionProofs, ownershipProof.proof_id);
    this.assertProofCapacity(this.usedIntentTickets, intentTicket.ticket_id);
    const candidates = [...this.byRawTab.values()].filter(
      (record) =>
        record.ownership === "unmanaged" &&
        record.sessionEpoch === this.browserSessionEpoch &&
        !this.retiredRawTabIds.has(record.rawTabId) &&
        browserHint(record.rawTabId, this.hintSalt) === tabHint &&
        !record.incognito,
    );
    if (candidates.length !== 1) {
      throw new ProtocolError(
        candidates.length === 0 ? "unmanaged_page" : "ambiguous_binding",
        "tab cannot be adopted conservatively",
      );
    }
    const candidate = candidates[0];
    logicalProof(ownershipProof, {
      spaceId,
      pageId,
      leaseEpoch,
      kind: "adoption",
      purpose: "adoption",
      requireExpiry: true,
      targetGeneration: candidate.targetGeneration,
      tabHint,
      profileInstanceId: this.profileInstanceId,
      browserSessionEpoch: this.browserSessionEpoch,
      requireProfileInstanceId: true,
      requireBrowserSessionEpoch: true,
      now: this.now(),
    });
    logicalProof(ticketProof, {
      spaceId,
      pageId,
      leaseEpoch,
      kind: "adoption intent",
      purpose: "adoption",
      requireExpiry: true,
      targetGeneration: candidate.targetGeneration,
      tabHint,
      profileInstanceId: this.profileInstanceId,
      browserSessionEpoch: this.browserSessionEpoch,
      requireProfileInstanceId: true,
      requireBrowserSessionEpoch: true,
      now: this.now(),
    });
    onDispatch();
    const record = this.bindManagedTab({
      tab: { id: candidate.rawTabId },
      spaceId,
      pageId,
      leaseEpoch,
      ownershipProof,
      targetGeneration: candidate.targetGeneration,
      navigationGeneration: candidate.navigationGeneration,
      documentGeneration: candidate.documentGeneration,
    });
    this.consumeProof(
      this.usedAdoptionProofs,
      ownershipProof.proof_id,
      ownershipProof.expires_at ?? ownershipProof.expires_at_ms,
    );
    this.consumeProof(
      this.usedIntentTickets,
      intentTicket.ticket_id,
      intentTicket.expires_at ?? intentTicket.expires_at_ms,
    );
    return record;
  }

  assertPageDispatch({
    spaceId,
    pageId,
    leaseEpoch,
    expectedGeneration,
    expectedTargetGeneration,
    expectedNavigationGeneration,
    expectedDocumentGeneration,
    mutation = false,
  } = {}) {
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    const record = this.byPage.get(pageId);
    if (!record || record.spaceId !== spaceId)
      throw new ProtocolError("page_not_found", "logical page is not bound");
    if (
      this.browserSessionEpoch !== undefined &&
      record.sessionEpoch !== this.browserSessionEpoch
    ) {
      throw new ProtocolError(
        "stale_epoch",
        "page belongs to another browser session",
      );
    }
    const fenced = this.fenceEpochs.get(spaceId) ?? 0;
    if (
      !Number.isSafeInteger(leaseEpoch) ||
      leaseEpoch < 1 ||
      leaseEpoch < fenced ||
      leaseEpoch !== record.leaseEpoch
    ) {
      throw new ProtocolError("stale_lease", "command lease is not current");
    }
    if (record.ownership !== "agent" || record.bindingState !== "bound") {
      throw new ProtocolError(
        "user_control_required",
        "page is not agent-controlled",
      );
    }
    if (
      expectedGeneration !== undefined &&
      expectedGeneration !== record.targetGeneration
    ) {
      throw new ProtocolError(
        "stale_generation",
        "command generation is not current",
      );
    }
    if (
      expectedTargetGeneration !== undefined &&
      expectedTargetGeneration !== record.targetGeneration
    )
      throw new ProtocolError(
        "stale_generation",
        "target generation is not current",
      );
    if (
      expectedNavigationGeneration !== undefined &&
      expectedNavigationGeneration !== record.navigationGeneration
    )
      throw new ProtocolError(
        "stale_generation",
        "navigation generation is not current",
      );
    if (
      expectedDocumentGeneration !== undefined &&
      expectedDocumentGeneration !== record.documentGeneration
    )
      throw new ProtocolError(
        "stale_generation",
        "document generation is not current",
      );
    if (mutation && isRestrictedUrl(record.url)) {
      throw new ProtocolError(
        "restricted_url",
        "page is not debugger-accessible",
      );
    }
    return record;
  }

  /** Restores a persisted barrier floor without emitting events or changing bindings. */
  restoreFence(spaceId, fenceEpoch) {
    assertLogicalScope({ spaceId });
    if (!Number.isSafeInteger(fenceEpoch) || fenceEpoch < 1) return;
    this.fenceEpochs.set(
      spaceId,
      Math.max(this.fenceEpochs.get(spaceId) ?? 0, fenceEpoch),
    );
  }

  beginFence(spaceId, fenceEpoch) {
    assertLogicalScope({ spaceId });
    if (!Number.isSafeInteger(fenceEpoch) || fenceEpoch < 1) {
      throw new ProtocolError("stale_fence", "fence epoch is invalid");
    }
    const current = this.fenceEpochs.get(spaceId) ?? 0;
    if (fenceEpoch < current)
      throw new ProtocolError(
        "stale_fence",
        "fence epoch is older than the live fence",
      );
    this.fenceEpochs.set(spaceId, Math.max(current, fenceEpoch));
    const affected = [];
    for (const record of this.byPage.values()) {
      if (
        record.spaceId === spaceId &&
        record.leaseEpoch !== undefined &&
        record.leaseEpoch < fenceEpoch
      ) {
        record.bindingState = "user_owned";
        affected.push(record.pageId);
      }
    }
    this.onEvent("space.fenced", {
      space_id: spaceId,
      fence_epoch: fenceEpoch,
      affected_page_count: affected.length,
    });
    return {
      space_id: spaceId,
      fence_epoch: fenceEpoch,
      affected_page_ids: affected,
    };
  }

  async closeManagedPage({
    spaceId,
    pageId,
    leaseEpoch,
    expectedGeneration,
    expectedNavigationGeneration,
    expectedDocumentGeneration,
    cleanupProof,
    onDispatch = () => {},
  } = {}) {
    this.pruneProofs();
    if (
      cleanupProof?.proof_id &&
      this.usedCleanupProofs.has(cleanupProof.proof_id)
    ) {
      throw new ProtocolError(
        "replay_rejected",
        "cleanup proof was already consumed",
      );
    }
    const record = this.assertPageDispatch({
      spaceId,
      pageId,
      leaseEpoch,
      expectedGeneration,
      expectedNavigationGeneration,
      expectedDocumentGeneration,
      mutation: true,
    });
    const expectedTabHint = browserHint(record.rawTabId, this.hintSalt);
    const proof = logicalProof(cleanupProof, {
      spaceId,
      pageId,
      leaseEpoch,
      kind: "cleanup",
      purpose: "cleanup",
      requireExpiry: true,
      targetGeneration: record.targetGeneration,
      tabHint: expectedTabHint,
      profileInstanceId: this.profileInstanceId,
      browserSessionEpoch: this.browserSessionEpoch,
      requireBrowserSessionEpoch: true,
      now: this.now(),
    });
    if (proof.ownership !== "agent") {
      throw new ProtocolError(
        "permission_denied",
        "cleanup proof does not prove agent ownership",
      );
    }
    this.pruneProofs();
    if (this.usedCleanupProofs.has(proof.proof_id)) {
      throw new ProtocolError(
        "replay_rejected",
        "cleanup proof was already consumed",
      );
    }
    if (typeof this.chrome?.tabs?.get !== "function") {
      throw new ProtocolError(
        "capability_unavailable",
        "Chrome tabs.get is unavailable for cleanup verification",
      );
    }
    if (typeof this.chrome?.tabs?.remove !== "function") {
      throw new ProtocolError(
        "capability_unavailable",
        "Chrome tabs.remove is unavailable",
      );
    }
    let liveTab;
    try {
      liveTab = await chromeCall(
        this.chrome.tabs.get.bind(this.chrome.tabs),
        record.rawTabId,
      );
    } catch (error) {
      throw new ProtocolError("stale_target", "managed tab is no longer live", {
        cause: error instanceof Error ? error.message : String(error),
      });
    }
    if (
      !liveTab ||
      liveTab.id !== record.rawTabId ||
      Boolean(liveTab.incognito) !== Boolean(record.incognito) ||
      (record.url !== undefined &&
        liveTab.url !== undefined &&
        safeText(liveTab.url, 4096) !== record.url) ||
      liveTab.active === true
    ) {
      throw new ProtocolError(
        liveTab?.active === true ? "user_control_required" : "stale_target",
        "live tab identity or user-control state changed",
      );
    }
    const current = this.byPage.get(pageId);
    if (
      current !== record ||
      current.targetGeneration !== record.targetGeneration ||
      current.sessionEpoch !== this.browserSessionEpoch ||
      current.bindingState !== "bound" ||
      current.ownership !== "agent"
    ) {
      throw new ProtocolError(
        "stale_generation",
        "managed tab changed during cleanup verification",
      );
    }
    this.assertFence({ spaceId, leaseEpoch });
    this.consumeProof(
      this.usedCleanupProofs,
      proof.proof_id,
      proof.expires_at ?? proof.expires_at_ms,
    );
    try {
      onDispatch();
      await this.chrome.tabs.remove(record.rawTabId);
    } catch (error) {
      throw unknownDispatch("tab close dispatch was not confirmed", {
        cause: error instanceof Error ? error.message : String(error),
      });
    }
    this.byRawTab.delete(record.rawTabId);
    this.byPage.delete(pageId);
    this.groups?.removeTab(record.rawTabId);
    this.emit("page.closed", record, { reason: "host_cleanup_proof" });
    return { space_id: spaceId, page_id: pageId, closed: true };
  }

  getInternalByPage(pageId) {
    return this.byPage.get(pageId);
  }

  findByHint(tabHint) {
    if (typeof tabHint !== "string" || tabHint.length < 2) return undefined;
    for (const record of this.byRawTab.values()) {
      if (browserHint(record.rawTabId, this.hintSalt) === tabHint)
        return record;
    }
    return undefined;
  }

  /** Find one inactive unbound tab by exact persisted page metadata. */
  findRehydrateCandidate({ url, title } = {}) {
    if (typeof url !== "string" || url.length === 0) return undefined;
    const candidates = [...this.byRawTab.values()].filter(
      (record) =>
        record.ownership === "unmanaged" &&
        record.bindingState === "unbound" &&
        record.active !== true &&
        record.incognito !== true &&
        record.url === url,
    );
    return candidates.length === 1 ? candidates[0] : undefined;
  }

  getInternalByTab(tabId) {
    return this.byRawTab.get(tabId);
  }

  getPageGeneration(pageId) {
    return this.byPage.get(pageId)?.targetGeneration;
  }

  inventory() {
    return [...this.byRawTab.values()].map((record) =>
      this.publicRecord(record),
    );
  }

  pagesForSpace(spaceId) {
    return [...this.byPage.values()]
      .filter((record) => record.spaceId === spaceId)
      .map((record) => this.publicRecord(record));
  }

  publicRecord(record) {
    return {
      page_id: record.pageId,
      space_id: record.spaceId,
      ownership: record.ownership,
      lifecycle: record.lifecycle,
      binding_state: record.bindingState,
      generation: record.generation,
      target_generation: record.targetGeneration,
      navigation_generation: record.navigationGeneration,
      document_generation: record.documentGeneration,
      lease_epoch: record.leaseEpoch,
      browser_session_epoch: record.sessionEpoch,
      url: record.url,
      title: record.title,
      incognito: record.incognito,
      discarded: record.discarded,
      frozen: record.frozen,
      active: record.active,
      window_hint:
        record.rawWindowId === undefined
          ? undefined
          : browserHint(record.rawWindowId, this.hintSalt),
      tab_hint: browserHint(record.rawTabId, this.hintSalt),
      ...(record.ownership === "unmanaged" && record.active === true
        ? {
            // This is a salted content fingerprint, not a URL/title. It lets
            // the runner prove focus continuity across Chrome session hint
            // rotation without exporting user-tab metadata.
            focus_hint: browserHint(
              `focus:${record.url ?? ""}\u0000${record.title ?? ""}`,
              this.profileInstanceId ?? "focus",
            ),
          }
        : {}),
    };
  }

  async handleUpdated(tabId, changeInfo = {}, tab) {
    const previous = this.byRawTab.get(tabId);
    const previousUrl = previous?.url;
    const record = tab
      ? this.observeTab({ ...tab, id: tabId }, "updated")
      : await this.hydrateTab(tabId);
    if (!record) return;
    const nextUrl =
      changeInfo.url === undefined
        ? record.url
        : safeText(changeInfo.url, 4096);
    const urlChanged = changeInfo.url !== undefined && nextUrl !== previousUrl;
    if (urlChanged) {
      record.url = nextUrl;
      record.navigationGeneration += 1;
      record.documentGeneration += 1;
      record.generation = record.targetGeneration;
    } else if (changeInfo.status === "loading") {
      record.documentGeneration += 1;
    }
    if (changeInfo.title !== undefined)
      record.title = safeText(changeInfo.title, 512);
    if (changeInfo.status === "loading" || changeInfo.status === "complete")
      record.lastObservedAt = this.now();
    if (changeInfo.groupId !== undefined) {
      record.rawGroupId = changeInfo.groupId;
      this.groups?.observeTabMembership(tabId, changeInfo.groupId);
    }
    if (record.pageId && (urlChanged || changeInfo.status === "loading"))
      this.onLifecycle("document_changed", tabId, record);
    this.emit(record.pageId ? "page.changed" : "tab.observed", record, {
      reason: "updated",
    });
  }

  handleRemoved(tabId, removeInfo = {}) {
    const record = this.byRawTab.get(tabId);
    this.retiredRawTabIds.add(tabId);
    this.hostCreatedTabIds.delete(tabId);
    this.onLifecycle("removed", tabId, record);
    if (!record) return;
    if (record.pageId) {
      record.targetGeneration += 1;
      record.generation = record.targetGeneration;
      record.lifecycle = "target_lost";
      record.bindingState = "lost";
      this.emit("page.lost", record, { reason: "tab_removed" });
      this.byPage.delete(record.pageId);
    } else {
      this.emit("tab.closed", record, { reason: "tab_removed" });
    }
    this.byRawTab.delete(tabId);
    this.groups?.removeTab(tabId);
  }

  handleReplaced(addedTabId, removedTabId) {
    const prior = this.byRawTab.get(removedTabId);
    const replacement =
      addedTabId === removedTabId ? prior : this.byRawTab.get(addedTabId);
    if (replacement && replacement !== prior) {
      this.retiredRawTabIds.add(addedTabId);
      this.onLifecycle("replacement_existing", addedTabId, replacement);
      if (replacement.pageId) this.byPage.delete(replacement.pageId);
      this.byRawTab.delete(addedTabId);
      this.groups?.removeTab(addedTabId);
    }
    this.retiredRawTabIds.add(removedTabId);
    this.hostCreatedTabIds.delete(removedTabId);
    this.onLifecycle("replaced", removedTabId, prior);
    this.onLifecycle("replacement", addedTabId, undefined);
    if (prior?.pageId) {
      prior.targetGeneration += 1;
      prior.generation = prior.targetGeneration;
      prior.lifecycle = "target_lost";
      prior.bindingState = "rebind_required";
      this.emit("page.replaced", prior, { reason: "tab_replaced" });
      this.byPage.delete(prior.pageId);
    }
    this.byRawTab.delete(removedTabId);
    this.groups?.removeTab(removedTabId);
    // The replacement is intentionally observed as unmanaged. URL similarity is
    // not sufficient proof for a logical rebind.
    void this.hydrateTab(addedTabId).then((record) => {
      if (record)
        this.emit("tab.observed", record, { reason: "replacement_unmanaged" });
    });
  }

  handleActivated(activeInfo = {}) {
    const activeTabId = activeInfo.tabId;
    const windowId = activeInfo.windowId;
    for (const record of this.byRawTab.values()) {
      if (windowId !== undefined && record.rawWindowId !== windowId) continue;
      const nextActive = record.rawTabId === activeTabId;
      if (record.active === nextActive) continue;
      record.active = nextActive;
      this.emit(
        record.pageId ? "page.focus_changed" : "tab.focus_changed",
        record,
        {
          reason: "chrome_activation",
        },
      );
    }
  }

  handleAttached(tabId, attachInfo = {}) {
    const record = this.byRawTab.get(tabId);
    if (!record) return;
    record.rawWindowId = attachInfo.newWindowId ?? record.rawWindowId;
    record.targetGeneration += 1;
    record.generation = record.targetGeneration;
    record.bindingState = record.pageId ? "lost" : record.bindingState;
    this.onLifecycle("attached", tabId, record);
    this.emit(record.pageId ? "page.changed" : "tab.observed", record, {
      reason: "tab_attached",
    });
  }

  handleDetached(tabId, detachInfo = {}) {
    const record = this.byRawTab.get(tabId);
    if (!record) return;
    record.targetGeneration += 1;
    record.generation = record.targetGeneration;
    if (record.pageId) record.bindingState = "lost";
    this.onLifecycle("detached", tabId, record);
    this.emit(record.pageId ? "page.changed" : "tab.observed", record, {
      reason: "tab_detached",
    });
  }

  emit(event, record, extra = {}) {
    this.onEvent(event, {
      ...this.publicRecord(record),
      ...extra,
      event,
    });
  }
}

export function tabOperationError(error) {
  return publicError(error, "tab_operation_failed");
}

export { unknownDispatch };
