import asyncio
import logging
from typing import Any

from agentyc.actions import ActionResult
from agentyc.browser import BrowserSession
from agentyc.browser.events import ScrollEvent, ScrollToTextEvent, SendKeysEvent
from agentyc.tools.javascript import (
	build_find_elements_js,
	build_search_page_js,
	format_find_results,
	format_search_results,
)
from agentyc.tools.views import FindElementsAction, ScrollAction, SearchPageAction, SendKeysAction

logger = logging.getLogger(__name__)


def register_exploration_actions(tools: Any) -> None:
	@tools.registry.action(
		"""Search page text for a pattern (like grep). Zero LLM cost, instant. Returns matches with surrounding context. Use to find specific text, verify content exists, or locate data on the page. Set regex=True for regex patterns. Use css_scope to search within a specific section.""",
		param_model=SearchPageAction,
	)
	async def search_page(params: SearchPageAction, browser_session: BrowserSession):
		js_code = build_search_page_js(
			pattern=params.pattern,
			regex=params.regex,
			case_sensitive=params.case_sensitive,
			context_chars=params.context_chars,
			css_scope=params.css_scope,
			max_results=params.max_results,
		)
		cdp_session = await browser_session.get_or_create_cdp_session()
		result = await cdp_session.cdp_client.send.Runtime.evaluate(
			params={'expression': js_code, 'returnByValue': True, 'awaitPromise': True},
			session_id=cdp_session.session_id,
		)
		if result.get('exceptionDetails'):
			error_text = result['exceptionDetails'].get('text', 'Unknown JS error')
			return ActionResult(error=f'search_page failed: {error_text}')

		data = result.get('result', {}).get('value')
		if data is None:
			return ActionResult(error='search_page returned no result')
		if isinstance(data, dict) and data.get('error'):
			return ActionResult(error=f'search_page: {data["error"]}')

		formatted = format_search_results(data, params.pattern)
		total = data.get('total', 0)
		memory = f'Searched page for "{params.pattern}": {total} match{"es" if total != 1 else ""} found.'
		logger.info(f'🔎 {memory}')
		return ActionResult(extracted_content=formatted, long_term_memory=memory)

	@tools.registry.action(
		"""Query DOM elements by CSS selector (like find). Zero LLM cost, instant. Returns matching elements with tag, text, and attributes. Use to explore page structure, count items, get links/attributes. Use attributes=["href","src"] to extract specific attributes.""",
		param_model=FindElementsAction,
	)
	async def find_elements(params: FindElementsAction, browser_session: BrowserSession):
		js_code = build_find_elements_js(
			selector=params.selector,
			attributes=params.attributes,
			max_results=params.max_results,
			include_text=params.include_text,
		)
		cdp_session = await browser_session.get_or_create_cdp_session()
		result = await cdp_session.cdp_client.send.Runtime.evaluate(
			params={'expression': js_code, 'returnByValue': True, 'awaitPromise': True},
			session_id=cdp_session.session_id,
		)
		if result.get('exceptionDetails'):
			error_text = result['exceptionDetails'].get('text', 'Unknown JS error')
			return ActionResult(error=f'find_elements failed: {error_text}')

		data = result.get('result', {}).get('value')
		if data is None:
			return ActionResult(error='find_elements returned no result')
		if isinstance(data, dict) and data.get('error'):
			return ActionResult(error=f'find_elements: {data["error"]}')

		formatted = format_find_results(data, params.selector)
		total = data.get('total', 0)
		memory = f'Found {total} element{"s" if total != 1 else ""} matching "{params.selector}".'
		logger.info(f'🔍 {memory}')
		return ActionResult(extracted_content=formatted, long_term_memory=memory)

	@tools.registry.action(
		"""Scroll by pages. REQUIRED: down=True/False (True=scroll down, False=scroll up, default=True). Optional: pages=0.5-10.0 (default 1.0). Use index for scroll elements (dropdowns/custom UI). High pages (10) reaches bottom. Multi-page scrolls sequentially. Viewport-based height, fallback 1000px/page.""",
		param_model=ScrollAction,
	)
	async def scroll(params: ScrollAction, browser_session: BrowserSession):
		try:
			node = None
			if params.index is not None and params.index != 0:
				node = await browser_session.get_element_by_index(params.index)
				if node is None:
					return ActionResult(error=f'Element index {params.index} not found in browser state')

			direction = 'down' if params.down else 'up'
			target = f'element {params.index}' if params.index is not None and params.index != 0 else ''

			try:
				cdp_session = await browser_session.get_or_create_cdp_session()
				metrics = await cdp_session.cdp_client.send.Page.getLayoutMetrics(session_id=cdp_session.session_id)
				css_viewport = metrics.get('cssVisualViewport', {})
				css_layout_viewport = metrics.get('cssLayoutViewport', {})
				viewport_height = int(css_viewport.get('clientHeight') or css_layout_viewport.get('clientHeight', 1000))
				logger.debug(f'Detected viewport height: {viewport_height}px')
			except Exception as error:
				viewport_height = 1000
				logger.debug(f'Failed to get viewport height, using fallback 1000px: {error}')

			if params.pages >= 1.0:
				num_full_pages = int(params.pages)
				remaining_fraction = params.pages - num_full_pages
				completed_scrolls = 0

				for index in range(num_full_pages):
					try:
						pixels = viewport_height if params.down else -viewport_height
						event = browser_session.event_bus.dispatch(
							ScrollEvent(direction=direction, amount=abs(pixels), node=node)
						)
						await event
						await event.event_result(raise_if_any=True, raise_if_none=False)
						completed_scrolls += 1
						await asyncio.sleep(0.15)
					except Exception as error:
						logger.warning(f'Scroll {index + 1}/{num_full_pages} failed: {error}')

				if remaining_fraction > 0:
					try:
						pixels = int(remaining_fraction * viewport_height)
						if not params.down:
							pixels = -pixels
						event = browser_session.event_bus.dispatch(
							ScrollEvent(direction=direction, amount=abs(pixels), node=node)
						)
						await event
						await event.event_result(raise_if_any=True, raise_if_none=False)
						completed_scrolls += remaining_fraction
					except Exception as error:
						logger.warning(f'Fractional scroll failed: {error}')

				if params.pages == 1.0:
					long_term_memory = f'Scrolled {direction} {target} {viewport_height}px'.replace('  ', ' ')
				else:
					long_term_memory = f'Scrolled {direction} {target} {completed_scrolls:.1f} pages'.replace('  ', ' ')
			else:
				pixels = int(params.pages * viewport_height)
				event = browser_session.event_bus.dispatch(
					ScrollEvent(direction='down' if params.down else 'up', amount=pixels, node=node)
				)
				await event
				await event.event_result(raise_if_any=True, raise_if_none=False)
				long_term_memory = f'Scrolled {direction} {target} {params.pages} pages'.replace('  ', ' ')

			message = f'🔍 {long_term_memory}'
			logger.info(message)
			return ActionResult(extracted_content=message, long_term_memory=long_term_memory)
		except Exception as error:
			logger.error(f'Failed to dispatch ScrollEvent: {type(error).__name__}: {error}')
			return ActionResult(error='Failed to execute scroll action.')

	@tools.registry.action('', param_model=SendKeysAction)
	async def send_keys(params: SendKeysAction, browser_session: BrowserSession):
		try:
			event = browser_session.event_bus.dispatch(SendKeysEvent(keys=params.keys))
			await event
			await event.event_result(raise_if_any=True, raise_if_none=False)
			memory = f'Sent keys: {params.keys}'
			logger.info(f'⌨️  {memory}')
			return ActionResult(extracted_content=memory, long_term_memory=memory)
		except Exception as error:
			logger.error(f'Failed to dispatch SendKeysEvent: {type(error).__name__}: {error}')
			return ActionResult(error=f'Failed to send keys: {error}')

	@tools.registry.action('Scroll to text.')
	async def find_text(text: str, browser_session: BrowserSession):  # type: ignore
		event = browser_session.event_bus.dispatch(ScrollToTextEvent(text=text))
		try:
			await event.event_result(raise_if_any=True, raise_if_none=False)
			memory = f'Scrolled to text: {text}'
			logger.info(f'🔍  {memory}')
			return ActionResult(extracted_content=memory, long_term_memory=memory)
		except Exception:
			msg = f"Text '{text}' not found or not visible on page"
			logger.info(msg)
			return ActionResult(
				extracted_content=msg,
				long_term_memory=f"Tried scrolling to text '{text}' but it was not found",
			)
