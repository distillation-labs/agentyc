import { ProtocolError, assertLogicalScope, browserHint } from "./protocol.mjs";

function chromeApiOrGlobal(chromeApi) {
  return chromeApi ?? globalThis.chrome;
}

async function callChrome(fn, ...args) {
  if (typeof fn !== "function")
    throw new ProtocolError(
      "capability_unavailable",
      "Chrome API is unavailable",
    );
  return fn(...args);
}

function boundedTitle(title, fallback) {
  if (typeof title !== "string" || title.length === 0) return fallback;
  return title.slice(0, 128);
}

/**
 * Chrome tab groups are presentation hints. This registry deliberately never
 * exposes a group id and has no operation that closes or adopts a group.
 */
export class GroupsRegistry {
  constructor({ chromeApi, onEvent = () => {}, hintSalt = "" } = {}) {
    this.chrome = chromeApiOrGlobal(chromeApi);
    this.onEvent = onEvent;
    this.hintSalt = hintSalt;
    this.bySpace = new Map();
    this.byRawGroup = new Map();
    this.removeListeners = [];
    this.started = false;
  }

  start() {
    if (this.started) return;
    this.started = true;
    const onUpdated = (group) => this.observeGroup(group);
    const onRemoved = (group) =>
      this.removeGroup(Number.isInteger(group?.id) ? group.id : group);
    const onCreated = (group) => this.observeGroup(group);
    this.chrome?.tabGroups?.onUpdated?.addListener?.(onUpdated);
    this.chrome?.tabGroups?.onRemoved?.addListener?.(onRemoved);
    this.chrome?.tabGroups?.onCreated?.addListener?.(onCreated);
    this.removeListeners.push(() =>
      this.chrome?.tabGroups?.onUpdated?.removeListener?.(onUpdated),
    );
    this.removeListeners.push(() =>
      this.chrome?.tabGroups?.onRemoved?.removeListener?.(onRemoved),
    );
    this.removeListeners.push(() =>
      this.chrome?.tabGroups?.onCreated?.removeListener?.(onCreated),
    );
  }

  stop() {
    for (const remove of this.removeListeners.splice(0)) remove();
    this.started = false;
  }

  /**
   * Associate a managed tab with the visual group for a space. The raw tab id
   * is an internal call boundary and never appears in the returned record.
   */
  async presentSpace({ spaceId, tabId, rawTabId, title = "agentyc" } = {}) {
    assertLogicalScope({ spaceId });
    const internalTabId = rawTabId ?? tabId;
    if (!Number.isInteger(internalTabId)) {
      throw new ProtocolError(
        "schema_invalid",
        "visual group presentation requires an internal tab",
      );
    }
    let hint = this.bySpace.get(spaceId);
    if (!hint) {
      hint = {
        spaceId,
        rawGroupId: undefined,
        title: boundedTitle(title, "agentyc"),
        color: undefined,
        collapsed: false,
        memberTabIds: new Set(),
        claimedTabIds: new Set(),
        drift: false,
        present: false,
      };
      this.bySpace.set(spaceId, hint);
    }

    hint.memberTabIds.add(internalTabId);
    hint.claimedTabIds.add(internalTabId);
    if (hint.rawGroupId === undefined && this.chrome?.tabs?.group) {
      try {
        hint.rawGroupId = await callChrome(
          this.chrome.tabs.group.bind(this.chrome.tabs),
          {
            tabIds: [internalTabId],
          },
        );
        if (Number.isInteger(hint.rawGroupId) && hint.rawGroupId >= 0) {
          this.byRawGroup.set(hint.rawGroupId, hint);
          hint.present = true;
        }
      } catch (error) {
        hint.drift = true;
        this.onEvent("group.presentation_failed", {
          space_id: spaceId,
          code: "group_unavailable",
          message: error instanceof Error ? error.message : String(error),
        });
      }
    }
    if (hint.rawGroupId !== undefined && this.chrome?.tabGroups?.update) {
      try {
        await callChrome(
          this.chrome.tabGroups.update.bind(this.chrome.tabGroups),
          hint.rawGroupId,
          {
            title: hint.title,
          },
        );
      } catch {
        hint.drift = true;
      }
    }
    this.emitChanged(hint, "present");
    return this.publicHint(hint);
  }

  /** Record a logical claim without changing visual grouping. */
  claimTab(spaceId, rawTabId) {
    const hint = this.bySpace.get(spaceId);
    if (!hint) return;
    hint.memberTabIds.add(rawTabId);
    hint.claimedTabIds.add(rawTabId);
  }

  /** Observe a tab's group membership; it cannot establish ownership. */
  observeTabMembership(rawTabId, rawGroupId) {
    for (const hint of this.bySpace.values()) {
      hint.memberTabIds.delete(rawTabId);
      hint.claimedTabIds.delete(rawTabId);
    }
    if (!Number.isInteger(rawGroupId) || rawGroupId < 0) return;
    const hint = this.byRawGroup.get(rawGroupId);
    if (!hint) return;
    hint.memberTabIds.add(rawTabId);
    hint.drift = true;
    this.emitChanged(hint, "membership_changed");
  }

  observeGroup(group) {
    if (!group || !Number.isInteger(group.id)) return;
    const hint = this.byRawGroup.get(group.id);
    if (!hint) return;
    if (typeof group.title === "string" && group.title !== hint.title)
      hint.drift = true;
    if (typeof group.color === "string") hint.color = group.color;
    if (typeof group.collapsed === "boolean") hint.collapsed = group.collapsed;
    this.emitChanged(hint, "group_changed");
  }

  removeGroup(rawGroupId) {
    const hint = this.byRawGroup.get(rawGroupId);
    if (!hint) return;
    this.byRawGroup.delete(rawGroupId);
    hint.rawGroupId = undefined;
    hint.present = false;
    hint.drift = true;
    this.emitChanged(hint, "group_removed");
  }

  /** A mixed/user group can never be treated as a cleanup unit. */
  canCleanupGroup(spaceId) {
    const hint = this.bySpace.get(spaceId);
    if (!hint || !hint.present) return false;
    return (
      hint.memberTabIds.size > 0 &&
      hint.memberTabIds.size === hint.claimedTabIds.size
    );
  }

  removeTab(rawTabId) {
    for (const hint of this.bySpace.values()) {
      hint.memberTabIds.delete(rawTabId);
      hint.claimedTabIds.delete(rawTabId);
    }
  }

  publicHint(hint) {
    return {
      space_id: hint.spaceId,
      title: hint.title,
      color: hint.color,
      collapsed: hint.collapsed,
      present: hint.present,
      drift: hint.drift,
      member_count: hint.memberTabIds.size,
      hint:
        hint.rawGroupId === undefined
          ? undefined
          : browserHint(hint.rawGroupId, this.hintSalt),
    };
  }

  listHints() {
    return [...this.bySpace.values()].map((hint) => this.publicHint(hint));
  }

  getInternal(spaceId) {
    return this.bySpace.get(spaceId);
  }

  emitChanged(hint, reason) {
    this.onEvent("group.changed", {
      ...this.publicHint(hint),
      reason,
    });
  }
}

export function createGroupsRegistry(options) {
  return new GroupsRegistry(options);
}
