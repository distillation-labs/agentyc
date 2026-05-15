from __future__ import annotations

from agentyc.dom.views import EnhancedDOMTreeNode


class DomServicePaginationMixin:
	@staticmethod
	def detect_pagination_buttons(selector_map: dict[int, EnhancedDOMTreeNode]) -> list[dict[str, str | int | bool]]:
		pagination_buttons: list[dict[str, str | int | bool]] = []
		next_patterns = ['next', '>', '»', '→', 'siguiente', 'suivant', 'weiter', 'volgende']
		prev_patterns = ['prev', 'previous', '<', '«', '←', 'anterior', 'précédent', 'zurück', 'vorige']
		first_patterns = ['first', '⇤', 'primera', 'première', 'erste', 'eerste']
		last_patterns = ['last', '⇥', 'última', 'dernier', 'letzte', 'laatste']

		for index, node in selector_map.items():
			if not node.snapshot_node or not node.snapshot_node.is_clickable:
				continue

			text = node.get_all_children_text().lower().strip()
			aria_label = node.attributes.get('aria-label', '').lower()
			title = node.attributes.get('title', '').lower()
			class_name = node.attributes.get('class', '').lower()
			role = node.attributes.get('role', '').lower()
			all_text = f'{text} {aria_label} {title} {class_name}'.strip()
			is_disabled = (
				node.attributes.get('disabled') == 'true'
				or node.attributes.get('aria-disabled') == 'true'
				or 'disabled' in class_name
			)

			button_type: str | None = None
			if any(pattern in all_text for pattern in first_patterns):
				button_type = 'first'
			elif any(pattern in all_text for pattern in last_patterns):
				button_type = 'last'
			elif any(pattern in all_text for pattern in next_patterns):
				button_type = 'next'
			elif any(pattern in all_text for pattern in prev_patterns):
				button_type = 'prev'
			elif text.isdigit() and len(text) <= 2 and role in ['button', 'link', '']:
				button_type = 'page_number'

			if button_type:
				pagination_buttons.append(
					{
						'button_type': button_type,
						'backend_node_id': index,
						'text': node.get_all_children_text().strip() or aria_label or title,
						'selector': node.xpath,
						'is_disabled': is_disabled,
					}
				)

		return pagination_buttons
