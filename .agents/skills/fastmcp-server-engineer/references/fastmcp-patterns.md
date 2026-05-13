# FastMCP Patterns

Grounded in the official FastMCP server, tools, resources, prompts, and context docs.

## What Good Looks Like

- `FastMCP(...)` is the server container; use it for identity, composition, behavior, and handlers.
- Tools use typed signatures, docstrings, `Annotated`/`Field`, and annotations to generate clear schemas.
- Resources are passive data; templates cover parameterized URIs.
- Prompts are reusable message templates, not hidden APIs.
- `CurrentContext()` is the preferred way to inject request-scoped context.
- `ToolResult`, `ResourceResult`, and `PromptResult` are for explicit output control, not default return types.
- Flexible validation is the default; strict validation is for risky coercion.
- Middleware should own logging, error shaping, timing, rate limiting, and response limits.
- Dynamic surfaces should use providers plus `list_changed` notifications.
- stdio is the default transport; Streamable HTTP is for remote or shared serving; SSE is legacy.
- Server config should carry duplicate handling, masking, pagination, visibility, and task settings.

## What To Avoid

- untyped or variadic tool signatures
- returning raw dicts from resources without serialization
- marking write tools as read-only
- storing workflow policy in a giant `server.py`
- turning every result into a custom result object
- changing transport just because it feels more modern
