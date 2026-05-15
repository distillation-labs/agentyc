import asyncio
import logging
import urllib.parse
from typing import Any

from agentyc.actions import ActionResult
from agentyc.browser import BrowserSession
from agentyc.browser.events import CloseTabEvent, GoBackEvent, NavigateToUrlEvent, SwitchTabEvent
from agentyc.tools.views import CloseTabAction, NavigateAction, NoParamsAction, SearchAction, SwitchTabAction

logger = logging.getLogger(__name__)


def register_navigation_actions(tools: Any) -> None:
	@tools.registry.action('', param_model=SearchAction, terminates_sequence=True)
	async def search(params: SearchAction, browser_session: BrowserSession):
		encoded_query = urllib.parse.quote_plus(params.query)
		search_engines = {
			'duckduckgo': f'https://duckduckgo.com/?q={encoded_query}',
			'google': f'https://www.google.com/search?q={encoded_query}&udm=14',
			'bing': f'https://www.bing.com/search?q={encoded_query}',
		}

		if params.engine.lower() not in search_engines:
			return ActionResult(error=f'Unsupported search engine: {params.engine}. Options: duckduckgo, google, bing')

		search_url = search_engines[params.engine.lower()]

		try:
			event = browser_session.event_bus.dispatch(
				NavigateToUrlEvent(
					url=search_url,
					new_tab=False,
				)
			)
			await event
			await event.event_result(raise_if_any=True, raise_if_none=False)
			memory = f"Searched {params.engine.title()} for '{params.query}'"
			logger.info(f'🔍  {memory}')
			return ActionResult(extracted_content=memory, long_term_memory=memory)
		except Exception as error:
			logger.error(f'Failed to search {params.engine}: {error}')
			return ActionResult(error=f'Failed to search {params.engine} for "{params.query}": {error}')

	@tools.registry.action('', param_model=NavigateAction, terminates_sequence=True)
	async def navigate(params: NavigateAction, browser_session: BrowserSession):
		try:
			event = browser_session.event_bus.dispatch(NavigateToUrlEvent(url=params.url, new_tab=params.new_tab))
			await event
			await event.event_result(raise_if_any=True, raise_if_none=False)

			def page_appears_empty(state: Any) -> bool:
				return state.dom_state._root is None or not state.dom_state.llm_representation().strip()

			if not params.new_tab:
				state = await browser_session.get_browser_state_summary(include_screenshot=False)
				url_is_http = state.url.lower().startswith(('http://', 'https://'))
				if url_is_http and page_appears_empty(state):
					browser_session.logger.warning(
						f'⚠️ Empty DOM detected after navigation to {params.url}, waiting 3s and rechecking...'
					)
					await asyncio.sleep(3.0)
					state = await browser_session.get_browser_state_summary(include_screenshot=False)
					if state.url.lower().startswith(('http://', 'https://')) and page_appears_empty(state):
						browser_session.logger.warning(
							f'⚠️ Still empty after 3s, attempting page reload for {params.url}...'
						)
						reload_event = browser_session.event_bus.dispatch(
							NavigateToUrlEvent(url=params.url, new_tab=False)
						)
						await reload_event
						await reload_event.event_result(raise_if_any=False, raise_if_none=False)
						await asyncio.sleep(5.0)
						state = await browser_session.get_browser_state_summary(include_screenshot=False)
						if state.url.lower().startswith(('http://', 'https://')) and state.dom_state._root is None:
							return ActionResult(
								error=(
									f'Page loaded but returned empty content for {params.url}. '
									'The page may require JavaScript that failed to render, use anti-bot measures, '
									'or have a connection issue (e.g. tunnel/proxy error). Try a different URL or approach.'
								)
							)

			if params.new_tab:
				memory = f'Opened new tab with URL {params.url}'
				message = f'🔗  Opened new tab with url {params.url}'
			else:
				memory = f'Navigated to {params.url}'
				message = f'🔗 {memory}'

			logger.info(message)
			return ActionResult(extracted_content=message, long_term_memory=memory)
		except Exception as error:
			error_msg = str(error)
			browser_session.logger.error(f'❌ Navigation failed: {error_msg}')

			if isinstance(error, RuntimeError) and 'CDP client not initialized' in error_msg:
				browser_session.logger.error('❌ Browser connection failed - CDP client not properly initialized')
				return ActionResult(error=f'Browser connection error: {error_msg}')
			if any(
				err in error_msg
				for err in [
					'ERR_NAME_NOT_RESOLVED',
					'ERR_INTERNET_DISCONNECTED',
					'ERR_CONNECTION_REFUSED',
					'ERR_TIMED_OUT',
					'ERR_TUNNEL_CONNECTION_FAILED',
					'net::',
				]
			):
				site_unavailable_msg = f'Navigation failed - site unavailable: {params.url}'
				browser_session.logger.warning(f'⚠️ {site_unavailable_msg} - {error_msg}')
				return ActionResult(error=site_unavailable_msg)
			return ActionResult(error=f'Navigation failed: {error}')

	@tools.registry.action('Go back', param_model=NoParamsAction, terminates_sequence=True)
	async def go_back(_: NoParamsAction, browser_session: BrowserSession):
		try:
			event = browser_session.event_bus.dispatch(GoBackEvent())
			await event
			memory = 'Navigated back'
			logger.info(f'🔙  {memory}')
			return ActionResult(extracted_content=memory)
		except Exception as error:
			logger.error(f'Failed to dispatch GoBackEvent: {type(error).__name__}: {error}')
			return ActionResult(error=f'Failed to go back: {error}')

	@tools.registry.action('Wait for x seconds.')
	async def wait(seconds: int = 3):
		actual_seconds = min(max(seconds - 1, 0), 30)
		memory = f'Waited for {seconds} seconds'
		logger.info(f'🕒 waited for {seconds} second{"" if seconds == 1 else "s"}')
		await asyncio.sleep(actual_seconds)
		return ActionResult(extracted_content=memory, long_term_memory=memory)

	@tools.registry.action(
		'Switch to another open tab by tab_id. Tab IDs are shown in browser state tabs list (last 4 chars of target_id). Use when you need to work with content in a different tab.',
		param_model=SwitchTabAction,
		terminates_sequence=True,
	)
	async def switch(params: SwitchTabAction, browser_session: BrowserSession):
		try:
			target_id = await browser_session.get_target_id_from_tab_id(params.tab_id)
			event = browser_session.event_bus.dispatch(SwitchTabEvent(target_id=target_id))
			await event
			new_target_id = await event.event_result(raise_if_any=False, raise_if_none=False)
			if new_target_id:
				memory = f'Switched to tab #{new_target_id[-4:]}'
			else:
				memory = f'Switched to tab #{params.tab_id}'
			logger.info(f'🔄  {memory}')
			return ActionResult(extracted_content=memory, long_term_memory=memory)
		except Exception as error:
			logger.warning(f'Tab switch may have failed: {error}')
			memory = f'Attempted to switch to tab #{params.tab_id}'
			return ActionResult(extracted_content=memory, long_term_memory=memory)

	@tools.registry.action(
		'Close a tab by tab_id. Tab IDs are shown in browser state tabs list (last 4 chars of target_id). Use to clean up tabs you no longer need.',
		param_model=CloseTabAction,
	)
	async def close(params: CloseTabAction, browser_session: BrowserSession):
		try:
			target_id = await browser_session.get_target_id_from_tab_id(params.tab_id)
			event = browser_session.event_bus.dispatch(CloseTabEvent(target_id=target_id))
			await event
			await event.event_result(raise_if_any=False, raise_if_none=False)
			memory = f'Closed tab #{params.tab_id}'
			logger.info(f'🗑️  {memory}')
			return ActionResult(extracted_content=memory, long_term_memory=memory)
		except Exception as error:
			logger.warning(f'Tab {params.tab_id} may already be closed: {error}')
			memory = f'Tab #{params.tab_id} closed (was already closed or invalid)'
			return ActionResult(extracted_content=memory, long_term_memory=memory)
