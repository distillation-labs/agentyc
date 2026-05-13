---
name: fastmcp-server-engineer
description: >
  Use for building or refactoring FastMCP servers, tools, resources, prompts, middleware,
  validation, annotations, structured outputs, context, visibility, timeouts, background tasks,
  and transport/runtime behavior. Trigger when the user asks how to implement a FastMCP tool,
  resource, or prompt; how to shape typed signatures or output schemas; when to use ToolResult,
  ResourceResult, PromptResult, or Context; how to add middleware or server configuration; or
  how to make a FastMCP server production-ready. Do not use for pure MCP protocol design with
  no FastMCP implementation intent.
when_to_use: >
  Especially useful when the repo already uses FastMCP and the question is about decorators,
  typed schemas, return shaping, request context, visibility, validation mode, deployment, or
  server lifecycle.
metadata:
  version: "0.1.0"
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

## Context And Middleware

- Use `CurrentContext()` / `Context` for logging, progress, resource access, prompt access,
  elicitation, session state, visibility, and client-aware behavior.
- Use `get_context()` only in deep helpers that already run inside a request.
- Put logging, timing, caching, error shaping, and rate limiting in middleware or shared helpers.
- Use session state intentionally; it is request/session scoped, not global state.
- Use visibility controls with `enable()` / `disable()` instead of ad hoc flags.
- Dynamic component sets should rely on list-changed notifications and providers.

## Deployment And Server Behavior

- Use stdio by default for local integrations.
- Use Streamable HTTP when remote access, auth, or shared serving is required.
- Treat SSE as legacy compatibility, not the default.
- Keep `if __name__ == "__main__"` around runnable server files.
- Use `custom_route` only for adjacent HTTP endpoints such as health checks.
- Configure duplicate handling, masking, pagination, auth, lifespan, and tasks on the server.

## Output Format

Return:

1. component choice
2. signature and validation guidance
3. output/result shape
4. context and middleware needs
5. runtime/deployment settings
6. rejected alternatives

## Anti-Patterns

- giant untyped tool signatures
- using `ToolResult` / `ResourceResult` / `PromptResult` everywhere by default
- returning dicts from resources without serialization
- putting all logic in `server.py`
- marking mutating tools as readOnlyHint
- choosing HTTP for a local subprocess by default
- ignoring list-changed notifications for dynamic surfaces

## References

- `references/fastmcp-patterns.md`
- `references/eval-rubric.md`
