from __future__ import annotations

import time
from typing import Any

from cdp_use.cdp.accessibility.types import AXNode
from cdp_use.cdp.dom.types import Node
from cdp_use.cdp.target import TargetID

from agentyc.dom.enhanced_snapshot import build_snapshot_lookup
from agentyc.dom.serializer.clickable_elements import ClickableElementDetector
from agentyc.dom.views import DOMRect, EnhancedDOMTreeNode, NodeType


class DomServiceBuildMixin:
	def _count_hidden_elements_in_iframes(self: Any, node: EnhancedDOMTreeNode) -> None:
		def is_hidden_by_threshold(element: EnhancedDOMTreeNode) -> bool:
			if element.is_visible or not element.snapshot_node or not element.snapshot_node.bounds:
				return False
			computed_styles = element.snapshot_node.computed_styles or {}
			display = computed_styles.get('display', '').lower()
			visibility = computed_styles.get('visibility', '').lower()
			opacity = computed_styles.get('opacity', '1')
			css_hidden = display == 'none' or visibility == 'hidden'
			try:
				css_hidden = css_hidden or float(opacity) <= 0
			except (ValueError, TypeError):
				pass
			return not css_hidden

		def collect_hidden_elements(subtree_root: EnhancedDOMTreeNode, viewport_height: float) -> list[dict[str, Any]]:
			hidden: list[dict[str, Any]] = []
			if subtree_root.node_type == NodeType.ELEMENT_NODE:
				is_interactive = ClickableElementDetector.is_interactive(subtree_root)
				if is_interactive and is_hidden_by_threshold(subtree_root):
					text = ''
					if subtree_root.ax_node and subtree_root.ax_node.name:
						text = subtree_root.ax_node.name[:40]
					elif subtree_root.attributes:
						text = (
							subtree_root.attributes.get('placeholder', '')
							or subtree_root.attributes.get('title', '')
							or subtree_root.attributes.get('aria-label', '')
						)[:40]
					y_pos = 0.0
					if subtree_root.snapshot_node and subtree_root.snapshot_node.bounds:
						y_pos = subtree_root.snapshot_node.bounds.y
					pages_down = round(y_pos / viewport_height, 1) if viewport_height > 0 else 0
					hidden.append({'tag': subtree_root.tag_name or '?', 'text': text or '(no label)', 'pages': pages_down})

			for child in subtree_root.children_nodes or []:
				hidden.extend(collect_hidden_elements(child, viewport_height))
			for shadow_root in subtree_root.shadow_roots or []:
				hidden.extend(collect_hidden_elements(shadow_root, viewport_height))
			return hidden

		def has_any_hidden_content(subtree_root: EnhancedDOMTreeNode) -> bool:
			if is_hidden_by_threshold(subtree_root):
				return True
			for child in subtree_root.children_nodes or []:
				if has_any_hidden_content(child):
					return True
			for shadow_root in subtree_root.shadow_roots or []:
				if has_any_hidden_content(shadow_root):
					return True
			return False

		def process_node(current_node: EnhancedDOMTreeNode) -> None:
			if (
				current_node.node_type == NodeType.ELEMENT_NODE
				and current_node.tag_name
				and current_node.tag_name.upper() in ('IFRAME', 'FRAME')
				and current_node.content_document
			):
				viewport_height = 0.0
				if current_node.snapshot_node and current_node.snapshot_node.clientRects:
					viewport_height = current_node.snapshot_node.clientRects.height
				hidden = collect_hidden_elements(current_node.content_document, viewport_height)
				hidden.sort(key=lambda x: x['pages'])
				current_node.hidden_elements_info = hidden[:10]
				if not hidden and has_any_hidden_content(current_node.content_document):
					current_node.has_hidden_content = True

			for child in current_node.children_nodes or []:
				process_node(child)
			if current_node.content_document:
				process_node(current_node.content_document)
			for shadow_root in current_node.shadow_roots or []:
				process_node(shadow_root)

		process_node(node)

	async def get_dom_tree(
		self: Any,
		target_id: TargetID,
		all_frames: dict | None = None,
		initial_html_frames: list[EnhancedDOMTreeNode] | None = None,
		initial_total_frame_offset: DOMRect | None = None,
		iframe_depth: int = 0,
	) -> tuple[EnhancedDOMTreeNode, dict[str, float]]:
		timing_info: dict[str, float] = {}
		timing_start_total = time.time()

		start_get_trees = time.time()
		trees = await self._get_all_trees(target_id)
		get_trees_ms = (time.time() - start_get_trees) * 1000
		timing_info.update(trees.cdp_timing)
		timing_info['get_all_trees_total_ms'] = get_trees_ms

		dom_tree = trees.dom_tree
		ax_tree = trees.ax_tree
		snapshot = trees.snapshot
		device_pixel_ratio = trees.device_pixel_ratio
		js_click_listener_backend_ids = trees.js_click_listener_backend_ids or set()
		try:
			session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
			target_session_id = session.session_id
		except ValueError:
			target_session_id = None

		start_ax = time.time()
		ax_tree_lookup: dict[int, AXNode] = {
			ax_node['backendDOMNodeId']: ax_node for ax_node in ax_tree['nodes'] if 'backendDOMNodeId' in ax_node
		}
		timing_info['build_ax_lookup_ms'] = (time.time() - start_ax) * 1000

		enhanced_dom_tree_node_lookup: dict[int, EnhancedDOMTreeNode] = {}
		start_snapshot = time.time()
		snapshot_lookup = build_snapshot_lookup(snapshot, device_pixel_ratio)
		timing_info['build_snapshot_lookup_ms'] = (time.time() - start_snapshot) * 1000

		async def _construct_enhanced_node(
			node: Node,
			html_frames: list[EnhancedDOMTreeNode] | None,
			total_frame_offset: DOMRect | None,
			all_frames: dict | None,
		) -> EnhancedDOMTreeNode:
			if html_frames is None:
				html_frames = []

			if total_frame_offset is None:
				total_frame_offset = DOMRect(x=0.0, y=0.0, width=0.0, height=0.0)
			else:
				total_frame_offset = DOMRect(
					total_frame_offset.x, total_frame_offset.y, total_frame_offset.width, total_frame_offset.height
				)

			if node['nodeId'] in enhanced_dom_tree_node_lookup:
				return enhanced_dom_tree_node_lookup[node['nodeId']]

			ax_node = ax_tree_lookup.get(node['backendNodeId'])
			enhanced_ax_node = self._build_enhanced_ax_node(ax_node) if ax_node else None

			attributes: dict[str, str] | None = None
			if 'attributes' in node and node['attributes']:
				attributes = {}
				for i in range(0, len(node['attributes']), 2):
					attributes[node['attributes'][i]] = node['attributes'][i + 1]

			shadow_root_type = None
			if 'shadowRootType' in node and node['shadowRootType']:
				try:
					shadow_root_type = node['shadowRootType']
				except ValueError:
					pass

			snapshot_data = snapshot_lookup.get(node['backendNodeId'], None)
			if not snapshot_data and node['nodeName'].upper() in ['INPUT', 'BUTTON', 'SELECT', 'TEXTAREA', 'A']:
				parent_info = ''
				if 'parentId' in node and node['parentId'] in enhanced_dom_tree_node_lookup:
					parent = enhanced_dom_tree_node_lookup[node['parentId']]
					if parent.shadow_root_type:
						parent_info = f'parent={parent.tag_name}(shadow={parent.shadow_root_type})'
				attr_str = ''
				if 'attributes' in node and node['attributes']:
					attrs_dict = {node['attributes'][i]: node['attributes'][i + 1] for i in range(0, len(node['attributes']), 2)}
					attr_str = f'name={attrs_dict.get("name", "N/A")} id={attrs_dict.get("id", "N/A")}'
				self.logger.debug(
					f'🔍 NO SNAPSHOT DATA for <{node["nodeName"]}> backendNodeId={node["backendNodeId"]} {attr_str} {parent_info} '
					f'(snapshot_lookup has {len(snapshot_lookup)} entries)'
				)

			absolute_position = None
			if snapshot_data and snapshot_data.bounds:
				absolute_position = DOMRect(
					x=snapshot_data.bounds.x + total_frame_offset.x,
					y=snapshot_data.bounds.y + total_frame_offset.y,
					width=snapshot_data.bounds.width,
					height=snapshot_data.bounds.height,
				)

			dom_tree_node = EnhancedDOMTreeNode(
				node_id=node['nodeId'],
				backend_node_id=node['backendNodeId'],
				node_type=NodeType(node['nodeType']),
				node_name=node['nodeName'],
				node_value=node['nodeValue'],
				attributes=attributes or {},
				is_scrollable=node.get('isScrollable', None),
				frame_id=node.get('frameId', None),
				session_id=target_session_id,
				target_id=target_id,
				content_document=None,
				shadow_root_type=shadow_root_type,
				shadow_roots=None,
				parent_node=None,
				children_nodes=None,
				ax_node=enhanced_ax_node,
				snapshot_node=snapshot_data,
				is_visible=None,
				has_js_click_listener=node['backendNodeId'] in js_click_listener_backend_ids,
				absolute_position=absolute_position,
			)

			enhanced_dom_tree_node_lookup[node['nodeId']] = dom_tree_node
			if 'parentId' in node and node['parentId']:
				dom_tree_node.parent_node = enhanced_dom_tree_node_lookup[node['parentId']]

			updated_html_frames = html_frames.copy()
			if node['nodeType'] == NodeType.ELEMENT_NODE.value and node['nodeName'] == 'HTML' and node.get('frameId') is not None:
				updated_html_frames.append(dom_tree_node)
				if snapshot_data and snapshot_data.scrollRects:
					total_frame_offset.x -= snapshot_data.scrollRects.x
					total_frame_offset.y -= snapshot_data.scrollRects.y
					self.logger.debug(
						f'🔍 DEBUG: HTML frame scroll - scrollY={snapshot_data.scrollRects.y}, scrollX={snapshot_data.scrollRects.x}, '
						f'frameId={node.get("frameId")}, nodeId={node["nodeId"]}'
					)

			if (
				(node['nodeName'].upper() == 'IFRAME' or node['nodeName'].upper() == 'FRAME')
				and snapshot_data
				and snapshot_data.bounds
			):
				updated_html_frames.append(dom_tree_node)
				total_frame_offset.x += snapshot_data.bounds.x
				total_frame_offset.y += snapshot_data.bounds.y

			if 'contentDocument' in node and node['contentDocument']:
				dom_tree_node.content_document = await _construct_enhanced_node(
					node['contentDocument'], updated_html_frames, total_frame_offset, all_frames
				)
				dom_tree_node.content_document.parent_node = dom_tree_node

			if 'shadowRoots' in node and node['shadowRoots']:
				dom_tree_node.shadow_roots = []
				for shadow_root in node['shadowRoots']:
					shadow_root_node = await _construct_enhanced_node(
						shadow_root, updated_html_frames, total_frame_offset, all_frames
					)
					shadow_root_node.parent_node = dom_tree_node
					dom_tree_node.shadow_roots.append(shadow_root_node)

			if 'children' in node and node['children']:
				dom_tree_node.children_nodes = []
				shadow_root_node_ids = set()
				if 'shadowRoots' in node and node['shadowRoots']:
					for shadow_root in node['shadowRoots']:
						shadow_root_node_ids.add(shadow_root['nodeId'])

				for child in node['children']:
					if child['nodeId'] in shadow_root_node_ids:
						continue
					dom_tree_node.children_nodes.append(
						await _construct_enhanced_node(child, updated_html_frames, total_frame_offset, all_frames)
					)

			dom_tree_node.is_visible = self.is_element_visible_according_to_all_parents(
				dom_tree_node, updated_html_frames, self.viewport_threshold
			)

			if dom_tree_node.tag_name and dom_tree_node.tag_name.upper() in ['INPUT', 'SELECT', 'TEXTAREA', 'LABEL']:
				attrs = dom_tree_node.attributes or {}
				elem_id = attrs.get('id', '')
				elem_name = attrs.get('name', '')
				if any(token in elem_id.lower() or token in elem_name.lower() for token in ['city', 'state', 'zip']):
					self.logger.debug(
						f"🔍 DEBUG: Form element {dom_tree_node.tag_name} id='{elem_id}' name='{elem_name}' - visible={dom_tree_node.is_visible}, "
						f'bounds={dom_tree_node.snapshot_node.bounds if dom_tree_node.snapshot_node else "NO_SNAPSHOT"}'
					)

			if self.cross_origin_iframes and node['nodeName'].upper() == 'IFRAME' and node.get('contentDocument', None) is None:
				if iframe_depth >= self.max_iframe_depth:
					self.logger.debug(
						f'Skipping iframe at depth {iframe_depth} to prevent infinite recursion (max depth: {self.max_iframe_depth})'
					)
				else:
					should_process_iframe = False
					if dom_tree_node.is_visible:
						if dom_tree_node.snapshot_node and dom_tree_node.snapshot_node.bounds:
							bounds = dom_tree_node.snapshot_node.bounds
							width = bounds.width
							height = bounds.height
							if width >= 50 and height >= 50:
								should_process_iframe = True
								self.logger.debug(f'Processing cross-origin iframe: visible=True, width={width}, height={height}')
							else:
								self.logger.debug(
									f'Skipping small cross-origin iframe: width={width}, height={height} (needs >= 50px)'
								)
						else:
							self.logger.debug('Skipping cross-origin iframe: no bounds available')
					else:
						self.logger.debug('Skipping invisible cross-origin iframe')

					if should_process_iframe:
						if all_frames is None:
							all_frames, _ = await self.browser_session.get_all_frames()
						assert all_frames is not None
						frame_id = node.get('frameId', None)
						if (not frame_id or frame_id not in all_frames) and attributes:
							src = attributes.get('src', '')
							if src:
								src_base = src.split('?')[0].rstrip('/')
								for fid, finfo in all_frames.items():
									frame_url = finfo.get('url', '').split('?')[0].rstrip('/')
									if frame_url and frame_url == src_base:
										frame_id = fid
										self.logger.debug(f'Matched cross-origin iframe by src URL: {src!r} -> frameId={fid}')
										break

						iframe_document_target = None
						if frame_id:
							frame_info = all_frames.get(frame_id)
							if frame_info and frame_info.get('frameTargetId'):
								iframe_target_id = frame_info['frameTargetId']
								iframe_target = self.browser_session.session_manager.get_target(iframe_target_id)
								iframe_document_target = {
									'targetId': iframe_target_id,
									'url': iframe_target.url if iframe_target else frame_info.get('url', ''),
									'title': iframe_target.title if iframe_target else frame_info.get('title', ''),
									'type': iframe_target.target_type if iframe_target else 'iframe',
								}

						if iframe_document_target:
							self.logger.debug(
								f'Getting content document for iframe {node.get("frameId", None)} at depth {iframe_depth + 1}'
							)
							try:
								content_document, _ = await self.get_dom_tree(
									target_id=iframe_document_target['targetId'],
									all_frames=all_frames,
									initial_total_frame_offset=total_frame_offset,
									iframe_depth=iframe_depth + 1,
								)
								dom_tree_node.content_document = content_document
								content_document.parent_node = dom_tree_node
							except Exception as e:
								self.logger.debug(f'Failed to get DOM tree for cross-origin iframe {frame_id}: {e}')

			return dom_tree_node

		start_construct = time.time()
		enhanced_dom_tree_node = await _construct_enhanced_node(
			dom_tree['root'], initial_html_frames, initial_total_frame_offset, all_frames
		)
		timing_info['construct_enhanced_tree_ms'] = (time.time() - start_construct) * 1000
		self._count_hidden_elements_in_iframes(enhanced_dom_tree_node)
		total_get_dom_tree_ms = (time.time() - timing_start_total) * 1000
		timing_info['get_dom_tree_total_ms'] = total_get_dom_tree_ms
		tracked_sub_operations_ms = (
			timing_info.get('get_all_trees_total_ms', 0)
			+ timing_info.get('build_ax_lookup_ms', 0)
			+ timing_info.get('build_snapshot_lookup_ms', 0)
			+ timing_info.get('construct_enhanced_tree_ms', 0)
		)
		get_dom_tree_overhead_ms = total_get_dom_tree_ms - tracked_sub_operations_ms
		if get_dom_tree_overhead_ms > 0.1:
			timing_info['get_dom_tree_overhead_ms'] = get_dom_tree_overhead_ms

		return enhanced_dom_tree_node, timing_info
