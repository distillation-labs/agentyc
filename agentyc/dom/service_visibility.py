from __future__ import annotations

from typing import Any

from cdp_use.cdp.target import TargetID

from agentyc.dom.views import EnhancedDOMTreeNode, NodeType


class DomServiceVisibilityMixin:
	async def _get_viewport_ratio(self: Any, target_id: TargetID) -> float:
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=target_id, focus=False)
		try:
			metrics = await cdp_session.cdp_client.send.Page.getLayoutMetrics(session_id=cdp_session.session_id)
			visual_viewport = metrics.get('visualViewport', {})
			css_visual_viewport = metrics.get('cssVisualViewport', {})
			css_layout_viewport = metrics.get('cssLayoutViewport', {})
			width = css_visual_viewport.get('clientWidth', css_layout_viewport.get('clientWidth', 1920.0))
			device_width = visual_viewport.get('clientWidth', width)
			css_width = css_visual_viewport.get('clientWidth', width)
			device_pixel_ratio = device_width / css_width if css_width > 0 else 1.0
			return float(device_pixel_ratio)
		except Exception as e:
			self.logger.debug(f'Viewport size detection failed: {e}')
			return 1.0

	@classmethod
	def is_element_visible_according_to_all_parents(
		cls, node: EnhancedDOMTreeNode, html_frames: list[EnhancedDOMTreeNode], viewport_threshold: int | None = 1000
	) -> bool:
		if not node.snapshot_node:
			return False

		computed_styles = node.snapshot_node.computed_styles or {}
		display = computed_styles.get('display', '').lower()
		visibility = computed_styles.get('visibility', '').lower()
		opacity = computed_styles.get('opacity', '1')

		if display == 'none' or visibility == 'hidden':
			return False

		try:
			if float(opacity) <= 0:
				return False
		except (ValueError, TypeError):
			pass

		current_bounds = node.snapshot_node.bounds
		if not current_bounds:
			return False

		if viewport_threshold is None:
			return True

		for frame in reversed(html_frames):
			if (
				frame.node_type == NodeType.ELEMENT_NODE
				and (frame.node_name.upper() == 'IFRAME' or frame.node_name.upper() == 'FRAME')
				and frame.snapshot_node
				and frame.snapshot_node.bounds
			):
				iframe_bounds = frame.snapshot_node.bounds
				current_bounds.x += iframe_bounds.x
				current_bounds.y += iframe_bounds.y

			if (
				frame.node_type == NodeType.ELEMENT_NODE
				and frame.node_name == 'HTML'
				and frame.snapshot_node
				and frame.snapshot_node.scrollRects
				and frame.snapshot_node.clientRects
			):
				viewport_left = 0
				viewport_top = 0
				viewport_right = frame.snapshot_node.clientRects.width
				viewport_bottom = frame.snapshot_node.clientRects.height
				adjusted_x = current_bounds.x - frame.snapshot_node.scrollRects.x
				adjusted_y = current_bounds.y - frame.snapshot_node.scrollRects.y

				frame_intersects = (
					adjusted_x < viewport_right
					and adjusted_x + current_bounds.width > viewport_left
					and adjusted_y < viewport_bottom + viewport_threshold
					and adjusted_y + current_bounds.height > viewport_top - viewport_threshold
				)
				if not frame_intersects:
					return False
				current_bounds.x -= frame.snapshot_node.scrollRects.x
				current_bounds.y -= frame.snapshot_node.scrollRects.y

		return True
