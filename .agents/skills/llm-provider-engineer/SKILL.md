---
name: llm-provider-engineer
description: >
  Use for adding, modifying, or debugging LLM provider integrations in agentyc: the
  BaseChatModel Protocol, ChatInvokeCompletion/ChatInvokeUsage response shapes, token
  tracking, provider-specific quirks (Anthropic thinking, Google image tokens, cached
  tokens), output_format structured parsing, and multi-provider routing. Trigger when the
  user asks how to add a new provider, how to handle provider-specific response fields, how
  token costs are tracked, how structured output works, or why a provider integration is
  misbehaving. Do not use for MCP tool design or browser CDP work.
when_to_use: >
  Especially useful for BaseChatModel Protocol compliance, ChatInvokeCompletion field mapping,
  provider-specific token fields, structured output via output_format, and adding a new
  provider module under agentyc/llm/.
metadata:
  version: "0.1.0"
  category: llm-integration
  tags: [llm, anthropic, openai, google, groq, ollama, provider, tokens, structured-output, protocol]
license: Proprietary
---

# LLM Provider Engineer

Every provider speaks a different dialect; `BaseChatModel` is the common lingua franca.
Map the provider's response fields onto `ChatInvokeCompletion` exactly — don't drop fields
and don't invent ones that don't exist on that provider.

## Core Rules

- All LLM providers must implement `BaseChatModel` (a `@runtime_checkable Protocol`).
- Always return `ChatInvokeCompletion[T]` from `ainvoke` — never raw provider response objects.
- Map `usage` fields precisely: `prompt_cached_tokens` is Anthropic/OpenAI, `prompt_image_tokens` is Google-only.
- Use `output_format: type[T] | None` to gate structured parsing — never parse JSON manually in the provider.
- Place provider modules under `agentyc/llm/<provider-name>/` following the existing pattern.
- Run `uv run pyright` after adding a new provider — the Protocol check is type-enforced.
- As provider integrations grow, split them into focused modules such as `chat.py`, serializers/parsers, views, and shared helpers instead of keeping every concern in one expanding provider file.
- Treat 700-800 lines as the general upper bound for active implementation files, scrutinize files above 800 lines for refactor, and treat files above 1000 lines as priority modular-refactor candidates.

## BaseChatModel Protocol

```python
from agentyc.llm.base import BaseChatModel

# Protocol surface (must implement all of these):
# - model: str
# - provider: str  (property)
# - name: str      (property)
# - model_name: str (property, alias for model — legacy support)
# - async ainvoke(messages, output_format=None, **kwargs) -> ChatInvokeCompletion
```

A class is compliant when `isinstance(obj, BaseChatModel)` returns `True` at runtime. The
Protocol also defines `__get_pydantic_core_schema__` so it can be used as a Pydantic field
type without `arbitrary_types_allowed` hacks.

## ChatInvokeCompletion / ChatInvokeUsage

```python
class ChatInvokeUsage(BaseModel):
    prompt_tokens: int
    prompt_cached_tokens: int | None      # Anthropic, OpenAI
    prompt_cache_creation_tokens: int | None  # Anthropic only
    prompt_image_tokens: int | None       # Google only
    completion_tokens: int
    total_tokens: int

class ChatInvokeCompletion(BaseModel, Generic[T]):
    completion: T                         # str or output_format instance
    thinking: str | None = None           # Anthropic extended thinking
    redacted_thinking: str | None = None  # Anthropic extended thinking
    usage: ChatInvokeUsage | None
    stop_reason: str | None = None        # 'end_turn', 'max_tokens', 'stop_sequence'
```

### Mapping provider fields
| Provider | cached tokens field | image tokens | thinking |
|---|---|---|---|
| Anthropic | `cache_read_input_tokens` → `prompt_cached_tokens` | — | `thinking` block |
| OpenAI | `cached_tokens` (in `usage.prompt_tokens_details`) | — | — |
| Google Gemini | — | `candidates[0].token_count` → `prompt_image_tokens` | — |
| Groq, Ollama | map to `None` | — | — |

## Structured Output

The `output_format` parameter drives structured parsing:

```python
class ClickAction(BaseModel):
    element_id: int
    reason: str

result: ChatInvokeCompletion[ClickAction] = await llm.ainvoke(
    messages,
    output_format=ClickAction,
)
action = result.completion  # typed as ClickAction
```

Provider implementation must:
1. Detect `output_format is not None`.
2. Use the provider's native structured output (OpenAI `response_format`, Anthropic tool-use, etc.) when available.
3. Fall back to JSON-mode + `output_format.model_validate_json(raw)` otherwise.
4. Catch parse errors and raise a typed `LLMParseError` from `agentyc.llm.exceptions`.

## Adding a New Provider

1. Create `agentyc/llm/<provider>/` with `__init__.py` and `chat.py`.
2. Implement a class that satisfies `BaseChatModel` — use `isinstance` to verify.
3. Map all response fields to `ChatInvokeCompletion` — never skip `usage`.
4. Export the class from `agentyc/llm/__init__.py`.
5. Add provider to `agentyc/llm/models.py` enum if applicable.
6. Test with `uv run pytest -vxs tests/ci/test_llm_retries.py`.

If the integration starts accumulating response mappers, output-format parsing, token accounting,
and provider-specific view types, split those concerns into dedicated modules rather than growing a
single provider implementation file.

## Provider-Specific Quirks

**Anthropic**:
- Extended thinking returns a `thinking` block before the content block — extract and set `ChatInvokeCompletion.thinking`.
- `prompt_cache_creation_tokens` is non-zero only on the first request that creates a cache entry.
- Rate limit errors should trigger exponential backoff with jitter — use `agentyc.llm.exceptions`.

**Google Gemini**:
- Image tokens are reported separately from text tokens in `usageMetadata`.
- Structured output uses `response_schema` in the generation config, not a tool call.
- `stop_reason` maps from `FinishReason` enum — translate to a string.

**Ollama**:
- No token caching fields — set all `*_cached_tokens` to `None`.
- `model_name` must include the tag (e.g., `llama3.2:3b`), not just the base name.

**OpenAI**:
- `cached_tokens` lives in `usage.prompt_tokens_details.cached_tokens` — not at the top level.
- Use `response_format={"type": "json_schema", ...}` for structured output on supported models.

## Output Format

Return:
1. BaseChatModel compliance checklist
2. ChatInvokeCompletion field mapping for the provider
3. structured output strategy
4. token field mapping
5. provider-specific edge cases
6. test plan

## Anti-Patterns

- returning raw provider SDK objects instead of `ChatInvokeCompletion`
- setting `usage=None` to skip token accounting
- parsing JSON manually instead of using `output_format`
- raising provider-specific exceptions instead of `agentyc.llm.exceptions` types
- hardcoding provider names as strings instead of using the `provider` property
- adding provider-specific fields to `ChatInvokeCompletion` (use `thinking` / existing fields or don't add)
- letting one provider module absorb chat invocation, response mapping, serializers, view models, and retries without splitting reusable helpers

## References

- `references/llm-patterns.md`
- `references/eval-rubric.md`
