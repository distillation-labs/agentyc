from __future__ import annotations

import asyncio
import time
from typing import Any

from cdp_use.cdp.dom.types import Node
from cdp_use.cdp.target import TargetID

from agentyc.dom.enhanced_snapshot import REQUIRED_COMPUTED_STYLES
from agentyc.dom.views import TargetAllTrees
from agentyc.utils import create_task_with_error_handling


class DomServiceFetchMixin:
	def _dom_tree_contains_frames(self: Any, dom_tree: Any) -> bool:
		def walk(node: Node | dict[str, Any] | None) -> bool:
			if not node:
				return False
			if str(node.get('nodeName', '')).upper() in {'IFRAME', 'FRAME'}:
				return True
			content_document = node.get('contentDocument')
			if content_document and walk(content_document):
				return True
			for shadow_root in node.get('shadowRoots') or []:
				if walk(shadow_root):
					return True
			for child in node.get('children') or []:
				if walk(child):
					return True
			return False

		return walk(dom_tree.get('root'))

	def _get_cached_js_click_listener_backend_ids(self: Any, target_id: TargetID) -> set[int] | None:
		cached = self._target_js_click_listener_backend_ids.get(target_id)
		if cached is None:
			return None
		cached_at, backend_ids = cached
		if time.monotonic() - cached_at > self._JS_CLICK_LISTENER_CACHE_TTL_S:
			self._target_js_click_listener_backend_ids.pop(target_id, None)
			return None
		return set(backend_ids)

	def _cache_js_click_listener_backend_ids(self: Any, target_id: TargetID, backend_ids: set[int]) -> None:
		self._target_js_click_listener_backend_ids[target_id] = (time.monotonic(), set(backend_ids))

	async def _get_all_trees(self: Any, target_id: TargetID) -> TargetAllTrees:
		cdp_session = await self.browser_session.get_or_create_cdp_session(target_id=target_id, focus=False)
		self.logger.debug(f'🔍 DEBUG: Capturing DOM snapshot for target {target_id}')

		start_js_listener_detection = time.time()
		js_click_listener_backend_ids = self._get_cached_js_click_listener_backend_ids(target_id) or set()
		if not js_click_listener_backend_ids and target_id not in self._target_js_click_listener_backend_ids:
			try:
				js_listener_result = await cdp_session.cdp_client.send.Runtime.evaluate(
					params={
						'expression': """
						(() => {
							if (typeof getEventListeners !== 'function') {
								return null;
							}

							const allElements = document.querySelectorAll('*');
							if (allElements.length > 10000) {
								return null;
							}

							const elementsWithListeners = [];
							for (const el of allElements) {
								try {
									const listeners = getEventListeners(el);
									if (listeners.click || listeners.mousedown || listeners.mouseup || listeners.pointerdown || listeners.pointerup) {
										elementsWithListeners.push(el);
									}
								} catch (e) {
								}
							}

							return elementsWithListeners;
						})()
						""",
						'includeCommandLineAPI': True,
						'returnByValue': False,
					},
					session_id=cdp_session.session_id,
				)

				result_object_id = js_listener_result.get('result', {}).get('objectId')
				if result_object_id:
					array_props = await cdp_session.cdp_client.send.Runtime.getProperties(
						params={'objectId': result_object_id, 'ownProperties': True},
						session_id=cdp_session.session_id,
					)
					element_object_ids: list[str] = []
					for prop in array_props.get('result', []):
						prop_name = prop.get('name', '') if isinstance(prop, dict) else ''
						if isinstance(prop_name, str) and prop_name.isdigit():
							prop_value = prop.get('value', {}) if isinstance(prop, dict) else {}
							if isinstance(prop_value, dict):
								object_id = prop_value.get('objectId')
								if object_id and isinstance(object_id, str):
									element_object_ids.append(object_id)

					async def get_backend_node_id(object_id: str) -> int | None:
						try:
							node_info = await cdp_session.cdp_client.send.DOM.describeNode(
								params={'objectId': object_id},
								session_id=cdp_session.session_id,
							)
							return node_info.get('node', {}).get('backendNodeId')
						except Exception:
							return None

					backend_ids = await asyncio.gather(*[get_backend_node_id(oid) for oid in element_object_ids])
					js_click_listener_backend_ids = {bid for bid in backend_ids if bid is not None}
					try:
						await cdp_session.cdp_client.send.Runtime.releaseObject(
							params={'objectId': result_object_id},
							session_id=cdp_session.session_id,
						)
					except Exception:
						pass

				self._cache_js_click_listener_backend_ids(target_id, js_click_listener_backend_ids)
				self.logger.debug(f'Detected {len(js_click_listener_backend_ids)} elements with JS click listeners')
			except Exception as e:
				self.logger.debug(f'Failed to detect JS event listeners: {e}')
		js_listener_detection_ms = (time.time() - start_js_listener_detection) * 1000

		def create_snapshot_request():
			return cdp_session.cdp_client.send.DOMSnapshot.captureSnapshot(
				params={
					'computedStyles': REQUIRED_COMPUTED_STYLES,
					'includePaintOrder': True,
					'includeDOMRects': True,
					'includeBlendedBackgroundColors': False,
					'includeTextColorOpacities': False,
				},
				session_id=cdp_session.session_id,
			)

		def create_dom_tree_request():
			return cdp_session.cdp_client.send.DOM.getDocument(
				params={'depth': -1, 'pierce': True}, session_id=cdp_session.session_id
			)

		def create_ax_tree_request(dom_tree: Any):
			if self._dom_tree_contains_frames(dom_tree):
				return self._get_ax_tree_for_all_frames(target_id)
			return self._get_ax_tree_for_current_frame(target_id)

		start_cdp_calls = time.time()
		has_frames_hint = self._target_has_frames.get(target_id)

		if has_frames_hint is not None:
			tasks = {
				'snapshot': create_task_with_error_handling(create_snapshot_request(), name='get_snapshot'),
				'dom_tree': create_task_with_error_handling(create_dom_tree_request(), name='get_dom_tree'),
				'ax_tree': create_task_with_error_handling(
					self._get_ax_tree_for_all_frames(target_id)
					if has_frames_hint
					else self._get_ax_tree_for_current_frame(target_id),
					name='get_ax_tree_with_frames' if has_frames_hint else 'get_ax_tree_current_frame',
				),
				'device_pixel_ratio': create_task_with_error_handling(
					self._get_viewport_ratio(target_id), name='get_viewport_ratio'
				),
			}
			done, pending = await asyncio.wait(tasks.values(), timeout=10.0)
			if pending:
				for task in pending:
					task.cancel()
				retry_map = {
					tasks['snapshot']: lambda: create_task_with_error_handling(
						create_snapshot_request(), name='get_snapshot_retry'
					),
					tasks['dom_tree']: lambda: create_task_with_error_handling(
						create_dom_tree_request(), name='get_dom_tree_retry'
					),
					tasks['ax_tree']: lambda: create_task_with_error_handling(
						self._get_ax_tree_for_all_frames(target_id)
						if has_frames_hint
						else self._get_ax_tree_for_current_frame(target_id),
						name='get_ax_tree_with_frames_retry' if has_frames_hint else 'get_ax_tree_current_frame_retry',
					),
					tasks['device_pixel_ratio']: lambda: create_task_with_error_handling(
						self._get_viewport_ratio(target_id), name='get_viewport_ratio_retry'
					),
				}
				for key, task in tasks.items():
					if task in pending and task in retry_map:
						tasks[key] = retry_map[task]()
				_done2, pending2 = await asyncio.wait([t for t in tasks.values() if not t.done()], timeout=2.0)
				if pending2:
					for task in pending2:
						task.cancel()

			results = {}
			failed = []
			for key, task in tasks.items():
				if task.done() and not task.cancelled():
					try:
						results[key] = task.result()
					except Exception as e:
						self.logger.warning(f'CDP request {key} failed with exception: {e}')
						failed.append(key)
				else:
					self.logger.warning(f'CDP request {key} timed out')
					failed.append(key)

			if failed:
				raise TimeoutError(f'CDP requests failed or timed out: {", ".join(failed)}')

			snapshot = results['snapshot']
			dom_tree = results['dom_tree']
			actual_has_frames = self._dom_tree_contains_frames(dom_tree)
			self._target_has_frames[target_id] = actual_has_frames
			ax_tree = results['ax_tree']
			if actual_has_frames and not has_frames_hint:
				ax_tree = await self._get_ax_tree_for_all_frames(target_id)
			device_pixel_ratio = results['device_pixel_ratio']
			cdp_calls_ms = (time.time() - start_cdp_calls) * 1000
		else:
			tasks = {
				'snapshot': create_task_with_error_handling(create_snapshot_request(), name='get_snapshot'),
				'device_pixel_ratio': create_task_with_error_handling(
					self._get_viewport_ratio(target_id), name='get_viewport_ratio'
				),
			}
			try:
				dom_tree = await asyncio.wait_for(create_dom_tree_request(), timeout=10.0)
			except Exception:
				try:
					dom_tree = await asyncio.wait_for(create_dom_tree_request(), timeout=2.0)
				except Exception:
					for task in tasks.values():
						task.cancel()
					raise

			actual_has_frames = self._dom_tree_contains_frames(dom_tree)
			self._target_has_frames[target_id] = actual_has_frames
			ax_tree_name = 'get_ax_tree_with_frames' if actual_has_frames else 'get_ax_tree_current_frame'
			tasks['ax_tree'] = create_task_with_error_handling(create_ax_tree_request(dom_tree), name=ax_tree_name)
			elapsed_s = time.time() - start_cdp_calls
			timeout_remaining = max(0.0, 10.0 - elapsed_s)
			done, pending = await asyncio.wait(tasks.values(), timeout=timeout_remaining)
			if pending:
				for task in pending:
					task.cancel()
				retry_map = {
					tasks['snapshot']: lambda: create_task_with_error_handling(
						create_snapshot_request(), name='get_snapshot_retry'
					),
					tasks['ax_tree']: lambda: create_task_with_error_handling(
						create_ax_tree_request(dom_tree), name=f'{ax_tree_name}_retry'
					),
					tasks['device_pixel_ratio']: lambda: create_task_with_error_handling(
						self._get_viewport_ratio(target_id), name='get_viewport_ratio_retry'
					),
				}
				for key, task in tasks.items():
					if task in pending and task in retry_map:
						tasks[key] = retry_map[task]()
				_done2, pending2 = await asyncio.wait([t for t in tasks.values() if not t.done()], timeout=2.0)
				if pending2:
					for task in pending2:
						task.cancel()

			results = {}
			failed = []
			for key, task in tasks.items():
				if task.done() and not task.cancelled():
					try:
						results[key] = task.result()
					except Exception as e:
						self.logger.warning(f'CDP request {key} failed with exception: {e}')
						failed.append(key)
				else:
					self.logger.warning(f'CDP request {key} timed out')
					failed.append(key)

			if failed:
				raise TimeoutError(f'CDP requests failed or timed out: {", ".join(failed)}')

			snapshot = results['snapshot']
			ax_tree = results['ax_tree']
			device_pixel_ratio = results['device_pixel_ratio']
			cdp_calls_ms = (time.time() - start_cdp_calls) * 1000

		start_snapshot_processing = time.time()
		if snapshot and 'documents' in snapshot:
			original_doc_count = len(snapshot['documents'])
			if original_doc_count > self.max_iframes:
				self.logger.warning(
					f'⚠️ Limiting processing of {original_doc_count} iframes on page to only first {self.max_iframes} to prevent crashes!'
				)
				snapshot['documents'] = snapshot['documents'][: self.max_iframes]

			total_nodes = sum(len(doc.get('nodes', [])) for doc in snapshot['documents'])
			self.logger.debug(f'🔍 DEBUG: Snapshot contains {len(snapshot["documents"])} frames with {total_nodes} total nodes')
			for doc_idx, doc in enumerate(snapshot['documents']):
				if doc_idx > 0:
					self.logger.debug(
						f'🔍 DEBUG: Iframe #{doc_idx} {doc.get("frameId", "no-frame-id")} {doc.get("url", "no-url")} has {len(doc.get("nodes", []))} nodes'
					)

		snapshot_processing_ms = (time.time() - start_snapshot_processing) * 1000
		return TargetAllTrees(
			snapshot=snapshot,
			dom_tree=dom_tree,
			ax_tree=ax_tree,
			device_pixel_ratio=device_pixel_ratio,
			cdp_timing={
				'js_listener_detection_ms': js_listener_detection_ms,
				'cdp_parallel_calls_ms': cdp_calls_ms,
				'snapshot_processing_ms': snapshot_processing_ms,
			},
			js_click_listener_backend_ids=js_click_listener_backend_ids if js_click_listener_backend_ids else None,
		)
