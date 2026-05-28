"""Field-clear/focus helpers for the default action watchdog."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from agentyc.dom.service import EnhancedDOMTreeNode


class DefaultActionTextFieldMixin:
	"""Field clearing, focus, and direct-value helpers."""

	if TYPE_CHECKING:
		logger: Any

	async def _clear_text_field(self, object_id: str, cdp_session) -> bool:
		try:
			self.logger.debug('🧹 Clearing text field using JavaScript value setting')
			clear_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
				params={
					'functionDeclaration': """
                        function() {
                            const hasContentEditable = this.getAttribute('contenteditable') === 'true' ||
                                                    this.getAttribute('contenteditable') === '' ||
                                                    this.isContentEditable === true;

                            if (hasContentEditable) {
                                while (this.firstChild) {
                                    this.removeChild(this.firstChild);
                                }
                                this.textContent = "";
                                this.innerHTML = "";
                                this.focus();
                                const selection = window.getSelection();
                                const range = document.createRange();
                                range.setStart(this, 0);
                                range.setEnd(this, 0);
                                selection.removeAllRanges();
                                selection.addRange(range);
                                this.dispatchEvent(new Event('input', { bubbles: true }));
                                this.dispatchEvent(new Event('change', { bubbles: true }));
                                return {cleared: true, method: 'contenteditable', finalText: this.textContent};
                            } else if (this.value !== undefined) {
                                try {
                                    this.select();
                                } catch (e) {
                                }
                                this.value = "";
                                this.dispatchEvent(new Event('input', { bubbles: true }));
                                this.dispatchEvent(new Event('change', { bubbles: true }));
                                return {cleared: true, method: 'value', finalText: this.value};
                            }
                            return {cleared: false, method: 'none', error: 'Not a supported input type'};
                        }
                    """,
					'objectId': object_id,
					'returnByValue': True,
				},
				session_id=cdp_session.session_id,
			)
			clear_info = clear_result.get('result', {}).get('value', {})
			self.logger.debug(f'Clear result: {clear_info}')
			if clear_info.get('cleared'):
				final_text = clear_info.get('finalText', '')
				if not final_text or not final_text.strip():
					self.logger.debug(f'✅ Text field cleared successfully using {clear_info.get("method")}')
					return True
				self.logger.debug(f'⚠️ JavaScript clear partially failed, field still contains: "{final_text}"')
			else:
				self.logger.debug(f'❌ JavaScript clear failed: {clear_info.get("error", "Unknown error")}')
		except Exception as error:
			self.logger.debug(f'JavaScript clear failed with exception: {error}')
			return False

		try:
			self.logger.debug('🧹 Fallback: Clearing using triple-click + Delete')
			bounds_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
				params={
					'functionDeclaration': 'function() { return this.getBoundingClientRect(); }',
					'objectId': object_id,
					'returnByValue': True,
				},
				session_id=cdp_session.session_id,
			)
			if bounds_result.get('result', {}).get('value'):
				bounds = bounds_result['result']['value']
				center_x = bounds['x'] + bounds['width'] / 2
				center_y = bounds['y'] + bounds['height'] / 2
				await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
					params={
						'type': 'mousePressed',
						'x': center_x,
						'y': center_y,
						'button': 'left',
						'clickCount': 3,
					},
					session_id=cdp_session.session_id,
				)
				await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
					params={
						'type': 'mouseReleased',
						'x': center_x,
						'y': center_y,
						'button': 'left',
						'clickCount': 3,
					},
					session_id=cdp_session.session_id,
				)
				await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
					params={'type': 'keyDown', 'key': 'Delete', 'code': 'Delete'},
					session_id=cdp_session.session_id,
				)
				await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
					params={'type': 'keyUp', 'key': 'Delete', 'code': 'Delete'},
					session_id=cdp_session.session_id,
				)
				self.logger.debug('✅ Text field cleared using triple-click + Delete')
				return True
		except Exception as error:
			self.logger.debug(f'Triple-click clear failed: {error}')

		try:
			import platform

			is_macos = platform.system() == 'Darwin'
			select_all_modifier = 4 if is_macos else 2
			modifier_name = 'Cmd' if is_macos else 'Ctrl'
			self.logger.debug(f'🧹 Last resort: Clearing using {modifier_name}+A + Backspace')

			await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
				params={'type': 'keyDown', 'key': 'a', 'code': 'KeyA', 'modifiers': select_all_modifier},
				session_id=cdp_session.session_id,
			)
			await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
				params={'type': 'keyUp', 'key': 'a', 'code': 'KeyA', 'modifiers': select_all_modifier},
				session_id=cdp_session.session_id,
			)
			await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
				params={'type': 'keyDown', 'key': 'Backspace', 'code': 'Backspace'},
				session_id=cdp_session.session_id,
			)
			await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
				params={'type': 'keyUp', 'key': 'Backspace', 'code': 'Backspace'},
				session_id=cdp_session.session_id,
			)
			self.logger.debug('✅ Text field cleared using keyboard shortcuts')
			return True
		except Exception as error:
			self.logger.debug(f'All clearing strategies failed: {error}')
			return False

	async def _focus_element_simple(
		self, backend_node_id: int, object_id: str, cdp_session, input_coordinates: dict | None = None
	) -> bool:
		try:
			result = await cdp_session.cdp_client.send.DOM.focus(
				params={'backendNodeId': backend_node_id},
				session_id=cdp_session.session_id,
			)
			self.logger.debug(f'Element focused using CDP DOM.focus (result: {result})')
			return True
		except Exception as error:
			self.logger.debug(f'❌ CDP DOM.focus threw exception: {type(error).__name__}: {error}')

		if input_coordinates and 'input_x' in input_coordinates and 'input_y' in input_coordinates:
			try:
				click_x = input_coordinates['input_x']
				click_y = input_coordinates['input_y']
				self.logger.debug(f'🎯 Attempting click-to-focus at ({click_x:.1f}, {click_y:.1f})')
				await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
					params={
						'type': 'mousePressed',
						'x': click_x,
						'y': click_y,
						'button': 'left',
						'clickCount': 1,
					},
					session_id=cdp_session.session_id,
				)
				await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
					params={
						'type': 'mouseReleased',
						'x': click_x,
						'y': click_y,
						'button': 'left',
						'clickCount': 1,
					},
					session_id=cdp_session.session_id,
				)
				self.logger.debug('✅ Element focused using click method')
				return True
			except Exception as error:
				self.logger.debug(f'Click focus failed: {error}')

		self.logger.debug('Focus strategies failed, will attempt typing anyway')
		return False

	def _requires_direct_value_assignment(self, element_node: EnhancedDOMTreeNode) -> bool:
		if not element_node.tag_name or not element_node.attributes:
			return False
		tag_name = element_node.tag_name.lower()
		if tag_name == 'input':
			input_type = element_node.attributes.get('type', '').lower()
			if input_type in {'date', 'time', 'datetime-local', 'month', 'week', 'color', 'range'}:
				return True
			if input_type in {'text', ''}:
				class_attr = element_node.attributes.get('class', '').lower()
				if any(
					indicator in class_attr
					for indicator in ['datepicker', 'daterangepicker', 'datetimepicker', 'bootstrap-datepicker']
				):
					return True
				if any(attr in element_node.attributes for attr in ['data-datepicker', 'data-date-format', 'data-provide']):
					return True
		return False

	async def _set_value_directly(self, element_node: EnhancedDOMTreeNode, text: str, object_id: str, cdp_session) -> None:
		try:
			set_value_js = f"""
            function() {{
                const oldValue = this.value;
                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype,
                    'value'
                ).set;
                nativeInputValueSetter.call(this, {json.dumps(text)});
                this.dispatchEvent(new FocusEvent('focus', {{ bubbles: true }}));
                const inputEvent = new Event('input', {{ bubbles: true, cancelable: true }});
                this.dispatchEvent(inputEvent);
                const changeEvent = new Event('change', {{ bubbles: true, cancelable: true }});
                this.dispatchEvent(changeEvent);
                this.dispatchEvent(new FocusEvent('blur', {{ bubbles: true }}));
                if (typeof jQuery !== 'undefined' && jQuery.fn) {{
                    try {{
                        jQuery(this).trigger('change');
                        if (jQuery(this).data('datepicker')) {{
                            jQuery(this).datepicker('update');
                        }}
                    }} catch (e) {{
                    }}
                }}
                return this.value;
            }}
            """
			result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
				params={
					'objectId': object_id,
					'functionDeclaration': set_value_js,
					'returnByValue': True,
				},
				session_id=cdp_session.session_id,
			)
			if 'result' in result and 'value' in result['result']:
				actual_value = result['result']['value']
				self.logger.debug(f'✅ Value set directly to: "{actual_value}"')
			else:
				self.logger.warning('⚠️ Could not verify value was set correctly')
		except Exception as error:
			self.logger.error(f'❌ Failed to set value directly: {error}')
			raise
