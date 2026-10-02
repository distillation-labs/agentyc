const MAX_LABEL = 128;
const MAX_PAGES = 256;

function text(value, max = MAX_LABEL) {
  return typeof value === "string" ? value.slice(0, max) : "";
}

export function sanitizePage(page = {}) {
  return {
    page_id: typeof page.page_id === "string" ? page.page_id : undefined,
    label: text(page.label || page.title || "Page"),
    status: text(page.lifecycle || page.binding_state || "unknown", 64),
    ownership: text(page.ownership || "unmanaged", 64),
    url: text(page.url, 512),
    warning: text(page.warning, 256),
  };
}

export function sanitizeSpace(space = {}) {
  return {
    space_id: typeof space.space_id === "string" ? space.space_id : undefined,
    label: text(space.label || "Task space"),
    status: text(space.lifecycle || space.status || "unknown", 64),
    owner: text(space.owner_class || space.owner || "unknown", 64),
    warning: text(space.warning || space.capability_warning, 256),
    intent_ticket:
      space.intent_ticket && typeof space.intent_ticket === "object"
        ? space.intent_ticket
        : undefined,
    pages: Array.isArray(space.pages)
      ? space.pages.slice(0, MAX_PAGES).map(sanitizePage)
      : [],
  };
}

export function initialState() {
  return {
    connected: false,
    spaces: [],
    notices: [],
    busy: false,
  };
}

export function reduceState(state, message) {
  const next = {
    ...state,
    spaces: state.spaces.map((space) => ({
      ...space,
      pages: [...space.pages],
    })),
    notices: [...state.notices].slice(-20),
  };
  if (!message || typeof message !== "object") return next;
  if (
    message.type === "agentyc.host_event" ||
    message.type === "agentyc.event"
  ) {
    const event = message.event || "";
    if (event.startsWith("native.connected")) next.connected = true;
    if (
      event.startsWith("native.disconnected") ||
      event.startsWith("native.rejected")
    )
      next.connected = false;
    if (event === "host.spaces" && Array.isArray(message.payload?.spaces)) {
      next.spaces = message.payload.spaces.map(sanitizeSpace);
    }
    if (message.payload?.space_id && message.payload?.lifecycle) {
      const index = next.spaces.findIndex(
        (space) => space.space_id === message.payload.space_id,
      );
      if (index >= 0)
        next.spaces[index] = sanitizeSpace({
          ...next.spaces[index],
          ...message.payload,
        });
    }
    if (
      event.includes("failed") ||
      event.includes("rejected") ||
      event.includes("unknown")
    ) {
      next.notices.push(text(message.payload?.code || event, 128));
    }
  }
  if (message.type === "agentyc.panel.state") {
    next.connected = Boolean(message.connected);
    if (Array.isArray(message.spaces))
      next.spaces = message.spaces.map(sanitizeSpace);
  }
  return next;
}

export function displayLabel(value, fallback = "Task space") {
  const label = text(value);
  return label || fallback;
}
