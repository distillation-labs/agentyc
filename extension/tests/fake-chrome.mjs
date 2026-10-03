export class FakeEvent {
  constructor() {
    this.listeners = new Set();
  }
  addListener(listener) {
    this.listeners.add(listener);
  }
  removeListener(listener) {
    this.listeners.delete(listener);
  }
  emit(...args) {
    for (const listener of [...this.listeners]) {
      try {
        listener(...args);
      } catch (error) {
        this.lastError = error;
      }
    }
  }
}

export class FakeNativePort {
  constructor(name) {
    this.name = name;
    this.onMessage = new FakeEvent();
    this.onDisconnect = new FakeEvent();
    this.sent = [];
    this.disconnected = false;
  }
  postMessage(message) {
    if (this.disconnected) throw new Error("port disconnected");
    this.sent.push(structuredClone(message));
  }
  receive(message) {
    if (!this.disconnected) this.onMessage.emit(structuredClone(message));
  }
  disconnect() {
    if (this.disconnected) return;
    this.disconnected = true;
    this.onDisconnect.emit();
  }
}

function copyTab(tab) {
  return { ...tab };
}

export class FakeChrome {
  constructor({ tabs = [] } = {}) {
    this.nextTabId = 100;
    this.nextGroupId = 20;
    this.ports = [];
    this.runtimeMessages = [];
    this.removedTabIds = [];
    this.groupUpdates = [];
    this.sidePanelBehaviorCalls = [];
    this.debuggerCommands = [];
    this.debuggerFailures = new Map();
    this.storageData = {};
    this.tabsGetCalls = [];
    this.tabsCreateCalls = [];
    this.tabsRemoveCalls = [];
    this.beforeTabCreate = null;
    this.beforeTabRemove = null;
    this.tabsData = new Map();
    for (const tab of tabs) {
      this.tabsData.set(tab.id, {
        windowId: 1,
        groupId: -1,
        active: false,
        status: "complete",
        url: "https://example.test/",
        title: "Example",
        incognito: false,
        discarded: false,
        frozen: false,
        ...tab,
      });
      this.nextTabId = Math.max(this.nextTabId, tab.id + 1);
    }
    this.tabGroupsData = new Map();
    this.chrome = this;
    this.runtime = {
      id: "fake-extension-id",
      onMessage: new FakeEvent(),
      onStartup: new FakeEvent(),
      connectNative: (name) => {
        const port = new FakeNativePort(name);
        this.ports.push(port);
        this.lastPort = port;
        return port;
      },
      sendMessage: async (message) => {
        this.runtimeMessages.push(structuredClone(message));
        return undefined;
      },
      lastError: undefined,
    };
    this.storage = {
      local: {
        get: async (key) => ({ [key]: this.storageData[key] }),
        set: async (value) =>
          Object.assign(this.storageData, structuredClone(value)),
      },
    };
    this.tabs = {
      onCreated: new FakeEvent(),
      onUpdated: new FakeEvent(),
      onRemoved: new FakeEvent(),
      onReplaced: new FakeEvent(),
      onAttached: new FakeEvent(),
      onDetached: new FakeEvent(),
      onActivated: new FakeEvent(),
      query: async (queryInfo = {}) => {
        let values = [...this.tabsData.values()];
        if (queryInfo.active !== undefined)
          values = values.filter((tab) => tab.active === queryInfo.active);
        if (queryInfo.windowId !== undefined)
          values = values.filter((tab) => tab.windowId === queryInfo.windowId);
        return values.map(copyTab);
      },
      get: async (tabId) => {
        this.tabsGetCalls.push(tabId);
        const tab = this.tabsData.get(tabId);
        if (!tab) throw new Error("tab not found");
        return copyTab(tab);
      },
      create: async (options = {}) => {
        this.tabsCreateCalls.push({ ...options });
        if (this.beforeTabCreate) await this.beforeTabCreate(options);
        const tab = {
          id: this.nextTabId++,
          windowId: options.windowId ?? 1,
          groupId: -1,
          active: options.active === true,
          status: "loading",
          url: options.url ?? "about:blank",
          title: options.title ?? "",
          incognito: false,
          discarded: false,
          frozen: false,
        };
        if (tab.active) {
          for (const other of this.tabsData.values()) {
            if (other.windowId === tab.windowId) other.active = false;
          }
        }
        this.tabsData.set(tab.id, tab);
        this.tabs.onCreated.emit(copyTab(tab));
        if (tab.active)
          this.tabs.onActivated.emit({ tabId: tab.id, windowId: tab.windowId });
        return copyTab(tab);
      },
      remove: async (tabId) => {
        this.tabsRemoveCalls.push(tabId);
        if (this.beforeTabRemove) await this.beforeTabRemove(tabId);
        const tab = this.tabsData.get(tabId);
        if (!tab) throw new Error("tab not found");
        this.tabsData.delete(tabId);
        this.removedTabIds.push(tabId);
        this.tabs.onRemoved.emit(tabId, {
          windowId: tab.windowId,
          isWindowClosing: false,
        });
      },
      group: async ({ tabIds, groupId: requestedGroupId }) => {
        const groupId = Number.isInteger(requestedGroupId)
          ? requestedGroupId
          : this.nextGroupId++;
        const group = this.tabGroupsData.get(groupId) ?? {
          id: groupId,
          title: "",
          color: "grey",
          collapsed: false,
        };
        this.tabGroupsData.set(groupId, group);
        for (const tabId of tabIds) {
          const tab = this.tabsData.get(tabId);
          if (tab) {
            tab.groupId = groupId;
            this.tabs.onUpdated.emit(tabId, { groupId }, copyTab(tab));
          }
        }
        if (!Number.isInteger(requestedGroupId))
          this.tabGroups.onCreated.emit({ ...group });
        return groupId;
      },
      sendMessage: async (tabId, message) => {
        if (!this.tabsData.has(tabId)) throw new Error("tab not found");
        this.lastContentMessage = { tabId, message: structuredClone(message) };
        return undefined;
      },
    };
    this.tabGroups = {
      onUpdated: new FakeEvent(),
      onRemoved: new FakeEvent(),
      onCreated: new FakeEvent(),
      update: async (groupId, updateInfo) => {
        const group = this.tabGroupsData.get(groupId);
        if (!group) throw new Error("group not found");
        Object.assign(group, updateInfo);
        this.groupUpdates.push({ groupId, updateInfo: { ...updateInfo } });
        this.tabGroups.onUpdated.emit({ ...group });
        return { ...group };
      },
    };
    this.debugger = {
      onEvent: new FakeEvent(),
      onDetach: new FakeEvent(),
      attached: new Set(),
      attach: async (source) => {
        this.debugger.attached.add(source.tabId);
      },
      detach: async (source) => {
        this.debugger.attached.delete(source.tabId);
      },
      sendCommand: async (source, method, params) => {
        this.debuggerCommands.push({
          source: { ...source },
          method,
          params: structuredClone(params),
        });
        const failure = this.debuggerFailures.get(method);
        if (failure) throw failure;
        return {
          ok: true,
          method,
          tabId: source.tabId,
          targetId: "raw-target",
          sessionId: "raw-session",
        };
      },
    };
    this.sidePanel = {
      setPanelBehavior: async (behavior) => {
        this.sidePanelBehaviorCalls.push({ ...behavior });
      },
      open: async () => {
        throw new Error("side panel open must be user gated");
      },
    };
    this.scripting = {
      executeScript: async () => ({}),
    };
  }

  receiveHost(message) {
    if (!this.lastPort) throw new Error("native port has not connected");
    this.lastPort.receive(message);
  }

  hostMessages(kind) {
    return this.lastPort?.sent.filter((message) => message.kind === kind) ?? [];
  }

  emitRuntimeMessage(message, sender = { id: this.runtime.id }) {
    return this.runtime.onMessage.emit(message, sender);
  }

  emitDebuggerEvent(tabId, method, params = {}, sessionId) {
    this.debugger.onEvent.emit(
      { tabId, ...(sessionId ? { sessionId } : {}) },
      method,
      params,
    );
  }

  emitDebuggerDetach(tabId, reason = "target_closed", sessionId) {
    this.debugger.onDetach.emit(
      { tabId, ...(sessionId ? { sessionId } : {}) },
      reason,
    );
  }

  replaceTab(removedTabId, addedTab) {
    this.tabsData.delete(removedTabId);
    this.tabsData.set(addedTab.id, { ...addedTab });
    this.tabs.onReplaced.emit(addedTab.id, removedTabId);
  }
}

export function makeHostHelloOk(
  hello,
  {
    brokerEpoch = 1,
    connectionEpoch = 1,
    workerInstanceEpoch = hello.worker_instance_epoch,
    browserSessionEpoch = hello.browser_session_epoch,
  } = {},
) {
  return {
    protocol: 1,
    kind: "hello_ok",
    nonce: hello.nonce,
    sequence: 1,
    broker_epoch: brokerEpoch,
    connection_epoch: connectionEpoch,
    worker_instance_epoch: workerInstanceEpoch,
    browser_session_epoch: browserSessionEpoch,
    capabilities: ["logical_tabs", "debugger_allowlist"],
  };
}

export function hostEnvelope(port, fields, sequence) {
  const hello = port.sent.find((message) => message.kind === "hello");
  const helloOk = port.sent.find((message) => message.kind === "hello_ok");
  const info = {
    protocol: 1,
    nonce: hello?.nonce,
    sequence,
    broker_epoch: 1,
    connection_epoch: 1,
    worker_instance_epoch: hello?.worker_instance_epoch,
    browser_session_epoch: hello?.browser_session_epoch,
    ...fields,
  };
  if (!info.nonce) throw new Error("hello must be sent before host messages");
  return info;
}
