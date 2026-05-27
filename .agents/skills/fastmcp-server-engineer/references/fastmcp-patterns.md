# FastMCP Patterns

Grounded in the official FastMCP server, tools, resources, prompts, and context docs.

## What Good Looks Like

- `FastMCP(...)` is the server container for identity and behavior; transport config lives in `run()` (v3+).
- Tools use typed signatures, docstrings, `Annotated`/`Field`, and annotations to generate clear schemas.
- Resources are passive data; templates cover parameterized URIs.
- Prompts return `Message` objects (v3+) or plain strings — dict coercion is removed.
- `CurrentContext()` is the preferred way to inject request-scoped context; `ctx.get_state()` / `ctx.set_state()` are async in v3.
- `ToolResult`, `ResourceResult`, and `PromptResult` are for explicit output control, not default return types.
- Flexible validation is the default; strict validation is for risky coercion.
- Middleware should own logging, error shaping, timing, rate limiting, and response limits.
- Dynamic surfaces should use providers plus `list_changed` notifications.
- stdio is the default transport; Streamable HTTP is for remote or shared serving; SSE is legacy.
- Server config should carry duplicate handling, masking, pagination, visibility, and task settings.
- Providers (OpenAPI, FileSystem, Proxy, Skills) compose server surfaces; transforms rename, namespace, filter, version, and secure them.

## What To Avoid

- untyped or variadic tool signatures
- putting transport config (`host`, `port`) in the `FastMCP()` constructor (v3+)
- returning raw dicts from resources without serialization
- marking write tools as read-only
- storing workflow policy in a giant `server.py`
- turning every result into a custom result object
- changing transport just because it feels more modern
- accessing `.name` or `.description` on decorated functions (v3 decorators return the function itself)
