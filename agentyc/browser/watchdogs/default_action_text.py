"""Text-entry helpers for the default action watchdog."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from agentyc.browser.events import TypeTextEvent
from agentyc.browser.views import BrowserError
from agentyc.dom.service import EnhancedDOMTreeNode


class DefaultActionTextMixin:
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
                self.logger.warning(
                    f'Failed to type to element {index_for_logging}: {error}. Falling back to page typing.'
                )
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
