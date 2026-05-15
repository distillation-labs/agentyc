"""Dropdown helpers for the default action watchdog."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from agentyc.browser.events import GetDropdownOptionsEvent, SelectDropdownOptionEvent
from agentyc.browser.views import BrowserError


class DefaultActionDropdownMixin:
    """Dropdown inspection and selection helpers."""

    if TYPE_CHECKING:
        logger: Any
        browser_session: Any

    async def on_GetDropdownOptionsEvent(self, event: GetDropdownOptionsEvent) -> dict[str, str]:
        try:
            element_node = event.node
            index_for_logging = element_node.backend_node_id or 'unknown'
            cdp_session = await self.browser_session.cdp_client_for_node(element_node)
            try:
                object_result = await cdp_session.cdp_client.send.DOM.resolveNode(
                    params={'backendNodeId': element_node.backend_node_id}, session_id=cdp_session.session_id
                )
                remote_object = object_result.get('object', {})
                object_id = remote_object.get('objectId')
                if not object_id:
                    raise ValueError('Could not get object ID from resolved node')
            except Exception as error:
                raise ValueError(f'Failed to resolve node to object: {error}') from error

            check_combobox_script = """
            function() {
                const element = this;
                const role = element.getAttribute('role');
                const ariaControls = element.getAttribute('aria-controls');
                const ariaExpanded = element.getAttribute('aria-expanded');

                if (role === 'combobox' && ariaControls) {
                    return {
                        isCombobox: true,
                        ariaControls: ariaControls,
                        isExpanded: ariaExpanded === 'true',
                        tagName: element.tagName.toLowerCase()
                    };
                }
                return { isCombobox: false };
            }
            """
            combobox_check = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                params={
                    'functionDeclaration': check_combobox_script,
                    'objectId': object_id,
                    'returnByValue': True,
                },
                session_id=cdp_session.session_id,
            )
            combobox_info = combobox_check.get('result', {}).get('value', {})
            if combobox_info.get('isCombobox'):
                return await self._handle_aria_combobox_options(cdp_session, object_id, combobox_info, index_for_logging)

            options_script = """
            function() {
                const startElement = this;
                function checkDropdownElement(element) {
                    if (element.tagName.toLowerCase() === 'select') {
                        return {
                            type: 'select',
                            options: Array.from(element.options).map((opt, idx) => ({
                                text: opt.text.trim(),
                                value: opt.value,
                                index: idx,
                                selected: opt.selected
                            })),
                            id: element.id || '',
                            name: element.name || '',
                            source: 'target'
                        };
                    }

                    const role = element.getAttribute('role');
                    if (role === 'menu' || role === 'listbox') {
                        const menuItems = element.querySelectorAll('[role="menuitem"], [role="option"]');
                        const options = [];
                        menuItems.forEach((item, idx) => {
                            const text = item.textContent ? item.textContent.trim() : '';
                            if (text) {
                                options.push({
                                    text: text,
                                    value: item.getAttribute('data-value') || text,
                                    index: idx,
                                    selected: item.getAttribute('aria-selected') === 'true' || item.classList.contains('selected')
                                });
                            }
                        });
                        return {
                            type: 'aria',
                            options: options,
                            id: element.id || '',
                            name: element.getAttribute('aria-label') || '',
                            source: 'target'
                        };
                    }

                    if (element.classList.contains('dropdown') || element.classList.contains('ui')) {
                        const menuItems = element.querySelectorAll('.item, .option, [data-value]');
                        const options = [];
                        menuItems.forEach((item, idx) => {
                            const text = item.textContent ? item.textContent.trim() : '';
                            if (text) {
                                options.push({
                                    text: text,
                                    value: item.getAttribute('data-value') || text,
                                    index: idx,
                                    selected: item.classList.contains('selected') || item.classList.contains('active')
                                });
                            }
                        });
                        if (options.length > 0) {
                            return {
                                type: 'custom',
                                options: options,
                                id: element.id || '',
                                name: element.getAttribute('aria-label') || '',
                                source: 'target'
                            };
                        }
                    }
                    return null;
                }

                function searchChildrenForDropdowns(element, maxDepth, currentDepth = 0) {
                    if (currentDepth >= maxDepth) return null;
                    for (let child of element.children) {
                        const result = checkDropdownElement(child);
                        if (result) {
                            result.source = `child-depth-${currentDepth + 1}`;
                            return result;
                        }
                        const childResult = searchChildrenForDropdowns(child, maxDepth, currentDepth + 1);
                        if (childResult) {
                            return childResult;
                        }
                    }
                    return null;
                }

                let dropdownResult = checkDropdownElement(startElement);
                if (dropdownResult) {
                    return dropdownResult;
                }

                dropdownResult = searchChildrenForDropdowns(startElement, 4);
                if (dropdownResult) {
                    return dropdownResult;
                }

                return {
                    error: `Element and its children (depth 4) are not recognizable dropdown types (tag: ${startElement.tagName}, role: ${startElement.getAttribute('role')}, classes: ${startElement.className})`
                };
            }
            """
            result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                params={'functionDeclaration': options_script, 'objectId': object_id, 'returnByValue': True},
                session_id=cdp_session.session_id,
            )
            dropdown_data = result.get('result', {}).get('value', {})
            if dropdown_data.get('error'):
                raise BrowserError(message=dropdown_data['error'], long_term_memory=dropdown_data['error'])
            if not dropdown_data.get('options'):
                msg = f'No options found in dropdown at index {index_for_logging}'
                return {
                    'error': msg,
                    'short_term_memory': msg,
                    'long_term_memory': msg,
                    'backend_node_id': str(index_for_logging),
                }

            formatted_options = []
            for opt in dropdown_data['options']:
                encoded_text = json.dumps(opt['text'])
                status = ' (selected)' if opt.get('selected') else ''
                formatted_options.append(f'{opt["index"]}: text={encoded_text}, value={json.dumps(opt["value"])}{status}')

            dropdown_type = dropdown_data.get('type', 'select')
            element_info = (
                f'Index: {index_for_logging}, Type: {dropdown_type}, '
                f'ID: {dropdown_data.get("id", "none")}, Name: {dropdown_data.get("name", "none")}'
            )
            source_info = dropdown_data.get('source', 'unknown')
            if source_info == 'target':
                msg = f'Found {dropdown_type} dropdown ({element_info}):\n' + '\n'.join(formatted_options)
            else:
                msg = f'Found {dropdown_type} dropdown in {source_info} ({element_info}):\n' + '\n'.join(formatted_options)
            msg += f'\n\nUse the exact text or value string (without quotes) in select_dropdown(index={index_for_logging}, text=...)'

            if source_info == 'target':
                self.logger.info(f'📋 Found {len(dropdown_data["options"])} dropdown options for index {index_for_logging}')
            else:
                self.logger.info(
                    f'📋 Found {len(dropdown_data["options"])} dropdown options for index {index_for_logging} in {source_info}'
                )

            return {
                'type': dropdown_type,
                'options': json.dumps(dropdown_data['options']),
                'element_info': element_info,
                'source': source_info,
                'formatted_options': '\n'.join(formatted_options),
                'message': msg,
                'short_term_memory': msg,
                'long_term_memory': f'Got dropdown options for index {index_for_logging}',
                'backend_node_id': str(index_for_logging),
            }
        except BrowserError:
            raise
        except TimeoutError:
            msg = f'Failed to get dropdown options for index {index_for_logging} due to timeout.'
            self.logger.error(msg)
            raise BrowserError(message=msg, long_term_memory=msg)
        except Exception as error:
            msg = 'Failed to get dropdown options'
            error_msg = f'{msg}: {str(error)}'
            self.logger.error(error_msg)
            raise BrowserError(
                message=error_msg,
                long_term_memory=f'Failed to get dropdown options for index {index_for_logging}.',
            )

    async def _handle_aria_combobox_options(
        self,
        cdp_session,
        object_id: str,
        combobox_info: dict,
        index_for_logging: int | str,
    ) -> dict[str, str]:
        aria_controls_id = combobox_info.get('ariaControls')
        was_expanded = combobox_info.get('isExpanded', False)
        if not was_expanded:
            expand_script = """
            function() {
                const element = this;
                const focusEvent = new FocusEvent('focus', { bubbles: true, cancelable: true });
                element.dispatchEvent(focusEvent);
                element.focus();
                const focusInEvent = new FocusEvent('focusin', { bubbles: true, cancelable: true });
                element.dispatchEvent(focusInEvent);
                const clickEvent = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });
                element.dispatchEvent(clickEvent);
                const mousedownEvent = new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window });
                element.dispatchEvent(mousedownEvent);
                return { success: true, ariaExpanded: element.getAttribute('aria-expanded') };
            }
            """
            await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                params={'functionDeclaration': expand_script, 'objectId': object_id, 'returnByValue': True},
                session_id=cdp_session.session_id,
            )
            await asyncio.sleep(0.5)

        extract_options_script = """
        function(ariaControlsId) {
            const combobox = this;
            const listbox = document.getElementById(ariaControlsId);
            if (!listbox) {
                return {
                    error: `Could not find listbox element with id "${ariaControlsId}" referenced by aria-controls`,
                    ariaControlsId: ariaControlsId
                };
            }

            const optionElements = listbox.querySelectorAll('[role="option"]');
            const options = [];
            optionElements.forEach((item, idx) => {
                const text = item.textContent ? item.textContent.trim() : '';
                if (text) {
                    options.push({
                        text: text,
                        value: item.getAttribute('data-value') || item.getAttribute('value') || text,
                        index: idx,
                        selected: item.getAttribute('aria-selected') === 'true' || item.classList.contains('selected')
                    });
                }
            });

            if (options.length === 0) {
                const liElements = listbox.querySelectorAll('li');
                liElements.forEach((item, idx) => {
                    const text = item.textContent ? item.textContent.trim() : '';
                    if (text) {
                        options.push({
                            text: text,
                            value: item.getAttribute('data-value') || item.getAttribute('value') || text,
                            index: idx,
                            selected: item.getAttribute('aria-selected') === 'true' || item.classList.contains('selected')
                        });
                    }
                });
            }

            return {
                type: 'aria-combobox',
                options: options,
                id: combobox.id || '',
                name: combobox.getAttribute('aria-label') || combobox.getAttribute('name') || '',
                listboxId: ariaControlsId,
                source: 'aria-controls'
            };
        }
        """
        result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
            params={
                'functionDeclaration': extract_options_script,
                'objectId': object_id,
                'arguments': [{'value': aria_controls_id}],
                'returnByValue': True,
            },
            session_id=cdp_session.session_id,
        )
        dropdown_data = result.get('result', {}).get('value', {})

        if not was_expanded:
            collapse_script = """
            function() {
                this.blur();
                const escEvent = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true });
                this.dispatchEvent(escEvent);
                return true;
            }
            """
            await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                params={'functionDeclaration': collapse_script, 'objectId': object_id, 'returnByValue': True},
                session_id=cdp_session.session_id,
            )

        if dropdown_data.get('error'):
            raise BrowserError(message=dropdown_data['error'], long_term_memory=dropdown_data['error'])
        if not dropdown_data.get('options'):
            msg = f'No options found in ARIA combobox at index {index_for_logging} (listbox: {aria_controls_id})'
            return {
                'error': msg,
                'short_term_memory': msg,
                'long_term_memory': msg,
                'backend_node_id': str(index_for_logging),
            }

        formatted_options = []
        for opt in dropdown_data['options']:
            encoded_text = json.dumps(opt['text'])
            status = ' (selected)' if opt.get('selected') else ''
            formatted_options.append(f'{opt["index"]}: text={encoded_text}, value={json.dumps(opt["value"])}{status}')

        dropdown_type = dropdown_data.get('type', 'aria-combobox')
        element_info = (
            f'Index: {index_for_logging}, Type: {dropdown_type}, '
            f'ID: {dropdown_data.get("id", "none")}, Name: {dropdown_data.get("name", "none")}'
        )
        source_info = f'aria-controls → {aria_controls_id}'
        msg = f'Found {dropdown_type} dropdown ({element_info}):\n' + '\n'.join(formatted_options)
        msg += f'\n\nUse the exact text or value string (without quotes) in select_dropdown(index={index_for_logging}, text=...)'
        self.logger.info(f'📋 Found {len(dropdown_data["options"])} options in ARIA combobox at index {index_for_logging}')
        return {
            'type': dropdown_type,
            'options': json.dumps(dropdown_data['options']),
            'element_info': element_info,
            'source': source_info,
            'formatted_options': '\n'.join(formatted_options),
            'message': msg,
            'short_term_memory': msg,
            'long_term_memory': f'Got dropdown options for ARIA combobox at index {index_for_logging}',
            'backend_node_id': str(index_for_logging),
        }

    async def on_SelectDropdownOptionEvent(self, event: SelectDropdownOptionEvent) -> dict[str, str]:
        try:
            element_node = event.node
            index_for_logging = element_node.backend_node_id or 'unknown'
            target_text = event.text
            cdp_session = await self.browser_session.cdp_client_for_node(element_node)
            try:
                object_result = await cdp_session.cdp_client.send.DOM.resolveNode(
                    params={'backendNodeId': element_node.backend_node_id}, session_id=cdp_session.session_id
                )
                remote_object = object_result.get('object', {})
                object_id = remote_object.get('objectId')
                if not object_id:
                    raise ValueError('Could not get object ID from resolved node')
            except Exception as error:
                raise ValueError(f'Failed to resolve node to object: {error}') from error

            try:
                selection_script = """
                function(targetText) {
                    const startElement = this;
                    function attemptSelection(element) {
                        if (element.tagName.toLowerCase() === 'select') {
                            const options = Array.from(element.options);
                            const targetTextLower = targetText.toLowerCase();
                            for (const option of options) {
                                const optionTextLower = option.text.trim().toLowerCase();
                                const optionValueLower = option.value.toLowerCase();
                                if (optionTextLower === targetTextLower || optionValueLower === targetTextLower) {
                                    const expectedValue = option.value;
                                    element.focus();
                                    element.value = expectedValue;
                                    option.selected = true;
                                    element.selectedIndex = option.index;
                                    element.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
                                    element.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
                                    element.blur();
                                    if (element.value !== expectedValue) {
                                        return {
                                            success: false,
                                            error: `Selection was set but reverted by page framework. The dropdown may require clicking.`,
                                            selectionReverted: true,
                                            targetOption: {
                                                text: option.text.trim(),
                                                value: expectedValue,
                                                index: option.index
                                            },
                                            availableOptions: Array.from(element.options).map(opt => ({
                                                text: opt.text.trim(),
                                                value: opt.value
                                            }))
                                        };
                                    }
                                    return {
                                        success: true,
                                        message: `Selected option: ${option.text.trim()} (value: ${option.value})`,
                                        value: option.value
                                    };
                                }
                            }
                            return {
                                success: false,
                                error: `Option with text or value '${targetText}' not found in select element`,
                                availableOptions: options.map(opt => ({ text: opt.text.trim(), value: opt.value }))
                            };
                        }

                        const role = element.getAttribute('role');
                        if (role === 'menu' || role === 'listbox' || role === 'combobox') {
                            const menuItems = element.querySelectorAll('[role="menuitem"], [role="option"]');
                            const targetTextLower = targetText.toLowerCase();
                            for (const item of menuItems) {
                                if (item.textContent) {
                                    const itemTextLower = item.textContent.trim().toLowerCase();
                                    const itemValueLower = (item.getAttribute('data-value') || '').toLowerCase();
                                    if (itemTextLower === targetTextLower || itemValueLower === targetTextLower) {
                                        menuItems.forEach(mi => {
                                            mi.setAttribute('aria-selected', 'false');
                                            mi.classList.remove('selected');
                                        });
                                        item.setAttribute('aria-selected', 'true');
                                        item.classList.add('selected');
                                        item.click();
                                        item.dispatchEvent(new MouseEvent('click', { view: window, bubbles: true, cancelable: true }));
                                        return { success: true, message: `Selected ARIA menu item: ${item.textContent.trim()}` };
                                    }
                                }
                            }
                            return {
                                success: false,
                                error: `Menu item with text or value '${targetText}' not found`,
                                availableOptions: Array.from(menuItems).map(item => ({
                                    text: item.textContent ? item.textContent.trim() : '',
                                    value: item.getAttribute('data-value') || ''
                                })).filter(opt => opt.text || opt.value)
                            };
                        }

                        if (element.classList.contains('dropdown') || element.classList.contains('ui')) {
                            const menuItems = element.querySelectorAll('.item, .option, [data-value]');
                            const targetTextLower = targetText.toLowerCase();
                            for (const item of menuItems) {
                                if (item.textContent) {
                                    const itemTextLower = item.textContent.trim().toLowerCase();
                                    const itemValueLower = (item.getAttribute('data-value') || '').toLowerCase();
                                    if (itemTextLower === targetTextLower || itemValueLower === targetTextLower) {
                                        menuItems.forEach(mi => mi.classList.remove('selected', 'active'));
                                        item.classList.add('selected', 'active');
                                        const textElement = element.querySelector('.text');
                                        if (textElement) {
                                            textElement.textContent = item.textContent.trim();
                                        }
                                        item.click();
                                        item.dispatchEvent(new MouseEvent('click', { view: window, bubbles: true, cancelable: true }));
                                        element.dispatchEvent(new Event('change', { bubbles: true }));
                                        return { success: true, message: `Selected custom dropdown item: ${item.textContent.trim()}` };
                                    }
                                }
                            }
                            return {
                                success: false,
                                error: `Custom dropdown item with text or value '${targetText}' not found`,
                                availableOptions: Array.from(menuItems).map(item => ({
                                    text: item.textContent ? item.textContent.trim() : '',
                                    value: item.getAttribute('data-value') || ''
                                })).filter(opt => opt.text || opt.value)
                            };
                        }

                        return null;
                    }

                    function searchChildrenForSelection(element, maxDepth, currentDepth = 0) {
                        if (currentDepth >= maxDepth) return null;
                        for (let child of element.children) {
                            const result = attemptSelection(child);
                            if (result && result.success) {
                                return result;
                            }
                            const childResult = searchChildrenForSelection(child, maxDepth, currentDepth + 1);
                            if (childResult && childResult.success) {
                                return childResult;
                            }
                        }
                        return null;
                    }

                    let selectionResult = attemptSelection(startElement);
                    if (selectionResult) {
                        return selectionResult;
                    }
                    selectionResult = searchChildrenForSelection(startElement, 4);
                    if (selectionResult && selectionResult.success) {
                        return selectionResult;
                    }
                    return {
                        success: false,
                        error: `Element and its children (depth 4) do not contain a dropdown with option '${targetText}' (tag: ${startElement.tagName}, role: ${startElement.getAttribute('role')}, classes: ${startElement.className})`
                    };
                }
                """
                result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                    params={
                        'functionDeclaration': selection_script,
                        'arguments': [{'value': target_text}],
                        'objectId': object_id,
                        'returnByValue': True,
                    },
                    session_id=cdp_session.session_id,
                )
                selection_result = result.get('result', {}).get('value', {})

                if not selection_result.get('success'):
                    available_options = selection_result.get('availableOptions', [])
                    all_empty = available_options and all(
                        (not opt.get('text', '').strip() and not opt.get('value', '').strip())
                        if isinstance(opt, dict)
                        else not str(opt).strip()
                        for opt in available_options
                    )
                    if all_empty:
                        self.logger.info(
                            '⚠️ All dropdown options are empty — options may be lazily loaded. Focusing element and retrying...'
                        )
                        try:
                            await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                                params={'functionDeclaration': 'function() { this.focus(); }', 'objectId': object_id},
                                session_id=cdp_session.session_id,
                            )
                        except Exception:
                            pass
                        await asyncio.sleep(1.0)
                        retry_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                            params={
                                'functionDeclaration': selection_script,
                                'arguments': [{'value': target_text}],
                                'objectId': object_id,
                                'returnByValue': True,
                            },
                            session_id=cdp_session.session_id,
                        )
                        selection_result = retry_result.get('result', {}).get('value', {})

                if selection_result.get('selectionReverted'):
                    self.logger.info('⚠️ Selection was reverted by page framework, trying click fallback...')
                    target_option = selection_result.get('targetOption', {})
                    option_index = target_option.get('index', 0)
                    click_fallback_script = """
                    function(optionIndex) {
                        const select = this;
                        if (select.tagName.toLowerCase() !== 'select') return { success: false, error: 'Not a select element' };
                        const option = select.options[optionIndex];
                        if (!option) return { success: false, error: 'Option not found at index ' + optionIndex };
                        select.focus();
                        select.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                        select.selectedIndex = optionIndex;
                        option.selected = true;
                        option.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        select.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                        select.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
                        select.blur();
                        if (select.value === option.value || select.selectedIndex === optionIndex) {
                            return {
                                success: true,
                                message: 'Selected via click fallback: ' + option.text.trim(),
                                value: option.value
                            };
                        }
                        return {
                            success: false,
                            error: 'Click fallback also failed - framework may block all programmatic selection',
                            finalValue: select.value,
                            expectedValue: option.value
                        };
                    }
                    """
                    fallback_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                        params={
                            'functionDeclaration': click_fallback_script,
                            'arguments': [{'value': option_index}],
                            'objectId': object_id,
                            'returnByValue': True,
                        },
                        session_id=cdp_session.session_id,
                    )
                    fallback_data = fallback_result.get('result', {}).get('value', {})
                    if fallback_data.get('success'):
                        msg = fallback_data.get('message', f'Selected option via click: {target_text}')
                        self.logger.info(f'✅ {msg}')
                        return {
                            'success': 'true',
                            'message': msg,
                            'value': fallback_data.get('value', target_text),
                            'backend_node_id': str(index_for_logging),
                        }
                    self.logger.warning(f'⚠️ Click fallback also failed: {fallback_data.get("error", "unknown")}')

                if selection_result.get('success'):
                    msg = selection_result.get('message', f'Selected option: {target_text}')
                    self.logger.debug(msg)
                    return {
                        'success': 'true',
                        'message': msg,
                        'value': selection_result.get('value', target_text),
                        'backend_node_id': str(index_for_logging),
                    }

                error_msg = selection_result.get('error', f'Failed to select option: {target_text}')
                available_options = selection_result.get('availableOptions', [])
                self.logger.error(f'❌ {error_msg}')
                self.logger.debug(f'Available options from JavaScript: {available_options}')
                if available_options:
                    short_term_options = []
                    for opt in available_options:
                        if isinstance(opt, dict):
                            text = opt.get('text', '').strip()
                            value = opt.get('value', '').strip()
                            if text:
                                short_term_options.append(f'- {text}')
                            elif value:
                                short_term_options.append(f'- {value}')
                        elif isinstance(opt, str):
                            short_term_options.append(f'- {opt}')
                    if short_term_options:
                        return {
                            'success': 'false',
                            'error': error_msg,
                            'short_term_memory': 'Available dropdown options  are:\n' + '\n'.join(short_term_options),
                            'long_term_memory': (
                                f"Couldn't select the dropdown option as '{target_text}' is not one of the available options."
                            ),
                            'backend_node_id': str(index_for_logging),
                        }

                return {
                    'success': 'false',
                    'error': error_msg,
                    'backend_node_id': str(index_for_logging),
                }
            except Exception as error:
                error_msg = f'Failed to select dropdown option: {str(error)}'
                self.logger.error(error_msg)
                raise ValueError(error_msg) from error
        except Exception as error:
            error_msg = f'Failed to select dropdown option "{target_text}" for element {index_for_logging}: {str(error)}'
            self.logger.error(error_msg)
            raise ValueError(error_msg) from error
