"""Keyboard helpers for the default action watchdog."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from cdp_use.cdp.input.commands import DispatchKeyEventParameters

from agentyc.browser.keymap import get_key_info

if TYPE_CHECKING:
	from agentyc.browser.events import SendKeysEvent
	from agentyc.browser.watchdogs.default_action_navigation import DefaultActionNavigationMixin


async def dispatch_key_event(
	watchdog: DefaultActionNavigationMixin, cdp_session, event_type: str, key: str, modifiers: int = 0
) -> None:
	code, vk_code = get_key_info(key)
	params: DispatchKeyEventParameters = {
		'type': event_type,
		'key': key,
		'code': code,
	}
	if modifiers:
		params['modifiers'] = modifiers
	if vk_code is not None:
		params['windowsVirtualKeyCode'] = vk_code
	await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params=params, session_id=cdp_session.session_id)


async def send_keys_event(watchdog: DefaultActionNavigationMixin, event: SendKeysEvent) -> None:
	cdp_session = await watchdog.browser_session.get_or_create_cdp_session(focus=True)
	key_aliases = {
		'ctrl': 'Control',
		'control': 'Control',
		'alt': 'Alt',
		'option': 'Alt',
		'meta': 'Meta',
		'cmd': 'Meta',
		'command': 'Meta',
		'shift': 'Shift',
		'enter': 'Enter',
		'return': 'Enter',
		'tab': 'Tab',
		'delete': 'Delete',
		'backspace': 'Backspace',
		'escape': 'Escape',
		'esc': 'Escape',
		'space': ' ',
		'up': 'ArrowUp',
		'down': 'ArrowDown',
		'left': 'ArrowLeft',
		'right': 'ArrowRight',
		'pageup': 'PageUp',
		'pagedown': 'PageDown',
		'home': 'Home',
		'end': 'End',
	}
	keys = event.keys
	if '+' in keys:
		normalized_keys = '+'.join(key_aliases.get(part.strip().lower(), part) for part in keys.split('+'))
	else:
		normalized_keys = key_aliases.get(keys.strip().lower(), keys)

	if '+' in normalized_keys:
		parts = normalized_keys.split('+')
		modifiers = parts[:-1]
		main_key = parts[-1]
		modifier_value = 0
		modifier_map = {'Alt': 1, 'Control': 2, 'Meta': 4, 'Shift': 8}
		for mod in modifiers:
			modifier_value |= modifier_map.get(mod, 0)
		for mod in modifiers:
			await dispatch_key_event(watchdog, cdp_session, 'keyDown', mod)
		await dispatch_key_event(watchdog, cdp_session, 'keyDown', main_key, modifier_value)
		await dispatch_key_event(watchdog, cdp_session, 'keyUp', main_key, modifier_value)
		for mod in reversed(modifiers):
			await dispatch_key_event(watchdog, cdp_session, 'keyUp', mod)
	else:
		special_keys = {
			'Enter',
			'Tab',
			'Delete',
			'Backspace',
			'Escape',
			'ArrowUp',
			'ArrowDown',
			'ArrowLeft',
			'ArrowRight',
			'PageUp',
			'PageDown',
			'Home',
			'End',
			'Control',
			'Alt',
			'Meta',
			'Shift',
			'F1',
			'F2',
			'F3',
			'F4',
			'F5',
			'F6',
			'F7',
			'F8',
			'F9',
			'F10',
			'F11',
			'F12',
		}
		if normalized_keys in special_keys:
			await dispatch_key_event(watchdog, cdp_session, 'keyDown', normalized_keys)
			if normalized_keys == 'Enter':
				await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
					params={'type': 'char', 'text': '\r', 'key': 'Enter'},
					session_id=cdp_session.session_id,
				)
			await dispatch_key_event(watchdog, cdp_session, 'keyUp', normalized_keys)
		else:
			for char in normalized_keys:
				if char in ('\n', '\r'):
					await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
						params={
							'type': 'rawKeyDown',
							'windowsVirtualKeyCode': 13,
							'unmodifiedText': '\r',
							'text': '\r',
						},
						session_id=cdp_session.session_id,
					)
					await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
						params={
							'type': 'char',
							'windowsVirtualKeyCode': 13,
							'unmodifiedText': '\r',
							'text': '\r',
						},
						session_id=cdp_session.session_id,
					)
					await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
						params={
							'type': 'keyUp',
							'windowsVirtualKeyCode': 13,
							'unmodifiedText': '\r',
							'text': '\r',
						},
						session_id=cdp_session.session_id,
					)
					continue

				modifiers, vk_code, base_key = watchdog._get_char_modifiers_and_vk(char)
				key_code = watchdog._get_key_code_for_char(base_key)
				await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
					params={
						'type': 'keyDown',
						'key': base_key,
						'code': key_code,
						'modifiers': modifiers,
						'windowsVirtualKeyCode': vk_code,
					},
					session_id=cdp_session.session_id,
				)
				await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
					params={'type': 'char', 'text': char, 'key': char},
					session_id=cdp_session.session_id,
				)
				await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
					params={
						'type': 'keyUp',
						'key': base_key,
						'code': key_code,
						'modifiers': modifiers,
						'windowsVirtualKeyCode': vk_code,
					},
					session_id=cdp_session.session_id,
				)
				await asyncio.sleep(0.010)

	watchdog.logger.info(f'⌨️ Sent keys: {event.keys}')
	if 'enter' in event.keys.lower() or 'return' in event.keys.lower():
		await asyncio.sleep(0.1)
