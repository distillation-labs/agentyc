export const PANEL_ACTIONS = Object.freeze([
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

const ACTION_LABELS = Object.freeze({
  create: "Create",
  pause: "Pause",
  stop: "Stop",
  takeover: "Take over",
  return_control: "Return control",
  handoff: "Hand off",
  finish: "Finish",
  retain: "Retain",
  release: "Release",
});

export function actionLabel(action) {
  return ACTION_LABELS[action] || action.replaceAll("_", " ");
}

export async function sendPanelAction({
  chromeApi = globalThis.chrome,
  action,
  params = {},
  intentTicket,
} = {}) {
  if (!PANEL_ACTIONS.includes(action))
    throw new Error("unsupported side-panel action");
  if (action !== "create" && !intentTicket)
    throw new Error("side-panel action requires a host intent ticket");
  const safeParams = {};
  for (const [key, value] of Object.entries(params)) {
    if (
      key === "intent_ticket" ||
      key === "tabId" ||
      key === "targetId" ||
      key === "sessionId" ||
      key === "groupId" ||
      (key.endsWith("_id") && key !== "space_id" && key !== "page_id")
    )
      continue;
    safeParams[key] = value;
  }
  return chromeApi?.runtime?.sendMessage?.({
    type: "agentyc.sidepanel.request",
    action,
    params: safeParams,
    ...(intentTicket ? { intent_ticket: intentTicket } : {}),
  });
}
