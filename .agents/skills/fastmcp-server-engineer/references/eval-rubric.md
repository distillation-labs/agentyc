# FastMCP Server Engineer Eval Rubric

Pass when the skill:

- triggers for FastMCP implementation or refactor questions
- recommends the right component and typed signature shape
- distinguishes plain returns from `ToolResult`, `ResourceResult`, and `PromptResult`
- covers validation, context, middleware, and runtime settings
- stays FastMCP-specific instead of drifting into generic MCP advice

Fail when the skill:

- gives protocol-only advice with no FastMCP implementation detail
- ignores annotations, context, or validation mode
- tells the user to cram everything into `server.py`
- treats every response as a custom result object
