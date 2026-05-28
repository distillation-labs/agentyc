"""Input typing helpers for the default action watchdog."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agentyc.browser.views import BrowserError
from agentyc.dom.service import EnhancedDOMTreeNode


class DefaultActionTextInputMixin:
	"""CDP typing and framework-event helpers."""

	if TYPE_CHECKING:
		logger: Any
		browser_session: Any

		async def _check_element_occlusion(self, backend_node_id: int, x: float, y: float, cdp_session) -> bool: ...
		def _get_char_modifiers_and_vk(self, char: str) -> tuple[int, int, str]: ...
		def _get_key_code_for_char(self, char: str) -> str: ...
		async def _focus_element_simple(
			self, backend_node_id: int, object_id: str, cdp_session, input_coordinates: dict | None = None
		) -> bool: ...
		def _requires_direct_value_assignment(self, element_node: EnhancedDOMTreeNode) -> bool: ...
		async def _set_value_directly(
			self, element_node: EnhancedDOMTreeNode, text: str, object_id: str, cdp_session
		) -> None: ...
		async def _clear_text_field(self, object_id: str, cdp_session) -> bool: ...

	async def _input_text_element_node_impl(
		self, element_node: EnhancedDOMTreeNode, text: str, clear: bool = True, is_sensitive: bool = False
	) -> dict | None:
		try:
			cdp_client = self.browser_session.cdp_client
			cdp_session = await self.browser_session.cdp_client_for_node(element_node)
			backend_node_id = element_node.backend_node_id
			input_coordinates = None

			try:
				await cdp_session.cdp_client.send.DOM.scrollIntoViewIfNeeded(
					params={'backendNodeId': backend_node_id}, session_id=cdp_session.session_id
				)
				await asyncio.sleep(0.01)
			except Exception as error:
				error_str = str(error)
				if 'Node is detached from document' in error_str or 'detached from document' in error_str:
					self.logger.debug(
						f'Element node temporarily detached during scroll (common with shadow DOM), continuing: {element_node}'
					)
				else:
					self.logger.debug(
						f'Failed to scroll element {element_node} into view before typing: {type(error).__name__}: {error}'
					)

			result = await cdp_client.send.DOM.resolveNode(
				params={'backendNodeId': backend_node_id},
				session_id=cdp_session.session_id,
			)
			assert 'object' in result and 'objectId' in result['object'], (
				'Failed to find DOM element based on backendNodeId, maybe page content changed?'
			)
			object_id = result['object']['objectId']

			coords = await self.browser_session.get_element_coordinates(backend_node_id, cdp_session)
			if coords:
				center_x = coords.x + coords.width / 2
				center_y = coords.y + coords.height / 2
				is_occluded = await self._check_element_occlusion(backend_node_id, center_x, center_y, cdp_session)
				if is_occluded:
					self.logger.debug('🚫 Input element is occluded, skipping coordinate-based focus')
					input_coordinates = None
				else:
					input_coordinates = {'input_x': center_x, 'input_y': center_y}
					self.logger.debug(f'Using unified coordinates: x={center_x:.1f}, y={center_y:.1f}')
			else:
				input_coordinates = None
				self.logger.debug('No coordinates found for element')

			if not object_id:
				raise ValueError('Could not get object_id for element')

			await self._focus_element_simple(
				backend_node_id=backend_node_id,
				object_id=object_id,
				cdp_session=cdp_session,
				input_coordinates=input_coordinates,
			)

			if self._requires_direct_value_assignment(element_node):
				self.logger.debug(
					f'🎯 Element type={element_node.attributes.get("type")} requires direct value assignment, setting value directly'
				)
				await self._set_value_directly(element_node, text, object_id, cdp_session)
				return input_coordinates

			if clear:
				cleared_successfully = await self._clear_text_field(object_id=object_id, cdp_session=cdp_session)
				if not cleared_successfully:
					self.logger.warning('⚠️ Text field clearing failed, typing may append to existing text')

			attrs = element_node.attributes or {}
			is_contenteditable = attrs.get('contenteditable') in ('true', '') or (
				attrs.get('role') == 'textbox' and element_node.tag_name not in ('input', 'textarea')
			)

			fast_path_used = False
			if text:
				try:
					await cdp_session.cdp_client.send.Input.insertText(
						params={'text': text},
						session_id=cdp_session.session_id,
					)
					fast_path_used = True
					self.logger.debug(f'🎯 insertText fast path: {len(text)} chars in 1 CDP call')
				except Exception as error:
					self.logger.debug(f'insertText unavailable, falling back to char-by-char: {error}')

			if not fast_path_used and text:
				check_first_char = is_contenteditable and len(text) > 0 and clear
				first_char = text[0] if check_first_char else None
				for index, char in enumerate(text):
					if char == '\n':
						await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
							params={'type': 'keyDown', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13},
							session_id=cdp_session.session_id,
						)
						await asyncio.sleep(0.001)
						await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
							params={'type': 'char', 'text': '\r', 'key': 'Enter'},
							session_id=cdp_session.session_id,
						)
						await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
							params={'type': 'keyUp', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13},
							session_id=cdp_session.session_id,
						)
					else:
						modifiers, vk_code, base_key = self._get_char_modifiers_and_vk(char)
						key_code = self._get_key_code_for_char(base_key)
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
						await asyncio.sleep(0.005)
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
					if index == 0 and check_first_char and first_char:
						check_result = await cdp_session.cdp_client.send.Runtime.evaluate(
							params={'expression': 'document.activeElement.textContent'},
							session_id=cdp_session.session_id,
						)
						content = check_result.get('result', {}).get('value', '')
						if first_char not in content:
							self.logger.debug(f'🎯 First char "{first_char}" was dropped (leaf-start bug), retyping')
							modifiers, vk_code, base_key = self._get_char_modifiers_and_vk(first_char)
							key_code = self._get_key_code_for_char(base_key)
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
							await asyncio.sleep(0.005)
							await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
								params={'type': 'char', 'text': first_char, 'key': first_char},
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
					await asyncio.sleep(0.001)

			await self._trigger_framework_events(object_id=object_id, cdp_session=cdp_session)

			if not is_sensitive:
				try:
					await asyncio.sleep(0.05)
					readback_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
						params={
							'objectId': object_id,
							'functionDeclaration': 'function() { return this.value !== undefined ? this.value : this.textContent; }',
							'returnByValue': True,
						},
						session_id=cdp_session.session_id,
					)
					actual_value = readback_result.get('result', {}).get('value')
					if actual_value is not None:
						if input_coordinates is None:
							input_coordinates = {}
						input_coordinates['actual_value'] = actual_value
				except Exception as error:
					self.logger.debug(f'Value readback failed (non-critical): {error}')

			if clear and not is_sensitive and input_coordinates and 'actual_value' in input_coordinates:
				actual_value = input_coordinates['actual_value']
				if (
					isinstance(actual_value, str)
					and actual_value != text
					and len(actual_value) > len(text)
					and (actual_value.endswith(text) or actual_value.startswith(text))
				):
					self.logger.info(f'🔄 Concatenation detected: got "{actual_value}", expected "{text}" — auto-retrying')
					try:
						retry_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
							params={
								'objectId': object_id,
								'functionDeclaration': """
                                    function(newValue) {
                                        if (this.value !== undefined) {
                                            var desc = Object.getOwnPropertyDescriptor(
                                                HTMLInputElement.prototype, 'value'
                                            ) || Object.getOwnPropertyDescriptor(
                                                HTMLTextAreaElement.prototype, 'value'
                                            );
                                            if (desc && desc.set) {
                                                desc.set.call(this, newValue);
                                            } else {
                                                this.value = newValue;
                                            }
                                        } else if (this.isContentEditable) {
                                            this.textContent = newValue;
                                        }
                                        this.dispatchEvent(new Event('input', { bubbles: true }));
                                        this.dispatchEvent(new Event('change', { bubbles: true }));
                                        return this.value !== undefined ? this.value : this.textContent;
                                    }
                                """,
								'arguments': [{'value': text}],
								'returnByValue': True,
							},
							session_id=cdp_session.session_id,
						)
						retry_value = retry_result.get('result', {}).get('value')
						if retry_value is not None:
							input_coordinates['actual_value'] = retry_value
							if retry_value == text:
								self.logger.info('✅ Auto-retry fixed concatenation')
							else:
								self.logger.warning(f'⚠️ Auto-retry value still differs: "{retry_value}"')
					except Exception as error:
						self.logger.debug(f'Auto-retry failed (non-critical): {error}')

			return input_coordinates
		except Exception as error:
			self.logger.error(f'Failed to input text via CDP: {type(error).__name__}: {error}')
			raise BrowserError(f'Failed to input text into element: {repr(element_node)}')

	async def _trigger_framework_events(self, object_id: str, cdp_session) -> None:
		try:
			framework_events_script = """
            function() {
                const element = this;
                if (!element) return false;
                element.focus();
                const events = [
                    { type: 'input', bubbles: true, cancelable: true },
                    { type: 'change', bubbles: true, cancelable: true },
                    { type: 'blur', bubbles: true, cancelable: true }
                ];
                let success = true;
                events.forEach(eventConfig => {
                    try {
                        const event = new Event(eventConfig.type, {
                            bubbles: eventConfig.bubbles,
                            cancelable: eventConfig.cancelable
                        });
                        if (eventConfig.type === 'input') {
                            const inputEvent = new InputEvent('input', {
                                bubbles: true,
                                cancelable: true,
                                data: element.value,
                                inputType: 'insertText'
                            });
                            element.dispatchEvent(inputEvent);
                        } else {
                            element.dispatchEvent(event);
                        }
                    } catch (e) {
                        success = false;
                        console.warn('Framework event dispatch failed:', eventConfig.type, e);
                    }
                });
                if (element._reactInternalFiber || element._reactInternalInstance || element.__reactInternalInstance) {
                    try {
                        const syntheticInputEvent = new InputEvent('input', {
                            bubbles: true,
                            cancelable: true,
                            data: element.value
                        });
                        Object.defineProperty(syntheticInputEvent, 'isTrusted', { value: true });
                        element.dispatchEvent(syntheticInputEvent);
                    } catch (e) {
                        console.warn('React synthetic event failed:', e);
                    }
                }
                if (element.__vue__ || element._vnode || element.__vueParentComponent) {
                    try {
                        const vueEvent = new Event('input', { bubbles: true });
                        setTimeout(() => element.dispatchEvent(vueEvent), 0);
                    } catch (e) {
                        console.warn('Vue reactivity trigger failed:', e);
                    }
                }
                return success;
            }
            """
			result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
				params={
					'objectId': object_id,
					'functionDeclaration': framework_events_script,
					'returnByValue': True,
				},
				session_id=cdp_session.session_id,
			)
			success = result.get('result', {}).get('value', False)
			if success:
				self.logger.debug('✅ Framework events triggered successfully')
			else:
				self.logger.warning('⚠️ Failed to trigger framework events')
		except Exception as error:
			self.logger.warning(f'⚠️ Failed to trigger framework events: {type(error).__name__}: {error}')
