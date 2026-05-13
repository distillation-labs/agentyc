# MCP Protocol Patterns

Grounded in the official MCP architecture and server docs.

## Primitive Mapping

- Tools are model-invoked executable actions.
- Resources are passive, client-readable context.
- Prompts are reusable user-invoked workflows.
- Do not use tools for static context that belongs in a resource.
- Do not use prompts to hide a bad API surface.

## Lifecycle And Discovery

- Capability negotiation is part of the protocol, not optional ceremony.
- Dynamic catalogs should be discoverable through list operations.
- When the surface can change, clients need list-changed notifications.
- Stable keys and names matter because clients cache what they discover.

## Transport

- Use stdio for local subprocess and single-host integrations.
- Use Streamable HTTP for remote access, shared serving, or auth-heavy deployments.
- Treat SSE as legacy compatibility, not the default.
- Transport choice should follow deployment reality, not style preference.

## Good MCP Design

- keeps primitives narrow and typed
- makes discovery explicit through list operations
- uses resources for browsable or fetchable context
- uses tools for actions or derived computation
- uses prompts to package repeated workflows, not to hide APIs

## Bad MCP Design

- exposing static documents as tools
- collapsing a browsable context surface into giant tool results
- using prompts instead of fixing the API surface
- ignoring notifications when lists can change at runtime
- choosing remote HTTP for a local subprocess by default
