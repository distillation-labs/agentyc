import logging
from typing import TYPE_CHECKING

from cdp_use.cdp.target import TargetID

from agentyc.dom.serializer.serializer import DOMTreeSerializer
from agentyc.dom.service_ax import DomServiceAXMixin
from agentyc.dom.service_build import DomServiceBuildMixin
from agentyc.dom.service_fetch import DomServiceFetchMixin
from agentyc.dom.service_pagination import DomServicePaginationMixin
from agentyc.dom.service_visibility import DomServiceVisibilityMixin
from agentyc.dom.views import DOMRect, EnhancedDOMTreeNode, SerializedDOMState
from agentyc.observability import observe_debug

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


class DomService(
	DomServiceAXMixin,
	DomServiceVisibilityMixin,
	DomServiceFetchMixin,
	DomServiceBuildMixin,
	DomServicePaginationMixin,
):
	_JS_CLICK_LISTENER_CACHE_TTL_S = 0.5
	logger: logging.Logger

	def __init__(
		self,
		browser_session: 'BrowserSession',
		logger: logging.Logger | None = None,
		cross_origin_iframes: bool = False,
		paint_order_filtering: bool = True,
		max_iframes: int = 100,
		max_iframe_depth: int = 5,
		viewport_threshold: int | None = 1000,
	):
		self.browser_session = browser_session
		self.logger = logger or browser_session.logger
		self.cross_origin_iframes = cross_origin_iframes
		self.paint_order_filtering = paint_order_filtering
		self.max_iframes = max_iframes
		self.max_iframe_depth = max_iframe_depth
		self.viewport_threshold = viewport_threshold
		self._target_has_frames: dict[TargetID, bool] = {}
		self._target_js_click_listener_backend_ids: dict[TargetID, tuple[float, set[int]]] = {}

	async def __aenter__(self):
		return self

	async def __aexit__(self, exc_type, exc_value, traceback):
		pass

	def clear_cache(self) -> None:
		self._target_has_frames.clear()
		self._target_js_click_listener_backend_ids.clear()

	@observe_debug(ignore_input=True, ignore_output=True, name='get_dom_tree')
	async def get_dom_tree(
		self,
		target_id: TargetID,
		all_frames: dict | None = None,
		initial_html_frames: list[EnhancedDOMTreeNode] | None = None,
		initial_total_frame_offset: DOMRect | None = None,
		iframe_depth: int = 0,
	) -> tuple[EnhancedDOMTreeNode, dict[str, float]]:
		return await DomServiceBuildMixin.get_dom_tree(
			self,
			target_id=target_id,
			all_frames=all_frames,
			initial_html_frames=initial_html_frames,
			initial_total_frame_offset=initial_total_frame_offset,
			iframe_depth=iframe_depth,
		)

	@observe_debug(ignore_input=True, ignore_output=True, name='get_serialized_dom_tree')
	async def get_serialized_dom_tree(
		self, previous_cached_state: SerializedDOMState | None = None
	) -> tuple[SerializedDOMState, EnhancedDOMTreeNode, dict[str, float]]:
		import time

		timing_info: dict[str, float] = {}
		start_total = time.time()
		assert self.browser_session.agent_focus_target_id is not None
		session_id = self.browser_session.id
		enhanced_dom_tree, dom_tree_timing = await self.get_dom_tree(
			target_id=self.browser_session.agent_focus_target_id,
			all_frames=None,
		)
		timing_info.update(dom_tree_timing)
		start_serialize = time.time()
		serialized_dom_state, serializer_timing = DOMTreeSerializer(
			enhanced_dom_tree,
			previous_cached_state,
			paint_order_filtering=self.paint_order_filtering,
			session_id=session_id,
		).serialize_accessible_elements()
		total_serialization_ms = (time.time() - start_serialize) * 1000
		for key, value in serializer_timing.items():
			timing_info[f'{key}_ms'] = value * 1000
		tracked_serialization_ms = sum(value * 1000 for value in serializer_timing.values())
		serialization_overhead_ms = total_serialization_ms - tracked_serialization_ms
		if serialization_overhead_ms > 0.1:
			timing_info['serialization_overhead_ms'] = serialization_overhead_ms
		total_get_serialized_dom_tree_ms = (time.time() - start_total) * 1000
		timing_info['get_serialized_dom_tree_total_ms'] = total_get_serialized_dom_tree_ms
		tracked_major_operations_ms = timing_info.get('get_dom_tree_total_ms', 0) + total_serialization_ms
		get_serialized_overhead_ms = total_get_serialized_dom_tree_ms - tracked_major_operations_ms
		if get_serialized_overhead_ms > 0.1:
			timing_info['get_serialized_dom_tree_overhead_ms'] = get_serialized_overhead_ms
		return serialized_dom_state, enhanced_dom_tree, timing_info
