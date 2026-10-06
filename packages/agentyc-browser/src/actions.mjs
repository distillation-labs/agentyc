import { AgentycError } from "./errors.mjs";
import {
  invalidArgument,
  normalizeNow,
  requireLeaseEpoch,
  transportOptions,
} from "./constants.mjs";
import { operationForAction } from "./operations.mjs";

export async function actionStatus(client, actionId, options = {}) {
  return client.request(
    "action.status",
    { action_id: actionId },
    transportOptions(options),
  );
}

export async function reconcileAction(
  client,
  actionId,
  leaseEpoch,
  now,
  options = {},
) {
  const authorizedLeaseEpoch = requireLeaseEpoch(leaseEpoch);
  return client.request(
    "action.reconcile",
    {
      action_id: actionId,
      lease_epoch: authorizedLeaseEpoch,
      now: normalizeNow(now),
    },
    transportOptions(options),
  );
}

let actionSequence = 0;

function logicalActionIdentity(prefix) {
  actionSequence = (actionSequence + 1) % 1_000_000_000;
  return `${prefix}sdk_${Date.now().toString(36)}_${actionSequence.toString(36)}`;
}

function actionArgumentError(operation) {
  return new AgentycError({
    code: "invalid_argument",
    message: `unsupported action operation: ${String(operation)}`,
    retryable: false,
    guidance: "none",
    details: { operation },
  });
}

function actionCapabilityError(operation, definition, reason = undefined) {
  return new AgentycError({
    code: "permission_denied",
    message:
      reason ??
      definition.unsupportedReason ??
      `action operation ${operation} is unavailable in the direct interface`,
    retryable: false,
    guidance: "none",
    details: { operation },
  });
}

function payloadRequiresIntent(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return false;
  }
  const sensitiveBoundaries = new Set([
    "login_challenge",
    "payment",
    "destructive_submit",
    "permission",
    "upload",
    "cookies",
    "cookie",
    "evaluate",
  ]);
  return Object.entries(payload).some(([key, value]) => {
    const normalizedKey = key.replace(/[^a-z0-9]/gi, "").toLowerCase();
    return (
      (normalizedKey === "sensitiveboundary" ||
        normalizedKey === "policyboundary") &&
      typeof value === "string" &&
      sensitiveBoundaries.has(value)
    );
  });
}

function requireElementKeyRef(payload) {
  const encoded = payload?.element_ref ?? payload?.ref;
  let elementRef = encoded;
  if (typeof encoded === "string") {
    try {
      elementRef = JSON.parse(encoded);
    } catch {
      throw invalidArgument("element_ref must be a target-bound element ref");
    }
  }
  if (
    !elementRef ||
    typeof elementRef !== "object" ||
    Array.isArray(elementRef) ||
    typeof elementRef.element_key !== "string" ||
    elementRef.element_key.length === 0
  ) {
    throw invalidArgument(
      "click and input actions require an element_ref bound to a snapshot element_key",
    );
  }
}

export function assertSupportedAction(operation, payload = undefined) {
  const definition = operationForAction(operation);
  if (!definition) throw actionArgumentError(operation);
  if (!definition.supported) throw actionCapabilityError(operation, definition);
  if (operation === "click" || operation === "input") {
    requireElementKeyRef(payload);
  }
  if (payloadRequiresIntent(payload)) {
    throw actionCapabilityError(
      operation,
      definition,
      "sensitive action payload requires a host-issued user-intent ticket; the direct interfaces do not expose ticket issuance",
    );
  }
  return definition;
}

export async function submitAction(client, request) {
  if (!request || typeof request !== "object" || Array.isArray(request)) {
    throw invalidArgument("action request must be an object");
  }
  const {
    signal,
    request_id: suppliedRequestId,
    requestId,
    action_id: suppliedActionId,
    actionId,
    idempotency_key: suppliedIdempotencyKey,
    idempotencyKey,
    deadline_ms: suppliedDeadlineMs,
    deadlineMs,
    now: suppliedNow,
    ...input
  } = request;

  assertSupportedAction(input.operation, input.payload);

  const completeRequest = Object.fromEntries(
    Object.entries({
      ...input,
      request_id:
        suppliedRequestId ?? requestId ?? logicalActionIdentity("req_"),
      action_id:
        suppliedActionId ?? actionId ?? logicalActionIdentity("action_"),
      idempotency_key:
        suppliedIdempotencyKey ??
        idempotencyKey ??
        logicalActionIdentity("idem_"),
      now: normalizeNow(suppliedNow),
    }).filter(([, value]) => value !== undefined),
  );
  return client.request("action.execute", completeRequest, {
    mayHaveSideEffects: true,
    requestId: completeRequest.request_id,
    idempotencyKey: completeRequest.idempotency_key,
    deadlineMs: suppliedDeadlineMs ?? deadlineMs,
    signal,
  });
}
