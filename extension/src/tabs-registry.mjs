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

function logicalProof(proof, { spaceId, pageId, leaseEpoch, kind } = {}) {
  if (!proof || typeof proof !== "object" || Array.isArray(proof)) {
    throw new ProtocolError(
      "permission_denied",
      `${kind} requires a host-issued proof object`,
    );
  }
  if (
    proof.issued_by_host !== true ||
    typeof proof.proof_id !== "string" ||
    proof.proof_id.length < 8
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
  } = {}) {
    this.chrome = chromeApiOrGlobal(chromeApi);
    this.groups = groups;
    this.onEvent = onEvent;
    this.hintSalt = hintSalt;
    this.now = now;
    this.byRawTab = new Map();
    this.byPage = new Map();
    this.windowHints = new Map();
    this.fenceEpochs = new Map();
    this.usedCleanupProofs = new Set();
    this.removeListeners = [];
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
    this.started = false;
  }

  installListeners() {
    const tabs = this.chrome?.tabs;
    if (!tabs) return;
    const onCreated = (tab) => this.observeTab(tab, "created");
    const onUpdated = (tabId, changeInfo, tab) =>
      void this.handleUpdated(tabId, changeInfo, tab);
    const onRemoved = (tabId, removeInfo) =>
      this.handleRemoved(tabId, removeInfo);
    const onReplaced = (addedTabId, removedTabId) =>
      this.handleReplaced(addedTabId, removedTabId);
    const onAttached = (tabId, attachInfo) =>
      this.handleAttached(tabId, attachInfo);
    const onDetached = (tabId, detachInfo) =>
      this.handleDetached(tabId, detachInfo);
    for (const [event, listener] of [
      [tabs.onCreated, onCreated],
      [tabs.onUpdated, onUpdated],
      [tabs.onRemoved, onRemoved],
      [tabs.onReplaced, onReplaced],
      [tabs.onAttached, onAttached],
      [tabs.onDetached, onDetached],
    ]) {
      event?.addListener?.(listener);
      this.removeListeners.push(() => event?.removeListener?.(listener));
    }
  }

  observeTab(tab, reason = "observed") {
    if (!tab || !Number.isInteger(tab.id)) return undefined;
    const existing = this.byRawTab.get(tab.id);
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
      generation: 1,
      url: undefined,
      title: undefined,
      incognito: Boolean(tab.incognito),
      discarded: Boolean(tab.discarded),
      frozen: Boolean(tab.frozen),
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
    record.lastObservedAt = this.now();
    if (record.incognito && record.ownership === "agent") {
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
    });
    const rawTabId = tabId ?? tab?.id;
    if (!Number.isInteger(rawTabId))
      throw new ProtocolError(
        "schema_invalid",
        "managed binding requires a tab",
      );
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
    record.spaceId = spaceId;
    record.pageId = pageId;
    record.ownership = "agent";
    record.lifecycle = "managed";
    record.bindingState = "bound";
    record.leaseEpoch = leaseEpoch;
    record.generation =
      Number.isSafeInteger(generation) && generation > 0
        ? generation
        : record.generation;
    record.claimProofId = proof.proof_id;
    this.byPage.set(pageId, record);
    this.groups?.claimTab(spaceId, rawTabId);
    this.emit("page.bound", record, { reason: "host_claim" });
    return this.publicRecord(record);
  }

  async createAgentPage({
    spaceId,
    pageId,
    leaseEpoch,
    url,
    title,
    ownershipProof,
    windowHint,
  } = {}) {
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    logicalProof(ownershipProof, {
      spaceId,
      pageId,
      leaseEpoch,
      kind: "page creation",
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
    let tab;
    try {
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
    if (tab.active === true) {
      const record = this.observeTab(tab, "focus_theft");
      record.lifecycle = "unmanaged";
      record.ownership = "unmanaged";
      throw new ProtocolError(
        "focus_theft",
        "Chrome activated an agent tab; it was not claimed",
      );
    }
    const publicRecord = this.bindManagedTab({
      tab,
      spaceId,
      pageId,
      leaseEpoch,
      ownershipProof,
      url,
      title,
    });
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
  } = {}) {
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    logicalProof(ownershipProof, {
      spaceId,
      pageId,
      leaseEpoch,
      kind: "adoption",
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
    const candidates = [...this.byRawTab.values()].filter(
      (record) =>
        record.ownership === "unmanaged" &&
        browserHint(record.rawTabId, this.hintSalt) === tabHint &&
        !record.incognito,
    );
    if (candidates.length !== 1) {
      throw new ProtocolError(
        candidates.length === 0 ? "unmanaged_page" : "ambiguous_binding",
        "tab cannot be adopted conservatively",
      );
    }
    return this.bindManagedTab({
      tab: { id: candidates[0].rawTabId },
      spaceId,
      pageId,
      leaseEpoch,
      ownershipProof,
      generation: candidates[0].generation,
    });
  }

  assertPageDispatch({
    spaceId,
    pageId,
    leaseEpoch,
    expectedGeneration,
    mutation = false,
  } = {}) {
    assertLogicalScope({ spaceId, pageId }, { pageRequired: true });
    const record = this.byPage.get(pageId);
    if (!record || record.spaceId !== spaceId)
      throw new ProtocolError("page_not_found", "logical page is not bound");
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
      expectedGeneration !== record.generation
    ) {
      throw new ProtocolError(
        "stale_generation",
        "command generation is not current",
      );
    }
    if (mutation && isRestrictedUrl(record.url)) {
      throw new ProtocolError(
        "restricted_url",
        "page is not debugger-accessible",
      );
    }
    return record;
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
    cleanupProof,
  } = {}) {
    const record = this.assertPageDispatch({
      spaceId,
      pageId,
      leaseEpoch,
      expectedGeneration,
      mutation: true,
    });
    const proof = logicalProof(cleanupProof, {
      spaceId,
      pageId,
      leaseEpoch,
      kind: "cleanup",
    });
    if (
      proof.generation !== undefined &&
      proof.generation !== record.generation
    ) {
      throw new ProtocolError(
        "stale_generation",
        "cleanup proof generation is not current",
      );
    }
    if (proof.ownership !== "agent") {
      throw new ProtocolError(
        "permission_denied",
        "cleanup proof does not prove agent ownership",
      );
    }
    if (this.usedCleanupProofs.has(proof.proof_id)) {
      throw new ProtocolError(
        "replay_rejected",
        "cleanup proof was already consumed",
      );
    }
    this.usedCleanupProofs.add(proof.proof_id);
    try {
      await chromeCall(
        this.chrome.tabs.remove.bind(this.chrome.tabs),
        record.rawTabId,
      );
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

  getInternalByTab(tabId) {
    return this.byRawTab.get(tabId);
  }

  getPageGeneration(pageId) {
    return this.byPage.get(pageId)?.generation;
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
      lease_epoch: record.leaseEpoch,
      url: record.url,
      title: record.title,
      incognito: record.incognito,
      discarded: record.discarded,
      frozen: record.frozen,
      window_hint:
        record.rawWindowId === undefined
          ? undefined
          : browserHint(record.rawWindowId, this.hintSalt),
      tab_hint: browserHint(record.rawTabId, this.hintSalt),
    };
  }

  async handleUpdated(tabId, changeInfo = {}, tab) {
    const record = tab
      ? this.observeTab({ ...tab, id: tabId }, "updated")
      : await this.hydrateTab(tabId);
    if (!record) return;
    if (changeInfo.url !== undefined) {
      record.url = safeText(changeInfo.url, 4096);
      record.generation += 1;
      if (record.pageId) record.bindingState = "bound";
    }
    if (changeInfo.title !== undefined)
      record.title = safeText(changeInfo.title, 512);
    if (changeInfo.status === "loading" || changeInfo.status === "complete")
      record.lastObservedAt = this.now();
    if (changeInfo.groupId !== undefined) {
      record.rawGroupId = changeInfo.groupId;
      this.groups?.observeTabMembership(tabId, changeInfo.groupId);
    }
    this.emit(record.pageId ? "page.changed" : "tab.observed", record, {
      reason: "updated",
    });
  }

  handleRemoved(tabId, removeInfo = {}) {
    const record = this.byRawTab.get(tabId);
    if (!record) return;
    if (record.pageId) {
      record.lifecycle = "target_lost";
      record.bindingState = "lost";
      this.emit("page.lost", record, { reason: "tab_removed" });
      this.byPage.delete(record.pageId);
    }
    this.byRawTab.delete(tabId);
    this.groups?.removeTab(tabId);
  }

  handleReplaced(addedTabId, removedTabId) {
    const prior = this.byRawTab.get(removedTabId);
    if (prior?.pageId) {
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

  handleAttached(tabId, attachInfo = {}) {
    const record = this.byRawTab.get(tabId);
    if (!record) return;
    record.rawWindowId = attachInfo.newWindowId ?? record.rawWindowId;
    record.generation += 1;
    record.bindingState = record.pageId ? "lost" : record.bindingState;
    this.emit(record.pageId ? "page.changed" : "tab.observed", record, {
      reason: "tab_attached",
    });
  }

  handleDetached(tabId, detachInfo = {}) {
    const record = this.byRawTab.get(tabId);
    if (!record) return;
    record.generation += 1;
    if (record.pageId) record.bindingState = "lost";
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
