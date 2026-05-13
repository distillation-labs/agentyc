# Eval Rubric — Pytest Async Engineer

## Triggering (routing)
- Skill loads for test design, pytest-asyncio patterns, pytest-httpserver usage, LLM fixtures, and CI placement.
- Skill does not load for production code design, CDP protocol work, or general Python debugging.

## Test Correctness
- No `@pytest.mark.asyncio` decorator on test functions.
- No real remote URLs — all HTML served via `pytest-httpserver`.
- No mocks except for the LLM.
- `await session.stop()` in all browser fixture teardowns.

## Fixture Design
- `pytest-httpserver` is used for all HTML content.
- Browser fixture uses `headless=True`.
- LLM fixture returns canned `ChatInvokeCompletion` via `AsyncMock`.

## CI Placement
- Test goes in `tests/ci/` once it is deterministic and dependency-free.
- Event-specific tests use `test_action_EventName.py` naming.

## Output Quality
- Response includes httpserver route setup.
- Response addresses teardown.
- Anti-patterns (remote URLs, mocking browser objects, sync sleep) are flagged.
