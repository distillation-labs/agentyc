# Eval Rubric — LLM Provider Engineer

## Pass when the skill:

- loads for BaseChatModel Protocol compliance, ChatInvokeCompletion field mapping, token accounting, structured output, and adding or debugging providers
- does not load for MCP tool design, CDP browser work, or generic Python refactors
- requires providers to satisfy `BaseChatModel`
- returns `ChatInvokeCompletion` rather than raw SDK objects
- populates `usage` with correct provider-specific fields
- normalizes `stop_reason`
- uses `output_format` for structured parsing instead of manual JSON parsing
- addresses provider quirks like Anthropic thinking, Google image tokens, or OpenAI cached-token nesting when relevant
- translates provider exceptions to shared agentyc exception types

## Fail when the skill:

- allows raw SDK responses to leak out of provider modules
- skips token accounting or leaves it vague
- recommends provider-specific top-level fields in shared completion models
- treats manual JSON parsing as the default structured-output path
- ignores the shared contract while focusing only on one provider's SDK
