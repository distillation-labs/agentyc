"""Highlight helpers for BrowserSession DOM access."""

from __future__ import annotations

import asyncio
import json
import traceback
from typing import TYPE_CHECKING

from agentyc.browser.session_dom_geometry import get_element_coordinates
from agentyc.dom.views import EnhancedDOMTreeNode

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession


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

			const scrollX = window.pageXOffset || document.documentElement.scrollLeft || 0;
			const scrollY = window.pageYOffset || document.documentElement.scrollTop || 0;

			const el = document.createElement('div');
			el.setAttribute('data-agentyc-interaction-highlight', 'true');
			el.style.cssText = `
				position: absolute;
				left: ${{rect.x + scrollX}}px;
				top: ${{rect.y + scrollY}}px;
				width: ${{rect.width}}px;
				height: ${{rect.height}}px;
				pointer-events: none;
				z-index: 2147483647;
				outline: 2px solid ${{color}};
				outline-offset: -2px;
				transition: opacity 0.15s ease-out;
			`;

			document.body.appendChild(el);

			setTimeout(() => {{
				el.style.opacity = '0';
				setTimeout(() => el.remove(), 150);
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

			const scrollX = window.pageXOffset || document.documentElement.scrollLeft || 0;
			const scrollY = window.pageYOffset || document.documentElement.scrollTop || 0;

			const dot = document.createElement('div');
			dot.setAttribute('data-agentyc-coordinate-highlight', 'true');
			dot.style.cssText = `
				position: absolute;
				left: ${{x + scrollX - 4}}px;
				top: ${{y + scrollY - 4}}px;
				width: 8px;
				height: 8px;
				background: ${{color}};
				border-radius: 50%;
				pointer-events: none;
				z-index: 2147483647;
				transition: opacity 0.15s ease-out;
			`;

			document.body.appendChild(dot);

			setTimeout(() => {{
				dot.style.opacity = '0';
				setTimeout(() => dot.remove(), 150);
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
					outline: 1px dashed #c25818;
					outline-offset: -1px;
					background: transparent;
					pointer-events: none;
					box-sizing: content-box;
					margin: 0;
					padding: 0;
					border: none;
				`;
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
