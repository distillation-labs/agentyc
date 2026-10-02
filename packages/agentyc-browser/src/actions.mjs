export async function actionStatus(client, actionId) {
  return client.request("action.status", { action_id: actionId });
}

export async function reconcileAction(client, actionId, leaseEpoch, now) {
  return client.request("action.reconcile", {
    action_id: actionId,
    lease_epoch: leaseEpoch,
    now,
  });
}

let actionSequence = 0;

function logicalActionIdentity(prefix) {
  actionSequence = (actionSequence + 1) % 1_000_000_000;
  return `${prefix}sdk_${Date.now().toString(36)}_${actionSequence.toString(36)}`;
}

export async function submitAction(client, request) {
  const completeRequest = {
    request_id: request.request_id ?? logicalActionIdentity("req_"),
    action_id: request.action_id ?? logicalActionIdentity("action_"),
    idempotency_key: request.idempotency_key ?? logicalActionIdentity("idem_"),
    ...request,
  };
  return client.request("action.execute", completeRequest, {
    mayHaveSideEffects: true,
  });
}
