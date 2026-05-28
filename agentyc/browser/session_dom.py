"""DOM and highlight helpers for BrowserSession."""

from agentyc.browser.session_dom_geometry import (
	_get_element_bounds,
	get_dom_element_at_coordinates,
	get_element_coordinates,
	screenshot_element,
)
from agentyc.browser.session_dom_highlight import (
	add_highlights,
	highlight_coordinate_click,
	highlight_interaction_element,
	remove_highlights,
)
from agentyc.browser.session_dom_lookup import (
	find_file_input_near_element,
	get_dom_element_by_index,
	get_element_by_index,
	get_index_by_class,
	get_index_by_id,
	get_selector_map,
	is_file_input,
	update_cached_selector_map,
)

__all__ = [
	'_get_element_bounds',
	'add_highlights',
	'find_file_input_near_element',
	'get_dom_element_at_coordinates',
	'get_dom_element_by_index',
	'get_element_by_index',
	'get_element_coordinates',
	'get_index_by_class',
	'get_index_by_id',
	'get_selector_map',
	'highlight_coordinate_click',
	'highlight_interaction_element',
	'is_file_input',
	'remove_highlights',
	'screenshot_element',
	'update_cached_selector_map',
]
