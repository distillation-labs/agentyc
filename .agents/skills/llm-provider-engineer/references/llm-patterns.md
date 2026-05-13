# LLM Provider Patterns

Grounded in traverse's BaseChatModel Protocol and ChatInvokeCompletion response contract.

## What Good Looks Like

- Every provider returns `ChatInvokeCompletion[T]` — never a raw SDK response object.
- `usage` is always populated, even if some sub-fields are `None`.
- `prompt_cached_tokens` is set for Anthropic and OpenAI; `None` for others.
- `prompt_image_tokens` is set for Google Gemini; `None` for others.
- `thinking` and `redacted_thinking` are set only for Anthropic extended thinking.
- `output_format` drives structured parsing via the provider's native schema mechanism.
- `stop_reason` is normalized to a plain string (`'end_turn'`, `'max_tokens'`, etc.).
- `isinstance(llm, BaseChatModel)` is `True` at runtime — no subclassing required.
- All new providers live under `traverse/llm/<provider>/chat.py`.
- Provider-specific errors are translated to `traverse.llm.exceptions` types before propagating.

## What To Avoid

- returning raw provider SDK objects (e.g. `openai.ChatCompletion`) instead of wrapping in `ChatInvokeCompletion`
- setting `usage=None` to skip token accounting
- parsing JSON manually instead of using `output_format` with the provider's native mechanism
- raising `openai.RateLimitError` or `anthropic.APIError` directly (wrap them)
- hardcoding the provider name as a string literal instead of using the `provider` property
- adding new top-level fields to `ChatInvokeCompletion` for provider-specific data that doesn't map to existing fields
