# Eval Rubric — LLM Provider Engineer

## Triggering (routing)
- Skill loads for BaseChatModel Protocol compliance, ChatInvokeCompletion field mapping, token accounting, structured output, and adding/debugging providers.
- Skill does not load for MCP tool design, CDP browser work, or generic Python refactors.

## Protocol Compliance
- New provider satisfies `BaseChatModel` Protocol.
- `isinstance(llm, BaseChatModel)` check is mentioned.
- `provider` and `name` properties are implemented.

## Response Mapping
- `ChatInvokeCompletion` is always returned — never a raw SDK object.
- `usage` is populated with correct provider-specific fields.
- `stop_reason` is normalized to a plain string.
- `output_format` drives structured parsing, not manual JSON.

## Provider Quirks
- Anthropic `thinking` block is handled if relevant.
- Google `prompt_image_tokens` is mapped if relevant.
- OpenAI `cached_tokens` nesting is addressed if relevant.

## Output Quality
- Response includes a field mapping table or checklist.
- Error translation to `traverse.llm.exceptions` is addressed.
- Anti-patterns (raw SDK objects, skipped usage, manual JSON parsing) are flagged.
