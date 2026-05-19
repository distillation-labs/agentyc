import asyncio

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.mcp.state import build_browser_state_payload
from scripts.benchmark_mcp_runtime import make_long_docs_html


@pytest.fixture
async def browser_session():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
		)
	)
	await session.start()
	yield session
	await session.stop()


async def test_min_mode_keeps_search_input_after_deep_scroll(httpserver: HTTPServer, browser_session: BrowserSession):
	httpserver.expect_request('/long-docs.html').respond_with_data(
		make_long_docs_html(),
		content_type='text/html',
	)
	url = httpserver.url_for('/long-docs.html').replace('localhost', '127.0.0.1')

	await browser_session.navigate_to(url)
	cdp_session = await browser_session.get_or_create_cdp_session(target_id=None, focus=False)
	await cdp_session.cdp_client.send.Runtime.evaluate(
		params={
			'expression': 'window.scrollTo(0, document.body.scrollHeight); true',
			'returnByValue': True,
		},
		session_id=cdp_session.session_id,
	)
	await asyncio.sleep(0.2)

	state = await browser_session.get_browser_state_summary(include_screenshot=False)
	payload = build_browser_state_payload(state, mode='min')

	assert any(
		element.get('text') == 'Search documentation' and element.get('tag') == 'input'
		for element in payload['interactive_elements']
	)
