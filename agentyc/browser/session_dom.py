"""DOM and highlight helpers for BrowserSession."""

from __future__ import annotations

import asyncio
import json
import traceback
from typing import TYPE_CHECKING, Any

from agentyc.browser.session_models import CDPSession
from agentyc.dom.views import DOMRect, EnhancedDOMTreeNode, NodeType

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


async def get_dom_element_by_index(session: BrowserSession, index: int) -> EnhancedDOMTreeNode | None:
	"""Get DOM element by index from cached selector map."""
	if session._cached_selector_map and index in session._cached_selector_map:
		return session._cached_selector_map[index]
	return None


def update_cached_selector_map(session: BrowserSession, selector_map: dict[int, EnhancedDOMTreeNode]) -> None:
	"""Update the cached selector map with new DOM state."""
	session._cached_selector_map = selector_map


async def get_element_by_index(session: BrowserSession, index: int) -> EnhancedDOMTreeNode | None:
	"""Alias for get_dom_element_by_index for backwards compatibility."""
	return await get_dom_element_by_index(session, index)


async def get_dom_element_at_coordinates(session: BrowserSession, x: int, y: int) -> EnhancedDOMTreeNode | None:
	"""Get DOM element at coordinates as EnhancedDOMTreeNode."""
	page = await session.get_current_page()
	if page is None:
		raise RuntimeError('No active page found')

	session_id = await page._ensure_session()

	try:
		result = await session.cdp_client.send.DOM.getNodeForLocation(
			params={
				'x': x,
				'y': y,
				'includeUserAgentShadowDOM': False,
				'ignorePointerEventsNone': False,
			},
			session_id=session_id,
		)

		backend_node_id = result.get('backendNodeId')
		if backend_node_id is None:
			session.logger.debug(f'No element found at coordinates ({x}, {y})')
			return None

		if session._cached_selector_map:
			for node in session._cached_selector_map.values():
				if node.backend_node_id == backend_node_id:
					session.logger.debug(f'Found element at ({x}, {y}) in cached selector_map')
					return node

		try:
			describe_result = await session.cdp_client.send.DOM.describeNode(
				params={'backendNodeId': backend_node_id},
				session_id=session_id,
			)
			node_info = describe_result.get('node', {})
			node_name = node_info.get('nodeName', '')

			attrs_list = node_info.get('attributes', [])
			attributes = {attrs_list[i]: attrs_list[i + 1] for i in range(0, len(attrs_list), 2)}

			return EnhancedDOMTreeNode(
				node_id=result.get('nodeId', 0),
				backend_node_id=backend_node_id,
				node_type=NodeType(node_info.get('nodeType', NodeType.ELEMENT_NODE.value)),
				node_name=node_name,
				node_value=node_info.get('nodeValue', '') or '',
				attributes=attributes,
				is_scrollable=None,
				frame_id=result.get('frameId'),
				session_id=session_id,
				target_id=session.agent_focus_target_id or '',
				content_document=None,
				shadow_root_type=None,
				shadow_roots=None,
				parent_node=None,
				children_nodes=None,
				ax_node=None,
				snapshot_node=None,
				is_visible=None,
				absolute_position=None,
			)
		except Exception as e:
			session.logger.debug(f'DOM.describeNode failed for backend_node_id={backend_node_id}: {e}')
			return EnhancedDOMTreeNode(
				node_id=result.get('nodeId', 0),
				backend_node_id=backend_node_id,
				node_type=NodeType.ELEMENT_NODE,
				node_name='',
				node_value='',
				attributes={},
				is_scrollable=None,
				frame_id=result.get('frameId'),
				session_id=session_id,
				target_id=session.agent_focus_target_id or '',
				content_document=None,
				shadow_root_type=None,
				shadow_roots=None,
				parent_node=None,
				children_nodes=None,
				ax_node=None,
				snapshot_node=None,
				is_visible=None,
				absolute_position=None,
			)

	except Exception as e:
		session.logger.warning(f'Failed to get DOM element at coordinates ({x}, {y}): {e}')
		return None


def is_file_input(session: BrowserSession, element: Any) -> bool:
	"""Check if element is a file input."""
	if session._dom_watchdog:
		return session._dom_watchdog.is_file_input(element)
	return (
		hasattr(element, 'node_name')
		and element.node_name.upper() == 'INPUT'
		and hasattr(element, 'attributes')
		and element.attributes.get('type', '').lower() == 'file'
	)


def find_file_input_near_element(
	session: BrowserSession,
	node: EnhancedDOMTreeNode,
	max_height: int = 3,
	max_descendant_depth: int = 3,
) -> EnhancedDOMTreeNode | None:
	"""Find the closest file input to the given element."""

	def _find_in_descendants(n: EnhancedDOMTreeNode, depth: int) -> EnhancedDOMTreeNode | None:
		if depth < 0:
			return None
		if is_file_input(session, n):
			return n
		for child in n.children_nodes or []:
			result = _find_in_descendants(child, depth - 1)
			if result:
				return result
		return None

	current: EnhancedDOMTreeNode | None = node
	for _ in range(max_height + 1):
		if current is None:
			break
		if is_file_input(session, current):
			return current
		result = _find_in_descendants(current, max_descendant_depth)
		if result:
			return result
		if current.parent_node:
			for sibling in current.parent_node.children_nodes or []:
				if sibling is current:
					continue
				if is_file_input(session, sibling):
					return sibling
				result = _find_in_descendants(sibling, max_descendant_depth)
				if result:
					return result
		current = current.parent_node
	return None


async def get_selector_map(session: BrowserSession) -> dict[int, EnhancedDOMTreeNode]:
	"""Get the current selector map from cached state or DOM watchdog."""
	if session._cached_selector_map:
		return session._cached_selector_map
	if session._dom_watchdog and hasattr(session._dom_watchdog, 'selector_map'):
		return session._dom_watchdog.selector_map or {}
	return {}


async def get_index_by_id(session: BrowserSession, element_id: str) -> int | None:
	"""Find element index by its id attribute."""
	selector_map = await get_selector_map(session)
	for idx, element in selector_map.items():
		if element.attributes and element.attributes.get('id') == element_id:
			return idx
	return None


async def get_index_by_class(session: BrowserSession, class_name: str) -> int | None:
	"""Find element index by its class attribute."""
	selector_map = await get_selector_map(session)
	for idx, element in selector_map.items():
		if element.attributes:
			element_class = element.attributes.get('class', '')
			if class_name in element_class.split():
				return idx
	return None


async def remove_highlights(session: BrowserSession) -> None:
	"""Remove highlights from the page using CDP."""
	if (
		not session.browser_profile.highlight_elements
		and not session.browser_profile.dom_highlight_elements
		and not session.session_manager
	):
		return

	try:
		async with asyncio.timeout(3.0):
			cdp_session = await session.get_or_create_cdp_session()
			script = """
			(function() {
				// Remove all agentyc highlight elements
				const highlights = document.querySelectorAll('[data-agentyc-highlight]');
				console.log('Removing', highlights.length, 'agentyc highlight elements');
				highlights.forEach(el => el.remove());

				// Also remove by ID in case selector missed anything
				const highlightContainer = document.getElementById('agentyc-debug-highlights');
				if (highlightContainer) {
					console.log('Removing highlight container by ID');
					highlightContainer.remove();
				}

				// Final cleanup - remove any orphaned tooltips
				const orphanedTooltips = document.querySelectorAll('[data-agentyc-highlight="tooltip"]');
				orphanedTooltips.forEach(el => el.remove());

				return { removed: highlights.length };
			})();
			"""
			result = await cdp_session.cdp_client.send.Runtime.evaluate(
				params={'expression': script, 'returnByValue': True}, session_id=cdp_session.session_id
			)

			if result and 'result' in result and 'value' in result['result']:
				removed_count = result['result']['value'].get('removed', 0)
				session.logger.debug(f'Successfully removed {removed_count} highlight elements')
			else:
				session.logger.debug('Highlight removal completed')

	except Exception as e:
		session.logger.warning(f'Failed to remove highlights: {e}')


async def get_element_coordinates(session: BrowserSession, backend_node_id: int, cdp_session: CDPSession) -> DOMRect | None:
	"""Get element coordinates for a backend node ID using multiple methods."""
	session_id = cdp_session.session_id
	quads = []

	try:
		content_quads_result = await cdp_session.cdp_client.send.DOM.getContentQuads(
			params={'backendNodeId': backend_node_id}, session_id=session_id
		)
		if 'quads' in content_quads_result and content_quads_result['quads']:
			quads = content_quads_result['quads']
			session.logger.debug(f'Got {len(quads)} quads from DOM.getContentQuads')
		else:
			session.logger.debug(f'No quads found from DOM.getContentQuads {content_quads_result}')
	except Exception as e:
		session.logger.debug(f'DOM.getContentQuads failed: {e}')

	if not quads:
		try:
			box_model = await cdp_session.cdp_client.send.DOM.getBoxModel(
				params={'backendNodeId': backend_node_id}, session_id=session_id
			)
			if 'model' in box_model and 'content' in box_model['model']:
				content_quad = box_model['model']['content']
				if len(content_quad) >= 8:
					quads = [
						[
							content_quad[0],
							content_quad[1],
							content_quad[2],
							content_quad[3],
							content_quad[4],
							content_quad[5],
							content_quad[6],
							content_quad[7],
						]
					]
					session.logger.debug('Got quad from DOM.getBoxModel')
		except Exception as e:
			session.logger.debug(f'DOM.getBoxModel failed: {e}')

	if not quads:
		try:
			result = await cdp_session.cdp_client.send.DOM.resolveNode(
				params={'backendNodeId': backend_node_id},
				session_id=session_id,
			)
			if 'object' in result and 'objectId' in result['object']:
				object_id = result['object']['objectId']
				js_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
					params={
						'objectId': object_id,
						'functionDeclaration': """
						function() {
							const rect = this.getBoundingClientRect();
							return {
								x: rect.x,
								y: rect.y,
								width: rect.width,
								height: rect.height
							};
						}
						""",
						'returnByValue': True,
					},
					session_id=session_id,
				)
				if 'result' in js_result and 'value' in js_result['result']:
					rect_data = js_result['result']['value']
					if rect_data['width'] > 0 and rect_data['height'] > 0:
						return DOMRect(x=rect_data['x'], y=rect_data['y'], width=rect_data['width'], height=rect_data['height'])
		except Exception as e:
			session.logger.debug(f'JavaScript getBoundingClientRect failed: {e}')

	if quads:
		quad = quads[0]
		if len(quad) >= 8:
			x_coords = [quad[i] for i in range(0, 8, 2)]
			y_coords = [quad[i] for i in range(1, 8, 2)]

			min_x = min(x_coords)
			min_y = min(y_coords)
			max_x = max(x_coords)
			max_y = max(y_coords)

			width = max_x - min_x
			height = max_y - min_y

			if width > 0 and height > 0:
				return DOMRect(x=min_x, y=min_y, width=width, height=height)

	return None


async def highlight_interaction_element(session: BrowserSession, node: EnhancedDOMTreeNode) -> None:
	"""Temporarily highlight an element during interaction for user visibility."""
	if not session.browser_profile.highlight_elements:
		return

	try:
		cdp_session = await session.get_or_create_cdp_session()
		rect = await get_element_coordinates(session, node.backend_node_id, cdp_session)

		color = session.browser_profile.interaction_highlight_color
		duration_ms = int(session.browser_profile.interaction_highlight_duration * 1000)

		if not rect:
			session.logger.debug(f'No coordinates found for backend node {node.backend_node_id}')
			return

		script = f"""
		(function() {{
			const rect = {json.dumps({'x': rect.x, 'y': rect.y, 'width': rect.width, 'height': rect.height})};
			const color = {json.dumps(color)};
			const duration = {duration_ms};
			const shadowColor = color.replace('rgb(', 'rgba(').replace(')', ', 0.18)');

			// Scale corner size based on element dimensions to ensure gaps between corners
			const maxCornerSize = 20;
			const minCornerSize = 8;
			const cornerSize = Math.max(
				minCornerSize,
				Math.min(maxCornerSize, Math.min(rect.width, rect.height) * 0.35)
			);
			const borderWidth = 3;
			const startOffset = 10; // Starting offset in pixels
			const finalOffset = -3; // Final position slightly outside the element

			// Get current scroll position
			const scrollX = window.pageXOffset || document.documentElement.scrollLeft || 0;
			const scrollY = window.pageYOffset || document.documentElement.scrollTop || 0;

			// Create container for all corners
			const container = document.createElement('div');
			container.setAttribute('data-agentyc-interaction-highlight', 'true');
			container.style.cssText = `
				position: absolute;
				left: ${{rect.x + scrollX}}px;
				top: ${{rect.y + scrollY}}px;
				width: ${{rect.width}}px;
				height: ${{rect.height}}px;
				pointer-events: none;
				z-index: 2147483647;
				filter: drop-shadow(0 4px 10px ${{shadowColor}});
			`;

			const focusRing = document.createElement('div');
			focusRing.style.cssText = `
				position: absolute;
				left: 0;
				top: 0;
				width: 100%;
				height: 100%;
				border-radius: 10px;
				background: linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0));
				box-shadow: inset 0 0 0 1px rgba(255,255,255,0.25);
				opacity: 0;
				transition: opacity 0.15s ease-out;
			`;
			container.appendChild(focusRing);

			// Create 4 corner brackets
			const corners = [
				{{ pos: 'top-left', startX: -startOffset, startY: -startOffset, finalX: finalOffset, finalY: finalOffset }},
				{{ pos: 'top-right', startX: startOffset, startY: -startOffset, finalX: -finalOffset, finalY: finalOffset }},
				{{ pos: 'bottom-left', startX: -startOffset, startY: startOffset, finalX: finalOffset, finalY: -finalOffset }},
				{{ pos: 'bottom-right', startX: startOffset, startY: startOffset, finalX: -finalOffset, finalY: -finalOffset }}
			];

			corners.forEach(corner => {{
				const bracket = document.createElement('div');
				bracket.style.cssText = `
					position: absolute;
					width: ${{cornerSize}}px;
					height: ${{cornerSize}}px;
					pointer-events: none;
					transition: all 0.15s ease-out;
				`;

				// Position corners
				if (corner.pos === 'top-left') {{
					bracket.style.top = '0';
					bracket.style.left = '0';
					bracket.style.borderTop = `${{borderWidth}}px solid ${{color}}`;
					bracket.style.borderLeft = `${{borderWidth}}px solid ${{color}}`;
					bracket.style.transform = `translate(${{corner.startX}}px, ${{corner.startY}}px)`;
				}} else if (corner.pos === 'top-right') {{
					bracket.style.top = '0';
					bracket.style.right = '0';
					bracket.style.borderTop = `${{borderWidth}}px solid ${{color}}`;
					bracket.style.borderRight = `${{borderWidth}}px solid ${{color}}`;
					bracket.style.transform = `translate(${{corner.startX}}px, ${{corner.startY}}px)`;
				}} else if (corner.pos === 'bottom-left') {{
					bracket.style.bottom = '0';
					bracket.style.left = '0';
					bracket.style.borderBottom = `${{borderWidth}}px solid ${{color}}`;
					bracket.style.borderLeft = `${{borderWidth}}px solid ${{color}}`;
					bracket.style.transform = `translate(${{corner.startX}}px, ${{corner.startY}}px)`;
				}} else if (corner.pos === 'bottom-right') {{
					bracket.style.bottom = '0';
					bracket.style.right = '0';
					bracket.style.borderBottom = `${{borderWidth}}px solid ${{color}}`;
					bracket.style.borderRight = `${{borderWidth}}px solid ${{color}}`;
					bracket.style.transform = `translate(${{corner.startX}}px, ${{corner.startY}}px)`;
				}}

				container.appendChild(bracket);

				// Animate to final position slightly outside the element
				setTimeout(() => {{
					bracket.style.transform = `translate(${{corner.finalX}}px, ${{corner.finalY}}px)`;
					focusRing.style.opacity = '1';
				}}, 10);
			}});

			document.body.appendChild(container);

			// Auto-remove after duration
			setTimeout(() => {{
				focusRing.style.opacity = '0';
				container.style.opacity = '0';
				container.style.transition = 'opacity 0.3s ease-out';
				setTimeout(() => container.remove(), 300);
			}}, duration);

			return {{ created: true }};
		}})();
		"""

		await cdp_session.cdp_client.send.Runtime.evaluate(
			params={'expression': script, 'returnByValue': True}, session_id=cdp_session.session_id
		)

	except Exception as e:
		session.logger.debug(f'Failed to highlight interaction element: {e}')


async def highlight_coordinate_click(session: BrowserSession, x: int, y: int) -> None:
	"""Temporarily highlight a coordinate click position for user visibility."""
	if not session.browser_profile.highlight_elements:
		return

	try:
		cdp_session = await session.get_or_create_cdp_session()

		color = session.browser_profile.interaction_highlight_color
		duration_ms = int(session.browser_profile.interaction_highlight_duration * 1000)

		script = f"""
		(function() {{
			const x = {x};
			const y = {y};
			const color = {json.dumps(color)};
			const duration = {duration_ms};
			const glowColor = color.replace('rgb(', 'rgba(').replace(')', ', 0.25)');

			// Get current scroll position
			const scrollX = window.pageXOffset || document.documentElement.scrollLeft || 0;
			const scrollY = window.pageYOffset || document.documentElement.scrollTop || 0;

			// Create container
			const container = document.createElement('div');
			container.setAttribute('data-agentyc-coordinate-highlight', 'true');
			container.style.cssText = `
				position: absolute;
				left: ${{x + scrollX}}px;
				top: ${{y + scrollY}}px;
				width: 0;
				height: 0;
				pointer-events: none;
				z-index: 2147483647;
			`;

			// Create outer circle
			const outerCircle = document.createElement('div');
			outerCircle.style.cssText = `
				position: absolute;
				left: -18px;
				top: -18px;
				width: 36px;
				height: 36px;
				border: 3px solid ${{color}};
				border-radius: 50%;
				opacity: 0;
				transform: scale(0.3);
				transition: all 0.2s ease-out;
				box-shadow: 0 0 0 6px ${{glowColor}};
			`;
			container.appendChild(outerCircle);

			// Create center dot
			const centerDot = document.createElement('div');
			centerDot.style.cssText = `
				position: absolute;
				left: -5px;
				top: -5px;
				width: 10px;
				height: 10px;
				background: ${{color}};
				border-radius: 50%;
				opacity: 0;
				transform: scale(0);
				transition: all 0.15s ease-out;
				box-shadow: 0 0 0 2px rgba(255,255,255,0.85);
			`;
			container.appendChild(centerDot);

			document.body.appendChild(container);

			// Animate in
			setTimeout(() => {{
				outerCircle.style.opacity = '0.8';
				outerCircle.style.transform = 'scale(1)';
				centerDot.style.opacity = '1';
				centerDot.style.transform = 'scale(1)';
			}}, 10);

			// Animate out and remove
			setTimeout(() => {{
				outerCircle.style.opacity = '0';
				outerCircle.style.transform = 'scale(1.5)';
				centerDot.style.opacity = '0';
				setTimeout(() => container.remove(), 300);
			}}, duration);

			return {{ created: true }};
		}})();
		"""

		await cdp_session.cdp_client.send.Runtime.evaluate(
			params={'expression': script, 'returnByValue': True}, session_id=cdp_session.session_id
		)

	except Exception as e:
		session.logger.debug(f'Failed to highlight coordinate click: {e}')


async def add_highlights(session: BrowserSession, selector_map: dict[int, EnhancedDOMTreeNode]) -> None:
	"""Add visual highlights to the browser DOM for user visibility."""
	if not session.browser_profile.dom_highlight_elements or not selector_map:
		return

	try:
		elements_data = []
		for _, node in selector_map.items():
			if node.absolute_position:
				rect = node.absolute_position
				bbox = {'x': rect.x, 'y': rect.y, 'width': rect.width, 'height': rect.height}

				if bbox and bbox.get('width', 0) > 0 and bbox.get('height', 0) > 0:
					element = {
						'x': bbox['x'],
						'y': bbox['y'],
						'width': bbox['width'],
						'height': bbox['height'],
						'element_name': node.node_name,
						'is_clickable': node.snapshot_node.is_clickable if node.snapshot_node else True,
						'is_scrollable': getattr(node, 'is_scrollable', False),
						'attributes': node.attributes or {},
						'frame_id': getattr(node, 'frame_id', None),
						'node_id': node.node_id,
						'backend_node_id': node.backend_node_id,
						'xpath': node.xpath,
						'text_content': node.get_all_children_text()[:50]
						if hasattr(node, 'get_all_children_text')
						else node.node_value[:50],
					}
					elements_data.append(element)

		if not elements_data:
			session.logger.debug('⚠️ No valid elements to highlight')
			return

		session.logger.debug(f'📍 Creating highlights for {len(elements_data)} elements')
		await remove_highlights(session)
		await asyncio.sleep(0.05)

		cdp_session = await session.get_or_create_cdp_session()

		script = f"""
		(function() {{
			// Interactive elements data
			const interactiveElements = {json.dumps(elements_data)};

			console.log('=== BROWSER-USE HIGHLIGHTING ===');
			console.log('Highlighting', interactiveElements.length, 'interactive elements');

			// Double-check: Remove any existing highlight container first
			const existingContainer = document.getElementById('agentyc-debug-highlights');
			if (existingContainer) {{
				console.log('⚠️ Found existing highlight container, removing it first');
				existingContainer.remove();
			}}

			// Also remove any stray highlight elements
			const strayHighlights = document.querySelectorAll('[data-agentyc-highlight]');
			if (strayHighlights.length > 0) {{
				console.log('⚠️ Found', strayHighlights.length, 'stray highlight elements, removing them');
				strayHighlights.forEach(el => el.remove());
			}}

			// Use maximum z-index for visibility
			const HIGHLIGHT_Z_INDEX = 2147483647;

			// Create container for all highlights - use FIXED positioning (key insight from v0.6.0)
			const container = document.createElement('div');
			container.id = 'agentyc-debug-highlights';
			container.setAttribute('data-agentyc-highlight', 'container');

			container.style.cssText = `
				position: absolute;
				top: 0;
				left: 0;
				width: 100vw;
				height: 100vh;
				pointer-events: none;
				z-index: ${{HIGHLIGHT_Z_INDEX}};
				overflow: visible;
				margin: 0;
				padding: 0;
				border: none;
				outline: none;
				box-shadow: none;
				background: none;
				font-family: inherit;
			`;

			// Helper function to create text elements safely
			function createTextElement(tag, text, styles) {{
				const element = document.createElement(tag);
				element.textContent = text;
				if (styles) element.style.cssText = styles;
				return element;
			}}

			// Add highlights for each element
			interactiveElements.forEach((element, index) => {{
				const highlight = document.createElement('div');
				highlight.setAttribute('data-agentyc-highlight', 'element');
				highlight.setAttribute('data-element-id', element.backend_node_id);
				highlight.style.cssText = `
					position: absolute;
					left: ${{element.x}}px;
					top: ${{element.y}}px;
					width: ${{element.width}}px;
					height: ${{element.height}}px;
					outline: 2px dashed #4a90e2;
					outline-offset: -2px;
					background: transparent;
					pointer-events: none;
					box-sizing: content-box;
					transition: outline 0.2s ease;
					margin: 0;
					padding: 0;
					border: none;
				`;

				// Enhanced label with backend node ID
				const label = createTextElement('div', element.backend_node_id, `
					position: absolute;
					top: -20px;
					left: 0;
					background-color: #4a90e2;
					color: white;
					padding: 2px 6px;
					font-size: 11px;
					font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
					font-weight: bold;
					border-radius: 3px;
					white-space: nowrap;
					z-index: ${{HIGHLIGHT_Z_INDEX + 1}};
					box-shadow: 0 2px 4px rgba(0,0,0,0.3);
					border: none;
					outline: none;
					margin: 0;
					line-height: 1.2;
				`);

				highlight.appendChild(label);
				container.appendChild(highlight);
			}});

			// Add container to document
			document.body.appendChild(container);

			console.log('Highlighting complete - added', interactiveElements.length, 'highlights');
			return {{ added: interactiveElements.length }};
		}})();
		"""

		result = await cdp_session.cdp_client.send.Runtime.evaluate(
			params={'expression': script, 'returnByValue': True}, session_id=cdp_session.session_id
		)

		if result and 'result' in result and 'value' in result['result']:
			added_count = result['result']['value'].get('added', 0)
			session.logger.debug(f'Successfully added {added_count} highlight elements to browser DOM')
		else:
			session.logger.debug('Browser highlight injection completed')

	except Exception as e:
		session.logger.warning(f'Failed to add browser highlights: {e}')
		session.logger.debug(f'Browser highlight traceback: {traceback.format_exc()}')


async def screenshot_element(
	session: BrowserSession,
	selector: str,
	path: str | None = None,
	format: str = 'png',
	quality: int | None = None,
) -> bytes:
	"""Take a screenshot of a specific element."""
	bounds = await _get_element_bounds(session, selector)
	if not bounds:
		raise ValueError(f"Element '{selector}' not found or has no bounds")

	return await session.take_screenshot(
		path=path,
		format=format,
		quality=quality,
		clip=bounds,
	)


async def _get_element_bounds(session: BrowserSession, selector: str) -> dict | None:
	"""Get element bounding box using CDP."""
	cdp_session = await session.get_or_create_cdp_session()

	doc = await cdp_session.cdp_client.send.DOM.getDocument(params={'depth': 1}, session_id=cdp_session.session_id)
	node_result = await cdp_session.cdp_client.send.DOM.querySelector(
		params={'nodeId': doc['root']['nodeId'], 'selector': selector}, session_id=cdp_session.session_id
	)

	node_id = node_result.get('nodeId')
	if not node_id:
		return None

	box_result = await cdp_session.cdp_client.send.DOM.getBoxModel(params={'nodeId': node_id}, session_id=cdp_session.session_id)

	box_model = box_result.get('model')
	if not box_model:
		return None

	content = box_model['content']
	return {
		'x': min(content[0], content[2], content[4], content[6]),
		'y': min(content[1], content[3], content[5], content[7]),
		'width': max(content[0], content[2], content[4], content[6]) - min(content[0], content[2], content[4], content[6]),
		'height': max(content[1], content[3], content[5], content[7]) - min(content[1], content[3], content[5], content[7]),
	}
