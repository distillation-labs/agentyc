const requestEntries = [
  {
    key: "space.create",
    kind: "request",
    wireMethods: ["space.create"],
    sideEffecting: true,
    supported: true,
    sdk: "BrowserClient.createSpace",
    cli: { command: ["space", "create"] },
  },
  {
    key: "space.list",
    kind: "request",
    wireMethods: ["space.list"],
    sideEffecting: false,
    supported: true,
    sdk: "BrowserClient.listSpaces",
    cli: { command: ["space", "list"] },
  },
  {
    key: "space.prune",
    kind: "request",
    wireMethods: ["space.prune"],
    sideEffecting: true,
    supported: true,
    sdk: "BrowserClient.pruneSpaces",
    cli: { command: ["space", "prune"] },
  },
  {
    key: "space.claim",
    kind: "request",
    wireMethods: ["space.claim", "lease.acquire"],
    sideEffecting: true,
    supported: true,
    sdk: "TaskSpace.claim",
    cli: { command: ["space", "claim"] },
  },
  {
    key: "space.renew",
    kind: "request",
    wireMethods: ["space.renew", "lease.renew"],
    sideEffecting: true,
    supported: true,
    sdk: "TaskSpace.renew",
    cli: { command: ["space", "renew"] },
  },
  {
    key: "space.takeover",
    kind: "request",
    wireMethods: ["space.takeover"],
    sideEffecting: true,
    supported: true,
    sdk: "TaskSpace.takeover",
    cli: { command: ["space", "takeover"] },
  },
  {
    key: "space.takeover_with_control_ticket",
    kind: "request",
    wireMethods: ["space.takeover_with_control_ticket"],
    sideEffecting: true,
    supported: true,
    sdk: "TaskSpace.reclaim",
    cli: { command: ["space", "reclaim"] },
  },
  {
    key: "space.return",
    kind: "request",
    wireMethods: ["space.return", "space.return_control"],
    sideEffecting: true,
    supported: true,
    sdk: "TaskSpace.returnControl",
    cli: { command: ["space", "return"] },
  },
  {
    key: "space.finish",
    kind: "request",
    wireMethods: ["space.finish"],
    sideEffecting: true,
    supported: true,
    sdk: "TaskSpace.finish",
    cli: { command: ["space", "finish"] },
  },
  {
    key: "space.release",
    kind: "request",
    wireMethods: ["space.release"],
    sideEffecting: true,
    supported: true,
    sdk: "TaskSpace.release",
    cli: { command: ["space", "release"] },
  },
  {
    key: "page.create",
    kind: "request",
    wireMethods: ["page.create"],
    sideEffecting: true,
    supported: true,
    sdk: "TaskSpace.newPage",
    cli: { command: ["page", "create"] },
  },
  {
    key: "page.create_managed",
    kind: "request",
    wireMethods: ["page.create_managed"],
    sideEffecting: true,
    supported: true,
    sdk: "TaskSpace.newManagedPage",
    cli: { command: ["page", "create-managed"] },
  },
  {
    key: "page.close",
    kind: "request",
    wireMethods: ["page.close"],
    sideEffecting: true,
    supported: true,
    sdk: "Page.close",
    cli: { command: ["page", "close"] },
  },
  {
    key: "page.list",
    kind: "request",
    wireMethods: ["page.list"],
    sideEffecting: false,
    supported: true,
    sdk: "TaskSpace.listPages",
    cli: { command: ["page", "list"] },
  },
  {
    key: "page.inventory",
    kind: "request",
    wireMethods: ["page.inventory"],
    sideEffecting: false,
    supported: true,
    sdk: "TaskSpace.inventory",
    cli: { command: ["page", "inventory"] },
  },
  {
    key: "action.execute",
    kind: "request",
    wireMethods: ["action.execute"],
    sideEffecting: true,
    supported: true,
    sdk: "Page.action",
    cli: { command: ["action", "execute"] },
  },
  {
    key: "action.status",
    kind: "request",
    wireMethods: ["action.status"],
    sideEffecting: false,
    supported: true,
    sdk: "BrowserClient.actionStatus",
    cli: { command: ["action", "status"] },
  },
  {
    key: "action.reconcile",
    kind: "request",
    wireMethods: ["action.reconcile"],
    sideEffecting: true,
    supported: true,
    sdk: "BrowserClient.reconcileAction",
    cli: { command: ["action", "reconcile"] },
  },
  {
    key: "snapshot.read",
    kind: "request",
    wireMethods: ["snapshot.read", "snapshot"],
    sideEffecting: false,
    supported: true,
    sdk: "Page.snapshot",
    cli: { command: ["snapshot"] },
  },
  {
    key: "events.read",
    kind: "request",
    wireMethods: ["events.read", "events.resume"],
    sideEffecting: false,
    supported: true,
    sdk: "BrowserClient.events",
    cli: { command: ["events"] },
  },
  {
    key: "wait.for",
    kind: "request",
    wireMethods: ["wait.for"],
    sideEffecting: false,
    supported: true,
    sdk: "BrowserClient.waitFor",
    cli: { command: ["wait"] },
  },
  {
    key: "host.status",
    kind: "request",
    wireMethods: ["host.status"],
    sideEffecting: false,
    supported: true,
    sdk: "BrowserClient.hostStatus",
    cli: { command: ["host", "status"] },
  },
  {
    key: "action.cancel",
    kind: "request",
    wireMethods: ["action.cancel"],
    sideEffecting: true,
    supported: false,
    sdk: undefined,
    cli: undefined,
    unsupportedReason: "cancellation is a request-envelope operation, not an action operation",
  },
  {
    key: "page.navigate",
    kind: "request",
    wireMethods: ["page.navigate"],
    sideEffecting: true,
    supported: false,
    sdk: undefined,
    cli: undefined,
    unsupportedReason: "navigation is represented by action.execute with operation navigate",
  },
  {
    key: "page.adopt",
    kind: "request",
    wireMethods: ["page.adopt"],
    sideEffecting: true,
    supported: false,
    sdk: undefined,
    cli: undefined,
    unsupportedReason: "page adoption is not a Phase 2 local protocol operation",
  },
];

const actionEntries = [
  ["navigate", true],
  ["click", true],
  ["input", true],
  ["evaluate", true],
  ["scroll", true],
  ["wait", true],
  ["screenshot", true],
  ["storage_write", true],
  ["cookie_write", true],
  ["upload", true],
  ["close", true],
].map(([operation, supported]) => ({
  key: `action.${operation}`,
  kind: "action",
  operation,
  wireMethods: ["action.execute"],
  sideEffecting: true,
  supported,
  sdk: "Page.action",
  cli: { command: ["action", "execute"], option: "--operation" },
}));

function freezeEntry(entry) {
  const frozen = {
    ...entry,
    wireMethods: Object.freeze([...entry.wireMethods]),
    ...(entry.cli
      ? {
          cli: Object.freeze({
            ...entry.cli,
            command: Object.freeze([...entry.cli.command]),
          }),
        }
      : {}),
  };
  return Object.freeze(frozen);
}

/** One registry for canonical wire methods, SDK methods, CLI commands, and action names. */
export const OPERATION_REGISTRY = Object.freeze(
  [...requestEntries, ...actionEntries].map(freezeEntry),
);

const ACTION_BY_NAME = new Map(
  OPERATION_REGISTRY.filter((entry) => entry.kind === "action").map((entry) => [
    entry.operation,
    entry,
  ]),
);
const METHOD_BY_NAME = new Map();
for (const entry of OPERATION_REGISTRY) {
  for (const method of entry.wireMethods) {
    if (!METHOD_BY_NAME.has(method)) METHOD_BY_NAME.set(method, entry);
  }
}

export function operationForAction(operation) {
  return typeof operation === "string"
    ? ACTION_BY_NAME.get(operation)
    : undefined;
}

export function operationForMethod(method) {
  return typeof method === "string" ? METHOD_BY_NAME.get(method) : undefined;
}

export function methodMayHaveSideEffects(method, requested = false) {
  return Boolean(requested) || operationForMethod(method)?.sideEffecting === true;
}

export function actionOperationNames() {
  return [...ACTION_BY_NAME.keys()];
}
