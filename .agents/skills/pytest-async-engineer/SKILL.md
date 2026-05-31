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
  version: "0.2.0"
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
- Treat 300-500 lines as the strict upper bound for test files. Files above 500 lines must be split up — this is a strict rule, no exceptions. Extract shared fixtures and helpers into `conftest.py` or nearby helper modules.

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

## Network Interception Testing

agentyc supports CDP Fetch domain network interception (see `agentyc/browser/session_network.py`).
Test these features with real HTTPServer fixtures:

```python
@pytest.fixture
async def browser_session_with_network():
    profile = BrowserProfile(headless=True)
    session = BrowserSession(browser_profile=profile)
    await session.start()
    # Navigate to a page first to establish a target
    yield session
    await session.stop()

async def test_network_mock_fulfill(browser_session_with_network, httpserver: HTTPServer):
    # Set up a real HTTP server to verify the mock intercepts before it
    httpserver.expect_request('/api/data').respond_with_json({'real': True})
    await browser_session_with_network.navigate(httpserver.url_for('/'))

    from agentyc.browser.session_network import add_network_mock
    result = await add_network_mock(
        browser_session_with_network,
        url_substring='/api/data',
        action='fulfill',
        status=200,
        body='{"mocked": true}',
        headers={'Content-Type': 'application/json'},
    )
    assert result['match_count'] == 0  # not yet matched

    # Now navigate to /api/data — the mock should intercept
    await browser_session_with_network.navigate(httpserver.url_for('/api/data'))
    # Verify the mock matched (match_count incremented)
```

Key patterns:
- Pair `add_network_mock()` / `remove_network_mock()` with real httpserver endpoints.
- Use `set_network_conditions()` to test offline, latency, and bandwidth throttling.
- Verify mock rules by inspecting `list_network_mocks()` and `get_network_conditions()`.
- Test that Fetch interceptors clean up properly in fixture teardown.

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
- Prefer feature-specific fixtures, page builders, and async helper functions in `conftest.py` or nearby helper modules over repeated setup embedded in large test files.

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

## Benchmark Harness Rules

- When a change claims browser improvement, encode the exact user-visible success metric and
  failure mode into a test or eval.
- Cover the representative page structures and stressors that match the claim: forms, modals,
  SPAs, iframes, downloads, network throttling, auth, collaboration, or session reuse.
- Use `HTTPServer(threaded=True)` for shared-browser or concurrent-request fixtures so serialized
  serving does not create false timeouts.
- For noisy metrics, rerun and compare medians; below-noise deltas are not wins.
- False-positive protection matters: assert the final browser outcome, not just that an
  intermediate event fired.

## Examples

Example 1: Button click test
User says: "Write a browser test for this page with one button and a local API."
Actions:
- serve the page and API with `pytest-httpserver`
- use a real `BrowserSession` fixture
- assert on public outputs after the interaction
Result: the test is realistic, deterministic, and CI-friendly

Example 2: Fixing a flaky async test
User says: "This test only passes sometimes on CI."
Actions:
- remove real URLs and shared browser state
- replace sync sleeps with bounded async waiting
- confirm teardown always stops the session
Result: flakiness is reduced without hiding the real behavior

## Troubleshooting

- If a test is flaky, first remove timing assumptions and shared state.
- If it touches the network, replace external URLs with local httpserver fixtures.
- If browser processes leak, audit fixture teardown for `await session.stop()`.

## Output Format

Return:
1. test file placement (ci/ or tests/)
2. fixture setup (httpserver routes, browser_session, LLM mock if needed)
3. test body (navigate, action, assertion)
4. teardown notes
5. benchmark / eval coverage
6. CI inclusion criteria

## Anti-Patterns

- mocking `BrowserSession`, `DomService`, or `Tools`
- using real URLs like `https://example.com`
- `@pytest.mark.asyncio` decorator on test functions (redundant with auto mode)
- passing `event_loop` as a fixture argument
- storing browser state across test functions
- asserting on internal private attributes instead of public API outputs
- letting one test module accumulate unrelated scenarios when fixtures or helper modules would keep the tests smaller and easier to debug
- asserting on a click or request without checking the final user-visible state
- masking flakiness with long sleeps or weaker assertions

## Composition Rule

- use `breakthrough-autoresearch` when the test strategy depends on ranking unknown fixes or proving a benchmark bottleneck
- use `cdp-browser-engineer` when the failure mode lives in target/session/watchdog/interception plumbing
- use `agentyc-browser-automation` when you need the end-to-end browser workflow before encoding it as coverage

## References

- `references/pytest-patterns.md`
- `references/eval-rubric.md`
- `evals/cases.yaml`
