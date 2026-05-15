from __future__ import annotations

from typing import Any

from agentyc.dom.views import EnhancedDOMTreeNode, NodeType, SimplifiedNode


class SerializerCompoundMixin:
	def _safe_parse_number(self, value_str: str, default: float) -> float:
		try:
			return float(value_str)
		except (ValueError, TypeError):
			return default

	def _safe_parse_optional_number(self, value_str: str | None) -> float | None:
		if not value_str:
			return None
		try:
			return float(value_str)
		except (ValueError, TypeError):
			return None

	def _add_compound_components(self, simplified: SimplifiedNode, node: EnhancedDOMTreeNode) -> None:
		if node.tag_name not in ['input', 'select', 'details', 'audio', 'video']:
			return

		if node.tag_name == 'input':
			if not node.attributes or node.attributes.get('type') not in [
				'date',
				'time',
				'datetime-local',
				'month',
				'week',
				'range',
				'number',
				'color',
				'file',
			]:
				return
		elif not node.ax_node or not node.ax_node.child_ids:
			return

		element_type = node.tag_name
		input_type = node.attributes.get('type', '') if node.attributes else ''

		if element_type == 'input':
			if input_type in ['date', 'time', 'datetime-local', 'month', 'week']:
				pass
			elif input_type == 'range':
				min_val = node.attributes.get('min', '0') if node.attributes else '0'
				max_val = node.attributes.get('max', '100') if node.attributes else '100'
				node._compound_children.append(
					{
						'role': 'slider',
						'name': 'Value',
						'valuemin': self._safe_parse_number(min_val, 0.0),
						'valuemax': self._safe_parse_number(max_val, 100.0),
						'valuenow': None,
					}
				)
				simplified.is_compound_component = True
			elif input_type == 'number':
				min_val = node.attributes.get('min') if node.attributes else None
				max_val = node.attributes.get('max') if node.attributes else None
				node._compound_children.extend(
					[
						{'role': 'button', 'name': 'Increment', 'valuemin': None, 'valuemax': None, 'valuenow': None},
						{'role': 'button', 'name': 'Decrement', 'valuemin': None, 'valuemax': None, 'valuenow': None},
						{
							'role': 'textbox',
							'name': 'Value',
							'valuemin': self._safe_parse_optional_number(min_val),
							'valuemax': self._safe_parse_optional_number(max_val),
							'valuenow': None,
						},
					]
				)
				simplified.is_compound_component = True
			elif input_type == 'color':
				node._compound_children.extend(
					[
						{'role': 'textbox', 'name': 'Hex Value', 'valuemin': None, 'valuemax': None, 'valuenow': None},
						{'role': 'button', 'name': 'Color Picker', 'valuemin': None, 'valuemax': None, 'valuenow': None},
					]
				)
				simplified.is_compound_component = True
			elif input_type == 'file':
				multiple = 'multiple' in node.attributes if node.attributes else False
				current_value = 'None'
				if node.ax_node and node.ax_node.properties:
					for prop in node.ax_node.properties:
						if prop.name == 'valuetext' and prop.value:
							value_str = str(prop.value).strip()
							if value_str and value_str.lower() not in ['', 'no file chosen', 'no file selected']:
								current_value = value_str
							break
						elif prop.name == 'value' and prop.value:
							value_str = str(prop.value).strip()
							if value_str:
								if '\\' in value_str:
									current_value = value_str.split('\\')[-1]
								elif '/' in value_str:
									current_value = value_str.split('/')[-1]
								else:
									current_value = value_str
								break

				node._compound_children.extend(
					[
						{'role': 'button', 'name': 'Browse Files', 'valuemin': None, 'valuemax': None, 'valuenow': None},
						{
							'role': 'textbox',
							'name': f'{"Files" if multiple else "File"} Selected',
							'valuemin': None,
							'valuemax': None,
							'valuenow': current_value,
						},
					]
				)
				simplified.is_compound_component = True

		elif element_type == 'select':
			base_components = [
				{'role': 'button', 'name': 'Dropdown Toggle', 'valuemin': None, 'valuemax': None, 'valuenow': None}
			]
			options_info = self._extract_select_options(node)
			if options_info:
				options_component = {
					'role': 'listbox',
					'name': 'Options',
					'valuemin': None,
					'valuemax': None,
					'valuenow': None,
					'options_count': options_info['count'],
					'first_options': options_info['first_options'],
				}
				if options_info['format_hint']:
					options_component['format_hint'] = options_info['format_hint']
				base_components.append(options_component)
			else:
				base_components.append(
					{'role': 'listbox', 'name': 'Options', 'valuemin': None, 'valuemax': None, 'valuenow': None}
				)

			node._compound_children.extend(base_components)
			simplified.is_compound_component = True

		elif element_type == 'details':
			node._compound_children.extend(
				[
					{'role': 'button', 'name': 'Toggle Disclosure', 'valuemin': None, 'valuemax': None, 'valuenow': None},
					{'role': 'region', 'name': 'Content Area', 'valuemin': None, 'valuemax': None, 'valuenow': None},
				]
			)
			simplified.is_compound_component = True

		elif element_type == 'audio':
			node._compound_children.extend(
				[
					{'role': 'button', 'name': 'Play/Pause', 'valuemin': None, 'valuemax': None, 'valuenow': None},
					{'role': 'slider', 'name': 'Progress', 'valuemin': 0, 'valuemax': 100, 'valuenow': None},
					{'role': 'button', 'name': 'Mute', 'valuemin': None, 'valuemax': None, 'valuenow': None},
					{'role': 'slider', 'name': 'Volume', 'valuemin': 0, 'valuemax': 100, 'valuenow': None},
				]
			)
			simplified.is_compound_component = True

		elif element_type == 'video':
			node._compound_children.extend(
				[
					{'role': 'button', 'name': 'Play/Pause', 'valuemin': None, 'valuemax': None, 'valuenow': None},
					{'role': 'slider', 'name': 'Progress', 'valuemin': 0, 'valuemax': 100, 'valuenow': None},
					{'role': 'button', 'name': 'Mute', 'valuemin': None, 'valuemax': None, 'valuenow': None},
					{'role': 'slider', 'name': 'Volume', 'valuemin': 0, 'valuemax': 100, 'valuenow': None},
					{'role': 'button', 'name': 'Fullscreen', 'valuemin': None, 'valuemax': None, 'valuenow': None},
				]
			)
			simplified.is_compound_component = True

	def _extract_select_options(self, select_node: EnhancedDOMTreeNode) -> dict[str, Any] | None:
		if not select_node.children:
			return None

		options = []
		option_values = []

		def extract_options_recursive(node: EnhancedDOMTreeNode) -> None:
			if node.tag_name.lower() == 'option':
				option_text = ''
				option_value = ''
				if node.attributes and 'value' in node.attributes:
					option_value = str(node.attributes['value']).strip()

				def get_direct_text_content(n: EnhancedDOMTreeNode) -> str:
					text = ''
					for child in n.children:
						if child.node_type == NodeType.TEXT_NODE and child.node_value:
							text += child.node_value.strip() + ' '
					return text.strip()

				option_text = get_direct_text_content(node)
				if not option_value and option_text:
					option_value = option_text

				if option_text or option_value:
					options.append({'text': option_text, 'value': option_value})
					option_values.append(option_value)
			elif node.tag_name.lower() == 'optgroup':
				for child in node.children:
					extract_options_recursive(child)
			else:
				for child in node.children:
					extract_options_recursive(child)

		for child in select_node.children:
			extract_options_recursive(child)

		if not options:
			return None

		first_options = []
		for option in options[:4]:
			display_text = option['text'] if option['text'] else option['value']
			if display_text:
				text = display_text[:30] + ('...' if len(display_text) > 30 else '')
				first_options.append(text)

		if len(options) > 4:
			first_options.append(f'... {len(options) - 4} more options...')

		format_hint = None
		if len(option_values) >= 2:
			if all(val.isdigit() for val in option_values[:5] if val):
				format_hint = 'numeric'
			elif all(len(val) == 2 and val.isupper() for val in option_values[:5] if val):
				format_hint = 'country/state codes'
			elif all('/' in val or '-' in val for val in option_values[:5] if val):
				format_hint = 'date/path format'
			elif any('@' in val for val in option_values[:5] if val):
				format_hint = 'email addresses'

		return {'count': len(options), 'first_options': first_options, 'format_hint': format_hint}
