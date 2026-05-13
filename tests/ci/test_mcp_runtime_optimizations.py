import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pytest_httpserver import HTTPServer

from traverse.actions import ActionResult
from traverse.browser import BrowserProfile, BrowserSession
from traverse.dom.serializer.serializer import DOMTreeSerializer
from traverse.dom.service import DomService
from traverse.dom.views import DOMRect, EnhancedAXNode, EnhancedDOMTreeNode, EnhancedSnapshotNode, NodeType
from traverse.filesystem.file_system import FileSystem
from traverse.mcp.server import TraverseServer
from traverse.mcp.state import build_browser_state_payload, parse_element_ref
from traverse.tools.service import Tools

_ACCESSIBLE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Accessible controls</title></head>
<body>
	<main>
		<h1>Checkout</h1>
		<label>Email address <input id="email" aria-label="Email address" placeholder="you@example.com" type="email"></label>
		<button id="start" aria-label="Start checkout">Start</button>
		<a href="/docs/getting-started">Documentation</a>
		<a href="/pricing">Pricing</a>
	</main>
</body>
</html>
"""

_KEY_VALUE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Status panel</title></head>
<body>
	<main>
		<section aria-label="Service status panel">
			<ul>
				<li>Status: Healthy</li>
				<li>Region: us-east-1</li>
				<li>Last deployed by: release-bot</li>
			</ul>
		</section>
		<div role="toolbar" aria-label="Runtime actions">
			<button aria-label="Restart service">Restart service</button>
			<a href="/docs/runbook">Open runbook</a>
		</div>
	</main>
</body>
</html>
"""

_SEARCH_RESULTS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Search results</title></head>
<body>
	<main>
		<label>Search <input aria-label="Search documentation" type="search" /></label>
		<ol>
			<li><a href="/search-results/auth-quickstart">Auth quickstart</a> - Learn how to issue session tokens.</li>
			<li><a href="/search-results/webhook-retries">Webhook retries</a> - Tune retry windows for failed deliveries.</li>
			<li><a href="/search-results/cache-tags">Cache tags</a> - Invalidate stale cached pages safely.</li>
		</ol>
		<nav aria-label="Pagination"><a href="/search-results?page=2">Next page</a></nav>
	</main>
</body>
</html>
"""

_CONTENTEDITABLE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Issue composer</title></head>
<body>
	<main>
		<h1>Issue composer</h1>
		<div id="editor" contenteditable="true" role="textbox" aria-label="Issue body editor">Draft issue details</div>
		<button id="publish" aria-label="Publish comment">Publish</button>
		<p id="status">Draft</p>
	</main>
	<script>
		document.getElementById('publish').addEventListener('click', () => {
			document.getElementById('status').textContent = document.getElementById('editor').textContent.trim();
		});
	</script>
</body>
</html>
"""

_COMBOBOX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Assignee combobox</title></head>
<body>
	<main>
		<label id="assignee-label">Assignee</label>
		<div
			id="assignee-combobox"
			role="combobox"
			aria-labelledby="assignee-label"
			aria-expanded="false"
			aria-controls="assignee-list"
			tabindex="0"
		>Choose assignee</div>
		<ul id="assignee-list" role="listbox" hidden>
			<li role="option">Alice</li>
			<li role="option">Bob</li>
			<li role="option">Charlie</li>
		</ul>
		<p id="chosen">None</p>
	</main>
	<script>
		const combobox = document.getElementById('assignee-combobox');
		const listbox = document.getElementById('assignee-list');
		combobox.addEventListener('click', () => {
			const expanded = combobox.getAttribute('aria-expanded') === 'true';
			combobox.setAttribute('aria-expanded', expanded ? 'false' : 'true');
			listbox.hidden = expanded;
		});
		listbox.addEventListener('click', (event) => {
			const option = event.target.closest('[role="option"]');
			if (!option) return;
			combobox.textContent = option.textContent.trim();
			combobox.setAttribute('aria-expanded', 'false');
			listbox.hidden = true;
			document.getElementById('chosen').textContent = option.textContent.trim();
		});
	</script>
</body>
</html>
"""

_IFRAME_WORKSPACE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Iframe workspace</title></head>
<body>
	<main>
		<h1>Release workspace</h1>
		<iframe src="/iframe-child" title="Editor frame" style="width: 640px; height: 220px;"></iframe>
		<p id="status">Idle</p>
	</main>
	<script>
		window.setFrameStatus = (value) => {
			document.getElementById('status').textContent = value;
		};
	</script>
</body>
</html>
"""

_IFRAME_WORKSPACE_CHILD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Iframe editor</title></head>
<body>
	<label>Frame input <input aria-label="Frame input" type="text"></label>
	<button aria-label="Submit frame value">Submit frame value</button>
	<script>
		document.querySelector('button').addEventListener('click', () => {
			window.parent.setFrameStatus(document.querySelector('input').value || 'Empty');
		});
	</script>
</body>
</html>
"""

_SHADOW_DOM_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Shadow workspace</title></head>
<body>
	<main>
		<div id="shadow-host"></div>
		<p id="status">Idle</p>
	</main>
	<script>
		const shadowRoot = document.getElementById('shadow-host').attachShadow({ mode: 'open' });
		shadowRoot.innerHTML = `
			<label>Email address <input id="email" aria-label="Email address" type="email"></label>
			<button id="publish" aria-label="Publish update">Publish</button>
		`;
		shadowRoot.getElementById('publish').addEventListener('click', () => {
			document.getElementById('status').textContent = shadowRoot.getElementById('email').value || 'Empty';
		});
	</script>
</body>
</html>
"""

_DEBOUNCED_AUTOCOMPLETE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Debounced autocomplete</title></head>
<body>
	<main>
		<label>
			Search user
			<input
				id="search"
				aria-label="Search user"
				role="combobox"
				aria-expanded="false"
				aria-controls="search-results"
				aria-autocomplete="list"
				type="search"
			>
		</label>
		<ul id="search-results" role="listbox" hidden></ul>
		<p id="status">Idle</p>
	</main>
	<script>
		const input = document.getElementById('search');
		const results = document.getElementById('search-results');
		const users = ['Alice Johnson', 'Alicia Keys', 'Bob Stone'];
		let timer = null;
		function render(query) {
			const matches = users.filter((name) => name.toLowerCase().includes(query.toLowerCase()));
			results.innerHTML = matches.map((name) => `<li role="option">${name}</li>`).join('');
			const show = matches.length > 0 && query.trim().length > 0;
			results.hidden = !show;
			input.setAttribute('aria-expanded', show ? 'true' : 'false');
		}
		input.addEventListener('input', () => {
			clearTimeout(timer);
			timer = setTimeout(() => render(input.value), 150);
		});
		results.addEventListener('click', (event) => {
			const option = event.target.closest('[role="option"]');
			if (!option) return;
			input.value = option.textContent.trim();
			results.hidden = true;
			input.setAttribute('aria-expanded', 'false');
			document.getElementById('status').textContent = option.textContent.trim();
		});
	</script>
</body>
</html>
"""

_CONFIRM_DIALOG_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Confirm dialog</title></head>
<body>
	<main>
		<button aria-label="Delete branch">Delete branch</button>
		<p id="status">Idle</p>
	</main>
	<script>
		document.querySelector('button').addEventListener('click', () => {
			const confirmed = confirm('Delete this branch?');
			document.getElementById('status').textContent = confirmed ? 'Deleted' : 'Cancelled';
		});
	</script>
</body>
</html>
"""

_REPEATED_ACTIONS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Repeated actions</title></head>
<body>
	<main>
		<section aria-label="Project Alpha actions">
			<h2>Project Alpha</h2>
			<button aria-label="Open" data-target="alpha">Open</button>
			<p id="alpha-status">Idle</p>
		</section>
		<section aria-label="Project Beta actions">
			<h2>Project Beta</h2>
			<button aria-label="Open" data-target="beta">Open</button>
			<p id="beta-status">Idle</p>
		</section>
	</main>
	<script>
		document.addEventListener('click', (event) => {
			const button = event.target.closest('button[data-target]');
			if (!button) return;
			const target = button.getAttribute('data-target');
			document.getElementById(`${target}-status`).textContent = `Opened ${target}.`;
		});
	</script>
</body>
</html>
"""

_DRIFT_RECOVERY_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>DOM drift recovery</title></head>
<body>
	<main>
		<button id="approve" aria-label="Approve deployment" data-action="approve">Approve deployment</button>
		<p id="status">Awaiting approval.</p>
	</main>
	<script>
		const replaceButton = () => {
			const current = document.querySelector('[data-action="approve"]');
			if (!current) return;
			const replacement = current.cloneNode(true);
			replacement.id = 'approve-replacement';
			current.replaceWith(replacement);
		};
		setTimeout(replaceButton, 180);
		document.addEventListener('click', (event) => {
			const target = event.target.closest('[data-action="approve"]');
			if (!target) return;
			document.getElementById('status').textContent = 'Deployment approved.';
		});
	</script>
</body>
</html>
"""

_DISABLED_BUTTON_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Disabled control</title></head>
<body>
	<main>
		<button aria-label="Publish release" disabled>Publish release</button>
	</main>
</body>
</html>
"""

_TABLE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Pricing table</title></head>
<body>
	<main>
		<h1>Pricing</h1>
		<table>
			<thead>
				<tr><th>Plan</th><th>Price</th><th>SLA</th></tr>
			</thead>
			<tbody>
				<tr><td><a href="/pricing/starter">Starter plan</a></td><td>$19</td><td>Email support</td></tr>
				<tr><td><a href="/pricing/business">Business plan</a></td><td>$99</td><td>4-hour response</td></tr>
			</tbody>
		</table>
	</main>
</body>
</html>
"""

_LIST_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Triage checklist</title></head>
<body>
	<main>
		<h1>Incident triage</h1>
		<ol>
			<li>Open the failing workflow run</li>
			<li>Download the failed job logs</li>
			<li>Update the incident channel</li>
		</ol>
	</main>
</body>
</html>
"""

_FORM_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Deploy form</title></head>
<body>
	<main>
		<h1>Deployment</h1>
		<form>
			<label>Project name <input aria-label="Project name" placeholder="my-app" required type="text"></label>
			<label>Repository URL <input aria-label="Repository URL" placeholder="https://github.com/org/repo" type="url"></label>
			<label>Environment
				<select aria-label="Environment">
					<option>Preview</option>
					<option>Production</option>
				</select>
			</label>
			<label><input aria-label="Deploy preview" type="checkbox"> Deploy preview</label>
		</form>
	</main>
</body>
</html>
"""


_UPLOAD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Upload docs</title></head>
<body>
	<main>
		<h1>Upload docs</h1>
		<label>Upload document <input id="upload" aria-label="Upload document" type="file"></label>
		<p id="status">No file selected</p>
	</main>
	<script>
		const input = document.getElementById('upload');
		input.addEventListener('change', () => {
			const file = input.files && input.files[0];
			document.getElementById('status').textContent = file ? file.name : 'No file selected';
		});
	</script>
</body>
</html>
"""


def _make_large_summary_html() -> str:
	cards = []
	for index in range(1, 26):
		cards.append(
			f"""
			<article class="product-card">
				<h2>Featured Product {index}</h2>
				<p>Detailed product narrative {index} with rollout notes, pricing details, and merchandising copy that should not all be needed for an action summary.</p>
				<a href="/products/{index}">View product {index}</a>
			</article>
			"""
		)
	return f"""
	<!DOCTYPE html>
	<html lang="en">
	<head><title>Catalog summary fixture</title></head>
	<body>
		<header>
			<h1>Spring Catalog</h1>
			<p>Shop the latest launches and compare pricing tiers.</p>
		</header>
		<main>
			<label>Search products <input aria-label="Search products" placeholder="Search products" type="search"></label>
			<button aria-label="Open cart">Open cart</button>
			<button aria-label="Open support">Open support</button>
			<a href="/collections/featured">Featured collection</a>
			<section>
				{''.join(cards)}
			</section>
		</main>
	</body>
	</html>
	"""


_LARGE_SUMMARY_HTML = _make_large_summary_html()


class _CompletedEvent:
	def __init__(self, result: dict[str, object] | None = None):
		self.result = result or {}

	def __await__(self):
		async def _noop():
			return None

		return _noop().__await__()

	async def event_result(self, *args, **kwargs):
		return self.result


class _StubElement:
	def __init__(
		self, backend_node_id: int, text: str, tag: str = 'button', attrs: dict[str, str] | None = None, role: str | None = None
	):
		self.node_id = backend_node_id
		self.backend_node_id = backend_node_id
		self.session_id = None
		self.frame_id = None
		self.target_id = 'test-target'
		self.node_type = NodeType.ELEMENT_NODE
		self.node_name = tag.upper()
		self.node_value = ''
		self.tag_name = tag
		self.attributes = attrs or {}
		self.is_scrollable = False
		self.is_visible = True
		self.absolute_position = None
		self.ax_node = SimpleNamespace(role=role, name=text) if role else None
		self._text = text
		self.parent_node = None
		self.children = []

	def get_meaningful_text_for_llm(self) -> str:
		return self._text

	def get_all_children_text(self, max_depth: int | None = None) -> str:
		return self._text

	def compute_stable_hash(self) -> int:
		return self.backend_node_id * 10


def _stub_state(selector_map: dict[int, _StubElement] | None = None):
	return SimpleNamespace(
		url='https://example.com',
		title='Example page',
		tabs=[SimpleNamespace(url='https://example.com', title='Example page')],
		screenshot='c2NyZWVuc2hvdA==',
		page_info=SimpleNamespace(
			viewport_width=1280,
			viewport_height=720,
			page_width=1280,
			page_height=2400,
			scroll_x=0,
			scroll_y=320,
		),
		dom_state=SimpleNamespace(
			selector_map=selector_map
			or {
				11: _StubElement(11, 'Email address', tag='input', attrs={'placeholder': 'you@example.com', 'type': 'email'}),
				42: _StubElement(42, 'Start checkout', tag='button', role='button'),
			}
		),
	)


def _extract_structured_json(text: str) -> dict[str, object]:
	start_tag = '<structured_result>\n'
	end_tag = '\n</structured_result>'
	start = text.index(start_tag) + len(start_tag)
	end = text.index(end_tag, start)
	return json.loads(text[start:end])


def _make_dom_node(
	node_id: int,
	backend_node_id: int,
	node_name: str,
	*,
	node_type: NodeType = NodeType.ELEMENT_NODE,
	node_value: str = '',
	attributes: dict[str, str] | None = None,
	is_scrollable: bool | None = None,
	is_visible: bool | None = None,
	shadow_root_type: str | None = None,
	ax_role: str | None = None,
	ax_name: str | None = None,
	snapshot_bounds: tuple[float, float, float, float] | None = None,
	snapshot_styles: dict[str, str] | None = None,
	children_nodes: list[EnhancedDOMTreeNode] | None = None,
	shadow_roots: list[EnhancedDOMTreeNode] | None = None,
	content_document: EnhancedDOMTreeNode | None = None,
) -> EnhancedDOMTreeNode:
	ax_node = None
	if ax_role is not None or ax_name is not None:
		ax_node = EnhancedAXNode(
			ax_node_id=f'ax-{node_id}',
			ignored=False,
			role=ax_role,
			name=ax_name,
			description=None,
			properties=None,
			child_ids=None,
		)

	snapshot_node = None
	if snapshot_bounds is not None:
		bounds = DOMRect(*snapshot_bounds)
		snapshot_node = EnhancedSnapshotNode(
			is_clickable=None,
			cursor_style=None,
			bounds=bounds,
			clientRects=bounds,
			scrollRects=bounds,
			computed_styles=snapshot_styles or {},
			paint_order=None,
			stacking_contexts=None,
		)

	node = EnhancedDOMTreeNode(
		node_id=node_id,
		backend_node_id=backend_node_id,
		node_type=node_type,
		node_name=node_name,
		node_value=node_value,
		attributes=attributes or {},
		is_scrollable=is_scrollable,
		is_visible=is_visible,
		absolute_position=None,
		target_id='test-target',
		frame_id=None,
		session_id='test-session',
		content_document=content_document,
		shadow_root_type=shadow_root_type,
		shadow_roots=shadow_roots,
		parent_node=None,
		children_nodes=children_nodes,
		ax_node=ax_node,
		snapshot_node=snapshot_node,
	)

	for child in children_nodes or []:
		child.parent_node = node
	for shadow_root in shadow_roots or []:
		shadow_root.parent_node = node
	if content_document is not None:
		content_document.parent_node = node

	return node


@pytest.fixture(scope='session')
def http_server():
	server = HTTPServer()
	server.start()
	server.expect_request('/accessible').respond_with_data(_ACCESSIBLE_HTML, content_type='text/html')
	server.expect_request('/editor').respond_with_data(_CONTENTEDITABLE_HTML, content_type='text/html')
	server.expect_request('/combobox').respond_with_data(_COMBOBOX_HTML, content_type='text/html')
	server.expect_request('/iframe-workspace').respond_with_data(_IFRAME_WORKSPACE_HTML, content_type='text/html')
	server.expect_request('/iframe-child').respond_with_data(_IFRAME_WORKSPACE_CHILD_HTML, content_type='text/html')
	server.expect_request('/shadow-form').respond_with_data(_SHADOW_DOM_HTML, content_type='text/html')
	server.expect_request('/autocomplete').respond_with_data(_DEBOUNCED_AUTOCOMPLETE_HTML, content_type='text/html')
	server.expect_request('/confirm-dialog').respond_with_data(_CONFIRM_DIALOG_HTML, content_type='text/html')
	server.expect_request('/table').respond_with_data(_TABLE_HTML, content_type='text/html')
	server.expect_request('/list').respond_with_data(_LIST_HTML, content_type='text/html')
	server.expect_request('/form').respond_with_data(_FORM_HTML, content_type='text/html')
	server.expect_request('/upload').respond_with_data(_UPLOAD_HTML, content_type='text/html')
	server.expect_request('/status').respond_with_data(_KEY_VALUE_HTML, content_type='text/html')
	server.expect_request('/results').respond_with_data(_SEARCH_RESULTS_HTML, content_type='text/html')
	server.expect_request('/repeated').respond_with_data(_REPEATED_ACTIONS_HTML, content_type='text/html')
	server.expect_request('/drift').respond_with_data(_DRIFT_RECOVERY_HTML, content_type='text/html')
	server.expect_request('/disabled').respond_with_data(_DISABLED_BUTTON_HTML, content_type='text/html')
	server.expect_request('/summary-large').respond_with_data(_LARGE_SUMMARY_HTML, content_type='text/html')
	yield server
	server.stop()


@pytest.fixture(scope='session')
def base_url(http_server):
	return f'http://{http_server.host}:{http_server.port}'


@pytest.fixture(scope='module')
async def browser_session():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			keep_alive=True,
		)
	)
	await session.start()
	yield session
	await session.kill()


@pytest.fixture
def tools():
	return Tools()


class TestMCPHotPathFixes:
	async def test_get_state_forwards_include_screenshot_flag(self):
		server = TraverseServer()
		server.browser_session = AsyncMock()
		server.browser_session.get_browser_state_summary = AsyncMock(return_value=_stub_state())

		state_json, screenshot_b64 = await server._get_browser_state(include_screenshot=False)

		server.browser_session.get_browser_state_summary.assert_awaited_once_with(include_screenshot=False)
		payload = json.loads(state_json)
		assert payload['mode'] == 'auto'
		assert payload['effective_mode'] == 'full'
		assert payload['interactive_elements'][0]['ref'] == 'e11'
		assert screenshot_b64 is None

	async def test_serializer_relaxed_fallback_recovers_shadow_dom_controls_without_snapshot(self):
		button = _make_dom_node(
			5,
			55,
			'div',
			attributes={'role': 'button', 'aria-label': 'Open menu'},
			ax_role='button',
			ax_name='Open menu',
		)
		shadow_root = _make_dom_node(
			4,
			54,
			'#document-fragment',
			node_type=NodeType.DOCUMENT_FRAGMENT_NODE,
			shadow_root_type='open',
			children_nodes=[button],
		)
		host = _make_dom_node(3, 53, 'div', shadow_roots=[shadow_root], is_visible=True)
		body = _make_dom_node(2, 52, 'body', children_nodes=[host], is_visible=True)
		html = _make_dom_node(1, 51, 'html', children_nodes=[body], is_visible=True)
		root = _make_dom_node(0, 50, '#document', node_type=NodeType.DOCUMENT_NODE, children_nodes=[html], is_visible=True)

		state, timing = DOMTreeSerializer(root).serialize_accessible_elements()

		assert len(state.selector_map) == 1
		assert state.selector_map[55].attributes['aria-label'] == 'Open menu'
		assert timing['relaxed_interactive_fallback'] > 0

	async def test_screenshot_reuses_manual_capture_without_requesting_state_screenshot(self):
		server = TraverseServer()
		server.browser_session = AsyncMock()
		server.browser_session.id = 'session-1'
		server.browser_session.take_screenshot = AsyncMock(return_value=b'png-data')
		server.browser_session.get_browser_state_summary = AsyncMock(return_value=_stub_state())
		server._update_session_activity = lambda *_args, **_kwargs: None

		meta_json, image_b64 = await server._screenshot(full_page=False)

		server.browser_session.get_browser_state_summary.assert_awaited_once_with(include_screenshot=False)
		assert json.loads(meta_json)['size_bytes'] == len(b'png-data')
		assert image_b64

	async def test_navigate_wait_uses_dom_ready_fallback_when_lifecycle_events_are_missing(self):
		session = BrowserSession(browser_profile=BrowserProfile(headless=True))
		session.session_manager = SimpleNamespace(get_target=lambda _target_id: SimpleNamespace(url='about:blank'))
		fake_send = SimpleNamespace(
			Page=SimpleNamespace(navigate=AsyncMock(return_value={'loaderId': 'loader-1'})),
			Runtime=SimpleNamespace(
				evaluate=AsyncMock(
					return_value={'result': {'value': {'readyState': 'complete', 'url': 'http://example.com/path'}}}
				)
			),
		)
		fake_cdp_session = SimpleNamespace(
			session_id='session-1',
			target_id='target-1',
			cdp_client=SimpleNamespace(send=fake_send),
			_lifecycle_events=[],
		)
		loop = asyncio.get_event_loop()

		with patch.object(BrowserSession, 'get_or_create_cdp_session', new=AsyncMock(return_value=fake_cdp_session)):
			started = loop.time()
			await session._navigate_and_wait('http://example.com/path', 'target-1', timeout=8.0, wait_until='load')
			elapsed = loop.time() - started

		assert elapsed < 0.5
		fake_send.Page.navigate.assert_awaited_once()
		fake_send.Runtime.evaluate.assert_awaited()

	async def test_get_all_trees_uses_single_frame_ax_call_when_dom_has_no_iframes(self):
		fake_send = SimpleNamespace(
			DOMSnapshot=SimpleNamespace(captureSnapshot=AsyncMock(return_value={'documents': []})),
			DOM=SimpleNamespace(
				getDocument=AsyncMock(
					return_value={'root': {'nodeName': 'HTML', 'children': [{'nodeName': 'BODY', 'children': []}]}}
				)
			),
			Accessibility=SimpleNamespace(getFullAXTree=AsyncMock(return_value={'nodes': []})),
		)
		fake_cdp_session = SimpleNamespace(session_id='session-1', cdp_client=SimpleNamespace(send=fake_send))
		fake_browser_session = SimpleNamespace(
			get_or_create_cdp_session=AsyncMock(return_value=fake_cdp_session),
			logger=SimpleNamespace(debug=lambda *_args, **_kwargs: None, warning=lambda *_args, **_kwargs: None),
		)
		service = DomService(browser_session=fake_browser_session, logger=fake_browser_session.logger)

		with patch.object(
			DomService, '_get_ax_tree_for_all_frames', new=AsyncMock(side_effect=AssertionError('frame merge should not run'))
		):
			with patch.object(DomService, '_get_viewport_ratio', new=AsyncMock(return_value=1.0)):
				trees = await service._get_all_trees('target-1')

		assert fake_send.Accessibility.getFullAXTree.await_count == 1
		assert trees.ax_tree == {'nodes': []}

	async def test_get_all_trees_keeps_multi_frame_ax_path_when_dom_contains_iframes(self):
		fake_send = SimpleNamespace(
			DOMSnapshot=SimpleNamespace(captureSnapshot=AsyncMock(return_value={'documents': []})),
			DOM=SimpleNamespace(
				getDocument=AsyncMock(
					return_value={
						'root': {
							'nodeName': 'HTML',
							'children': [{'nodeName': 'BODY', 'children': [{'nodeName': 'IFRAME', 'children': []}]}],
						}
					}
				)
			),
			Accessibility=SimpleNamespace(getFullAXTree=AsyncMock(return_value={'nodes': []})),
		)
		fake_cdp_session = SimpleNamespace(session_id='session-1', cdp_client=SimpleNamespace(send=fake_send))
		fake_browser_session = SimpleNamespace(
			get_or_create_cdp_session=AsyncMock(return_value=fake_cdp_session),
			logger=SimpleNamespace(debug=lambda *_args, **_kwargs: None, warning=lambda *_args, **_kwargs: None),
		)
		service = DomService(browser_session=fake_browser_session, logger=fake_browser_session.logger)

		with patch.object(
			DomService, '_get_ax_tree_for_all_frames', new=AsyncMock(return_value={'nodes': ['iframe-node']})
		) as ax_all_frames:
			with patch.object(DomService, '_get_viewport_ratio', new=AsyncMock(return_value=1.0)):
				trees = await service._get_all_trees('target-1')

		ax_all_frames.assert_awaited_once_with('target-1')
		assert fake_send.Accessibility.getFullAXTree.await_count == 0
		assert trees.ax_tree == {'nodes': ['iframe-node']}

	async def test_get_all_trees_reuses_cached_no_iframe_hint(self):
		fake_send = SimpleNamespace(
			DOMSnapshot=SimpleNamespace(captureSnapshot=AsyncMock(return_value={'documents': []})),
			DOM=SimpleNamespace(
				getDocument=AsyncMock(
					return_value={'root': {'nodeName': 'HTML', 'children': [{'nodeName': 'BODY', 'children': []}]}}
				)
			),
			Accessibility=SimpleNamespace(getFullAXTree=AsyncMock(return_value={'nodes': ['root-node']})),
		)
		fake_cdp_session = SimpleNamespace(session_id='session-1', cdp_client=SimpleNamespace(send=fake_send))
		fake_browser_session = SimpleNamespace(
			get_or_create_cdp_session=AsyncMock(return_value=fake_cdp_session),
			logger=SimpleNamespace(debug=lambda *_args, **_kwargs: None, warning=lambda *_args, **_kwargs: None),
		)
		service = DomService(browser_session=fake_browser_session, logger=fake_browser_session.logger)
		service._target_has_frames['target-1'] = False

		with patch.object(DomService, '_get_viewport_ratio', new=AsyncMock(return_value=1.0)):
			trees = await service._get_all_trees('target-1')

		assert fake_send.Accessibility.getFullAXTree.await_count == 1
		assert trees.ax_tree == {'nodes': ['root-node']}
		assert service._target_has_frames['target-1'] is False

	async def test_get_all_trees_corrects_stale_no_iframe_hint(self):
		fake_send = SimpleNamespace(
			DOMSnapshot=SimpleNamespace(captureSnapshot=AsyncMock(return_value={'documents': []})),
			DOM=SimpleNamespace(
				getDocument=AsyncMock(
					return_value={
						'root': {
							'nodeName': 'HTML',
							'children': [{'nodeName': 'BODY', 'children': [{'nodeName': 'IFRAME', 'children': []}]}],
						}
					}
				)
			),
			Accessibility=SimpleNamespace(getFullAXTree=AsyncMock(return_value={'nodes': ['stale-root']})),
		)
		fake_cdp_session = SimpleNamespace(session_id='session-1', cdp_client=SimpleNamespace(send=fake_send))
		fake_browser_session = SimpleNamespace(
			get_or_create_cdp_session=AsyncMock(return_value=fake_cdp_session),
			logger=SimpleNamespace(debug=lambda *_args, **_kwargs: None, warning=lambda *_args, **_kwargs: None),
		)
		service = DomService(browser_session=fake_browser_session, logger=fake_browser_session.logger)
		service._target_has_frames['target-1'] = False

		with patch.object(
			DomService, '_get_ax_tree_for_all_frames', new=AsyncMock(return_value={'nodes': ['iframe-node']})
		) as ax_all_frames:
			with patch.object(DomService, '_get_viewport_ratio', new=AsyncMock(return_value=1.0)):
				trees = await service._get_all_trees('target-1')

		ax_all_frames.assert_awaited_once_with('target-1')
		assert trees.ax_tree == {'nodes': ['iframe-node']}
		assert service._target_has_frames['target-1'] is True

	async def test_get_all_trees_reuses_recent_js_listener_probe(self):
		fake_send = SimpleNamespace(
			DOMSnapshot=SimpleNamespace(captureSnapshot=AsyncMock(return_value={'documents': []})),
			DOM=SimpleNamespace(
				getDocument=AsyncMock(
					return_value={'root': {'nodeName': 'HTML', 'children': [{'nodeName': 'BODY', 'children': []}]}}
				)
			),
			Accessibility=SimpleNamespace(getFullAXTree=AsyncMock(return_value={'nodes': []})),
			Runtime=SimpleNamespace(
				evaluate=AsyncMock(return_value={'result': {}}),
				getProperties=AsyncMock(return_value={'result': []}),
				releaseObject=AsyncMock(return_value={}),
			),
		)
		fake_cdp_session = SimpleNamespace(session_id='session-1', cdp_client=SimpleNamespace(send=fake_send))
		fake_browser_session = SimpleNamespace(
			get_or_create_cdp_session=AsyncMock(return_value=fake_cdp_session),
			logger=SimpleNamespace(debug=lambda *_args, **_kwargs: None, warning=lambda *_args, **_kwargs: None),
		)
		service = DomService(browser_session=fake_browser_session, logger=fake_browser_session.logger)

		with patch.object(DomService, '_get_viewport_ratio', new=AsyncMock(return_value=1.0)):
			await service._get_all_trees('target-1')
			await service._get_all_trees('target-1')

		fake_send.Runtime.evaluate.assert_awaited_once()

	async def test_extract_content_skips_unused_browser_state_fetch(self, tmp_path):
		server = TraverseServer()
		server.browser_session = AsyncMock()
		server.tools = AsyncMock()
		server.tools.act = AsyncMock(return_value=ActionResult(extracted_content='done'))
		server.file_system = FileSystem(base_dir=str(tmp_path))

		result = await server._extract_content('list all links on the page', extract_links=True)

		assert result == 'done'
		server.browser_session.get_browser_state_summary.assert_not_called()

	async def test_extract_content_forwards_output_schema(self, tmp_path):
		server = TraverseServer()
		server.browser_session = AsyncMock()
		server.tools = AsyncMock()
		server.tools.act = AsyncMock(return_value=ActionResult(extracted_content='done'))
		server.file_system = FileSystem(base_dir=str(tmp_path))
		schema = {'type': 'object', 'properties': {'rows': {'type': 'array', 'items': {'type': 'object'}}}}

		await server._extract_content('extract the pricing table on the page', output_schema=schema)

		action = server.tools.act.await_args.kwargs['action']
		assert action.model_dump(mode='python')['extract']['output_schema'] == schema

	async def test_click_and_type_accept_refs(self):
		server = TraverseServer()
		server.browser_session = AsyncMock()
		server.browser_session.id = 'session-1'
		server.browser_session.get_dom_element_by_index = AsyncMock(
			side_effect=[
				_StubElement(42, 'Start checkout'),
				_StubElement(42, 'Email address', tag='input', attrs={'type': 'text'}),
			]
		)
		server.tools = SimpleNamespace(act=AsyncMock(return_value=ActionResult(extracted_content='clicked')))
		server.browser_session.event_bus = SimpleNamespace(dispatch=lambda _event: _CompletedEvent())
		server._update_session_activity = lambda *_args, **_kwargs: None

		click_result = await server._click(ref='e42')
		type_result = await server._type_text(ref='42', text='hello')

		assert click_result == 'Clicked element e42'
		assert type_result == "Typed 'hello' into element 42"
		assert server.browser_session.get_dom_element_by_index.await_args_list[0].args == (42,)
		assert server.browser_session.get_dom_element_by_index.await_args_list[1].args == (42,)

	async def test_extract_content_appends_extraction_metadata(self, tmp_path):
		server = TraverseServer()
		server.browser_session = AsyncMock()
		server.tools = AsyncMock()
		server.tools.act = AsyncMock(
			return_value=ActionResult(
				extracted_content='<url>\nhttps://example.com\n</url>\n<result>\nready\n</result>',
				metadata={
					'route': 'deterministic-links',
					'llm_used': False,
					'is_partial': False,
					'structured_extraction': False,
					'deterministic_extraction': True,
				},
			)
		)
		server.file_system = FileSystem(base_dir=str(tmp_path))

		result = await server._extract_content('list all links on the page', extract_links=True)

		assert '<extraction_metadata>' in result
		assert '"route": "deterministic-links"' in result
		assert '"llm_used": false' in result

	async def test_type_returns_postcondition_error_on_value_mismatch(self):
		server = TraverseServer()
		server.browser_session = AsyncMock()
		server.browser_session.get_dom_element_by_index = AsyncMock(
			return_value=_StubElement(42, 'Release name', tag='input', attrs={'type': 'text'})
		)
		server.browser_session.event_bus = SimpleNamespace(
			dispatch=lambda _event: _CompletedEvent({'actual_value': 'v1.2.3-final'})
		)

		result = await server._type_text(ref='e42', text='v1.2.3')

		assert result.startswith('Error [postcondition_failed]:')
		assert 'v1.2.3-final' in result


class TestMCPStateProtocolAndExtraction:
	def test_ranked_auto_mode_compacts_large_pages_and_preserves_high_signal_controls(self):
		selector_map = {
			1: _StubElement(1, 'Search products', tag='input', attrs={'placeholder': 'Search products', 'type': 'search'}),
			2: _StubElement(2, 'Sort by', tag='select', attrs={'aria-label': 'Sort by'}),
			3: _StubElement(3, 'Open cart', tag='button', role='button'),
			4: _StubElement(4, 'API Reference', tag='a', attrs={'href': '/docs/api'}, role='link'),
		}
		for index in range(5, 55):
			selector_map[index] = _StubElement(index, 'Read more', tag='a', attrs={'href': f'/items/{index}'}, role='link')

		payload = build_browser_state_payload(_stub_state(selector_map=selector_map), mode='auto', max_min_elements=10)

		texts = [element.get('text') for element in payload['interactive_elements']]
		assert payload['effective_mode'] == 'min'
		assert payload['interactive_elements_truncated'] is True
		assert 'Search products' in texts
		assert 'Sort by' in texts
		assert 'Open cart' in texts
		assert 'API Reference' in texts
		assert texts.count('Read more') <= 3

	async def test_browser_get_state_min_focus_and_delta_modes(self, browser_session: BrowserSession, base_url: str):
		await browser_session.navigate_to(f'{base_url}/accessible')

		server = TraverseServer()
		server.browser_session = browser_session

		state_json, _ = await server._get_browser_state(mode='min')
		payload = json.loads(state_json)

		assert payload['mode'] == 'min'
		assert payload['interactive_element_count'] == 4
		assert sum(1 for element in payload['interactive_elements'] if element.get('text') == 'Email address') == 1
		email = next(element for element in payload['interactive_elements'] if element.get('text') == 'Email address')
		assert email['ref'].startswith('e')
		assert 'index' not in email
		assert parse_element_ref(email['ref']) > 0
		assert email['tag'] == 'input'

		start_checkout = next(element for element in payload['interactive_elements'] if element.get('text') == 'Start checkout')
		assert start_checkout['tag'] == 'button'

		focus_json, _ = await server._get_browser_state(mode='focus', focus_ref=email['ref'])
		focus_payload = json.loads(focus_json)
		assert focus_payload['focus_ref'] == email['ref']
		assert [element['ref'] for element in focus_payload['interactive_elements']] == [email['ref']]

		unchanged_json, _ = await server._get_browser_state(mode='min', since_hash=payload['state_hash'])
		unchanged_payload = json.loads(unchanged_json)
		assert unchanged_payload['changed'] is False
		assert unchanged_payload['interactive_elements'] == []

	async def test_browser_get_state_auto_uses_full_on_small_pages(self, browser_session: BrowserSession, base_url: str):
		await browser_session.navigate_to(f'{base_url}/accessible')

		server = TraverseServer()
		server.browser_session = browser_session

		state_json, _ = await server._get_browser_state()
		payload = json.loads(state_json)

		assert payload['mode'] == 'auto'
		assert payload['effective_mode'] == 'full'
		assert len(payload['interactive_elements']) == payload['interactive_element_count']

	async def test_extract_links_can_run_without_llm(
		self, tools: Tools, browser_session: BrowserSession, base_url: str, tmp_path
	):
		await tools.navigate(url=f'{base_url}/accessible', new_tab=False, browser_session=browser_session)

		result = await tools.extract(
			query='list all links on the page',
			extract_links=True,
			browser_session=browser_session,
			page_extraction_llm=None,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		assert result.error is None
		assert result.metadata['strategy'] == 'deterministic-links'
		assert 'Documentation' in result.extracted_content
		assert '/pricing' in result.extracted_content

	async def test_extract_table_can_run_without_llm(
		self, tools: Tools, browser_session: BrowserSession, base_url: str, tmp_path
	):
		await tools.navigate(url=f'{base_url}/table', new_tab=False, browser_session=browser_session)

		result = await tools.extract(
			query='extract the pricing table on the page',
			extract_links=False,
			browser_session=browser_session,
			page_extraction_llm=None,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		assert result.error is None
		assert result.metadata['strategy'] == 'deterministic-tables'
		assert 'Columns: Plan | Price | SLA' in result.extracted_content
		assert 'Starter plan' in result.extracted_content
		assert '$99' in result.extracted_content

	async def test_extract_list_can_run_without_llm(self, tools: Tools, browser_session: BrowserSession, base_url: str, tmp_path):
		await tools.navigate(url=f'{base_url}/list', new_tab=False, browser_session=browser_session)

		result = await tools.extract(
			query='list all checklist items on the page',
			extract_links=False,
			browser_session=browser_session,
			page_extraction_llm=None,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		assert result.error is None
		assert result.metadata['strategy'] == 'deterministic-lists'
		assert 'Open the failing workflow run' in result.extracted_content
		assert 'Update the incident channel' in result.extracted_content

	async def test_extract_form_fields_can_run_without_llm(
		self, tools: Tools, browser_session: BrowserSession, base_url: str, tmp_path
	):
		await tools.navigate(url=f'{base_url}/form', new_tab=False, browser_session=browser_session)

		result = await tools.extract(
			query='list all form fields on the page',
			extract_links=False,
			browser_session=browser_session,
			page_extraction_llm=None,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		assert result.error is None
		assert result.metadata['strategy'] == 'deterministic-form-fields'
		assert 'Project name' in result.extracted_content
		assert 'required' in result.extracted_content
		assert 'options=Preview | Production' in result.extracted_content
		assert 'Deploy preview' in result.extracted_content

	async def test_summary_queries_return_explicit_error_on_large_pages(
		self, tools: Tools, browser_session: BrowserSession, base_url: str, tmp_path
	):
		await tools.navigate(url=f'{base_url}/summary-large', new_tab=False, browser_session=browser_session)

		result = await tools.extract(
			query='Summarize the shopping controls and primary calls to action',
			extract_links=True,
			browser_session=browser_session,
			page_extraction_llm=None,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		assert result.error is not None
		assert 'No deterministic extraction route matched this query' in result.error
		assert result.metadata is None

	async def test_summary_queries_return_explicit_error_on_small_pages(
		self, tools: Tools, browser_session: BrowserSession, base_url: str, tmp_path
	):
		await tools.navigate(url=f'{base_url}/form', new_tab=False, browser_session=browser_session)

		result = await tools.extract(
			query='Summarize the form fields and deployment actions',
			extract_links=True,
			browser_session=browser_session,
			page_extraction_llm=None,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		assert result.error is not None
		assert 'No deterministic extraction route matched this query' in result.error
		assert result.metadata is None

	async def test_extract_key_value_panel_can_return_structured_json_without_llm(
		self, tools: Tools, browser_session: BrowserSession, base_url: str, tmp_path
	):
		await tools.navigate(url=f'{base_url}/status', new_tab=False, browser_session=browser_session)

		result = await tools.extract(
			query='extract the status panel on the page',
			extract_links=False,
			output_schema={
				'type': 'object',
				'properties': {
					'status': {'type': 'string'},
					'region': {'type': 'string'},
					'properties': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'key': {'type': 'string'},
								'value': {'type': 'string'},
							},
						},
					},
				},
				'required': ['status', 'region'],
			},
			browser_session=browser_session,
			page_extraction_llm=None,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		data = _extract_structured_json(result.extracted_content)
		assert result.error is None
		assert result.metadata['strategy'] == 'deterministic-key-values'
		assert data['status'] == 'Healthy'
		assert data['region'] == 'us-east-1'
		assert data['properties'][0] == {'key': 'Status', 'value': 'Healthy'}

	async def test_extract_search_results_can_return_structured_json_without_llm(
		self, tools: Tools, browser_session: BrowserSession, base_url: str, tmp_path
	):
		await tools.navigate(url=f'{base_url}/results', new_tab=False, browser_session=browser_session)

		result = await tools.extract(
			query='extract the search results on the page',
			extract_links=False,
			output_schema={
				'type': 'object',
				'properties': {
					'result_count': {'type': 'integer'},
					'results': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'title': {'type': 'string'},
								'url': {'type': 'string'},
								'summary': {'type': 'string'},
							},
						},
					},
				},
				'required': ['results'],
			},
			browser_session=browser_session,
			page_extraction_llm=None,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		data = _extract_structured_json(result.extracted_content)
		assert result.error is None
		assert result.metadata['strategy'] == 'deterministic-link-collections'
		assert data['result_count'] == 3
		assert data['results'][0]['title'] == 'Auth quickstart'
		assert data['results'][1]['url'].endswith('/search-results/webhook-retries')

	async def test_browser_get_state_includes_context_for_repeated_labels(self, browser_session: BrowserSession, base_url: str):
		await browser_session.navigate_to(f'{base_url}/repeated')

		server = TraverseServer()
		server.browser_session = browser_session

		state_json, _ = await server._get_browser_state(mode='auto')
		payload = json.loads(state_json)
		open_elements = [element for element in payload['interactive_elements'] if element.get('text') == 'Open']

		assert len(open_elements) == 2
		assert any('Project Alpha actions' in element.get('context', '') for element in open_elements)
		assert any('Project Beta actions' in element.get('context', '') for element in open_elements)

	async def test_browser_get_state_skips_wrapper_labels_for_visible_text_inputs(
		self, browser_session: BrowserSession, base_url: str
	):
		await browser_session.navigate_to(f'{base_url}/iframe-workspace')

		server = TraverseServer()
		server.browser_session = browser_session

		state_json, _ = await server._get_browser_state(mode='auto')
		payload = json.loads(state_json)
		frame_input_elements = [element for element in payload['interactive_elements'] if element.get('text') == 'Frame input']

		assert len(frame_input_elements) == 1
		assert frame_input_elements[0]['tag'] == 'input'

	async def test_public_mcp_contenteditable_workflow(self, browser_session: BrowserSession, base_url: str):
		await browser_session.navigate_to(f'{base_url}/editor')

		server = TraverseServer()
		server.browser_session = browser_session
		server.tools = Tools()
		server._update_session_activity = lambda *_args, **_kwargs: None

		state_json, _ = await server._get_browser_state(mode='auto')
		payload = json.loads(state_json)
		editor_ref = next(
			element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Issue body editor'
		)
		publish_ref = next(
			element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Publish comment'
		)

		type_result = await server._type_text(ref=editor_ref, text='Hello from editor')
		click_result = await server._click(ref=publish_ref)
		status_html = await server._get_html('#status')

		assert not type_result.startswith('Error')
		assert not click_result.startswith('Error')
		assert 'Hello from editor' in status_html

	async def test_public_mcp_custom_combobox_workflow(self, browser_session: BrowserSession, base_url: str):
		await browser_session.navigate_to(f'{base_url}/combobox')

		server = TraverseServer()
		server.browser_session = browser_session
		server.tools = Tools()
		server._update_session_activity = lambda *_args, **_kwargs: None

		state_json, _ = await server._get_browser_state(mode='auto')
		payload = json.loads(state_json)
		combobox_ref = next(
			element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Choose assignee'
		)

		open_result = await server._click(ref=combobox_ref)
		state_json, _ = await server._get_browser_state(mode='auto')
		payload = json.loads(state_json)
		option_ref = next(element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Bob')
		select_result = await server._click(ref=option_ref)
		chosen_html = await server._get_html('#chosen')

		assert not open_result.startswith('Error')
		assert not select_result.startswith('Error')
		assert 'Bob' in chosen_html

	async def test_public_mcp_same_origin_iframe_workflow(self, browser_session: BrowserSession, base_url: str):
		await browser_session.navigate_to(f'{base_url}/iframe-workspace')

		server = TraverseServer()
		server.browser_session = browser_session
		server.tools = Tools()
		server._update_session_activity = lambda *_args, **_kwargs: None

		state_json, _ = await server._get_browser_state(mode='auto')
		payload = json.loads(state_json)
		frame_input_ref = next(
			element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Frame input'
		)
		submit_ref = next(
			element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Submit frame value'
		)

		type_result = await server._type_text(ref=frame_input_ref, text='from iframe')
		click_result = await server._click(ref=submit_ref)
		status_html = await server._get_html('#status')

		assert not type_result.startswith('Error')
		assert not click_result.startswith('Error')
		assert 'from iframe' in status_html

	async def test_public_mcp_shadow_dom_workflow(self, browser_session: BrowserSession, base_url: str):
		await browser_session.navigate_to(f'{base_url}/shadow-form')

		server = TraverseServer()
		server.browser_session = browser_session
		server.tools = Tools()
		server._update_session_activity = lambda *_args, **_kwargs: None

		state_json, _ = await server._get_browser_state(mode='auto')
		payload = json.loads(state_json)
		email_elements = [element for element in payload['interactive_elements'] if element.get('text') == 'Email address']
		assert len(email_elements) == 1
		assert email_elements[0]['tag'] == 'input'
		email_ref = email_elements[0]['ref']
		publish_ref = next(
			element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Publish update'
		)

		type_result = await server._type_text(ref=email_ref, text='shadow@example.com')
		click_result = await server._click(ref=publish_ref)
		status_html = await server._get_html('#status')

		assert not type_result.startswith('Error')
		assert not click_result.startswith('Error')
		assert 'shadow@example.com' in status_html

	async def test_public_mcp_upload_file_workflow(self, browser_session: BrowserSession, base_url: str, tmp_path):
		await browser_session.navigate_to(f'{base_url}/upload')

		upload_path = tmp_path / 'release-notes.pdf'
		upload_path.write_text('traverse upload fixture')

		server = TraverseServer()
		server.browser_session = browser_session
		server.tools = Tools()
		server._file_system_base_dir = tmp_path
		server._update_session_activity = lambda *_args, **_kwargs: None

		state_json, _ = await server._get_browser_state(mode='auto')
		payload = json.loads(state_json)
		upload_ref = next(
			element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Upload document'
		)

		upload_result = await server._execute_tool('browser_upload_file', {'ref': upload_ref, 'path': str(upload_path)})
		status_html = await server._get_html('#status')

		assert not str(upload_result).startswith('Error')
		assert upload_path.name in status_html

	async def test_public_mcp_debounced_autocomplete_workflow(self, browser_session: BrowserSession, base_url: str):
		await browser_session.navigate_to(f'{base_url}/autocomplete')

		server = TraverseServer()
		server.browser_session = browser_session
		server.tools = Tools()
		server._update_session_activity = lambda *_args, **_kwargs: None

		state_json, _ = await server._get_browser_state(mode='auto')
		payload = json.loads(state_json)
		search_elements = [element for element in payload['interactive_elements'] if element.get('text') == 'Search user']
		assert len(search_elements) == 1
		assert search_elements[0]['tag'] == 'input'
		search_ref = search_elements[0]['ref']

		type_result = await server._type_text(ref=search_ref, text='ali')
		await asyncio.sleep(0.25)

		state_json, _ = await server._get_browser_state(mode='auto')
		payload = json.loads(state_json)
		option_ref = next(element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Alice Johnson')

		click_result = await server._click(ref=option_ref)
		status_html = await server._get_html('#status')

		assert not type_result.startswith('Error')
		assert not click_result.startswith('Error')
		assert 'Alice Johnson' in status_html

	async def test_public_mcp_confirm_dialog_workflow(self, browser_session: BrowserSession, base_url: str):
		await browser_session.navigate_to(f'{base_url}/confirm-dialog')

		server = TraverseServer()
		server.browser_session = browser_session
		server.tools = Tools()
		server._update_session_activity = lambda *_args, **_kwargs: None

		state_json, _ = await server._get_browser_state(mode='auto')
		payload = json.loads(state_json)
		delete_ref = next(element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Delete branch')

		click_result = await server._click(ref=delete_ref)
		status_html = await server._get_html('#status')

		assert not click_result.startswith('Error')
		assert 'Deleted' in status_html

	async def test_click_recovers_after_dom_drift(self, browser_session: BrowserSession, base_url: str):
		await browser_session.navigate_to(f'{base_url}/drift')

		server = TraverseServer()
		server.browser_session = browser_session
		server.tools = Tools()
		server.tools.set_coordinate_clicking(True)
		server._update_session_activity = lambda *_args, **_kwargs: None

		state_json, _ = await server._get_browser_state(mode='auto')
		payload = json.loads(state_json)
		approve_ref = next(
			element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Approve deployment'
		)

		await asyncio.sleep(0.35)
		click_result = await server._click(ref=approve_ref)
		status_html = await server._get_html('#status')

		assert not click_result.startswith('Error')
		assert 'Deployment approved.' in status_html

	async def test_focus_mode_recovers_after_dom_drift(self, browser_session: BrowserSession, base_url: str):
		await browser_session.navigate_to(f'{base_url}/drift')

		server = TraverseServer()
		server.browser_session = browser_session

		state_json, _ = await server._get_browser_state(mode='auto')
		payload = json.loads(state_json)
		approve_ref = next(
			element['ref'] for element in payload['interactive_elements'] if element.get('text') == 'Approve deployment'
		)

		await asyncio.sleep(0.35)
		focus_json, _ = await server._get_browser_state(mode='focus', focus_ref=approve_ref)
		focus_payload = json.loads(focus_json)

		assert focus_payload['mode'] == 'focus'
		assert len(focus_payload['interactive_elements']) == 1
		assert focus_payload['interactive_elements'][0]['text'] == 'Approve deployment'

	async def test_click_disabled_element_returns_explicit_error(self):
		server = TraverseServer()
		server.browser_session = AsyncMock()
		server.browser_session.id = 'session-1'
		server.browser_session.get_dom_element_by_index = AsyncMock(
			return_value=_StubElement(42, 'Publish release', attrs={'disabled': '', 'aria-label': 'Publish release'})
		)
		server.tools = SimpleNamespace(act=AsyncMock(return_value=ActionResult(extracted_content='clicked')))
		server._update_session_activity = lambda *_args, **_kwargs: None

		result = await server._click(ref='e42')

		assert result.startswith('Error [target_disabled]:')

	async def test_extract_table_can_return_structured_json_without_llm(
		self, tools: Tools, browser_session: BrowserSession, base_url: str, tmp_path
	):
		await tools.navigate(url=f'{base_url}/table', new_tab=False, browser_session=browser_session)

		result = await tools.extract(
			query='extract the pricing table on the page',
			extract_links=False,
			output_schema={
				'type': 'object',
				'properties': {
					'columns': {'type': 'array', 'items': {'type': 'string'}},
					'rows': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'plan': {'type': 'string'},
								'price': {'type': 'string'},
								'sla': {'type': 'string'},
							},
						},
					},
				},
				'required': ['rows'],
			},
			browser_session=browser_session,
			page_extraction_llm=None,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		data = _extract_structured_json(result.extracted_content)
		assert result.error is None
		assert result.metadata['structured_extraction'] is True
		assert result.metadata['deterministic_extraction'] is True
		assert result.metadata['strategy'] == 'deterministic-tables'
		assert data['columns'] == ['Plan', 'Price', 'SLA']
		assert data['rows'][0]['plan'] == 'Starter plan'
		assert data['rows'][1]['price'] == '$99'

	async def test_extract_list_can_return_structured_json_without_llm(
		self, tools: Tools, browser_session: BrowserSession, base_url: str, tmp_path
	):
		await tools.navigate(url=f'{base_url}/list', new_tab=False, browser_session=browser_session)

		result = await tools.extract(
			query='list all checklist items on the page',
			extract_links=False,
			output_schema={
				'type': 'object',
				'properties': {
					'step_count': {'type': 'integer'},
					'steps': {'type': 'array', 'items': {'type': 'string'}},
				},
				'required': ['steps'],
			},
			browser_session=browser_session,
			page_extraction_llm=None,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		data = _extract_structured_json(result.extracted_content)
		assert result.error is None
		assert result.metadata['strategy'] == 'deterministic-lists'
		assert data['step_count'] == 3
		assert data['steps'][0] == 'Open the failing workflow run'
		assert data['steps'][2] == 'Update the incident channel'

	async def test_extract_form_fields_can_return_structured_json_without_llm(
		self, tools: Tools, browser_session: BrowserSession, base_url: str, tmp_path
	):
		await tools.navigate(url=f'{base_url}/form', new_tab=False, browser_session=browser_session)

		result = await tools.extract(
			query='list all form fields on the page',
			extract_links=False,
			output_schema={
				'type': 'object',
				'properties': {
					'field_count': {'type': 'integer'},
					'fields': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'name': {'type': 'string'},
								'type': {'type': 'string'},
								'required': {'type': 'boolean'},
								'options': {'type': 'array', 'items': {'type': 'string'}},
							},
						},
					},
				},
				'required': ['field_count', 'fields'],
			},
			browser_session=browser_session,
			page_extraction_llm=None,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		data = _extract_structured_json(result.extracted_content)
		assert result.error is None
		assert result.metadata['strategy'] == 'deterministic-form-fields'
		assert data['field_count'] == 4
		assert data['fields'][0]['name'] == 'Project name'
		assert data['fields'][0]['required'] is True
		assert data['fields'][2]['options'] == ['Preview', 'Production']

	async def test_incompatible_schema_returns_error_without_llm(
		self, tools: Tools, browser_session: BrowserSession, base_url: str, tmp_path
	):
		await tools.navigate(url=f'{base_url}/table', new_tab=False, browser_session=browser_session)

		result = await tools.extract(
			query='extract the pricing table on the page',
			extract_links=False,
			output_schema={
				'type': 'object',
				'properties': {'summary': {'type': 'string'}},
				'required': ['summary'],
			},
			browser_session=browser_session,
			page_extraction_llm=None,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		assert result.error is not None
		assert 'Deterministic structured extraction is not available' in result.error
		assert result.metadata is None

	async def test_non_deterministic_query_returns_explicit_error(
		self, tools: Tools, browser_session: BrowserSession, base_url: str, tmp_path
	):
		await tools.navigate(url=f'{base_url}/editor', new_tab=False, browser_session=browser_session)

		result = await tools.extract(
			query='summarize the page',
			browser_session=browser_session,
			page_extraction_llm=None,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		assert result.error is not None
		assert 'No deterministic extraction route matched this query' in result.error
		assert result.metadata is None
