const MAX_LABEL = 128;
const MAX_STATUS = 64;
const MAX_URL = 512;
const MAX_WARNING = 256;
const MAX_WARNINGS = 8;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f]/g;
const UUID_PATTERN =
  /\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/gi;
const IDENTIFIER_TOKEN_PATTERN =
  /\b(?:space|page|tab|target|session|group|window|frame|backend[\s_-]*node|execution[\s_-]*context|loader|object|request|script)[_-][a-z0-9][a-z0-9_-]*\b/gi;
const IDENTIFIER_ASSIGNMENT_PATTERN =
  /\b(?:space|page|tab|target|session|group|window|frame|backend[\s_-]*node|execution[\s_-]*context|loader|object|request|script)(?:[\s_-]*id)\b\s*(?::|=|->|\bis\b|\bwas\b)?\s*["'`]?[-a-z0-9][a-z0-9._:/-]*/gi;
const GENERIC_ID_ASSIGNMENT_PATTERN =
  /\b(?:id|identifier)\b\s*(?::|=)\s*["'`]?[-a-z0-9][a-z0-9._:/-]*/gi;

function text(value, max = MAX_LABEL) {
  return typeof value === "string"
    ? value.replace(CONTROL_CHARACTERS, " ").slice(0, max).trim()
    : "";
}

function redactWarningIdentifiers(value) {
  return value
    .replace(IDENTIFIER_ASSIGNMENT_PATTERN, "[hidden identifier]")
    .replace(GENERIC_ID_ASSIGNMENT_PATTERN, "[hidden identifier]")
    .replace(IDENTIFIER_TOKEN_PATTERN, "[hidden identifier]")
    .replace(UUID_PATTERN, "[hidden identifier]");
}

function warningValues(value) {
  if (Array.isArray(value)) return value.flatMap(warningValues);
  if (typeof value === "string") return [value];
  if (value && typeof value === "object") {
    return warningValues(value.message ?? value.warning ?? value.code);
  }
  return [];
}

export function sanitizeWarning(value) {
  const bounded = text(value, MAX_WARNING);
  if (!bounded) return "";
  return redactWarningIdentifiers(bounded).replace(/\s+/g, " ").trim();
}

export function sanitizeWarnings(...values) {
  const output = [];
  for (const value of values.flatMap(warningValues)) {
    const warning = sanitizeWarning(value);
    if (warning && !output.includes(warning)) output.push(warning);
    if (output.length >= MAX_WARNINGS) break;
  }
  return output;
}

function warningsFor(record = {}) {
  return sanitizeWarnings(
    record.warnings,
    record.warning,
    record.capability_warnings,
    record.capability_warning,
  );
}

export function sanitizePage(page = {}) {
  const warnings = warningsFor(page);
  return {
    page_id: typeof page.page_id === "string" ? page.page_id : undefined,
    label: text(page.label || page.title || "Page"),
    status: text(page.lifecycle || page.binding_state || "unknown", MAX_STATUS),
    ownership: text(page.ownership || "unmanaged", MAX_STATUS),
    url: text(page.url, MAX_URL),
    warning: warnings[0] || "",
    warnings,
  };
}

export function sanitizeSpace(space = {}) {
  const warnings = warningsFor(space);
  return {
    space_id: typeof space.space_id === "string" ? space.space_id : undefined,
    label: text(space.label || "Task space"),
    status: text(space.lifecycle || space.status || "unknown", MAX_STATUS),
    owner: text(space.owner_class || space.owner || "unknown", MAX_STATUS),
    warning: warnings[0] || "",
    warnings,
    intent_ticket:
      space.intent_ticket && typeof space.intent_ticket === "object"
        ? space.intent_ticket
        : undefined,
    intent_tickets:
      space.intent_tickets && typeof space.intent_tickets === "object"
        ? Object.fromEntries(
            Object.entries(space.intent_tickets).filter(
              ([, ticket]) => ticket && typeof ticket === "object",
            ),
          )
        : {},
    pages: Array.isArray(space.pages)
      ? space.pages.slice(0, 256).map(sanitizePage)
      : [],
  };
}

export function initialState() {
  return {
    connected: false,
    spacesLoaded: false,
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
    notices: [...state.notices].slice(-19),
  };
  if (!message || typeof message !== "object") return next;
  if (
    message.type === "agentyc.host_event" ||
    message.type === "agentyc.event"
  ) {
    const event = typeof message.event === "string" ? message.event : "";
    if (event.startsWith("native.connected")) next.connected = true;
    if (
      event.startsWith("native.disconnected") ||
      event.startsWith("native.rejected")
    ) {
      next.connected = false;
      next.spacesLoaded = false;
    }
    if (event === "host.spaces" && Array.isArray(message.payload?.spaces)) {
      next.spacesLoaded = true;
      next.spaces = message.payload.spaces.map(sanitizeSpace);
    }
    if (typeof message.payload?.space_id === "string") {
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
      const notice = sanitizeWarning(
        message.payload?.message || message.payload?.code || event,
      );
      if (notice) next.notices.push(notice);
    }
  }
  if (message.type === "agentyc.panel.state") {
    next.connected = Boolean(message.connected);
    if (Array.isArray(message.spaces)) {
      next.spacesLoaded = true;
      next.spaces = message.spaces.map(sanitizeSpace);
    }
  }
  return next;
}

export function displayLabel(value, fallback = "Task space") {
  const label = text(value);
  return label || fallback;
}
