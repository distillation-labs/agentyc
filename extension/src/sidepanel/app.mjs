import { PANEL_ACTIONS, actionLabel, sendPanelAction } from "./controls.mjs";
import { displayLabel, initialState, reduceState } from "./state.mjs";

const chromeApi = globalThis.chrome;
let state = initialState();

function escapeText(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[character],
  );
}

function render() {
  const root = document.getElementById("spaces");
  const status = document.getElementById("connection-status");
  const notices = document.getElementById("notices");
  if (!root || !status || !notices) return;
  status.textContent = state.connected ? "Connected" : "Waiting for host";
  status.dataset.connected = String(state.connected);
  root.replaceChildren();
  if (state.spaces.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No task spaces are available.";
    root.append(empty);
  }
  for (const space of state.spaces) {
    const card = document.createElement("section");
    card.className = "space-card";
    card.innerHTML = `<div class="space-heading"><div><h2>${escapeText(displayLabel(space.label))}</h2><p>${escapeText(space.status)} · ${escapeText(space.owner)}</p></div></div>`;
    if (space.warning) {
      const warning = document.createElement("p");
      warning.className = "warning";
      warning.textContent = space.warning;
      card.append(warning);
    }
    const pages = document.createElement("ul");
    pages.className = "pages";
    for (const page of space.pages) {
      const item = document.createElement("li");
      item.innerHTML = `<span>${escapeText(displayLabel(page.label, "Page"))}</span><small>${escapeText(page.status)}</small>`;
      pages.append(item);
    }
    card.append(pages);
    const actions = document.createElement("div");
    actions.className = "actions";
    for (const action of PANEL_ACTIONS.filter(
      (candidate) => candidate !== "create",
    )) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.action = action;
      button.dataset.spaceId = space.space_id || "";
      button.textContent = actionLabel(action);
      button.addEventListener(
        "click",
        () => void invoke(action, { space_id: space.space_id }, space),
      );
      actions.append(button);
    }
    card.append(actions);
    root.append(card);
  }
  notices.replaceChildren(
    ...state.notices.map((notice) => {
      const item = document.createElement("li");
      item.textContent = notice;
      return item;
    }),
  );
}

async function invoke(action, params, space) {
  state = { ...state, busy: true };
  render();
  try {
    const response = await sendPanelAction({
      chromeApi,
      action,
      params,
      intentTicket:
        action === "create"
          ? undefined
          : space?.intent_tickets?.[action] ?? space?.intent_ticket,
    });
    if (response?.error)
      state = reduceState(state, {
        type: "agentyc.event",
        event: "panel.rejected",
        payload: response.error,
      });
  } catch (error) {
    state = reduceState(state, {
      type: "agentyc.event",
      event: "panel.failed",
      payload: { code: error.message },
    });
  } finally {
    state = { ...state, busy: false };
    render();
  }
}

function install() {
  document.getElementById("create-space")?.addEventListener("click", () => {
    const input = document.getElementById("space-label");
    const label = input?.value?.trim() || "Task space";
    void invoke("create", { label });
    if (input) input.value = "";
  });
  chromeApi?.runtime?.onMessage?.addListener?.((message, sender) => {
    const expectedUrl = chromeApi?.runtime?.getURL?.(
      "src/service-worker.mjs",
    );
    if (
      !sender ||
      sender.id !== chromeApi?.runtime?.id ||
      sender.tab !== undefined ||
      (sender.frameId !== undefined && sender.frameId !== 0) ||
      (expectedUrl && sender.url !== expectedUrl)
    )
      return;
    state = reduceState(state, message);
    render();
  });
  render();
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", install, { once: true });
  else install();
}

export { install, render, invoke };
