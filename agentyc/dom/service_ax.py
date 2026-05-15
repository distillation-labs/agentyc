from __future__ import annotations

import asyncio
from typing import Any

from cdp_use.cdp.accessibility.commands import GetFullAXTreeReturns
from cdp_use.cdp.accessibility.types import AXNode
from cdp_use.cdp.target import TargetID

from agentyc.dom.views import EnhancedAXNode, EnhancedAXProperty


class DomServiceAXMixin:
	def _build_enhanced_ax_node(self: Any, ax_node: AXNode) -> EnhancedAXNode:
		properties: list[EnhancedAXProperty] | None = None
		if 'properties' in ax_node and ax_node['properties']:
			properties = []
			for property in ax_node['properties']:
				try:
					properties.append(
						EnhancedAXProperty(
							name=property['name'],
							value=property.get('value', {}).get('value', None),
						)
					)
				except ValueError:
					pass

		ax_value_raw = ax_node.get('value', {})
		ax_value = ax_value_raw.get('value', None) if isinstance(ax_value_raw, dict) else None
		return EnhancedAXNode(
			ax_node_id=ax_node['nodeId'],
			ignored=ax_node['ignored'],
			role=ax_node.get('role', {}).get('value', None),
			name=ax_node.get('name', {}).get('value', None),
			description=ax_node.get('description', {}).get('value', None),
			properties=properties,
			child_ids=ax_node.get('childIds', []) if ax_node.get('childIds') else None,
			value=str(ax_value) if ax_value is not None else None,
		)

	async def _get_ax_tree_for_all_frames(self: Any, target_id: TargetID) -> GetFullAXTreeReturns:
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=target_id, focus=False)
		frame_tree = await cdp_session.cdp_client.send.Page.getFrameTree(session_id=cdp_session.session_id)

		def collect_all_frame_ids(frame_tree_node) -> list[str]:
			frame_ids = [frame_tree_node['frame']['id']]
			if 'childFrames' in frame_tree_node and frame_tree_node['childFrames']:
				for child_frame in frame_tree_node['childFrames']:
					frame_ids.extend(collect_all_frame_ids(child_frame))
			return frame_ids

		all_frame_ids = collect_all_frame_ids(frame_tree['frameTree'])
		ax_tree_requests = []
		for frame_id in all_frame_ids:
			ax_tree_requests.append(
				cdp_session.cdp_client.send.Accessibility.getFullAXTree(
					params={'frameId': frame_id}, session_id=cdp_session.session_id
				)
			)

		ax_trees = await asyncio.gather(*ax_tree_requests)
		merged_nodes: list[AXNode] = []
		for ax_tree in ax_trees:
			merged_nodes.extend(ax_tree['nodes'])
		return {'nodes': merged_nodes}

	async def _get_ax_tree_for_current_frame(self: Any, target_id: TargetID) -> GetFullAXTreeReturns:
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=target_id, focus=False)
		return await cdp_session.cdp_client.send.Accessibility.getFullAXTree(session_id=cdp_session.session_id)
