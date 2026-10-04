import test from "node:test";
import assert from "node:assert/strict";

class MiniEventTarget {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  dispatchEvent(event) {
    event.target ??= this;
    event.currentTarget = this;
    for (const listener of [...(this.listeners.get(event.type) ?? [])])
      listener(event);
    return !event.defaultPrevented;
  }
}

class MiniElement extends MiniEventTarget {
  constructor(ownerDocument, tagName) {
    super();
    this.ownerDocument = ownerDocument;
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = new Map();
    this.dataset = {};
    this._text = "";
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.checked = false;
    this.isConnected = false;
  }

  set id(value) {
    this._id = String(value);
    if (this._id) this.attributes.set("id", this._id);
  }

  get id() {
    return this._id || "";
  }

  set className(value) {
    this._className = String(value);
    if (this._className) this.attributes.set("class", this._className);
  }

  get className() {
    return this._className || "";
  }

  set textContent(value) {
    this.replaceChildren();
    this._text = String(value ?? "");
  }

  get textContent() {
    return (
      this._text + this.children.map((child) => child.textContent).join("")
    );
  }

  append(...children) {
    for (const child of children) this.appendChild(child);
  }

  appendChild(child) {
    if (!child) return child;
    child.parentNode = this;
    child.setConnected(this.isConnected || this.tagName === "BODY");
    this.children.push(child);
    return child;
  }

  replaceChildren(...children) {
    for (const child of this.children) {
      child.parentNode = null;
      child.setConnected(false);
    }
    this.children = [];
    this._text = "";
    for (const child of children) this.appendChild(child);
  }

  setConnected(connected) {
    this.isConnected = Boolean(connected);
    for (const child of this.children) child.setConnected(this.isConnected);
  }

  setAttribute(name, value) {
    const stringValue = String(value);
    this.attributes.set(name, stringValue);
    if (name === "id") this._id = stringValue;
    if (name === "class") this._className = stringValue;
    if (name.startsWith("data-"))
      this.dataset[
        name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())
      ] = stringValue;
  }

  removeAttribute(name) {
    this.attributes.delete(name);
    if (name.startsWith("data-"))
      delete this.dataset[
        name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())
      ];
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  focus() {
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }

  click() {
    if (this.disabled) return;
    this.focus();
    this.dispatchEvent({
      type: "click",
      preventDefault() {},
      stopPropagation() {},
    });
  }

  outerHTML() {
    const attributes = [...this.attributes.entries()]
      .map(([name, value]) => ` ${name}="${String(value)}"`)
      .join("");
    const hidden = this.hidden ? " hidden" : "";
    const text = this._text;
    const children = this.children.map((child) => child.outerHTML()).join("");
    return `<${this.tagName.toLowerCase()}${attributes}${hidden}>${text}${children}</${this.tagName.toLowerCase()}>`;
  }
}

class MiniDocument extends MiniEventTarget {
  constructor() {
    super();
    this.readyState = "complete";
    this.activeElement = null;
    this.body = new MiniElement(this, "body");
    this.body.setConnected(true);
    this.activeElement = this.body;
  }

  createElement(tagName) {
    return new MiniElement(this, tagName);
  }

  getElementById(id) {
    return this.find((candidate) => candidate.id === id);
  }

  querySelector(selector) {
    if (selector === "main")
      return this.find((candidate) => candidate.tagName === "MAIN");
    return undefined;
  }

  contains(candidate) {
    return Boolean(candidate?.isConnected);
  }

  find(predicate, root = this.body) {
    if (predicate(root)) return root;
    for (const child of root.children) {
      const match = this.find(predicate, child);
      if (match) return match;
    }
    return undefined;
  }

  findAll(predicate, root = this.body, output = []) {
    if (predicate(root)) output.push(root);
    for (const child of root.children) this.findAll(predicate, child, output);
    return output;
  }
}

function add(document, parent, tagName, id, text = "") {
  const node = document.createElement(tagName);
  if (id) node.id = id;
  if (text) node.textContent = text;
  parent.append(node);
  return node;
}

function createDocument() {
  const document = new MiniDocument();
  const main = add(document, document.body, "main");
  add(document, main, "button", "create-space", "Create");
  add(document, main, "input", "space-label");
  add(document, main, "input", "profile-disclosure-acknowledged");
  add(document, main, "div", "spaces");
  add(document, main, "span", "connection-status");
  add(document, main, "ul", "notices");

  const dialog = add(document, document.body, "div", "confirmation-dialog");
  dialog.hidden = true;
  const panel = add(document, dialog, "section", "confirmation-dialog-panel");
  panel.setAttribute("tabindex", "-1");
  add(document, panel, "h2", "confirmation-title");
  add(document, panel, "p", "confirmation-description");
  add(document, panel, "p", "confirmation-guidance");
  const actions = add(document, panel, "div");
  add(document, actions, "button", "confirmation-cancel", "Cancel");
  add(document, actions, "button", "confirmation-confirm", "Confirm");
  return document;
}

function keyEvent(key, options = {}) {
  let prevented = false;
  return {
    type: "keydown",
    key,
    shiftKey: options.shiftKey === true,
    get defaultPrevented() {
      return prevented;
    },
    preventDefault() {
      prevented = true;
    },
    stopPropagation() {},
  };
}

function waitForTurn() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function harness(name, sendMessage = async () => undefined) {
  const document = createDocument();
  const messages = [];
  const listeners = [];
  const chrome = {
    runtime: {
      id: "extension-test",
      getURL: (path) => `chrome-extension://extension-test/${path}`,
      onMessage: { addListener: (listener) => listeners.push(listener) },
      sendMessage: async (message) => {
        messages.push(message);
        return sendMessage(message);
      },
    },
  };
  globalThis.document = document;
  globalThis.chrome = chrome;
  const moduleUrl = new URL("../src/sidepanel/app.mjs", import.meta.url);
  const app = await import(
    `${moduleUrl.href}?case=${encodeURIComponent(name)}`
  );
  const receive = (message) =>
    listeners[0]?.(message, {
      id: chrome.runtime.id,
      url: chrome.runtime.getURL("src/service-worker.mjs"),
      frameId: 0,
    });
  return { app, chrome, document, messages, receive };
}

function actionButton(document, action) {
  return document.find(
    (candidate) =>
      candidate.tagName === "BUTTON" && candidate.dataset.action === action,
  );
}

test("side panel renders sanitized space and page warnings without logical or browser IDs", async () => {
  const rawSpaceId = "space_private_42";
  const rawPageId = "page_private_99";
  const { document, receive } = await harness("warnings");
  receive({
    type: "agentyc.host_event",
    event: "host.spaces",
    payload: {
      spaces: [
        {
          space_id: rawSpaceId,
          label: "Research",
          lifecycle: "managed",
          owner: "agent",
          warning: `page_id=${rawPageId}; tabId=77 is not available`,
          pages: [
            {
              page_id: rawPageId,
              label: "Notes",
              lifecycle: "bound",
              ownership: "agent",
              warnings: [`targetId=target_private_1 for ${rawPageId}`],
            },
          ],
        },
      ],
    },
  });

  const rendered = document.body.outerHTML();
  assert.match(document.body.textContent, /not available/);
  assert.match(document.body.textContent, /\[hidden identifier\]/);
  assert.doesNotMatch(rendered, new RegExp(rawSpaceId));
  assert.doesNotMatch(rendered, new RegExp(rawPageId));
  assert.doesNotMatch(rendered, /target_private_1|tabId=77/);
  assert.equal(actionButton(document, "pause").dataset.spaceId, undefined);
});

test("every non-create action opens a cancellable confirmation and traps keyboard focus", async () => {
  const { app, document, messages, receive } = await harness("confirmation");
  const spaceId = "space_confirm_1";
  receive({
    type: "agentyc.host_event",
    event: "host.spaces",
    payload: {
      spaces: [
        {
          space_id: spaceId,
          label: "Review",
          lifecycle: "managed",
          owner: "agent",
          intent_tickets: { pause: { ticket_id: "ticket_pause_1" } },
          pages: [],
        },
      ],
    },
  });

  const pause = actionButton(document, "pause");
  pause.click();
  const dialog = document.getElementById("confirmation-dialog");
  const cancel = document.getElementById("confirmation-cancel");
  const confirm = document.getElementById("confirmation-confirm");
  assert.equal(dialog.hidden, false);
  assert.equal(document.activeElement, cancel);
  assert.match(dialog.textContent, /Confirm Pause/);
  assert.match(dialog.textContent, /Nothing is sent until you choose Confirm/);
  assert.equal(messages.length, 0);
  assert.equal(pause.disabled, true);

  const tabForward = keyEvent("Tab");
  document.dispatchEvent(tabForward);
  assert.equal(document.activeElement, confirm);
  assert.equal(tabForward.defaultPrevented, true);
  document.dispatchEvent(keyEvent("Tab"));
  assert.equal(document.activeElement, cancel);
  document.dispatchEvent(keyEvent("Tab", { shiftKey: true }));
  assert.equal(document.activeElement, confirm);

  document.dispatchEvent(keyEvent("Escape"));
  assert.equal(dialog.hidden, true);
  assert.equal(document.activeElement, pause);
  assert.equal(messages.length, 0);
  assert.equal(await app.confirmConfirmation(), false);
});

test("confirmation sends only after approval, preserves the host ticket, and restores focus after busy state", async () => {
  let release;
  const request = new Promise((resolve) => {
    release = resolve;
  });
  const { app, document, messages, receive } = await harness(
    "busy",
    async () => request,
  );
  const spaceId = "space_busy_1";
  const ticket = { ticket_id: "ticket_pause_busy", action: "pause" };
  receive({
    type: "agentyc.host_event",
    event: "host.spaces",
    payload: {
      spaces: [
        {
          space_id: spaceId,
          label: "Operations",
          lifecycle: "managed",
          owner: "agent",
          intent_tickets: { pause: ticket },
          pages: [],
        },
      ],
    },
  });

  const pause = actionButton(document, "pause");
  pause.click();
  const confirmation = app.confirmConfirmation();
  await waitForTurn();
  assert.equal(messages.length, 1);
  assert.equal(messages[0].action, "pause");
  assert.deepEqual(messages[0].params, { space_id: spaceId });
  assert.deepEqual(messages[0].intent_ticket, ticket);
  assert.equal(document.getElementById("confirmation-dialog").hidden, false);
  assert.equal(document.getElementById("confirmation-confirm").disabled, true);
  assert.equal(document.getElementById("confirmation-cancel").disabled, true);
  assert.equal(actionButton(document, "finish").disabled, true);
  assert.match(
    document.getElementById("confirmation-dialog").textContent,
    /still in progress/,
  );

  release();
  await confirmation;
  assert.equal(document.getElementById("confirmation-dialog").hidden, true);
  assert.equal(document.activeElement.dataset.action, "pause");
  assert.equal(document.activeElement.dataset.spaceId, undefined);
});
