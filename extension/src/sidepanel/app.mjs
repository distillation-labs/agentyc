import { PANEL_ACTIONS, actionLabel, sendPanelAction } from "./controls.mjs";
import {
  displayLabel,
  initialState,
  reduceState,
  sanitizeWarnings,
} from "./state.mjs";

const chromeApi = globalThis.chrome;
const CREATE_HANDLER_BOUND = Symbol("create-handler-bound");
const DIALOG_HANDLER_BOUND = Symbol("dialog-handler-bound");
let state = initialState();
let pendingConfirmation;
let confirmationSequence = 0;
let installed = false;
let keydownInstalled = false;
let renderedActionButtons = new Map();

function append(parent, ...children) {
  if (typeof parent?.append === "function") parent.append(...children);
  else children.forEach((child) => parent?.appendChild?.(child));
}

function setAttribute(element, name, value) {
  element?.setAttribute?.(name, String(value));
}

function removeAttribute(element, name) {
  element?.removeAttribute?.(name);
}

function setDisabled(element, disabled) {
  if (!element) return;
  element.disabled = Boolean(disabled);
  if (disabled) setAttribute(element, "aria-disabled", "true");
  else removeAttribute(element, "aria-disabled");
}

function element(id) {
  return document.getElementById(id);
}

function actionText(action) {
  return actionLabel(action);
}

function actionButtonKey(action, spaceId) {
  return `${action}\u0000${spaceId || ""}`;
}

function displayWarnings(record) {
  return sanitizeWarnings(record?.warnings, record?.warning);
}

function appendWarnings(container, record) {
  for (const warningText of displayWarnings(record)) {
    const warning = document.createElement("p");
    warning.className = "warning";
    warning.setAttribute("role", "status");
    warning.textContent = warningText;
    append(container, warning);
  }
}

function render() {
  const root = element("spaces");
  const status = element("connection-status");
  const notices = element("notices");
  if (!root || !status || !notices) return;

  status.textContent = state.busy
    ? "Working"
    : state.connected
      ? "Connected"
      : "Waiting for host";
  setAttribute(status, "data-connected", state.connected);
  const main = document.querySelector?.("main");
  setAttribute(main, "aria-busy", state.busy);

  root.replaceChildren?.();
  renderedActionButtons = new Map();
  if (state.spaces.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No task spaces are available.";
    append(root, empty);
  }

  for (const space of state.spaces) {
    const card = document.createElement("section");
    card.className = "space-card";
    setAttribute(card, "aria-label", displayLabel(space.label));

    const heading = document.createElement("div");
    heading.className = "space-heading";
    const headingText = document.createElement("div");
    const title = document.createElement("h2");
    title.textContent = displayLabel(space.label);
    const metadata = document.createElement("p");
    metadata.textContent = `${space.status} · ${space.owner}`;
    append(headingText, title, metadata);
    append(heading, headingText);
    append(card, heading);
    appendWarnings(card, space);

    const pages = document.createElement("ul");
    pages.className = "pages";
    for (const page of space.pages) {
      const item = document.createElement("li");
      const pageContent = document.createElement("div");
      pageContent.className = "page-content";
      const pageLabel = document.createElement("span");
      pageLabel.className = "page-label";
      pageLabel.textContent = displayLabel(page.label, "Page");
      const pageStatus = document.createElement("small");
      pageStatus.textContent = `${page.status} · ${page.ownership}`;
      append(pageContent, pageLabel, pageStatus);
      appendWarnings(pageContent, page);
      append(item, pageContent);
      append(pages, item);
    }
    append(card, pages);

    const actions = document.createElement("div");
    actions.className = "actions";
    for (const action of PANEL_ACTIONS.filter(
      (candidate) => candidate !== "create",
    )) {
      const button = document.createElement("button");
      button.type = "button";
      if (button.dataset) button.dataset.action = action;
      else setAttribute(button, "data-action", action);
      button.textContent = actionText(action);
      setAttribute(
        button,
        "aria-label",
        `${actionText(action)} ${displayLabel(space.label)} task space`,
      );
      button.addEventListener("click", () => {
        if (!button.disabled)
          requestConfirmation(
            action,
            { space_id: space.space_id },
            space,
            button,
          );
      });
      renderedActionButtons.set(
        actionButtonKey(action, space.space_id),
        button,
      );
      append(actions, button);
    }
    append(card, actions);
    append(root, card);
  }

  notices.replaceChildren?.(
    ...state.notices.map((notice) => {
      const item = document.createElement("li");
      item.textContent = notice;
      return item;
    }),
  );
  setControlDisabledState();
}

function createDialogParts() {
  const root = document.createElement("div");
  root.id = "confirmation-dialog";
  root.className = "dialog-backdrop";
  root.hidden = true;
  setAttribute(root, "aria-hidden", "true");

  const panel = document.createElement("section");
  panel.id = "confirmation-dialog-panel";
  panel.className = "dialog";
  setAttribute(panel, "role", "dialog");
  setAttribute(panel, "aria-modal", "true");
  setAttribute(panel, "aria-labelledby", "confirmation-title");
  setAttribute(
    panel,
    "aria-describedby",
    "confirmation-description confirmation-guidance",
  );
  setAttribute(panel, "tabindex", "-1");

  const title = document.createElement("h2");
  title.id = "confirmation-title";
  title.textContent = "Confirm action";
  const description = document.createElement("p");
  description.id = "confirmation-description";
  const guidance = document.createElement("p");
  guidance.id = "confirmation-guidance";
  guidance.className = "dialog-guidance";
  const dialogActions = document.createElement("div");
  dialogActions.className = "dialog-actions";
  const cancel = document.createElement("button");
  cancel.id = "confirmation-cancel";
  cancel.type = "button";
  cancel.textContent = "Cancel";
  const confirm = document.createElement("button");
  confirm.id = "confirmation-confirm";
  confirm.type = "button";
  confirm.className = "primary";
  confirm.textContent = "Confirm";

  append(dialogActions, cancel, confirm);
  append(panel, title, description, guidance, dialogActions);
  append(root, panel);
  append(document.body, root);
  return { root, panel, title, description, guidance, cancel, confirm };
}

function confirmationElements() {
  const root = element("confirmation-dialog");
  const panel = element("confirmation-dialog-panel");
  const title = element("confirmation-title");
  const description = element("confirmation-description");
  const guidance = element("confirmation-guidance");
  const cancel = element("confirmation-cancel");
  const confirm = element("confirmation-confirm");
  if (root && panel && title && description && guidance && cancel && confirm)
    return { root, panel, title, description, guidance, cancel, confirm };
  if (document.body?.append) return createDialogParts();
  return null;
}

function setDialogVisible(visible) {
  const dialog = confirmationElements();
  if (!dialog) return;
  dialog.root.hidden = !visible;
  setAttribute(dialog.root, "aria-hidden", !visible);
}

function setDialogCopy() {
  const dialog = confirmationElements();
  const confirmation = pendingConfirmation;
  if (!dialog) return;
  if (!confirmation) {
    setDisabled(dialog.cancel, false);
    setDisabled(dialog.confirm, false);
    setAttribute(dialog.panel, "aria-busy", false);
    return;
  }
  const label = displayLabel(confirmation.space?.label, "this task space");
  const action = actionText(confirmation.action);
  if (confirmation.busy) {
    dialog.title.textContent = `Sending ${action}`;
    dialog.description.textContent = `Your choice was sent for "${label}". The host request is still in progress.`;
    dialog.guidance.textContent =
      "Please wait. The controls are disabled until the request completes.";
    dialog.confirm.textContent = "Sending…";
  } else {
    dialog.title.textContent = `Confirm ${action}`;
    dialog.description.textContent = `You are about to send ${action} for "${label}".`;
    dialog.guidance.textContent =
      "Nothing is sent until you choose Confirm. Choose Cancel or press Escape to keep the current state.";
    dialog.confirm.textContent = `Confirm ${action}`;
  }
  setDisabled(dialog.confirm, confirmation.busy);
  setDisabled(dialog.cancel, confirmation.busy);
  setAttribute(dialog.panel, "aria-busy", confirmation.busy);
}

function setControlDisabledState() {
  const locked = state.busy || Boolean(pendingConfirmation);
  setDisabled(element("create-space"), locked);
  setDisabled(element("space-label"), locked);
  setDisabled(element("profile-disclosure-acknowledged"), locked);
  for (const button of renderedActionButtons.values())
    setDisabled(button, locked);
  setDialogCopy();
}

function focusableDialogElements(dialog) {
  return [dialog.cancel, dialog.confirm].filter(
    (candidate) =>
      candidate && !candidate.disabled && candidate.hidden !== true,
  );
}

function focusElement(candidate) {
  candidate?.focus?.();
}

function handleKeydown(event) {
  if (!pendingConfirmation) return;
  if (event.key === "Escape") {
    if (pendingConfirmation.busy) return;
    event.preventDefault?.();
    event.stopPropagation?.();
    cancelConfirmation();
    return;
  }
  if (event.key !== "Tab") return;
  const dialog = confirmationElements();
  if (!dialog) return;
  const focusable = focusableDialogElements(dialog);
  event.preventDefault?.();
  if (focusable.length === 0) {
    focusElement(dialog.panel);
    return;
  }
  const active = document.activeElement;
  const index = focusable.indexOf(active);
  const nextIndex = event.shiftKey
    ? index <= 0
      ? focusable.length - 1
      : index - 1
    : index < 0 || index === focusable.length - 1
      ? 0
      : index + 1;
  focusElement(focusable[nextIndex]);
}

function bindDialog(dialog) {
  if (!dialog || dialog.root[DIALOG_HANDLER_BOUND]) return;
  dialog.root[DIALOG_HANDLER_BOUND] = true;
  dialog.cancel.addEventListener("click", () => cancelConfirmation());
  dialog.confirm.addEventListener("click", () => void confirmConfirmation());
  if (!keydownInstalled && document.addEventListener) {
    document.addEventListener("keydown", handleKeydown);
    keydownInstalled = true;
  }
}

function focusRestorationTarget(confirmation) {
  const rendered = renderedActionButtons.get(
    actionButtonKey(confirmation.action, confirmation.space?.space_id),
  );
  if (rendered) return rendered;
  const current = confirmation.trigger;
  if (current && current.isConnected !== false) return current;
  return undefined;
}

function closeConfirmation(confirmation, restoreFocus = true) {
  setDialogVisible(false);
  setControlDisabledState();
  if (restoreFocus) {
    const target = focusRestorationTarget(confirmation);
    focusElement(target || element("create-space"));
  }
}

function requestConfirmation(action, params = {}, space, trigger) {
  if (action === "create" || !PANEL_ACTIONS.includes(action)) return false;
  if (state.busy || pendingConfirmation) return false;
  const dialog = confirmationElements();
  if (!dialog) return false;
  pendingConfirmation = {
    id: ++confirmationSequence,
    action,
    params: { ...params },
    space,
    trigger: trigger || document.activeElement,
    busy: false,
  };
  setDialogCopy();
  setDialogVisible(true);
  setControlDisabledState();
  focusElement(dialog.cancel);
  return true;
}

async function confirmConfirmation() {
  const confirmation = pendingConfirmation;
  if (!confirmation || confirmation.busy || state.busy) return false;
  pendingConfirmation = { ...confirmation, busy: true };
  setDialogCopy();
  setControlDisabledState();
  focusElement(confirmationElements()?.panel);
  await invoke(confirmation.action, confirmation.params, confirmation.space, {
    confirmationId: confirmation.id,
  });
  return true;
}

function cancelConfirmation() {
  const confirmation = pendingConfirmation;
  if (!confirmation || confirmation.busy) return false;
  pendingConfirmation = undefined;
  closeConfirmation(confirmation);
  return true;
}

function resolveSpace(params, space) {
  if (space) return space;
  return state.spaces.find(
    (candidate) => candidate.space_id === params?.space_id,
  );
}

async function invoke(action, params = {}, space, options = {}) {
  const confirmed =
    action !== "create" &&
    options.confirmationId !== undefined &&
    pendingConfirmation?.id === options.confirmationId;
  if (action !== "create" && !confirmed) {
    requestConfirmation(action, params, resolveSpace(params, space));
    return undefined;
  }
  if (state.busy || (action === "create" && pendingConfirmation))
    return undefined;

  state = { ...state, busy: true };
  render();
  let response;
  try {
    response = await sendPanelAction({
      chromeApi,
      action,
      params,
      intentTicket:
        action === "create"
          ? undefined
          : (space?.intent_tickets?.[action] ?? space?.intent_ticket),
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
      payload: { code: error?.message || "side-panel request failed" },
    });
  } finally {
    state = { ...state, busy: false };
    const confirmation =
      options.confirmationId !== undefined &&
      pendingConfirmation?.id === options.confirmationId
        ? pendingConfirmation
        : undefined;
    if (confirmation) pendingConfirmation = undefined;
    render();
    if (confirmation) closeConfirmation(confirmation);
  }
  return response;
}

function install() {
  if (installed || typeof document === "undefined") return;
  installed = true;
  const dialog = confirmationElements();
  bindDialog(dialog);
  const createButton = element("create-space");
  if (createButton && !createButton[CREATE_HANDLER_BOUND]) {
    createButton[CREATE_HANDLER_BOUND] = true;
    createButton.addEventListener("click", () => {
      const input = element("space-label");
      const disclosure = element("profile-disclosure-acknowledged");
      if (disclosure?.checked !== true) {
        state = reduceState(state, {
          type: "agentyc.event",
          event: "panel.rejected",
          payload: {
            code: "profile_disclosure_required",
            message:
              "Accept the shared-profile notice before creating a space.",
          },
        });
        render();
        focusElement(disclosure);
        return;
      }
      const label = input?.value?.trim() || "Task space";
      void invoke("create", {
        label,
        profile_scope: "shared_existing_profile",
        shared_state_notice: "shared_profile_state",
        isolation_claim: false,
        profile_disclosure_acknowledged: true,
      });
      if (input) input.value = "";
    });
  }
  chromeApi?.runtime?.onMessage?.addListener?.((message, sender) => {
    const expectedUrl = chromeApi?.runtime?.getURL?.("src/service-worker.mjs");
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

export {
  cancelConfirmation,
  confirmConfirmation,
  install,
  invoke,
  render,
  requestConfirmation,
};
