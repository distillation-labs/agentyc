"""Dropdown selection helpers for the default action watchdog."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from agentyc.browser.events import SelectDropdownOptionEvent
from agentyc.browser.watchdogs.default_action_dropdown_common import resolve_dropdown_object_id

if TYPE_CHECKING:
	from agentyc.browser.watchdogs.default_action_dropdowns import DefaultActionDropdownMixin


async def select_dropdown_option(watchdog: 'DefaultActionDropdownMixin', event: SelectDropdownOptionEvent) -> dict[str, str]:
	try:
		element_node = event.node
		index_for_logging = element_node.backend_node_id or 'unknown'
		target_text = event.text
		cdp_session = await watchdog.browser_session.cdp_client_for_node(element_node)
		object_id = await resolve_dropdown_object_id(cdp_session, element_node.backend_node_id)

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
					watchdog.logger.info(
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
				watchdog.logger.info('⚠️ Selection was reverted by page framework, trying click fallback...')
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
					watchdog.logger.info(f'✅ {msg}')
					return {
						'success': 'true',
						'message': msg,
						'value': fallback_data.get('value', target_text),
						'backend_node_id': str(index_for_logging),
					}
				watchdog.logger.warning(f'⚠️ Click fallback also failed: {fallback_data.get("error", "unknown")}')

			if selection_result.get('success'):
				msg = selection_result.get('message', f'Selected option: {target_text}')
				watchdog.logger.debug(msg)
				return {
					'success': 'true',
					'message': msg,
					'value': selection_result.get('value', target_text),
					'backend_node_id': str(index_for_logging),
				}

			error_msg = selection_result.get('error', f'Failed to select option: {target_text}')
			available_options = selection_result.get('availableOptions', [])
			watchdog.logger.error(f'❌ {error_msg}')
			watchdog.logger.debug(f'Available options from JavaScript: {available_options}')
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
			watchdog.logger.error(error_msg)
			raise ValueError(error_msg) from error
	except Exception as error:
		error_msg = f'Failed to select dropdown option "{target_text}" for element {index_for_logging}: {str(error)}'
		watchdog.logger.error(error_msg)
		raise ValueError(error_msg) from error
