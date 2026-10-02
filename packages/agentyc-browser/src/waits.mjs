export async function waitFor(client, condition, options = {}) {
  return client.request("wait.for", {
    condition,
    timeout_ms: options.timeoutMs,
    after: options.after,
  }, { mayHaveSideEffects: false });
}
