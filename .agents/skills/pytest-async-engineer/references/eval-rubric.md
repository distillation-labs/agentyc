# Eval Rubric — Pytest Async Engineer

## Pass when the skill:

- loads for test design, pytest-asyncio patterns, pytest-httpserver usage, LLM fixtures, and CI placement
- does not load for production code design, CDP protocol work, or general Python debugging
- avoids `@pytest.mark.asyncio` on test functions in this repo
- uses `pytest-httpserver` for local HTML and API content
- avoids real remote URLs
- avoids mocks except for the LLM
- ensures browser fixtures use `headless=True` and call `await session.stop()` in teardown
- places deterministic coverage in `tests/ci/` with sensible naming and fixture structure

## Fail when the skill:

- uses remote URLs or browser mocks in tests
- leaves teardown implicit or incomplete
- relies on `time.sleep` inside async tests
- stores browser state across unrelated test functions
- asserts on private internals when public outputs are available
