# Public API Reference

Agentyc's primary interface is the host-backed CLI and Node SDK. MCP is compatibility-only and exposes logical task-space operations over stdio; it does not own browser state.

## CLI

Create a task space only after disclosing that the existing Chrome profile is shared:

```bash
agentyc space create --label Research --accept-shared-profile-disclosure
```

Existing-Chrome spaces share cookies, storage, history, permissions, and installed extensions. The host rejects creation without the explicit acknowledgement.

Connect an MCP client over stdio with `agentyc mcp`; `agentyc` with no subcommand is equivalent. `--offline` selects the deterministic fake-host test seam; it is not a browser connection. The old direct-CDP MCP mode and the MCP Streamable HTTP `serve` command have been removed.

## MCP client configuration

```json
{
  "mcp": {
    "agentyc": {
      "type": "local",
      "command": ["agentyc", "mcp"]
    }
  }
}
```

The connected adapter uses the owner-only local host socket. It accepts logical space/page/action identities and returns canonical structured host errors. Remote routes unsupported by the local protocol return `capability_unavailable`; the current route inventory and limitations are listed in [MCP compatibility](mcp-compatibility.md).

## Tool domains

The current host-backed MCP contract groups operations into spaces, leases/control, pages, snapshots, actions, and events. Tool names use the `host_*` prefix. It does not expose the old `browser_*` API, raw tab IDs, a CDP URL, or per-tab rename. Creating a task space requires:

```json
{
  "label": "research",
  "profile_scope": "shared_existing_profile",
  "shared_state_notice": "shared_profile_state",
  "isolation_claim": false,
  "profile_disclosure_acknowledged": true
}
```

The acknowledgement means the task space uses the existing shared Chrome profile; it is not a profile-isolation promise.

`host_lease_acknowledge_fence` retries the current authority's pending takeover fence at the same lease epoch and renews its TTL. It still requires durable extension-fence acknowledgement and page-rebind proof; it cannot bypass either boundary.

## Result semantics

Tool-operation failures are returned with MCP `isError=true` and structured error metadata (`code`, `retryable`, `guidance`, and `message`). Malformed requests, unknown methods/tools, invalid protocol state, and transport errors remain protocol/transport failures. A dispatched action with an uncertain outcome must be reconciled before retrying.

## Related docs

- [Local CLI and SDK API](api-local.md)
- [Features](features.md)
- [Architecture](architecture.md)
- [Configuration](configuration.md)
- [MCP compatibility status](mcp-compatibility.md)
- [Release gate](release-gate.md)
