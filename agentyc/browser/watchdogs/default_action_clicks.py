"""Click-related behavior for the default action watchdog."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agentyc.browser.events import ClickCoordinateEvent, ClickElementEvent
from agentyc.browser.views import BrowserError, URLNotAllowedError
from agentyc.dom.service import EnhancedDOMTreeNode
from agentyc.observability import observe_debug


class DefaultActionClickMixin:
    """Click pipeline helpers and handlers."""

    if TYPE_CHECKING:
        logger: Any
        browser_session: Any

    async def _execute_click_with_download_detection(
        self,
        click_coro,
        download_start_timeout: float = 0.5,
        download_complete_timeout: float = 30.0,
    ) -> dict | None:
        """Execute a click operation and automatically wait for any triggered download."""
        import time

        download_started = asyncio.Event()
        download_completed = asyncio.Event()
        download_info: dict = {}
        progress_info: dict = {'last_update': 0.0, 'received_bytes': 0, 'total_bytes': 0, 'state': ''}

        def on_download_start(info: dict) -> None:
            if info.get('auto_download'):
                return
            download_info['guid'] = info.get('guid', '')
            download_info['url'] = info.get('url', '')
            download_info['suggested_filename'] = info.get('suggested_filename', 'download')
            download_started.set()
            self.logger.debug(f'[ClickWithDownload] Download started: {download_info["suggested_filename"]}')

        def on_download_progress(info: dict) -> None:
            if download_info.get('guid') and info.get('guid') != download_info['guid']:
                return
            progress_info['last_update'] = time.time()
            progress_info['received_bytes'] = info.get('received_bytes', 0)
            progress_info['total_bytes'] = info.get('total_bytes', 0)
            progress_info['state'] = info.get('state', '')
            self.logger.debug(
                f'[ClickWithDownload] Progress: {progress_info["received_bytes"]}/{progress_info["total_bytes"]} bytes ({progress_info["state"]})'
            )

        def on_download_complete(info: dict) -> None:
            if info.get('auto_download'):
                return
            if download_info.get('guid') and info.get('guid') and info.get('guid') != download_info['guid']:
                return
            download_info['path'] = info.get('path', '')
            download_info['file_name'] = info.get('file_name', '')
            download_info['file_size'] = info.get('file_size', 0)
            download_info['file_type'] = info.get('file_type')
            download_info['mime_type'] = info.get('mime_type')
            download_completed.set()
            self.logger.debug(f'[ClickWithDownload] Download completed: {download_info["file_name"]}')

        downloads_watchdog = self.browser_session._downloads_watchdog
        self.logger.debug(f'[ClickWithDownload] downloads_watchdog={downloads_watchdog is not None}')
        if downloads_watchdog:
            self.logger.debug('[ClickWithDownload] Registering download callbacks...')
            downloads_watchdog.register_download_callbacks(
                on_start=on_download_start,
                on_progress=on_download_progress,
                on_complete=on_download_complete,
            )
        else:
            self.logger.warning('[ClickWithDownload] No downloads_watchdog available!')

        try:
            click_metadata = await click_coro
            if isinstance(click_metadata, dict) and 'validation_error' in click_metadata:
                return click_metadata

            try:
                await asyncio.wait_for(download_started.wait(), timeout=download_start_timeout)
                self.logger.info(f'📥 Download started: {download_info.get("suggested_filename", "unknown")}')

                try:
                    await asyncio.wait_for(download_completed.wait(), timeout=download_complete_timeout)
                    msg = (
                        f'Downloaded file: {download_info["file_name"]} ({download_info["file_size"]} bytes) '
                        f'saved to {download_info["path"]}'
                    )
                    self.logger.info(f'💾 {msg}')

                    if click_metadata is None:
                        click_metadata = {}
                    click_metadata['download'] = {
                        'path': download_info['path'],
                        'file_name': download_info['file_name'],
                        'file_size': download_info['file_size'],
                        'file_type': download_info.get('file_type'),
                        'mime_type': download_info.get('mime_type'),
                    }
                except TimeoutError:
                    if click_metadata is None:
                        click_metadata = {}

                    filename = download_info.get('suggested_filename', 'unknown')
                    received = progress_info.get('received_bytes', 0)
                    total = progress_info.get('total_bytes', 0)
                    state = progress_info.get('state', 'unknown')
                    last_update = progress_info.get('last_update', 0.0)
                    time_since_update = time.time() - last_update if last_update > 0 else float('inf')
                    is_still_active = time_since_update < 5.0 and state == 'inProgress'

                    if is_still_active:
                        if total > 0:
                            percent = (received / total) * 100
                            progress_str = f'{percent:.1f}% ({received:,}/{total:,} bytes)'
                        else:
                            progress_str = f'{received:,} bytes downloaded (total size unknown)'

                        msg = (
                            f'Download timed out after {download_complete_timeout}s but is still in progress: '
                            f'{filename} - {progress_str}. '
                            f'The download appears to be progressing normally. Consider using the wait action '
                            f'to allow more time for the download to complete.'
                        )
                        self.logger.warning(f'⏱️ {msg}')
                        click_metadata['download_in_progress'] = {
                            'file_name': filename,
                            'received_bytes': received,
                            'total_bytes': total,
                            'state': state,
                            'message': msg,
                        }
                    else:
                        if received > 0:
                            msg = (
                                f'Download timed out after {download_complete_timeout}s: {filename}. '
                                f'Last progress: {received:,} bytes received. '
                                f'The download may have stalled or completed - check the downloads folder.'
                            )
                        else:
                            msg = (
                                f'Download timed out after {download_complete_timeout}s: {filename}. '
                                f'No progress data received - the download may have failed to start properly.'
                            )
                        self.logger.warning(f'⏱️ {msg}')
                        click_metadata['download_timeout'] = {
                            'file_name': filename,
                            'received_bytes': received,
                            'total_bytes': total,
                            'message': msg,
                        }
            except TimeoutError:
                pass

            return click_metadata if isinstance(click_metadata, dict) else None
        finally:
            if downloads_watchdog:
                downloads_watchdog.unregister_download_callbacks(
                    on_start=on_download_start,
                    on_progress=on_download_progress,
                    on_complete=on_download_complete,
                )

    def _is_print_related_element(self, element_node: EnhancedDOMTreeNode) -> bool:
        onclick = element_node.attributes.get('onclick', '').lower() if element_node.attributes else ''
        return bool(onclick and 'print' in onclick)

    async def _handle_print_button_click(self, element_node: EnhancedDOMTreeNode) -> dict | None:
        try:
            import base64
            import os
            import re
            from pathlib import Path

            import anyio

            from agentyc.browser.events import FileDownloadedEvent

            cdp_session = await self.browser_session.get_or_create_cdp_session(focus=True)
            result = await asyncio.wait_for(
                cdp_session.cdp_client.send.Page.printToPDF(
                    params={
                        'printBackground': True,
                        'preferCSSPageSize': True,
                    },
                    session_id=cdp_session.session_id,
                ),
                timeout=15.0,
            )

            pdf_data = result.get('data')
            if not pdf_data:
                self.logger.warning('⚠️ PDF generation returned no data')
                return None

            pdf_bytes = base64.b64decode(pdf_data)
            downloads_path = self.browser_session.browser_profile.downloads_path
            if not downloads_path:
                self.logger.warning('⚠️ No downloads path configured, cannot save PDF')
                return None

            try:
                page_title = await asyncio.wait_for(self.browser_session.get_current_page_title(), timeout=2.0)
                safe_title = re.sub(r'[^\w\s-]', '', page_title)[:50]
                filename = f'{safe_title}.pdf' if safe_title else 'print.pdf'
            except Exception:
                filename = 'print.pdf'

            downloads_dir = Path(downloads_path).expanduser().resolve()
            downloads_dir.mkdir(parents=True, exist_ok=True)

            final_path = downloads_dir / filename
            if final_path.exists():
                base, ext = os.path.splitext(filename)
                counter = 1
                while (downloads_dir / f'{base} ({counter}){ext}').exists():
                    counter += 1
                final_path = downloads_dir / f'{base} ({counter}){ext}'

            async with await anyio.open_file(final_path, 'wb') as file_handle:
                await file_handle.write(pdf_bytes)

            file_size = final_path.stat().st_size
            self.logger.info(f'✅ Generated PDF via CDP: {final_path} ({file_size:,} bytes)')

            page_url = await self.browser_session.get_current_page_url()
            self.browser_session.event_bus.dispatch(
                FileDownloadedEvent(
                    url=page_url,
                    path=str(final_path),
                    file_name=final_path.name,
                    file_size=file_size,
                    file_type='pdf',
                    mime_type='application/pdf',
                    auto_download=False,
                )
            )
            return {'pdf_generated': True, 'path': str(final_path)}
        except TimeoutError:
            self.logger.warning('⏱️ PDF generation timed out')
            return None
        except Exception as error:
            self.logger.warning(f'⚠️ Failed to generate PDF via CDP: {type(error).__name__}: {error}')
            return None

    @observe_debug(ignore_input=True, ignore_output=True, name='click_element_event')
    async def on_ClickElementEvent(self, event: ClickElementEvent) -> dict | None:
        try:
            if not self.browser_session.agent_focus_target_id:
                error_msg = 'Cannot execute click: browser session is corrupted (target_id=None). Session may have crashed.'
                self.logger.error(error_msg)
                raise BrowserError(error_msg)

            element_node = event.node
            index_for_logging = element_node.backend_node_id or 'unknown'

            if self.browser_session.is_file_input(element_node):
                msg = (
                    f'Index {index_for_logging} - has an element which opens file upload dialog. '
                    'To upload files please use a specific function to upload files'
                )
                self.logger.info(msg)
                return {'validation_error': msg}

            if self._is_print_related_element(element_node):
                self.logger.info(
                    f'🖨️ Detected print button (index {index_for_logging}), generating PDF directly instead of opening dialog...'
                )
                click_metadata = await self._handle_print_button_click(element_node)
                if click_metadata and click_metadata.get('pdf_generated'):
                    msg = f'Generated PDF: {click_metadata.get("path")}'
                    self.logger.info(f'💾 {msg}')
                    return click_metadata
                self.logger.warning('⚠️ PDF generation failed, falling back to regular click')

            click_metadata = await self._execute_click_with_download_detection(self._click_element_node_impl(element_node))
            if isinstance(click_metadata, dict) and 'validation_error' in click_metadata:
                self.logger.info(click_metadata['validation_error'])
                return click_metadata

            if 'download' not in (click_metadata or {}):
                msg = f'Clicked button {element_node.node_name}: {element_node.get_all_children_text(max_depth=2)}'
                self.logger.debug(f'🖱️ {msg}')
            self.logger.debug(f'Element xpath: {element_node.xpath}')
            return click_metadata
        except Exception:
            raise

    async def on_ClickCoordinateEvent(self, event: ClickCoordinateEvent) -> dict | None:
        try:
            if not self.browser_session.agent_focus_target_id:
                error_msg = 'Cannot execute click: browser session is corrupted (target_id=None). Session may have crashed.'
                self.logger.error(error_msg)
                raise BrowserError(error_msg)

            if event.force:
                self.logger.debug(f'Force clicking at coordinates ({event.coordinate_x}, {event.coordinate_y})')
                return await self._execute_click_with_download_detection(
                    self._click_on_coordinate(event.coordinate_x, event.coordinate_y, force=True)
                )

            element_node = await self.browser_session.get_dom_element_at_coordinates(event.coordinate_x, event.coordinate_y)
            if element_node is None:
                self.logger.debug(
                    f'No element found at coordinates ({event.coordinate_x}, {event.coordinate_y}), proceeding with click anyway'
                )
                return await self._execute_click_with_download_detection(
                    self._click_on_coordinate(event.coordinate_x, event.coordinate_y, force=False)
                )

            if self.browser_session.is_file_input(element_node):
                msg = (
                    f'Cannot click at ({event.coordinate_x}, {event.coordinate_y}) - element is a file input. '
                    'To upload files please use upload_file action'
                )
                self.logger.info(msg)
                return {'validation_error': msg}

            tag_name = element_node.tag_name.lower() if element_node.tag_name else ''
            if tag_name == 'select':
                msg = (
                    f'Cannot click at ({event.coordinate_x}, {event.coordinate_y}) - element is a <select>. '
                    'Use dropdown_options action instead.'
                )
                self.logger.info(msg)
                return {'validation_error': msg}

            if self._is_print_related_element(element_node):
                self.logger.info(
                    f'🖨️ Detected print button at ({event.coordinate_x}, {event.coordinate_y}), generating PDF directly instead of opening dialog...'
                )
                click_metadata = await self._handle_print_button_click(element_node)
                if click_metadata and click_metadata.get('pdf_generated'):
                    msg = f'Generated PDF: {click_metadata.get("path")}'
                    self.logger.info(f'💾 {msg}')
                    return click_metadata
                self.logger.warning('⚠️ PDF generation failed, falling back to regular click')

            return await self._execute_click_with_download_detection(
                self._click_on_coordinate(event.coordinate_x, event.coordinate_y, force=False)
            )
        except Exception:
            raise

    async def _check_element_occlusion(self, backend_node_id: int, x: float, y: float, cdp_session) -> bool:
        try:
            session_id = cdp_session.session_id
            target_result = await cdp_session.cdp_client.send.DOM.resolveNode(
                params={'backendNodeId': backend_node_id}, session_id=session_id
            )
            if 'object' not in target_result:
                self.logger.debug('Could not resolve target element, assuming occluded')
                return True

            object_id = target_result['object']['objectId']
            target_info_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                params={
                    'objectId': object_id,
                    'functionDeclaration': """
                    function() {
                        const getElementInfo = (el) => {
                            return {
                                tagName: el.tagName,
                                id: el.id || '',
                                className: el.className || '',
                                textContent: (el.textContent || '').substring(0, 100)
                            };
                        };

                        const elementAtPoint = document.elementFromPoint(arguments[0], arguments[1]);
                        if (!elementAtPoint) {
                            return { targetInfo: getElementInfo(this), isClickable: false };
                        }

                        let isClickable = this === elementAtPoint ||
                            this.contains(elementAtPoint) ||
                            elementAtPoint.contains(this);

                        if (!isClickable) {
                            const target = this;
                            const atPoint = elementAtPoint;

                            if (target.tagName === 'INPUT' && target.id) {
                                const escapedId = CSS.escape(target.id);
                                const assocLabel = document.querySelector('label[for="' + escapedId + '"]');
                                if (assocLabel && (assocLabel === atPoint || assocLabel.contains(atPoint))) {
                                    isClickable = true;
                                }
                            }

                            if (!isClickable && target.tagName === 'INPUT') {
                                let ancestor = atPoint;
                                for (let i = 0; i < 3 && ancestor; i++) {
                                    if (ancestor.tagName === 'LABEL' && ancestor.contains(target)) {
                                        isClickable = true;
                                        break;
                                    }
                                    ancestor = ancestor.parentElement;
                                }
                            }

                            if (!isClickable && target.tagName === 'LABEL') {
                                if (target.htmlFor && atPoint.tagName === 'INPUT' && atPoint.id === target.htmlFor) {
                                    isClickable = true;
                                }
                                if (!isClickable && atPoint.tagName === 'INPUT' && target.contains(atPoint)) {
                                    isClickable = true;
                                }
                            }
                        }

                        return {
                            targetInfo: getElementInfo(this),
                            elementAtPointInfo: getElementInfo(elementAtPoint),
                            isClickable: isClickable
                        };
                    }
                    """,
                    'arguments': [{'value': x}, {'value': y}],
                    'returnByValue': True,
                },
                session_id=session_id,
            )
            if 'result' not in target_info_result or 'value' not in target_info_result['result']:
                self.logger.debug('Could not get target element info, assuming occluded')
                return True

            target_data = target_info_result['result']['value']
            if target_data.get('isClickable', False):
                self.logger.debug('Element is clickable (target, contained, or semantically related)')
                return False

            target_info = target_data.get('targetInfo', {})
            element_at_point_info = target_data.get('elementAtPointInfo', {})
            self.logger.debug(
                f'Element is occluded. Target: {target_info.get("tagName", "unknown")} '
                f'(id={target_info.get("id", "none")}), '
                f'ElementAtPoint: {element_at_point_info.get("tagName", "unknown")} '
                f'(id={element_at_point_info.get("id", "none")})'
            )
            return True
        except Exception as error:
            self.logger.debug(f'Occlusion check failed: {error}, assuming not occluded')
            return False

    async def _click_element_node_impl(self, element_node) -> dict | None:
        try:
            tag_name = element_node.tag_name.lower() if element_node.tag_name else ''
            element_type = element_node.attributes.get('type', '').lower() if element_node.attributes else ''
            if tag_name == 'select':
                msg = (
                    f'Cannot click on <select> elements. '
                    f'Use dropdown_options(index={element_node.backend_node_id}) action instead.'
                )
                return {'validation_error': msg}
            if tag_name == 'input' and element_type == 'file':
                msg = (
                    f'Cannot click on file input element (index={element_node.backend_node_id}). '
                    'File uploads must be handled using upload_file_to_element action.'
                )
                return {'validation_error': msg}

            cdp_session = await self.browser_session.cdp_client_for_node(element_node)
            session_id = cdp_session.session_id
            backend_node_id = element_node.backend_node_id

            is_toggle_element = tag_name == 'input' and element_type in ('checkbox', 'radio')
            pre_click_checked: bool | None = None
            checkbox_object_id: str | None = None
            if is_toggle_element and backend_node_id:
                try:
                    resolve_res = await cdp_session.cdp_client.send.DOM.resolveNode(
                        params={'backendNodeId': backend_node_id}, session_id=session_id
                    )
                    obj_info = resolve_res.get('object', {})
                    checkbox_object_id = obj_info.get('objectId') if obj_info else None
                    if not checkbox_object_id:
                        raise Exception('Failed to resolve checkbox element objectId')
                    state_res = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                        params={
                            'functionDeclaration': 'function() { return this.checked; }',
                            'objectId': checkbox_object_id,
                            'returnByValue': True,
                        },
                        session_id=session_id,
                    )
                    pre_click_checked = state_res.get('result', {}).get('value')
                    self.logger.debug(f'Checkbox pre-click state: checked={pre_click_checked}')
                except Exception as error:
                    self.logger.debug(f'Could not capture pre-click checkbox state: {error}')

            layout_metrics = await cdp_session.cdp_client.send.Page.getLayoutMetrics(session_id=session_id)
            viewport_width = layout_metrics['layoutViewport']['clientWidth']
            viewport_height = layout_metrics['layoutViewport']['clientHeight']

            try:
                await cdp_session.cdp_client.send.DOM.scrollIntoViewIfNeeded(
                    params={'backendNodeId': backend_node_id}, session_id=session_id
                )
                await asyncio.sleep(0.05)
                self.logger.debug('Scrolled element into view before getting coordinates')
            except Exception as error:
                self.logger.debug(f'Failed to scroll element into view: {error}')

            element_rect = await self.browser_session.get_element_coordinates(backend_node_id, cdp_session)
            quads = []
            if element_rect:
                x, y, width, height = element_rect.x, element_rect.y, element_rect.width, element_rect.height
                quads = [[x, y, x + width, y, x + width, y + height, x, y + height]]
                self.logger.debug(
                    f'Got coordinates from unified method: {element_rect.x}, {element_rect.y}, {element_rect.width}x{element_rect.height}'
                )

            if not quads:
                self.logger.warning('Could not get element geometry from any method, falling back to JavaScript click')
                try:
                    result = await cdp_session.cdp_client.send.DOM.resolveNode(
                        params={'backendNodeId': backend_node_id},
                        session_id=session_id,
                    )
                    assert 'object' in result and 'objectId' in result['object'], (
                        'Failed to find DOM element based on backendNodeId, maybe page content changed?'
                    )
                    object_id = result['object']['objectId']
                    await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                        params={
                            'functionDeclaration': 'function() { this.click(); }',
                            'objectId': object_id,
                        },
                        session_id=session_id,
                    )
                    await asyncio.sleep(0.05)
                    return None
                except Exception as js_error:
                    self.logger.warning(f'CDP JavaScript click also failed: {js_error}')
                    if 'No node with given id found' in str(js_error):
                        raise Exception('Element with given id not found')
                    raise Exception(f'Failed to click element: {js_error}')

            best_quad = None
            best_area = 0
            for quad in quads:
                if len(quad) < 8:
                    continue
                xs = [quad[i] for i in range(0, 8, 2)]
                ys = [quad[i] for i in range(1, 8, 2)]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                if max_x < 0 or max_y < 0 or min_x > viewport_width or min_y > viewport_height:
                    continue
                visible_min_x = max(0, min_x)
                visible_max_x = min(viewport_width, max_x)
                visible_min_y = max(0, min_y)
                visible_max_y = min(viewport_height, max_y)
                visible_area = (visible_max_x - visible_min_x) * (visible_max_y - visible_min_y)
                if visible_area > best_area:
                    best_area = visible_area
                    best_quad = quad

            if not best_quad:
                best_quad = quads[0]
                self.logger.warning('No visible quad found, using first quad')

            center_x = sum(best_quad[i] for i in range(0, 8, 2)) / 4
            center_y = sum(best_quad[i] for i in range(1, 8, 2)) / 4
            center_x = max(0, min(viewport_width - 1, center_x))
            center_y = max(0, min(viewport_height - 1, center_y))

            is_occluded = await self._check_element_occlusion(backend_node_id, center_x, center_y, cdp_session)
            if is_occluded:
                self.logger.debug('🚫 Element is occluded, falling back to JavaScript click')
                try:
                    result = await cdp_session.cdp_client.send.DOM.resolveNode(
                        params={'backendNodeId': backend_node_id},
                        session_id=session_id,
                    )
                    assert 'object' in result and 'objectId' in result['object'], (
                        'Failed to find DOM element based on backendNodeId'
                    )
                    object_id = result['object']['objectId']
                    await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                        params={
                            'functionDeclaration': 'function() { this.click(); }',
                            'objectId': object_id,
                        },
                        session_id=session_id,
                    )
                    await asyncio.sleep(0.05)
                    return None
                except Exception as js_error:
                    self.logger.error(f'JavaScript click fallback failed: {js_error}')
                    raise Exception(f'Failed to click occluded element: {js_error}')

            try:
                self.logger.debug(f'👆 Dragging mouse over element before clicking x: {center_x}px y: {center_y}px ...')
                await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
                    params={
                        'type': 'mouseMoved',
                        'x': center_x,
                        'y': center_y,
                    },
                    session_id=session_id,
                )
                await asyncio.sleep(0.05)

                self.logger.debug(f'👆🏾 Clicking x: {center_x}px y: {center_y}px ...')
                try:
                    await asyncio.wait_for(
                        cdp_session.cdp_client.send.Input.dispatchMouseEvent(
                            params={
                                'type': 'mousePressed',
                                'x': center_x,
                                'y': center_y,
                                'button': 'left',
                                'clickCount': 1,
                            },
                            session_id=session_id,
                        ),
                        timeout=3.0,
                    )
                    await asyncio.sleep(0.08)
                except TimeoutError:
                    self.logger.debug('⏱️ Mouse down timed out (likely due to dialog), continuing...')

                try:
                    await asyncio.wait_for(
                        cdp_session.cdp_client.send.Input.dispatchMouseEvent(
                            params={
                                'type': 'mouseReleased',
                                'x': center_x,
                                'y': center_y,
                                'button': 'left',
                                'clickCount': 1,
                            },
                            session_id=session_id,
                        ),
                        timeout=0.5,
                    )
                except TimeoutError:
                    self.logger.debug('⏱️ Mouse up timed out (possibly due to lag or dialog popup), continuing...')

                self.logger.debug('🖱️ Clicked successfully using x,y coordinates')
                if is_toggle_element and pre_click_checked is not None and checkbox_object_id:
                    try:
                        await asyncio.sleep(0.05)
                        state_res = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                            params={
                                'functionDeclaration': 'function() { return this.checked; }',
                                'objectId': checkbox_object_id,
                                'returnByValue': True,
                            },
                            session_id=session_id,
                        )
                        post_click_checked = state_res.get('result', {}).get('value')
                        if post_click_checked == pre_click_checked:
                            self.logger.debug(
                                f'Checkbox state unchanged after CDP click (checked={pre_click_checked}), using JS fallback'
                            )
                            await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                                params={'functionDeclaration': 'function() { this.click(); }', 'objectId': checkbox_object_id},
                                session_id=session_id,
                            )
                            await asyncio.sleep(0.05)
                            final_res = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                                params={
                                    'functionDeclaration': 'function() { return this.checked; }',
                                    'objectId': checkbox_object_id,
                                    'returnByValue': True,
                                },
                                session_id=session_id,
                            )
                            post_click_checked = final_res.get('result', {}).get('value')
                        self.logger.debug(f'Checkbox post-click state: checked={post_click_checked}')
                        return {'click_x': center_x, 'click_y': center_y, 'checked': post_click_checked}
                    except Exception as error:
                        self.logger.debug(f'Checkbox state verification failed (non-critical): {error}')

                return {'click_x': center_x, 'click_y': center_y}
            except Exception as error:
                self.logger.warning(f'CDP click failed: {type(error).__name__}: {error}')
                try:
                    result = await cdp_session.cdp_client.send.DOM.resolveNode(
                        params={'backendNodeId': backend_node_id},
                        session_id=session_id,
                    )
                    assert 'object' in result and 'objectId' in result['object'], (
                        'Failed to find DOM element based on backendNodeId, maybe page content changed?'
                    )
                    object_id = result['object']['objectId']
                    await cdp_session.cdp_client.send.Runtime.callFunctionOn(
                        params={
                            'functionDeclaration': 'function() { this.click(); }',
                            'objectId': object_id,
                        },
                        session_id=session_id,
                    )
                    await asyncio.sleep(0.1)
                    return None
                except Exception as js_error:
                    self.logger.warning(f'CDP JavaScript click also failed: {js_error}')
                    raise Exception(f'Failed to click element: {error}')
            finally:
                try:
                    cdp_session = await asyncio.wait_for(self.browser_session.get_or_create_cdp_session(focus=True), timeout=0.4)
                    await asyncio.wait_for(
                        cdp_session.cdp_client.send.Runtime.runIfWaitingForDebugger(session_id=cdp_session.session_id),
                        timeout=0.3,
                    )
                except TimeoutError:
                    self.logger.debug('⏱️ Refocus after click timed out (page may be blocked by dialog). Continuing...')
                except Exception as refocus_error:
                    self.logger.debug(f'⚠️ Refocus error (non-critical): {type(refocus_error).__name__}: {refocus_error}')
        except URLNotAllowedError as error:
            raise error
        except BrowserError as error:
            raise error
        except Exception as error:
            element_info = f'<{element_node.tag_name or "unknown"}'
            if element_node.backend_node_id:
                element_info += f' index={element_node.backend_node_id}'
            element_info += '>'
            error_detail = f'Failed to click element {element_info}. The element may not be interactable or visible.'
            if element_node.backend_node_id:
                error_detail += (
                    f' If the page changed after navigation/interaction, the index [{element_node.backend_node_id}] '
                    'may be stale. Get fresh browser state before retrying.'
                )
            raise BrowserError(
                message=f'Failed to click element: {str(error)}',
                long_term_memory=error_detail,
            )

    async def _click_on_coordinate(self, coordinate_x: int, coordinate_y: int, force: bool = False) -> dict | None:
        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()
            session_id = cdp_session.session_id
            self.logger.debug(f'👆 Moving mouse to ({coordinate_x}, {coordinate_y})...')

            await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
                params={
                    'type': 'mouseMoved',
                    'x': coordinate_x,
                    'y': coordinate_y,
                },
                session_id=session_id,
            )
            await asyncio.sleep(0.05)

            self.logger.debug(f'👆🏾 Clicking at ({coordinate_x}, {coordinate_y})...')
            try:
                await asyncio.wait_for(
                    cdp_session.cdp_client.send.Input.dispatchMouseEvent(
                        params={
                            'type': 'mousePressed',
                            'x': coordinate_x,
                            'y': coordinate_y,
                            'button': 'left',
                            'clickCount': 1,
                        },
                        session_id=session_id,
                    ),
                    timeout=3.0,
                )
                await asyncio.sleep(0.05)
            except TimeoutError:
                self.logger.debug('⏱️ Mouse down timed out (likely due to dialog), continuing...')

            try:
                await asyncio.wait_for(
                    cdp_session.cdp_client.send.Input.dispatchMouseEvent(
                        params={
                            'type': 'mouseReleased',
                            'x': coordinate_x,
                            'y': coordinate_y,
                            'button': 'left',
                            'clickCount': 1,
                        },
                        session_id=session_id,
                    ),
                    timeout=0.5,
                )
            except TimeoutError:
                self.logger.debug('⏱️ Mouse up timed out (possibly due to lag or dialog popup), continuing...')

            self.logger.debug(f'🖱️ Clicked successfully at ({coordinate_x}, {coordinate_y})')
            return {'click_x': coordinate_x, 'click_y': coordinate_y}
        except Exception as error:
            self.logger.error(
                f'Failed to click at coordinates ({coordinate_x}, {coordinate_y}): {type(error).__name__}: {error}'
            )
            raise BrowserError(
                message=f'Failed to click at coordinates: {error}',
                long_term_memory=(
                    f'Failed to click at coordinates ({coordinate_x}, {coordinate_y}). '
                    'The coordinates may be outside viewport or the page may have changed.'
                ),
            )
