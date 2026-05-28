"""Text-entry helpers for the default action watchdog."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agentyc.browser.events import TypeTextEvent
from agentyc.browser.watchdogs.default_action_text_field import DefaultActionTextFieldMixin
from agentyc.browser.watchdogs.default_action_text_input import DefaultActionTextInputMixin


class DefaultActionTextMixin(DefaultActionTextFieldMixin, DefaultActionTextInputMixin):
	"""Typing and value-assignment helpers."""

	if TYPE_CHECKING:
		logger: Any
		browser_session: Any

		async def _click_element_node_impl(self, element_node) -> dict | None: ...
		async def _check_element_occlusion(self, backend_node_id: int, x: float, y: float, cdp_session) -> bool: ...

	async def on_TypeTextEvent(self, event: TypeTextEvent) -> dict | None:
		try:
			element_node = event.node
			index_for_logging = element_node.backend_node_id or 'unknown'
			if not element_node.backend_node_id or element_node.backend_node_id == 0:
				await self._type_to_page(event.text)
				if event.is_sensitive:
					if event.sensitive_key_name:
						self.logger.info(f'⌨️ Typed <{event.sensitive_key_name}> to the page (current focus)')
					else:
						self.logger.info('⌨️ Typed <sensitive> to the page (current focus)')
				else:
					self.logger.info(f'⌨️ Typed "{event.text}" to the page (current focus)')
				return None

			try:
				input_metadata = await self._input_text_element_node_impl(
					element_node,
					event.text,
					clear=event.clear or (not event.text),
					is_sensitive=event.is_sensitive,
				)
				if event.is_sensitive:
					if event.sensitive_key_name:
						self.logger.info(f'⌨️ Typed <{event.sensitive_key_name}> into element with index {index_for_logging}')
					else:
						self.logger.info(f'⌨️ Typed <sensitive> into element with index {index_for_logging}')
				else:
					self.logger.info(f'⌨️ Typed "{event.text}" into element with index {index_for_logging}')
				self.logger.debug(f'Element xpath: {element_node.xpath}')
				return input_metadata
			except Exception as error:
				self.logger.warning(f'Failed to type to element {index_for_logging}: {error}. Falling back to page typing.')
				try:
					await asyncio.wait_for(self._click_element_node_impl(element_node), timeout=10.0)
				except Exception:
					pass
				await self._type_to_page(event.text)
				if event.is_sensitive:
					if event.sensitive_key_name:
						self.logger.info(f'⌨️ Typed <{event.sensitive_key_name}> to the page as fallback')
					else:
						self.logger.info('⌨️ Typed <sensitive> to the page as fallback')
				else:
					self.logger.info(f'⌨️ Typed "{event.text}" to the page as fallback')
				return None
		except Exception:
			raise

	async def _type_to_page(self, text: str):
		try:
			cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=None, focus=True)
			for char in text:
				if char == '\n':
					await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
						params={'type': 'keyDown', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13},
						session_id=cdp_session.session_id,
					)
					await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
						params={'type': 'char', 'text': '\r'},
						session_id=cdp_session.session_id,
					)
					await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
						params={'type': 'keyUp', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13},
						session_id=cdp_session.session_id,
					)
				else:
					await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
						params={'type': 'keyDown', 'key': char},
						session_id=cdp_session.session_id,
					)
					await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
						params={'type': 'char', 'text': char},
						session_id=cdp_session.session_id,
					)
					await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
						params={'type': 'keyUp', 'key': char},
						session_id=cdp_session.session_id,
					)
				await asyncio.sleep(0.010)
		except Exception as error:
			raise Exception(f'Failed to type to page: {str(error)}')

	def _get_char_modifiers_and_vk(self, char: str) -> tuple[int, int, str]:
		shift_chars = {
			'!': ('1', 49),
			'@': ('2', 50),
			'#': ('3', 51),
			'$': ('4', 52),
			'%': ('5', 53),
			'^': ('6', 54),
			'&': ('7', 55),
			'*': ('8', 56),
			'(': ('9', 57),
			')': ('0', 48),
			'_': ('-', 189),
			'+': ('=', 187),
			'{': ('[', 219),
			'}': (']', 221),
			'|': ('\\', 220),
			':': (';', 186),
			'"': ("'", 222),
			'<': (',', 188),
			'>': ('.', 190),
			'?': ('/', 191),
			'~': ('`', 192),
		}
		if char in shift_chars:
			base_key, vk_code = shift_chars[char]
			return (8, vk_code, base_key)
		if char.isupper():
			return (8, ord(char), char.lower())
		if char.islower():
			return (0, ord(char.upper()), char)
		if char.isdigit():
			return (0, ord(char), char)

		no_shift_chars = {
			' ': 32,
			'-': 189,
			'=': 187,
			'[': 219,
			']': 221,
			'\\': 220,
			';': 186,
			"'": 222,
			',': 188,
			'.': 190,
			'/': 191,
			'`': 192,
		}
		if char in no_shift_chars:
			return (0, no_shift_chars[char], char)
		return (0, ord(char.upper()) if char.isalpha() else ord(char), char)

	def _get_key_code_for_char(self, char: str) -> str:
		key_codes = {
			' ': 'Space',
			'.': 'Period',
			',': 'Comma',
			'-': 'Minus',
			'_': 'Minus',
			'@': 'Digit2',
			'!': 'Digit1',
			'?': 'Slash',
			':': 'Semicolon',
			';': 'Semicolon',
			'(': 'Digit9',
			')': 'Digit0',
			'[': 'BracketLeft',
			']': 'BracketRight',
			'{': 'BracketLeft',
			'}': 'BracketRight',
			'/': 'Slash',
			'\\': 'Backslash',
			'=': 'Equal',
			'+': 'Equal',
			'*': 'Digit8',
			'&': 'Digit7',
			'%': 'Digit5',
			'$': 'Digit4',
			'#': 'Digit3',
			'^': 'Digit6',
			'~': 'Backquote',
			'`': 'Backquote',
			"'": 'Quote',
			'"': 'Quote',
		}
		if char.isdigit():
			return f'Digit{char}'
		if char.isalpha():
			return f'Key{char.upper()}'
		if char in key_codes:
			return key_codes[char]
		return f'Key{char.upper()}'
