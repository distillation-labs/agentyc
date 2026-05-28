"""Dropdown option discovery helpers for the default action watchdog."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from agentyc.browser.events import GetDropdownOptionsEvent
from agentyc.browser.views import BrowserError
from agentyc.browser.watchdogs.default_action_dropdown_common import resolve_dropdown_object_id

if TYPE_CHECKING:
	from agentyc.browser.watchdogs.default_action_dropdowns import DefaultActionDropdownMixin


async def get_dropdown_options(watchdog: DefaultActionDropdownMixin, event: GetDropdownOptionsEvent) -> dict[str, str]:
	try:
		element_node = event.node
		index_for_logging = element_node.backend_node_id or 'unknown'
		cdp_session = await watchdog.browser_session.cdp_client_for_node(element_node)
		object_id = await resolve_dropdown_object_id(cdp_session, element_node.backend_node_id)

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
			return await handle_aria_combobox_options(watchdog, cdp_session, object_id, combobox_info, index_for_logging)

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
			watchdog.logger.info(f'📋 Found {len(dropdown_data["options"])} dropdown options for index {index_for_logging}')
		else:
			watchdog.logger.info(
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
		watchdog.logger.error(msg)
		raise BrowserError(message=msg, long_term_memory=msg)
	except Exception as error:
		msg = 'Failed to get dropdown options'
		error_msg = f'{msg}: {str(error)}'
		watchdog.logger.error(error_msg)
		raise BrowserError(
			message=error_msg,
			long_term_memory=f'Failed to get dropdown options for index {index_for_logging}.',
		)


async def handle_aria_combobox_options(
	watchdog: DefaultActionDropdownMixin,
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
	watchdog.logger.info(f'📋 Found {len(dropdown_data["options"])} options in ARIA combobox at index {index_for_logging}')
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
