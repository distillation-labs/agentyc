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

export function actionLabel(action) {
  return action.replaceAll("_", " ");
}

export async function sendPanelAction({
  chromeApi = globalThis.chrome,
  action,
  params = {},
  intentTicket,
} = {}) {
  if (!PANEL_ACTIONS.includes(action))
    throw new Error("unsupported side-panel action");
  const safeParams = {};
  for (const [key, value] of Object.entries(params)) {
    if (
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
