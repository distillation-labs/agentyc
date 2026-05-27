# Pytest Async Patterns

Grounded in agentyc's test suite: pytest-asyncio auto mode, pytest-httpserver, and the no-mock constraint.

## What Good Looks Like

- All async tests are plain `async def test_*` functions — no `@pytest.mark.asyncio` needed.
- Async fixtures use `@pytest.fixture` only — no extra arguments.
- `pytest-httpserver` serves all HTML fixtures; no remote URLs anywhere in tests.
- `BrowserProfile(headless=True)` for all CI browser sessions.
- `await session.stop()` in fixture teardown — no leaked browser processes.
- LLM responses are canned `ChatInvokeCompletion` objects via `AsyncMock`.
- One `tests/ci/test_action_EventName.py` file per event or feature under test.
- Assertions target `ActionResult` fields and public session state, not private attrs.

## Network Interception Testing

- Use `add_network_mock()` and `remove_network_mock()` from `agentyc.browser.session_network`
  with real `pytest-httpserver` endpoints to verify mock interception.
- Use `set_network_conditions()` for offline, latency, and bandwidth throttling tests.
- Verify mock rules via `list_network_mocks()` and `get_network_conditions()` public APIs.
- Test that Fetch domain interceptors clean up properly in fixture teardown.
- Network tests live in `tests/ci/browser/` alongside other browser interaction tests.

## What To Avoid

- `@pytest.mark.asyncio` on test functions
- passing `event_loop` as a fixture argument
- using `https://example.com` or any real URL
- mocking `BrowserSession`, `DomService`, or `Tools`
- `time.sleep(...)` inside async tests (use `asyncio.wait_for`)
- storing browser session state across test functions
- writing tests that only pass on a specific OS or screen resolution
