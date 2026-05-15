from __future__ import annotations

from agentyc.dom.utils import cap_text_length
from agentyc.dom.views import EnhancedDOMTreeNode, NodeType, SimplifiedNode


def serialize_tree(node: SimplifiedNode | None, include_attributes: list[str], depth: int = 0) -> str:
	if not node:
		return ''

	if hasattr(node, 'excluded_by_parent') and node.excluded_by_parent:
		formatted_text = []
		for child in node.children:
			child_text = serialize_tree(child, include_attributes, depth)
			if child_text:
				formatted_text.append(child_text)
		return '\n'.join(formatted_text)

	formatted_text = []
	depth_str = depth * '\t'
	next_depth = depth

	if node.original_node.node_type == NodeType.ELEMENT_NODE:
		if not node.should_display:
			for child in node.children:
				child_text = serialize_tree(child, include_attributes, depth)
				if child_text:
					formatted_text.append(child_text)
			return '\n'.join(formatted_text)

		if node.original_node.tag_name.lower() == 'svg':
			shadow_prefix = ''
			if node.is_shadow_host:
				has_closed_shadow = any(
					child.original_node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE
					and child.original_node.shadow_root_type
					and child.original_node.shadow_root_type.lower() == 'closed'
					for child in node.children
				)
				shadow_prefix = '|SHADOW(closed)|' if has_closed_shadow else '|SHADOW(open)|'

			line = f'{depth_str}{shadow_prefix}'
			if node.is_interactive:
				new_prefix = '*' if node.is_new else ''
				line += f'{new_prefix}[{node.original_node.backend_node_id}]'
			line += '<svg'
			attributes_html_str = build_attributes_string(node.original_node, include_attributes, '')
			if attributes_html_str:
				line += f' {attributes_html_str}'
			line += ' /> <!-- SVG content collapsed -->'
			formatted_text.append(line)
			return '\n'.join(formatted_text)

		is_any_scrollable = node.original_node.is_actually_scrollable or node.original_node.is_scrollable
		should_show_scroll = node.original_node.should_show_scroll_info
		if (
			node.is_interactive
			or is_any_scrollable
			or node.original_node.tag_name.upper() == 'IFRAME'
			or node.original_node.tag_name.upper() == 'FRAME'
		):
			next_depth += 1
			text_content = ''
			attributes_html_str = build_attributes_string(node.original_node, include_attributes, text_content)

			if node.original_node._compound_children:
				compound_info = []
				for child_info in node.original_node._compound_children:
					parts = []
					if child_info['name']:
						parts.append(f'name={child_info["name"]}')
					if child_info['role']:
						parts.append(f'role={child_info["role"]}')
					if child_info['valuemin'] is not None:
						parts.append(f'min={child_info["valuemin"]}')
					if child_info['valuemax'] is not None:
						parts.append(f'max={child_info["valuemax"]}')
					if child_info['valuenow'] is not None:
						parts.append(f'current={child_info["valuenow"]}')
					if 'options_count' in child_info and child_info['options_count'] is not None:
						parts.append(f'count={child_info["options_count"]}')
					if 'first_options' in child_info and child_info['first_options']:
						options_str = '|'.join(child_info['first_options'][:4])
						parts.append(f'options={options_str}')
					if 'format_hint' in child_info and child_info['format_hint']:
						parts.append(f'format={child_info["format_hint"]}')
					if parts:
						compound_info.append(f'({",".join(parts)})')
				if compound_info:
					compound_attr = f'compound_components={",".join(compound_info)}'
					if attributes_html_str:
						attributes_html_str += f' {compound_attr}'
					else:
						attributes_html_str = compound_attr

			shadow_prefix = ''
			if node.is_shadow_host:
				has_closed_shadow = any(
					child.original_node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE
					and child.original_node.shadow_root_type
					and child.original_node.shadow_root_type.lower() == 'closed'
					for child in node.children
				)
				shadow_prefix = '|SHADOW(closed)|' if has_closed_shadow else '|SHADOW(open)|'

			if should_show_scroll and not node.is_interactive:
				line = f'{depth_str}{shadow_prefix}|scroll element|<{node.original_node.tag_name}'
			elif node.is_interactive:
				new_prefix = '*' if node.is_new else ''
				scroll_prefix = '|scroll element[' if should_show_scroll else '['
				line = f'{depth_str}{shadow_prefix}{new_prefix}{scroll_prefix}{node.original_node.backend_node_id}]<{node.original_node.tag_name}'
			elif node.original_node.tag_name.upper() == 'IFRAME':
				line = f'{depth_str}{shadow_prefix}|IFRAME|<{node.original_node.tag_name}'
			elif node.original_node.tag_name.upper() == 'FRAME':
				line = f'{depth_str}{shadow_prefix}|FRAME|<{node.original_node.tag_name}'
			else:
				line = f'{depth_str}{shadow_prefix}<{node.original_node.tag_name}'

			if attributes_html_str:
				line += f' {attributes_html_str}'
			line += ' />'

			if should_show_scroll:
				scroll_info_text = node.original_node.get_scroll_info_text()
				if scroll_info_text:
					line += f' ({scroll_info_text})'

			formatted_text.append(line)

	elif node.original_node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE:
		if node.original_node.shadow_root_type and node.original_node.shadow_root_type.lower() == 'closed':
			formatted_text.append(f'{depth_str}Closed Shadow')
		else:
			formatted_text.append(f'{depth_str}Open Shadow')
		next_depth += 1
		for child in node.children:
			child_text = serialize_tree(child, include_attributes, next_depth)
			if child_text:
				formatted_text.append(child_text)
		if node.children:
			formatted_text.append(f'{depth_str}Shadow End')

	elif node.original_node.node_type == NodeType.TEXT_NODE:
		is_visible = node.original_node.snapshot_node and node.original_node.is_visible
		if (
			is_visible
			and node.original_node.node_value
			and node.original_node.node_value.strip()
			and len(node.original_node.node_value.strip()) > 1
		):
			clean_text = node.original_node.node_value.strip()
			formatted_text.append(f'{depth_str}{clean_text}')

	if node.original_node.node_type != NodeType.DOCUMENT_FRAGMENT_NODE:
		for child in node.children:
			child_text = serialize_tree(child, include_attributes, next_depth)
			if child_text:
				formatted_text.append(child_text)

		if (
			node.original_node.node_type == NodeType.ELEMENT_NODE
			and node.original_node.tag_name
			and node.original_node.tag_name.upper() in ('IFRAME', 'FRAME')
		):
			if node.original_node.hidden_elements_info:
				hidden = node.original_node.hidden_elements_info
				hint_lines = [f'{depth_str}... ({len(hidden)} more elements below - scroll to reveal):']
				for elem in hidden:
					hint_lines.append(f'{depth_str}    <{elem["tag"]}> "{elem["text"]}" ~{elem["pages"]} pages down')
				formatted_text.extend(hint_lines)
			elif node.original_node.has_hidden_content:
				formatted_text.append(f'{depth_str}... (more content below viewport - scroll to reveal)')

	return '\n'.join(formatted_text)


def build_attributes_string(node: EnhancedDOMTreeNode, include_attributes: list[str], text: str) -> str:
	attributes_to_include = {}

	if node.attributes:
		attributes_to_include.update(
			{
				key: str(value).strip()
				for key, value in node.attributes.items()
				if key in include_attributes and str(value).strip() != ''
			}
		)

	if node.tag_name and node.tag_name.lower() == 'input' and node.attributes:
		input_type = node.attributes.get('type', '').lower()
		if input_type in ['date', 'time', 'datetime-local', 'month', 'week']:
			format_map = {
				'date': 'YYYY-MM-DD',
				'time': 'HH:MM',
				'datetime-local': 'YYYY-MM-DDTHH:MM',
				'month': 'YYYY-MM',
				'week': 'YYYY-W##',
			}
			attributes_to_include['format'] = format_map[input_type]

		if 'placeholder' in include_attributes and 'placeholder' not in attributes_to_include:
			if input_type == 'date':
				attributes_to_include['placeholder'] = 'YYYY-MM-DD'
			elif input_type == 'time':
				attributes_to_include['placeholder'] = 'HH:MM'
			elif input_type == 'datetime-local':
				attributes_to_include['placeholder'] = 'YYYY-MM-DDTHH:MM'
			elif input_type == 'month':
				attributes_to_include['placeholder'] = 'YYYY-MM'
			elif input_type == 'week':
				attributes_to_include['placeholder'] = 'YYYY-W##'
			elif input_type == 'tel' and 'pattern' not in attributes_to_include:
				attributes_to_include['placeholder'] = '123-456-7890'
			elif input_type in {'text', ''}:
				class_attr = node.attributes.get('class', '').lower()
				if 'uib-datepicker-popup' in node.attributes:
					date_format = node.attributes.get('uib-datepicker-popup', '')
					if date_format:
						attributes_to_include['expected_format'] = date_format
						attributes_to_include['format'] = date_format
				elif any(indicator in class_attr for indicator in ['datepicker', 'datetimepicker', 'daterangepicker']):
					date_format = node.attributes.get('data-date-format', '')
					if date_format:
						attributes_to_include['placeholder'] = date_format
						attributes_to_include['format'] = date_format
					else:
						attributes_to_include['placeholder'] = 'mm/dd/yyyy'
						attributes_to_include['format'] = 'mm/dd/yyyy'
				elif any(attr in node.attributes for attr in ['data-datepicker']):
					date_format = node.attributes.get('data-date-format', '')
					if date_format:
						attributes_to_include['placeholder'] = date_format
						attributes_to_include['format'] = date_format
					else:
						attributes_to_include['placeholder'] = 'mm/dd/yyyy'
						attributes_to_include['format'] = 'mm/dd/yyyy'

	is_password_field = (
		node.tag_name
		and node.tag_name.lower() == 'input'
		and node.attributes
		and node.attributes.get('type', '').lower() == 'password'
	)

	if node.ax_node and node.ax_node.properties:
		value_properties = {'value', 'valuetext'}
		for prop in node.ax_node.properties:
			try:
				if prop.name in include_attributes and prop.value is not None:
					if is_password_field and prop.name in value_properties:
						continue
					if isinstance(prop.value, bool):
						attributes_to_include[prop.name] = str(prop.value).lower()
					else:
						prop_value_str = str(prop.value).strip()
						if prop_value_str:
							attributes_to_include[prop.name] = prop_value_str
			except (AttributeError, ValueError):
				continue

	if node.tag_name and node.tag_name.lower() in ['input', 'textarea', 'select']:
		if is_password_field:
			attributes_to_include.pop('value', None)
		elif node.ax_node and node.ax_node.properties:
			for prop in node.ax_node.properties:
				if prop.name == 'valuetext' and prop.value:
					value_str = str(prop.value).strip()
					if value_str:
						attributes_to_include['value'] = value_str
						break
				elif prop.name == 'value' and prop.value:
					value_str = str(prop.value).strip()
					if value_str:
						attributes_to_include['value'] = value_str
						break

	if not attributes_to_include:
		return ''

	ordered_keys = [key for key in include_attributes if key in attributes_to_include]
	if len(ordered_keys) > 1:
		keys_to_remove = set()
		seen_values = {}
		protected_attrs = {'format', 'expected_format', 'placeholder', 'value', 'aria-label', 'title'}
		for key in ordered_keys:
			value = attributes_to_include[key]
			if len(value) > 5:
				if value in seen_values and key not in protected_attrs:
					keys_to_remove.add(key)
				else:
					seen_values[value] = key
		for key in keys_to_remove:
			del attributes_to_include[key]

	role = node.ax_node.role if node.ax_node else None
	if role and node.node_name == role:
		attributes_to_include.pop('role', None)

	if 'type' in attributes_to_include and attributes_to_include['type'].lower() == node.node_name.lower():
		del attributes_to_include['type']

	if 'invalid' in attributes_to_include and attributes_to_include['invalid'].lower() == 'false':
		del attributes_to_include['invalid']

	boolean_attrs = {'required'}
	for attr in boolean_attrs:
		if attr in attributes_to_include and attributes_to_include[attr].lower() in {'false', '0', 'no'}:
			del attributes_to_include[attr]

	if 'expanded' in attributes_to_include and 'aria-expanded' in attributes_to_include:
		del attributes_to_include['aria-expanded']

	attrs_to_remove_if_text_matches = ['aria-label', 'placeholder', 'title']
	for attr in attrs_to_remove_if_text_matches:
		if attributes_to_include.get(attr) and attributes_to_include.get(attr, '').strip().lower() == text.strip().lower():
			del attributes_to_include[attr]

	if attributes_to_include:
		formatted_attrs = []
		for key, value in attributes_to_include.items():
			capped_value = cap_text_length(value, 100)
			if not capped_value:
				formatted_attrs.append(f"{key}=''")
			else:
				formatted_attrs.append(f'{key}={capped_value}')
		return ' '.join(formatted_attrs)

	return ''
