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

  if (!operationForAction(input.operation)) {
    throw actionArgumentError(input.operation);
  }

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
