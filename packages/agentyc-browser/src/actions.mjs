export async function actionStatus(client, actionId, options = {}) {
  return client.request(
    "action.status",
    { action_id: actionId },
    { signal: options.signal },
  );
}

export async function reconcileAction(
  client,
  actionId,
  leaseEpoch,
  now,
  options = {},
) {
  return client.request(
    "action.reconcile",
    {
      action_id: actionId,
      lease_epoch: leaseEpoch,
      now,
    },
    { signal: options.signal },
  );
}

let actionSequence = 0;

function logicalActionIdentity(prefix) {
  actionSequence = (actionSequence + 1) % 1_000_000_000;
  return `${prefix}sdk_${Date.now().toString(36)}_${actionSequence.toString(36)}`;
}

export async function submitAction(client, request) {
  const { signal, ...input } = request;
  const completeRequest = {
    request_id: input.request_id ?? logicalActionIdentity("req_"),
    action_id: input.action_id ?? logicalActionIdentity("action_"),
    idempotency_key: input.idempotency_key ?? logicalActionIdentity("idem_"),
    ...input,
  };
  return client.request("action.execute", completeRequest, {
    mayHaveSideEffects: true,
    requestId: completeRequest.request_id,
    idempotencyKey: completeRequest.idempotency_key,
    signal,
  });
}
