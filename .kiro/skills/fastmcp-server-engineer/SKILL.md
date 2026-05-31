---
name: fastmcp-server-engineer
description: >
  Use for designing, building, or refactoring FastMCP servers and MCP surfaces: tool/resource/
  prompt boundaries, lifecycle, capability negotiation, discovery, notifications, transport,
  middleware, validation, context, structured outputs, and deployment/runtime behavior. Trigger
  when the user asks how an MCP feature should be modeled in FastMCP, how to choose between a
  tool, resource, or prompt, how to structure a production-ready FastMCP server, or how to wire
  typed schemas and middleware correctly. Do not use for pure protocol theory with no FastMCP
  implementation intent.
when_to_use: >
  Especially useful when the repo already uses FastMCP and the question is about protocol
  primitive selection, decorators, typed schemas, return shaping, request context, discovery,
  notifications, validation mode, deployment, or server lifecycle.
metadata:
  version: "0.4.0"
  category: mcp-development
  tags: [fastmcp, server, tools, resources, prompts, validation, context, middleware, transport]
license: Proprietary
---

# FastMCP Server Engineer

Implement FastMCP in the framework's grain. Keep the server thin, the schema explicit, and the
output shape intentional.

## Core Rules

- Keep the server entrypoint small; move business logic into normal modules.
- Prefer the simplest component that fits the job.
- Use typed Python signatures so FastMCP can build useful schemas.
- Make annotations and metadata match the real behavior.
- Reach for result objects only when you need explicit control.
- Treat 300-500 lines as the strict upper bound for server and tool implementation files. Files above 500 lines must be split up — no exceptions.
- Prefer thin server and tool entrypoints with shared logic extracted into validators, adapters, serializers, transport helpers, and domain modules instead of growing `server.py` or giant tool modules.

## Design Sequence

1. Name the workflow the user is trying to accomplish.
2. Split it into tool, resource, and prompt sized responsibilities.
3. Decide what is static, what is dynamic, and what needs change notifications.
4. Pick the transport that matches deployment reality.
5. Map the design onto concrete FastMCP components and shared helpers.

## Protocol Surface Guidance

- Use a tool for model-invoked actions or derived computation.
- Use a resource for passive or browsable context.
- Use a prompt for reusable user-invoked workflows.
- Keep static context out of tools when a resource fits better.
- Do not use prompts to paper over a poorly designed primitive boundary.

## Lifecycle And Discovery

- Respect initialization and capability negotiation.
- Model dynamic catalogs explicitly and pair them with list-changed notifications.
- Prefer stable component names and keys because clients cache discovered surfaces.
- If a component is role-based or filtered, make that visibility model explicit.

## Component Guidance

### Tools

- Use tools for model-invoked actions, active computation, or side effects.
- Avoid `*args` and `**kwargs`; FastMCP needs a complete schema.
- Write real docstrings because they become tool descriptions.
- Use `Annotated` and `Field` for parameter descriptions and constraints.
- Mark true read-only tools with `annotations={"readOnlyHint": True}`.
- Use `timeout` for foreground work that should fail fast.
- Use `task=True` for long-running work that should be backgrounded.

### Resources

- Use resources for passive, read-only data.
- Use URI templates for parameterized lookups.
- Return `str`, `bytes`, or `ResourceResult`.
- Serialize dicts or lists to JSON strings yourself.
- Mark `readOnlyHint` and `idempotentHint` only when they are true.

### Prompts

- Use prompts for reusable, user-invoked workflows.
- Return `str`, `list[Message | str]`, or `PromptResult`.
- Keep prompt bodies focused on the conversation the client should start.
- Use typed arguments sparingly and make the formatting obvious.

## Validation And Schemas

- Default flexible validation is usually the right call.
- Enable `strict_input_validation` only when coercion is risky or ambiguous.
- Use `output_schema` when you need a strict machine-readable contract.
- Prefer simple signatures over dict blobs or ad hoc parsing.
- Keep `dereference_schemas=True` unless you know the client handles refs well.

## Outputs

- Return plain values for simple tool cases.
- Use `ToolResult` when you need `content`, `structured_content`, or `meta`.
- Use `ResourceResult` when a resource needs multiple contents or MIME control.
- Use `PromptResult` when prompt rendering needs multiple messages or metadata.
- Shape outputs for both humans and clients; do not dump raw internal objects.
- For resources, remember that dicts are not automatically valid return values.

## Agent-Optimized MCP Contract

- Optimize tool, resource, and prompt boundaries for low round-trip latency, low input tokens, and
  low hallucination risk.
- Prefer compact, typed result shapes that give agents enough to act without dumping raw internals.
- Use background tasks only when work is truly long-running; foreground tools should fail fast and
  surface progress or errors explicitly.
- Keep static context in resources, active work in tools, and reusable user workflows in prompts so
  agents do not pay to re-request the same context.
- When the server fronts browser automation, preserve stable refs, inspectability, and deterministic
  failure surfaces.

## Context And Middleware

- Use `CurrentContext()` / `Context` for logging, progress, resource access, prompt access,
  elicitation, session state, visibility, and client-aware behavior.
- Use `get_context()` only in deep helpers that already run inside a request.
- Put logging, timing, caching, error shaping, and rate limiting in middleware or shared helpers.
- Use session state intentionally; it is request/session scoped, not global state.
- Use visibility controls with `enable()` / `disable()` instead of ad hoc flags.
- Dynamic component sets should rely on list-changed notifications and providers.
- Split transport adapters, middleware, tool implementations, and result-shaping helpers by concern rather than centralizing them in one oversized server module.

## FastMCP v3.x (Current — 2026)

FastMCP v3.0 (released early 2026) is the current major version. Key differences from v2:

**Transport configuration** — `host`, `port`, `debug`, `log_level` are now `run()` kwargs, NOT `FastMCP()` constructor args:
```python
# v2 (deprecated)
mcp = FastMCP("server", host="0.0.0.0", port=8080)

# v3 (current)
mcp = FastMCP("server")
mcp.run(transport="http", host="0.0.0.0", port=8080)
```

**Decorators return functions** — `@mcp.tool`, `@mcp.resource`, `@mcp.prompt` return the original function, not a component object. Code that accesses `.name` or `.description` on the decorated result will break.

**Async context state** — `ctx.get_state()` and `ctx.set_state()` are now async:
```python
# v3
state = await ctx.get_state("key")
await ctx.set_state("key", value)
```

**Prompt return types** — Prompt functions must return `Message` objects (from `fastmcp.prompts`) or plain strings. Dict coercion is removed.
```python
from fastmcp.prompts import Message

@mcp.prompt(title="greet")
async def greet(name: str) -> list[Message]:
    return [Message.user(f"Hello, {name}!")]
```

**OpenAPI provider** — Use `OpenAPIProvider` instead of the removed `FastMCPOpenAPI`:
```python
from fastmcp.server.providers.openapi import OpenAPIProvider
mcp = FastMCP("api-server", providers=[OpenAPIProvider(spec, client=client)])
```

**Renamed methods** — `get_tools()` → `list_tools()`, `get_resources()` → `list_resources()`, etc. These now return lists, not dicts.

**Provider architecture** — v3 uses a provider/transform architecture for composability. Providers exist for filesystems, OpenAPI specs, proxies, and skills.

## Deployment And Server Behavior

- Use stdio by default for local integrations.
- Use Streamable HTTP when remote access, auth, or shared serving is required.
- Treat SSE as legacy compatibility, not the default.
- Keep `if __name__ == "__main__"` around runnable server files.
- Use `custom_route` only for adjacent HTTP endpoints such as health checks.
- Configure duplicate handling, masking, pagination, auth, lifespan, and tasks on the server.

## Examples

Example 1: Primitive selection
User says: "Should this MCP feature be a tool, resource, or prompt?"
Actions:
- define the workflow
- map stable context to resources and actions to tools
- use prompts only for reusable user-invoked flows
Result: the surface matches the protocol and the user workflow

Example 2: Typed tool plus structured output
User says: "Implement a FastMCP tool with a strict machine-readable response."
Actions:
- choose a typed signature
- decide between plain returns and `ToolResult`
- wire validation, context, and runtime settings intentionally
Result: the tool is both client-friendly and implementation-friendly

## Troubleshooting

- If the surface feels awkward, revisit primitive choice before adding more code.
- If clients cannot discover dynamic changes, add list-changed notifications and clearer visibility rules.
- If `server.py` is growing too large, extract middleware, validators, and shared adapters before adding more endpoints.

## Output Format

Return:

1. component choice
2. signature and validation guidance
3. output/result shape
4. context and middleware needs
5. runtime/deployment settings
6. latency/token impact
7. rejected alternatives

## Anti-Patterns

- giant untyped tool signatures
- using `ToolResult` / `ResourceResult` / `PromptResult` everywhere by default
- returning dicts from resources without serialization
- putting all logic in `server.py`
- marking mutating tools as readOnlyHint
- choosing HTTP for a local subprocess by default
- ignoring list-changed notifications for dynamic surfaces
- growing monolithic server or tool files instead of extracting reusable adapters, validators, and output-shaping helpers
- returning large unfiltered payloads that force agents to spend tokens just to find the answer
- backgrounding cheap calls that should return synchronously

## Composition Rule

- use `agentyc-browser-automation` when the MCP surface depends on concrete browser workflows
- use `llm-provider-engineer` when tool or prompt behavior depends on model routing or structured output
- use `breakthrough-autoresearch` when surface changes are still benchmark exploration rather than known implementation work

## References

- `references/fastmcp-patterns.md`
- `references/eval-rubric.md`
- `evals/cases.yaml`
