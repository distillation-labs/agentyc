---
name: pytest-async-engineer
description: >
  Use for writing or fixing tests in agentyc: async test patterns with pytest-asyncio,
  HTTP fixture servers with pytest-httpserver, the no-mock constraint, LLM fixture setup,
  BrowserSession lifecycle in tests, and CI test organization. Trigger when the user asks
  how to write a test for a browser automation feature, how to serve HTML for a test, how
  to structure a new test file, or why a test is flaky or failing. Do not use for test
  design of non-browser Python libraries.
when_to_use: >
  Especially useful for pytest-asyncio auto mode, pytest-httpserver fixture patterns,
  BrowserSession setup/teardown in tests, LLM mock fixtures, and deciding what belongs
  in tests/ci vs tests/.
metadata:
  version: "0.1.0"
  category: testing
  tags: [pytest, pytest-asyncio, pytest-httpserver, async-testing, browser-testing, fixtures, ci, no-mocks]
license: Proprietary
---

# Pytest Async Engineer

Tests must use real objects. The only mock allowed is the LLM. Every other dependency —
BrowserSession, DOM, HTTP servers — must be real and isolated to the test process.

## Core Rules

- Never mock `BrowserSession`, `DomService`, `Tools`, or any browser object.
- The only mockable component is the LLM — use `conftest.py` fixtures that return canned `ChatInvokeCompletion` objects.
- Never use real remote URLs (`https://google.com`, `https://example.com`) — serve all HTML through `pytest-httpserver`.
- Do not add `@pytest.mark.asyncio` decorators — `asyncio_mode = "auto"` is configured globally.
- Async fixture functions only need `@pytest.fixture`, not `@pytest.fixture(scope=...)` with event loop args.
- Use `asyncio.get_event_loop()` inside tests that need the loop; never pass `event_loop` as a fixture argument.

## Test File Structure

```
tests/
├── ci/                    # Default CI set, discovered automatically on every commit
│   ├── test_cdp_timeout.py
│   ├── test_mcp_runtime_optimizations.py
│   └── browser/
│       └── test_navigation.py
└── conftest.py            # Shared fixtures (LLM mocks, browser profile, etc.)
```

- Move a test to `tests/ci/` once it passes reliably with no external dependencies.
- Group browser interaction tests under `tests/ci/browser/`, MCP tests under `tests/ci/`, etc.
- Each event or feature gets its own `test_action_EventName.py` file.

## pytest-httpserver Patterns

### Basic HTML fixture
```python
from pytest_httpserver import HTTPServer

@pytest.fixture
def html_server(httpserver: HTTPServer):
    httpserver.expect_request('/').respond_with_data(
        '<html><body><button id="go">Click me</button></body></html>',
        content_type='text/html',
    )
    return httpserver

async def test_click_button(html_server, browser_session):
    await browser_session.navigate(html_server.url_for('/'))
    # ... assert something
```

### Multiple routes
```python
httpserver.expect_request('/page-a').respond_with_data(PAGE_A_HTML, content_type='text/html')
httpserver.expect_request('/page-b').respond_with_data(PAGE_B_HTML, content_type='text/html')
```

### JSON API endpoint
```python
httpserver.expect_request('/api/data').respond_with_json({'status': 'ok'})
```

## BrowserSession in Tests

```python
import pytest
from agentyc.browser import BrowserProfile, BrowserSession

@pytest.fixture
async def browser_session():
    profile = BrowserProfile(headless=True)
    session = BrowserSession(browser_profile=profile)
    await session.start()
    yield session
    await session.stop()
```

- Always `await session.stop()` in the fixture teardown — browser processes leak otherwise.
- Use `headless=True` in all CI fixtures; never open a visible browser in CI.
- Create a fresh session per test, not per module, unless the test explicitly validates session reuse.

## LLM Fixtures (the one real mock)

LLM fixtures return pre-canned `ChatInvokeCompletion` objects. See `conftest.py` for the
existing fixtures. To add a new canned response:

```python
from agentyc.llm.views import ChatInvokeCompletion, ChatInvokeUsage
from unittest.mock import AsyncMock

@pytest.fixture
def mock_llm_click():
    llm = AsyncMock()
    llm.ainvoke.return_value = ChatInvokeCompletion(
        completion='{"action": "click", "element_id": 1}',
        usage=ChatInvokeUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15,
                              prompt_cached_tokens=None, prompt_cache_creation_tokens=None,
                              prompt_image_tokens=None),
        stop_reason='end_turn',
    )
    return llm
```

## Assertions

- Assert on `ActionResult` fields, not on internal state.
- For DOM assertions, use `DomService.get_state()` and inspect the returned `SerializedDOMState`.
- For URL assertions, use `browser_session.active_target.url`.
- Avoid `time.sleep` — use `asyncio.wait_for` with a short timeout.

## Output Format

Return:
1. test file placement (ci/ or tests/)
2. fixture setup (httpserver routes, browser_session, LLM mock if needed)
3. test body (navigate, action, assertion)
4. teardown notes
5. CI inclusion criteria

## Anti-Patterns

- mocking `BrowserSession`, `DomService`, or `Tools`
- using real URLs like `https://example.com`
- `@pytest.mark.asyncio` decorator on test functions (redundant with auto mode)
- passing `event_loop` as a fixture argument
- storing browser state across test functions
- asserting on internal private attributes instead of public API outputs

## References

- `references/pytest-patterns.md`
- `references/eval-rubric.md`
