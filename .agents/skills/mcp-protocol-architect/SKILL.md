---
name: mcp-protocol-architect
description: >
  Use for designing or refactoring MCP server surfaces around protocol primitives, transport
  choices, lifecycle, capability negotiation, discovery, notifications, and tool/resource/prompt
  boundaries. Trigger when the user asks how an MCP feature should be modeled; when to use a tool
  vs resource vs prompt; how clients should discover or react to changes; how to structure a
  protocol surface; or how to choose transports and capabilities cleanly. Do not use for ordinary
  Python refactors or low-level performance work.
when_to_use: >
  Especially useful for primitive selection, protocol correctness, lifecycle design, transport
  decisions, notifications, and long-term MCP API shape.
metadata:
  version: "0.2.0"
  category: mcp-development
  tags: [mcp, protocol, tools, resources, prompts, transport, lifecycle, capabilities]
license: Proprietary
---

# MCP Protocol Architect

Design the protocol surface first. Choose the right primitive, define the lifecycle, and only
then worry about the server implementation.

## Core Rule

Model the user-facing workflow before writing code.

- Tools are model-invoked actions with typed inputs.
- Resources are passive context the application reads.
- Prompts are reusable user-invoked interaction templates.

## Design Sequence

1. Name the workflow the user is trying to accomplish.
2. Split the workflow into primitive-sized responsibilities.
3. Decide what changes over time and what stays stable.
4. Choose the transport that matches deployment reality.
5. Define discovery, visibility, and notification behavior.
6. Only then map the design to server implementation.

## Protocol Guidance

### Primitive Selection

- Use a tool when the model should invoke an action or computation.
- Use a resource when the client should fetch browsable or passive data.
- Use a prompt when the user needs a reusable guided workflow.
- Keep static data out of tools when a resource fits better.
- Do not use prompts to hide a broken API surface.

### Lifecycle And Capabilities

- Respect initialization and capability negotiation.
- Model dynamic surfaces explicitly.
- Use list operations plus change notifications when components can appear or disappear.
- Prefer stable component keys and naming.

### Discovery And Visibility

- Make component discovery explicit.
- If a surface is filtered or role-based, model that up front.
- Dynamic catalogs should advertise changes instead of relying on clients to guess.

## Protocol Spec Versions

The MCP specification has evolved through three major versions:

| Version | Date | Key changes |
|---|---|---|
| `2024-11-05` | Nov 2024 | Initial spec; HTTP+SSE transport (dual endpoint) |
| `2025-03-26` | Mar 2025 | Streamable HTTP transport (single endpoint); current **stable** |
| `2026-07-28` | Jul 2026 | **Release candidate** (locked May 2026); stateless sessions, `Mcp-Method`/`Mcp-Name` headers, `ttlMs` caching, SEP-based extensions framework |

### Streamable HTTP (2025-03-26 — Current Stable)

- Single `/mcp` endpoint for all operations (vs dual-endpoint HTTP+SSE).
- Session identified via `Mcp-Session-Id` header (not query params).
- Client sends POST; server streams response as SSE when needed.
- No sticky sessions or shared session store required.
- `DELETE /mcp` for explicit session termination.
- Stateless mode available by setting `sessionId` to undefined.

### 2026-07-28 Release Candidate (Locked May 2026)

Key additions over 2025-03-26:
- `Mcp-Method` and `Mcp-Name` headers allow load balancers to route on operation type.
- `ttlMs` and `cacheScope` enable clients to cache `tools/list` responses.
- Stateless session model eliminates need for per-instance affinity.
- SEP (Specification Enhancement Proposal) framework for extensions like Tasks and MCP Apps.
- Tier 1 SDKs expected to ship support within the release window.

### Transport Choice Guidelines

- **stdio**: Local subprocess workflows, single-host integrations, IDE tool servers. Default for agentyc.
- **Streamable HTTP**: Remote access, auth, shared serving, stateless scaling. Use for production deployments.
- **HTTP+SSE (legacy)**: Only for backward compatibility with older clients. Don't choose for new designs.

## Output Expectations

Return:

1. primitive mapping
2. lifecycle and capability notes
3. transport recommendation
4. discovery and notification plan
5. implementation implications
6. tradeoffs and rejected alternatives

## Anti-Patterns

- treating everything as a tool
- using prompts to patch bad primitive design
- ignoring capability negotiation
- designing remote-first when the deployment is local-first
- confusing client-driven resources with model-driven tools
- skipping notifications for dynamic catalogs

## References

- `references/mcp-patterns.md`
- `references/eval-rubric.md`
