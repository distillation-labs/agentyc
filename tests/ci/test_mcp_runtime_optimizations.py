import asyncio
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from bubus import EventBus
from pytest_httpserver import HTTPServer
from websockets.protocol import State

from agentyc.actions import ActionResult
from agentyc.browser import BrowserProfile, BrowserSession, session_navigation, session_runtime
from agentyc.browser.events import BrowserLaunchEvent, BrowserStartEvent, NavigateToUrlEvent, RefreshEvent
from agentyc.browser.session_models import BrowserWindowBounds, RuntimeOwnershipMetadata
from agentyc.browser.views import TabInfo
from agentyc.browser.watchdogs.default_action_navigation import DefaultActionNavigationMixin
from agentyc.dom.serializer.serializer import DOMTreeSerializer
from agentyc.dom.service import DomService
from agentyc.dom.views import DOMRect, EnhancedAXNode, EnhancedDOMTreeNode, EnhancedSnapshotNode, NodeType
from agentyc.filesystem.file_system import FileSystem
from agentyc.mcp.server import AgentycServer
from agentyc.mcp.state import build_browser_state_payload, parse_element_ref
from agentyc.tools.extraction.projection import build_table_structured_payload
from agentyc.tools.javascript import validate_and_fix_javascript
from agentyc.tools.service import Tools

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


def _stub_state(
	selector_map: dict[int, _StubElement] | None = None,
	*,
	tabs: list[object] | None = None,
	current_tab_id: str | None = None,
):
	return SimpleNamespace(
		url='https://example.com',
		title='Example page',
		tabs=tabs or [SimpleNamespace(url='https://example.com', title='Example page')],
		current_tab_id=current_tab_id,
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
	# Chrome shared-tab navigation is measurably more stable against the IPv4
	# loopback address than the hostname alias on macOS CI.
	return f'http://127.0.0.1:{http_server.port}'


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
	def test_browser_startup_timeouts_allow_slower_stdio_cold_start(self):
		assert BrowserLaunchEvent().event_timeout == 60.0
		assert BrowserStartEvent().event_timeout == 90.0

	def test_validate_and_fix_javascript_preserves_escaped_selector_quotes(self):
		code = (
			'(function(){ var el=document.querySelector("button[aria-label=\\"Publish comment\\"]"); '
			'if(!el) return null; return el.getAttribute("aria-label"); })()'
		)

		result = validate_and_fix_javascript(code)

		assert 'querySelector("button[aria-label=\\"Publish comment\\"]")' in result

	async def test_init_browser_session_clears_partial_runtime_after_start_failure(self):
		server = AgentycServer()
		broken_session = SimpleNamespace(
			id='session-broken', start=AsyncMock(side_effect=TimeoutError('startup timed out')), kill=AsyncMock()
		)

		with patch('agentyc.browser.BrowserProfile', side_effect=lambda **kwargs: SimpleNamespace(**kwargs)):
			with patch('agentyc.browser.BrowserSession', return_value=broken_session):
				with patch('agentyc.config.get_default_profile', return_value={}):
					with pytest.raises(TimeoutError, match='startup timed out'):
						await server._init_browser_session()

		broken_session.kill.assert_awaited_once()
		assert server.browser_session is None
		assert server.tools is None
		assert server.file_system is None

	async def test_init_browser_session_reuses_registered_local_browser(self):
		server = AgentycServer()
		attached_session = SimpleNamespace(
			id='session-shared',
			start=AsyncMock(),
			create_collaborative_page=AsyncMock(return_value=SimpleNamespace(_target_id='target-shared')),
			event_bus=SimpleNamespace(dispatch=AsyncMock()),
			browser_profile=SimpleNamespace(cdp_url='http://127.0.0.1:9222/', keep_alive=True),
		)

		with patch.dict(os.environ, {'AGENTYC_REUSE_LOCAL_BROWSER': 'true'}, clear=False):
			with patch('agentyc.config.get_default_profile', return_value={'headless': True}):
				with patch(
					'agentyc.mcp.shared_browser_registry.get_reusable_local_browser_cdp_url',
					new=AsyncMock(return_value='http://127.0.0.1:9222/'),
				) as reusable_cdp_url:
					with patch(
						'agentyc.browser.BrowserProfile', side_effect=lambda **kwargs: SimpleNamespace(**kwargs)
					) as browser_profile:
						with patch('agentyc.browser.BrowserSession', return_value=attached_session):
							await server._init_browser_session()

		reusable_cdp_url.assert_awaited_once_with(headless=True)
		profile_kwargs = browser_profile.call_args.kwargs
		assert profile_kwargs['cdp_url'] == 'http://127.0.0.1:9222/'
		assert profile_kwargs['keep_alive'] is True
		assert profile_kwargs['shared_browser_mode'] == 'tab'
		assert profile_kwargs['shared_browser_focus_policy'] == 'preserve'
		attached_session.start.assert_awaited_once()
		assert server._cdp_url == 'http://127.0.0.1:9222/'

	async def test_init_browser_session_does_not_register_local_browser_for_reuse_by_default(self):
		server = AgentycServer()
		fresh_session = SimpleNamespace(
			id='session-local',
			start=AsyncMock(),
			browser_profile=SimpleNamespace(cdp_url='http://127.0.0.1:9333/', keep_alive=True, headless=True, user_data_dir=None),
			_local_browser_watchdog=SimpleNamespace(browser_pid=1234, _owns_browser_resources=True),
		)

		with patch('agentyc.config.get_default_profile', return_value={'headless': True}):
			with patch(
				'agentyc.mcp.shared_browser_registry.get_reusable_local_browser_cdp_url',
				new=AsyncMock(return_value=None),
			):
				with patch('agentyc.browser.BrowserProfile', side_effect=lambda **kwargs: SimpleNamespace(**kwargs)) as browser_profile:
					with patch('agentyc.browser.BrowserSession', return_value=fresh_session):
						with patch('agentyc.mcp.shared_browser_registry.register_local_shared_browser') as register_shared:
							await server._init_browser_session(headless=True, user_data_dir=None)

		profile_kwargs = browser_profile.call_args.kwargs
		assert profile_kwargs['keep_alive'] is False
		assert 'shared_browser_mode' not in profile_kwargs
		register_shared.assert_not_called()
		assert fresh_session._local_browser_watchdog._owns_browser_resources is True

	async def test_init_browser_session_does_not_reuse_registered_local_browser_by_default(self):
		server = AgentycServer()
		local_session = SimpleNamespace(
			id='session-local',
			start=AsyncMock(),
			browser_profile=SimpleNamespace(cdp_url='http://127.0.0.1:9333/', keep_alive=False),
		)

		with patch.dict(os.environ, {}, clear=False):
			os.environ.pop('AGENTYC_REUSE_LOCAL_BROWSER', None)
			with patch('agentyc.config.get_default_profile', return_value={'headless': True}):
				with patch(
					'agentyc.mcp.shared_browser_registry.get_reusable_local_browser_cdp_url',
					new=AsyncMock(return_value='http://127.0.0.1:9222/'),
				) as reusable_cdp_url:
					with patch(
						'agentyc.browser.BrowserProfile', side_effect=lambda **kwargs: SimpleNamespace(**kwargs)
					) as browser_profile:
						with patch('agentyc.browser.BrowserSession', return_value=local_session):
							await server._init_browser_session()

		reusable_cdp_url.assert_not_awaited()
		profile_kwargs = browser_profile.call_args.kwargs
		assert profile_kwargs.get('cdp_url') is None
		assert profile_kwargs['keep_alive'] is False

	async def test_execute_tool_reinitializes_unhealthy_cached_browser_runtime(self):
		server = AgentycServer()
		server.browser_session = SimpleNamespace(is_cdp_connected=False)
		server.tools = None
		server._reset_broken_browser_runtime = AsyncMock()
		server._init_browser_session = AsyncMock()
		server._navigate = AsyncMock(return_value='Navigated to: https://example.com')

		result = await server._execute_tool('browser_navigate', {'url': 'https://example.com'})

		assert result == 'Navigated to: https://example.com'
		server._reset_broken_browser_runtime.assert_awaited_once()
		server._init_browser_session.assert_awaited_once()
		server._navigate.assert_awaited_once_with('https://example.com', False)

	async def test_close_session_stops_keep_alive_runtime_without_killing_browser(self):
		server = AgentycServer()
		shared_session = SimpleNamespace(
			id='session-shared',
			_browser_context_id='context-123',
			_cdp_client_root=SimpleNamespace(
				send=SimpleNamespace(Target=SimpleNamespace(disposeBrowserContext=AsyncMock(return_value={})))
			),
			browser_profile=SimpleNamespace(keep_alive=True),
			stop=AsyncMock(),
			kill=AsyncMock(),
		)
		server.browser_session = shared_session
		server.tools = SimpleNamespace()
		server.active_sessions[shared_session.id] = {
			'session': shared_session,
			'created_at': 0.0,
			'last_activity': 0.0,
			'url': 'about:blank',
		}

		result = await server._close_session(shared_session.id)

		assert 'Successfully closed session' in result
		shared_session.stop.assert_awaited_once()
		shared_session.kill.assert_not_called()
		assert shared_session._browser_context_id is None
		assert server.browser_session is None

	async def test_session_kill_preserves_local_watchdog_cleanup_after_stop_reset(self):
		class _AwaitableEvent:
			def __init__(self, on_await=None):
				self._on_await = on_await

			def __await__(self):
				async def _runner():
					if self._on_await is not None:
						self._on_await()
					return None

				return _runner().__await__()

		local_watchdog = SimpleNamespace(
			_subprocess=object(),
			_owns_browser_resources=True,
			on_BrowserKillEvent=AsyncMock(),
		)
		session = SimpleNamespace(
			_intentional_stop=False,
			logger=SimpleNamespace(debug=lambda *_args, **_kwargs: None),
			_local_browser_watchdog=local_watchdog,
		)

		def dispatch(event):
			if type(event).__name__ == 'BrowserStopEvent':
				return _AwaitableEvent(lambda: setattr(session, '_local_browser_watchdog', None))
			return _AwaitableEvent()

		stop_mock = AsyncMock()
		session.event_bus = SimpleNamespace(dispatch=dispatch, stop=stop_mock)

		with patch('agentyc.browser.session_runtime.reset', new=AsyncMock()):
			with patch('agentyc.browser.session_runtime.EventBus', return_value=SimpleNamespace()):
				await session_runtime.kill(session)

		local_watchdog.on_BrowserKillEvent.assert_awaited_once()
		stop_mock.assert_awaited_once_with(clear=True, timeout=5)

	async def test_second_server_reuses_existing_local_browser_without_explicit_cdp_url(self, monkeypatch):
		from agentyc.mcp.shared_browser_registry import clear_registered_local_shared_browser

		monkeypatch.setenv('AGENTYC_REUSE_LOCAL_BROWSER', '1')
		clear_registered_local_shared_browser()
		primary = AgentycServer()
		secondary = AgentycServer()
		launched_process = None

		try:
			with patch.dict(os.environ, {'AGENTYC_REUSE_LOCAL_BROWSER': 'true'}, clear=False):
				await primary._init_browser_session(headless=True, user_data_dir=None)
				primary_watchdog = getattr(primary.browser_session, '_local_browser_watchdog', None)
				launched_process = getattr(primary_watchdog, '_subprocess', None)
				primary_cdp_url = primary.browser_session.browser_profile.cdp_url
				assert primary_cdp_url

				await secondary._init_browser_session(headless=True, user_data_dir=None)
				secondary_cdp_url = secondary.browser_session.browser_profile.cdp_url
				assert secondary_cdp_url == primary_cdp_url
				assert secondary.browser_session.id != primary.browser_session.id
				assert secondary.browser_session.agent_focus_target_id != primary.browser_session.agent_focus_target_id
		finally:
			await secondary._shutdown()
			await primary._shutdown()
			if launched_process is not None:
				from agentyc.browser.watchdogs.local_browser_watchdog import LocalBrowserWatchdog

				await LocalBrowserWatchdog._cleanup_process(launched_process)
			clear_registered_local_shared_browser()

	async def test_get_state_forwards_include_screenshot_flag(self):
		server = AgentycServer()
		server.browser_session = AsyncMock()
		server.browser_session.get_browser_state_summary = AsyncMock(return_value=_stub_state())

		state_json, screenshot_b64 = await server._get_browser_state(include_screenshot=False)

		server.browser_session.get_browser_state_summary.assert_awaited_once_with(
			include_screenshot=False,
			include_recent_events=True,
		)
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
		server = AgentycServer()
		server.browser_session = AsyncMock()
		server.browser_session.id = 'session-1'
		server.browser_session.take_screenshot = AsyncMock(return_value=b'png-data')
		server.browser_session.get_browser_state_summary = AsyncMock(return_value=_stub_state())
		server._update_session_activity = lambda *_args, **_kwargs: None

		meta_json, image_b64 = await server._screenshot(full_page=False)

		server.browser_session.get_browser_state_summary.assert_awaited_once_with(include_screenshot=False)
		assert json.loads(meta_json)['size_bytes'] == len(b'png-data')
		assert image_b64

	def test_browser_state_payload_includes_trimmed_debug_surface(self):
		state = _stub_state()
		state.browser_errors = ['Primary renderer error', 'Follow-up warning']
		state.pending_network_requests = [
			SimpleNamespace(
				url='https://example.com/api/releases?cursor=abc123',
				method='GET',
				loading_duration_ms=187.65,
				resource_type='Fetch',
			)
		]
		state.recent_events = json.dumps(
			[
				{
					'event_type': 'NavigateEvent',
					'timestamp': '2026-05-15T18:00:00Z',
					'url': 'https://example.com/releases',
				},
				{
					'event_type': 'ClickEvent',
					'timestamp': '2026-05-15T18:00:01Z',
					'error_message': 'Button intercepted',
				},
			]
		)
		state.closed_popup_messages = ['Leave site?', 'Unsaved changes']

		payload = build_browser_state_payload(state, mode='min')

		assert payload['debug']['browser_errors'] == ['Primary renderer error', 'Follow-up warning']
		assert payload['debug']['pending_network_requests'][0]['resource_type'] == 'Fetch'
		assert payload['debug']['pending_network_requests'][0]['loading_duration_ms'] == 187.7
		assert payload['debug']['recent_events'][0]['event_type'] == 'NavigateEvent'
		assert payload['debug']['recent_events'][1]['error_message'] == 'Button intercepted'
		assert payload['debug']['closed_popup_messages'] == ['Leave site?', 'Unsaved changes']

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

	async def test_navigate_recovers_focus_before_reporting_browser_not_connected(self):
		session = BrowserSession(browser_profile=BrowserProfile(headless=True))
		session.agent_focus_target_id = None
		session.session_manager = SimpleNamespace(
			ensure_valid_focus=AsyncMock(side_effect=self._recover_focus_for_navigation(session)),
			get_target=lambda _target_id: SimpleNamespace(url='about:blank'),
		)
		session.event_bus = EventBus()
		session.event_bus.dispatch = lambda _event: _CompletedEvent()  # type: ignore[method-assign]
		fake_send = SimpleNamespace(
			Page=SimpleNamespace(navigate=AsyncMock(return_value={'loaderId': 'loader-1'})),
			Runtime=SimpleNamespace(
				evaluate=AsyncMock(
					return_value={'result': {'value': {'readyState': 'complete', 'url': 'http://example.com/recovered'}}}
				)
			),
		)
		fake_cdp_session = SimpleNamespace(
			session_id='session-1',
			target_id='target-1',
			cdp_client=SimpleNamespace(send=fake_send),
			_lifecycle_events=[],
		)

		with patch.object(BrowserSession, 'get_or_create_cdp_session', new=AsyncMock(return_value=fake_cdp_session)):
			await session.on_NavigateToUrlEvent(
				NavigateToUrlEvent(url='http://example.com/recovered', new_tab=False, wait_until='load')
			)

		session.session_manager.ensure_valid_focus.assert_awaited_once_with(timeout=3.0)
		assert session.agent_focus_target_id == 'target-1'
		fake_send.Page.navigate.assert_awaited_once()

	@staticmethod
	def _recover_focus_for_navigation(session: BrowserSession):
		async def _recover(*, timeout: float) -> bool:
			session.agent_focus_target_id = 'target-1'
			return True

		return _recover

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
		server = AgentycServer()
		server.browser_session = AsyncMock()
		server.tools = AsyncMock()
		server.tools.act = AsyncMock(return_value=ActionResult(extracted_content='done'))
		server.file_system = FileSystem(base_dir=str(tmp_path))

		result = await server._extract_content('list all links on the page', extract_links=True)

		assert result == 'done'
		server.browser_session.get_browser_state_summary.assert_not_called()

	async def test_extract_content_forwards_output_schema(self, tmp_path):
		server = AgentycServer()
		server.browser_session = AsyncMock()
		server.tools = AsyncMock()
		server.tools.act = AsyncMock(return_value=ActionResult(extracted_content='done'))
		server.file_system = FileSystem(base_dir=str(tmp_path))
		schema = {'type': 'object', 'properties': {'rows': {'type': 'array', 'items': {'type': 'object'}}}}

		await server._extract_content('extract the pricing table on the page', output_schema=schema)

		action = server.tools.act.await_args.kwargs['action']
		assert action.model_dump(mode='python')['extract']['output_schema'] == schema

	async def test_click_and_type_accept_refs(self):
		server = AgentycServer()
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

	async def test_navigate_new_tab_accepts_reused_blank_tab(self):
		server = AgentycServer()
		server.browser_session = AsyncMock()
		server.browser_session.id = 'session-1'
		server.browser_session.get_tabs = AsyncMock(return_value=[TabInfo(tab_id='blank1', url='about:blank', title='')])
		server.browser_session.agent_focus_target_id = 'blank1'
		server.tools = SimpleNamespace(act=AsyncMock(return_value=ActionResult(extracted_content='opened')))
		server._update_session_activity = lambda *_args, **_kwargs: None

		result = await server._navigate('https://example.com/next', new_tab=True)

		assert result == 'Opened new tab with URL: https://example.com/next'

	async def test_click_new_tab_accepts_reused_blank_tab(self):
		server = AgentycServer()
		server.browser_session = AsyncMock()
		server.browser_session.id = 'session-1'
		server.browser_session.get_dom_element_by_index = AsyncMock(
			return_value=_StubElement(42, 'Documentation', tag='a', attrs={'href': '/docs'})
		)
		server.browser_session.get_current_page_url = AsyncMock(return_value='https://example.com/current')
		server.browser_session.get_tabs = AsyncMock(
			return_value=[
				TabInfo(tab_id='cur1', url='https://example.com/current', title='Current'),
				TabInfo(tab_id='blank1', url='about:blank', title=''),
			]
		)
		server.browser_session.agent_focus_target_id = 'blank1'
		server.tools = SimpleNamespace(act=AsyncMock(return_value=ActionResult(extracted_content='opened')))
		server._update_session_activity = lambda *_args, **_kwargs: None

		result = await server._click(ref='e42', new_tab=True)

		assert result == 'Clicked element e42 and opened in new tab https://example.com/...'

	async def test_extract_content_appends_extraction_metadata(self, tmp_path):
		server = AgentycServer()
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
		server = AgentycServer()
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

	async def test_close_tab_waits_for_target_list_to_reflect_detach(self):
		server = AgentycServer()
		server.browser_session = AsyncMock()
		server.browser_session.get_target_id_from_tab_id = AsyncMock(return_value='target-2')
		server.browser_session.event_bus = SimpleNamespace(dispatch=lambda _event: _CompletedEvent())
		server.browser_session.get_tabs = AsyncMock(
			side_effect=[
				[
					TabInfo(tab_id='target-1', url='https://example.com/1', title='One'),
					TabInfo(tab_id='target-2', url='https://example.com/2', title='Two'),
				],
				[TabInfo(tab_id='target-1', url='https://example.com/1', title='One')],
			]
		)
		server.browser_session.get_current_page_url = AsyncMock(return_value='https://example.com/1')

		result = await server._close_tab('2')

		assert result == 'Closed tab # 2, now on https://example.com/1'
		assert server.browser_session.get_tabs.await_count == 2

	async def test_close_tab_returns_postcondition_error_when_target_never_disappears(self):
		server = AgentycServer()
		server.browser_session = AsyncMock()
		server.browser_session.get_target_id_from_tab_id = AsyncMock(return_value='target-2')
		server.browser_session.event_bus = SimpleNamespace(dispatch=lambda _event: _CompletedEvent())
		server.browser_session.get_tabs = AsyncMock(
			return_value=[
				TabInfo(tab_id='target-1', url='https://example.com/1', title='One'),
				TabInfo(tab_id='target-2', url='https://example.com/2', title='Two'),
			]
		)

		result = await server._close_tab('2')

		assert result.startswith('Error [postcondition_failed]:')
		assert 'Close tab 2 completed but the tab is still present.' in result

	async def test_switch_tab_returns_action_error_when_runtime_does_not_own_target(self):
		server = AgentycServer()
		server.browser_session = AsyncMock()
		server.browser_session.get_target_id_from_tab_id = AsyncMock(return_value='target-peer')

		class _DeniedEvent(_CompletedEvent):
			async def event_result(self, *args, **kwargs):
				raise RuntimeError('Cannot switch to tab peer: it is owned by Peer runtime')

		server.browser_session.event_bus = SimpleNamespace(dispatch=lambda _event: _DeniedEvent())

		result = await server._switch_tab('peer')

		assert result.startswith('Error [action_failed]:')
		assert 'owned by Peer runtime' in result

	async def test_close_tab_returns_action_error_when_runtime_does_not_own_target(self):
		server = AgentycServer()
		server.browser_session = AsyncMock()
		server.browser_session.get_target_id_from_tab_id = AsyncMock(return_value='target-peer')

		class _DeniedEvent(_CompletedEvent):
			async def event_result(self, *args, **kwargs):
				raise RuntimeError('Cannot close tab peer: it is owned by Peer runtime')

		server.browser_session.event_bus = SimpleNamespace(dispatch=lambda _event: _DeniedEvent())

		result = await server._close_tab('peer')

		assert result.startswith('Error [action_failed]:')
		assert 'owned by Peer runtime' in result

	async def test_close_tab_returns_action_error_when_tab_is_human_owned(self):
		server = AgentycServer()
		server.browser_session = AsyncMock()
		server.browser_session.get_target_id_from_tab_id = AsyncMock(return_value='target-human')

		class _DeniedEvent(_CompletedEvent):
			async def event_result(self, *args, **kwargs):
				raise RuntimeError('Cannot close tab human: it is owned by Human')

		server.browser_session.event_bus = SimpleNamespace(dispatch=lambda _event: _DeniedEvent())

		result = await server._close_tab('human')

		assert result.startswith('Error [action_failed]:')
		assert 'owned by Human' in result

	async def test_wait_for_network_idle_uses_pending_requests_until_loading_finishes(self):
		server = AgentycServer()
		server.browser_session = AsyncMock()
		server.browser_session.get_or_create_cdp_session = AsyncMock(return_value=SimpleNamespace())
		server._network_pending = {'req-1': {'request_id': 'req-1'}}

		async def _finish_request():
			await asyncio.sleep(0.05)
			server._network_pending.pop('req-1', None)

		finish_task = asyncio.create_task(_finish_request())
		started = asyncio.get_event_loop().time()
		result = await server._wait_for_network_idle(timeout_seconds=1.0, idle_duration_ms=100)
		elapsed = asyncio.get_event_loop().time() - started
		await finish_task

		assert result.startswith('Network idle after ')
		assert elapsed < 0.4

	async def test_wait_for_network_idle_ignores_requests_that_started_before_wait(self):
		server = AgentycServer()
		server.browser_session = AsyncMock()
		server.browser_session.get_or_create_cdp_session = AsyncMock(return_value=SimpleNamespace())
		server._network_pending = {'req-stale': {'request_id': 'req-stale', 'start_time': time.time() - 5.0}}

		started = asyncio.get_event_loop().time()
		result = await server._wait_for_network_idle(timeout_seconds=1.0, idle_duration_ms=100)
		elapsed = asyncio.get_event_loop().time() - started

		assert result.startswith('Network idle after ')
		assert elapsed < 0.3

	async def test_list_sessions_uses_connection_flag_without_touching_cdp_client_property(self):
		server = AgentycServer()
		connected_session = BrowserSession(headless=True, user_data_dir=None)
		connected_session._cdp_client_root = SimpleNamespace(ws=SimpleNamespace(state=State.OPEN))

		uninitialized_session = BrowserSession(headless=True, user_data_dir=None)
		server.active_sessions = {
			'session-open': {
				'session': connected_session,
				'created_at': 1000.0,
				'last_activity': 1060.0,
				'url': 'https://example.com/open',
			},
			'session-closed': {
				'session': uninitialized_session,
				'created_at': 1000.0,
				'last_activity': 1060.0,
				'url': 'https://example.com/closed',
			},
		}

		result = json.loads(await server._list_sessions())

		assert [entry['active'] for entry in result] == [True, False]

	async def test_scroll_reports_error_when_gesture_fails(self):
		watchdog = DefaultActionNavigationMixin()
		watchdog.browser_session = SimpleNamespace(agent_focus_target_id='target-1', _dom_watchdog=None)
		watchdog.logger = SimpleNamespace(debug=lambda *_args, **_kwargs: None)
		watchdog._scroll_with_cdp_gesture = AsyncMock(return_value=False)

		with pytest.raises(Exception, match='Failed to scroll page via CDP gesture'):
			await watchdog.on_ScrollEvent(SimpleNamespace(direction='down', amount=400, node=None))

	async def test_refresh_waits_for_navigation_readiness_instead_of_fixed_sleep(self):
		watchdog = DefaultActionNavigationMixin()
		fake_cdp_session = SimpleNamespace(
			target_id='target-1',
			session_id='session-1',
			cdp_client=SimpleNamespace(send=SimpleNamespace(Page=SimpleNamespace(reload=AsyncMock(return_value={})))),
		)
		watchdog.browser_session = SimpleNamespace(
			agent_focus_target_id='target-1',
			session_manager=SimpleNamespace(get_target=lambda _target_id: SimpleNamespace(url='https://example.com/current')),
			get_current_page_url=AsyncMock(return_value='https://example.com/current'),
			get_or_create_cdp_session=AsyncMock(return_value=fake_cdp_session),
		)
		watchdog.logger = SimpleNamespace(info=lambda *_args, **_kwargs: None)

		with patch.object(session_navigation, '_navigate_and_wait', new=AsyncMock()) as navigate_and_wait:
			await watchdog.on_RefreshEvent(RefreshEvent())

		fake_cdp_session.cdp_client.send.Page.reload.assert_awaited_once_with(session_id='session-1')
		navigate_and_wait.assert_awaited_once_with(
			watchdog.browser_session,
			'https://example.com/current',
			'target-1',
			timeout=3.0,
			wait_until='load',
			nav_timeout=8.0,
		)

	async def test_network_log_entries_finalize_on_loading_finished_and_failures(self):
		server = AgentycServer()
		captured: dict[str, object] = {}

		def _capture_request(handler):
			captured['request'] = handler

		def _capture_response(handler):
			captured['response'] = handler

		def _capture_finished(handler):
			captured['finished'] = handler

		def _capture_failed(handler):
			captured['failed'] = handler

		server.browser_session = AsyncMock()
		server.browser_session.get_or_create_cdp_session = AsyncMock(
			return_value=SimpleNamespace(
				session_id='session-1',
				target_id='target-1',
				cdp_client=SimpleNamespace(
					register=SimpleNamespace(
						Runtime=SimpleNamespace(
							consoleAPICalled=lambda *_args, **_kwargs: None,
							exceptionThrown=lambda *_args, **_kwargs: None,
						),
						Network=SimpleNamespace(
							requestWillBeSent=_capture_request,
							responseReceived=_capture_response,
							loadingFinished=_capture_finished,
							loadingFailed=_capture_failed,
						),
					),
					send=SimpleNamespace(Runtime=SimpleNamespace(enable=AsyncMock(return_value={}))),
				),
			)
		)
		server.browser_session.session_manager = None

		await server._register_cdp_event_listeners()

		request = captured['request']
		response = captured['response']
		finished = captured['finished']
		failed = captured['failed']

		request(
			{
				'requestId': 'req-1',
				'request': {'url': 'https://example.com/api/data', 'method': 'GET'},
				'type': 'Fetch',
				'timestamp': 10.0,
			},
			'session-1',
		)
		response(
			{
				'requestId': 'req-1',
				'response': {'status': 200, 'statusText': 'OK'},
				'timestamp': 10.1,
			},
			'session-1',
		)
		assert 'req-1' in server._network_pending
		finished({'requestId': 'req-1', 'timestamp': 10.25}, 'session-1')

		request(
			{
				'requestId': 'req-2',
				'request': {'url': 'https://example.com/api/submit', 'method': 'POST'},
				'type': 'Fetch',
				'timestamp': 20.0,
			},
			'session-1',
		)
		failed({'requestId': 'req-2', 'errorText': 'net::ERR_FAILED', 'timestamp': 20.2}, 'session-1')

		log_entries = json.loads(await server._get_network_log(max_entries=10))

		assert [entry['url'] for entry in log_entries] == [
			'https://example.com/api/data',
			'https://example.com/api/submit',
		]
		assert log_entries[0]['status'] == 200
		assert log_entries[0]['status_text'] == 'OK'
		assert log_entries[0]['duration_ms'] == 250.0
		assert log_entries[1]['error'] == 'net::ERR_FAILED'


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

	def test_auto_mode_compacts_medium_pages_without_dropping_elements(self):
		selector_map = {
			1: _StubElement(1, 'Project name', tag='input', attrs={'placeholder': 'Project name', 'type': 'text'}),
			2: _StubElement(2, 'Repository URL', tag='input', attrs={'placeholder': 'Repository URL', 'type': 'url'}),
			3: _StubElement(3, 'Environment', tag='select', attrs={'aria-label': 'Environment'}),
			4: _StubElement(4, 'Deploy preview', tag='button', role='button'),
			5: _StubElement(5, 'Open validation help', tag='a', attrs={'href': '/help'}, role='link'),
			6: _StubElement(6, 'Enable notifications', tag='input', attrs={'type': 'checkbox'}, role='checkbox'),
			7: _StubElement(7, 'Webhook URL', tag='input', attrs={'placeholder': 'Webhook URL', 'type': 'url'}),
			8: _StubElement(8, 'Retry failed jobs', tag='button', role='button'),
			9: _StubElement(9, 'Cancel', tag='button', role='button'),
			10: _StubElement(10, 'Open docs', tag='a', attrs={'href': '/docs'}, role='link'),
		}

		payload = build_browser_state_payload(_stub_state(selector_map=selector_map), mode='auto')

		assert payload['effective_mode'] == 'min'
		assert len(payload['interactive_elements']) == 10
		assert 'interactive_elements_truncated' not in payload
		assert all('index' not in element for element in payload['interactive_elements'])

	def test_table_projection_supports_issue_queue_schema_aliases(self):
		payload = build_table_structured_payload(
			tables=[
				{
					'columns': ['Title', 'Severity', 'Status'],
					'rows': [
						{'Title': 'Build #482 failing on main', 'Severity': 'Critical', 'Status': 'Open'},
						{'Title': 'OAuth callback latency spike', 'Severity': 'High', 'Status': 'Investigating'},
					],
				}
			],
			output_schema={
				'type': 'object',
				'properties': {
					'issue_count': {'type': 'integer'},
					'issues': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'title': {'type': 'string'},
								'severity': {'type': 'string'},
								'status': {'type': 'string'},
							},
						},
					},
				},
			},
		)

		assert payload == {
			'issue_count': 2,
			'issues': [
				{'title': 'Build #482 failing on main', 'severity': 'Critical', 'status': 'Open'},
				{'title': 'OAuth callback latency spike', 'severity': 'High', 'status': 'Investigating'},
			],
		}

	async def test_browser_get_state_min_focus_and_delta_modes(self, browser_session: BrowserSession, base_url: str):
		await browser_session.navigate_to(f'{base_url}/accessible')

		server = AgentycServer()
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
		assert unchanged_payload['current_tab_id'] == payload['current_tab_id']
		assert 'current_tab' not in unchanged_payload
		assert 'tabs' not in unchanged_payload
		assert 'viewport' not in unchanged_payload
		assert 'page' not in unchanged_payload
		assert 'scroll' not in unchanged_payload

	def test_browser_state_payload_uses_metadata_only_fast_path_for_unchanged_since_hash(self):
		state = _stub_state(
			tabs=[
				TabInfo(
					target_id='target-owned-1234',
					url='https://example.com',
					title='Example page',
					display_title='[Agent 1234] Example page',
					ownership={
						'target_id': 'target-owned-1234',
						'owner_kind': 'agent',
						'source': 'current_runtime',
						'display_label': 'Agent 1234',
						'runtime': RuntimeOwnershipMetadata.create(
							session_id='session-1234',
							runtime_id='runtime-1234',
							runtime_label='Agent 1234',
						),
					},
					window_bounds=BrowserWindowBounds(left=10, top=20, width=1200, height=800),
				)
			],
			current_tab_id='target-owned-1234',
		)
		state_hash = build_browser_state_payload(state, mode='min')['state_hash']

		payload = build_browser_state_payload(state, mode='min', since_hash=state_hash)

		assert payload == {
			'url': 'https://example.com',
			'title': 'Example page',
			'mode': 'min',
			'effective_mode': 'min',
			'state_hash': state_hash,
			'changed': False,
			'interactive_element_count': 2,
			'interactive_elements': [],
			'current_tab_id': '1234',
			'scroll': {'x': 0, 'y': 320},
		}

	async def test_browser_get_state_auto_uses_full_on_small_pages(self, browser_session: BrowserSession, base_url: str):
		await browser_session.navigate_to(f'{base_url}/accessible')

		server = AgentycServer()
		server.browser_session = browser_session

		state_json, _ = await server._get_browser_state()
		payload = json.loads(state_json)

		assert payload['mode'] == 'auto'
		assert payload['effective_mode'] == 'full'
		assert len(payload['interactive_elements']) == payload['interactive_element_count']

	def test_browser_state_payload_includes_collaboration_metadata_for_current_tab(self):
		runtime = RuntimeOwnershipMetadata.create(
			session_id='session-1234',
			runtime_id='runtime-1234',
			runtime_label='Agent 1234',
		)
		other_runtime = RuntimeOwnershipMetadata.create(
			session_id='session-5678',
			runtime_id='runtime-5678',
			runtime_label='Agent 5678',
		)
		payload = build_browser_state_payload(
			_stub_state(
				tabs=[
					TabInfo(
						target_id='target-owned-1234',
						url='https://example.com',
						title='Example page',
						display_title=f'{runtime.title_prefix}Example page',
						ownership={
							'target_id': 'target-owned-1234',
							'owner_kind': 'agent',
							'source': 'current_runtime',
							'display_label': runtime.runtime_label,
							'runtime': runtime,
						},
						window_bounds=BrowserWindowBounds(left=10, top=20, width=1200, height=800),
					),
					TabInfo(
						target_id='target-owned-5678',
						url='https://example.com/status',
						title='Status page',
						display_title=f'{other_runtime.title_prefix}Status page',
						ownership={
							'target_id': 'target-owned-5678',
							'owner_kind': 'runtime',
							'source': 'detected_runtime',
							'display_label': other_runtime.runtime_label,
							'runtime': other_runtime,
						},
					),
				],
				current_tab_id='target-owned-1234',
			),
			mode='min',
		)

		assert payload['tabs'][0]['tab_id'] == '1234'
		assert payload['tabs'][0]['ownership']['owner_kind'] == 'agent'
		assert payload['tabs'][0]['ownership']['runtime']['runtime_id'] == runtime.runtime_id
		assert payload['tabs'][0]['window_bounds']['width'] == 1200
		assert payload['current_tab_id'] == '1234'
		assert payload['current_tab']['tab_id'] == '1234'
		assert payload['current_tab']['display_title'].startswith('[Agent 1234]')
		assert payload['current_tab']['window_bounds']['height'] == 800
		assert 'url' not in payload['current_tab']
		assert 'title' not in payload['current_tab']
		assert payload['ownership']['runtime']['runtime_id'] == runtime.runtime_id
		assert payload['runtime']['runtime_id'] == runtime.runtime_id

	async def test_browser_list_tabs_includes_collaboration_metadata(self):
		runtime = RuntimeOwnershipMetadata.create(
			session_id='session-1234',
			runtime_id='runtime-1234',
			runtime_label='Agent 1234',
		)
		server = AgentycServer()
		server.browser_session = AsyncMock()
		server.browser_session.get_tabs = AsyncMock(
			return_value=[
				TabInfo(
					target_id='target-owned-1234',
					url='https://example.com',
					title='Example page',
					display_title=f'{runtime.title_prefix}Example page',
					ownership={
						'target_id': 'target-owned-1234',
						'owner_kind': 'agent',
						'source': 'current_runtime',
						'display_label': runtime.runtime_label,
						'runtime': runtime,
					},
					window_bounds=BrowserWindowBounds(left=10, top=20, width=1200, height=800),
				)
			]
		)

		result = json.loads(await server._list_tabs())

		assert result[0]['tab_id'] == '1234'
		assert result[0]['display_title'].startswith('[Agent 1234]')
		assert result[0]['ownership']['owner_kind'] == 'agent'
		assert result[0]['ownership']['runtime']['runtime_id'] == runtime.runtime_id
		assert result[0]['window_bounds']['left'] == 10

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
		assert 'No deterministic extraction route matched' in result.error
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
		assert 'No deterministic extraction route matched' in result.error
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

		server = AgentycServer()
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

		server = AgentycServer()
		server.browser_session = browser_session

		state_json, _ = await server._get_browser_state(mode='auto')
		payload = json.loads(state_json)
		frame_input_elements = [element for element in payload['interactive_elements'] if element.get('text') == 'Frame input']

		assert len(frame_input_elements) == 1
		assert frame_input_elements[0]['tag'] == 'input'

	async def test_browser_find_elements_uses_accessible_names_for_form_controls(
		self, browser_session: BrowserSession, base_url: str
	):
		await browser_session.navigate_to(f'{base_url}/form')

		server = AgentycServer()
		server.browser_session = browser_session
		server.tools = Tools()
		server._update_session_activity = lambda *_args, **_kwargs: None

		find_result = await server._find_elements('input, select, button', max_results=10)

		assert 'Project name' in find_result
		assert 'Repository URL' in find_result
		assert 'Deploy preview' in find_result

	async def test_public_mcp_contenteditable_workflow(self, browser_session: BrowserSession, base_url: str):
		await browser_session.navigate_to(f'{base_url}/editor')

		server = AgentycServer()
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

		server = AgentycServer()
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

		server = AgentycServer()
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

		server = AgentycServer()
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
		upload_path.write_text('agentyc upload fixture')

		server = AgentycServer()
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

		server = AgentycServer()
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

		server = AgentycServer()
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

		server = AgentycServer()
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

		server = AgentycServer()
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
		server = AgentycServer()
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
		assert 'No deterministic extraction route matched' in result.error
		assert result.metadata is None


class TestParallelTabExecution:
	"""Verify that multiple AgentycServer instances can share one Chrome browser via cdp_url.

	Each server gets its own isolated tab — actions taken in one should not affect the other.
	"""

	async def test_two_agents_operate_on_independent_tabs(self, base_url: str):
		"""Two servers attach to the same browser. Each navigates to a different page.
		Reading state from server A must not see server B's page and vice-versa.
		"""
		# Start a primary browser (keep_alive=True so the second agent can attach)
		primary_session = BrowserSession(
			browser_profile=BrowserProfile(
				headless=True,
				user_data_dir=None,
				keep_alive=True,
			)
		)
		await primary_session.start()

		try:
			# Get the CDP URL of the shared browser
			cdp_url = primary_session.browser_profile.cdp_url
			assert cdp_url, 'Primary session must expose a CDP URL for shared-browser attachment'

			# Server A: the primary session itself (owns the first tab)
			server_a = AgentycServer(cdp_url=cdp_url)
			server_a.browser_session = primary_session
			server_a.tools = Tools()
			server_a.tools.set_coordinate_clicking(True)

			# Server B: creates a new tab in the same browser via cdp_url
			server_b = AgentycServer(cdp_url=cdp_url)
			await server_b._init_browser_session(headless=True, user_data_dir=None)

			try:
				# Navigate each agent to a different page concurrently
				nav_a, nav_b = await asyncio.gather(
					server_a._navigate(f'{base_url}/accessible'),
					server_b._navigate(f'{base_url}/status'),
				)
				assert not nav_a.startswith('Error'), f'Server A navigation failed: {nav_a}'
				assert not nav_b.startswith('Error'), f'Server B navigation failed: {nav_b}'

				# Poll until both agents have interactive elements — avoids fixed sleeps
				# that are brittle under a loaded event loop (e.g. after a full test suite).
				state_a: dict = {}
				state_b: dict = {}
				for _ in range(20):
					state_a_json, _ = await server_a._get_browser_state(include_screenshot=False)
					state_b_json, _ = await server_b._get_browser_state(include_screenshot=False)
					state_a = json.loads(state_a_json)
					state_b = json.loads(state_b_json)
					if state_a.get('interactive_elements') and state_b.get('interactive_elements'):
						break
					await asyncio.sleep(0.1)

				assert (
					state_a['current_tab']['ownership']['runtime']['runtime_id']
					== server_a.browser_session.runtime_metadata.runtime_id
				)
				assert (
					state_b['current_tab']['ownership']['runtime']['runtime_id']
					== server_b.browser_session.runtime_metadata.runtime_id
				)
				assert state_a['current_tab']['tab_id'] != state_b['current_tab']['tab_id']
				# Server_a must see server_b's tab in its tab list.
				# We check by tab_id. Ownership attribution is best-effort and depends on JS running
				# in server_b's page, so we don't assert on owner_kind here.
				server_b_tab_id = state_b['current_tab']['tab_id']
				assert any(tab.get('tab_id') == server_b_tab_id for tab in state_a['tabs']), (
					f'Server A does not see server B tab {server_b_tab_id} in its tab list: {[t.get("tab_id") for t in state_a["tabs"]]}'
				)

				# Server A is on /accessible which has "Email address" and "Start checkout"
				texts_a = {el.get('text', '') for el in state_a['interactive_elements']}
				assert any('Email' in t or 'Start' in t for t in texts_a), (
					f'Server A should be on /accessible, got elements: {texts_a}'
				)

				# Server B is on /status which has "Restart service"
				texts_b = {el.get('text', '') for el in state_b['interactive_elements']}
				assert any('Restart' in t for t in texts_b), f'Server B should be on /status, got elements: {texts_b}'

				# The two sets of elements must be completely different (no bleed-over)
				assert texts_a.isdisjoint(texts_b), f'Tab isolation violation — shared elements: {texts_a & texts_b}'

			finally:
				await server_b._shutdown()

		finally:
			await primary_session.kill()

	async def test_parallel_actions_do_not_interfere(self, base_url: str):
		"""Two agents fire click/type actions simultaneously on their own tabs.
		Each action must succeed and land on the correct tab.
		"""
		primary_session = BrowserSession(
			browser_profile=BrowserProfile(
				headless=True,
				user_data_dir=None,
				keep_alive=True,
			)
		)
		await primary_session.start()

		try:
			cdp_url = primary_session.browser_profile.cdp_url
			assert cdp_url

			server_a = AgentycServer(cdp_url=cdp_url)
			server_a.browser_session = primary_session
			server_a.tools = Tools()
			server_a.tools.set_coordinate_clicking(True)

			server_b = AgentycServer(cdp_url=cdp_url)
			await server_b._init_browser_session(headless=True, user_data_dir=None)

			try:
				# Point each agent at the accessible form
				nav_a = await server_a._navigate(f'{base_url}/accessible')
				nav_b = await server_b._navigate(f'{base_url}/accessible')
				assert not nav_a.startswith('Error'), f'Server A navigation failed: {nav_a}'
				assert not nav_b.startswith('Error'), f'Server B navigation failed: {nav_b}'

				# Poll until both agents have interactive elements
				state_a: dict = {}
				state_b: dict = {}
				for _ in range(20):
					state_a_json, _ = await server_a._get_browser_state(include_screenshot=False)
					state_b_json, _ = await server_b._get_browser_state(include_screenshot=False)
					state_a = json.loads(state_a_json)
					state_b = json.loads(state_b_json)
					if state_a.get('interactive_elements') and state_b.get('interactive_elements'):
						break
					await asyncio.sleep(0.1)

				email_ref_a = next(
					(
						el['ref']
						for el in state_a['interactive_elements']
						if 'Email' in el.get('text', '') or 'email' in el.get('placeholder', '').lower()
					),
					None,
				)
				email_ref_b = next(
					(
						el['ref']
						for el in state_b['interactive_elements']
						if 'Email' in el.get('text', '') or 'email' in el.get('placeholder', '').lower()
					),
					None,
				)
				assert email_ref_a is not None, (
					f'Server A state has no email field. Elements: {[el.get("text") or el.get("placeholder") for el in state_a["interactive_elements"]]}'
				)
				assert email_ref_b is not None, (
					f'Server B state has no email field. Elements: {[el.get("text") or el.get("placeholder") for el in state_b["interactive_elements"]]}'
				)

				# Both agents type simultaneously into their own tabs
				result_a, result_b = await asyncio.gather(
					server_a._type_text(ref=email_ref_a, text='agent-a@example.com'),
					server_b._type_text(ref=email_ref_b, text='agent-b@example.com'),
				)

				assert not result_a.startswith('Error'), f'Server A type failed: {result_a}'
				assert not result_b.startswith('Error'), f'Server B type failed: {result_b}'

				# Verify each tab has the correct value (not bleed-over from the other agent).
				# Use get_browser_state which surfaces the live input `value` field.
				post_state_a_json, _ = await server_a._get_browser_state(include_screenshot=False)
				post_state_b_json, _ = await server_b._get_browser_state(include_screenshot=False)
				post_state_a = json.loads(post_state_a_json)
				post_state_b = json.loads(post_state_b_json)

				values_a = {el.get('value', '') for el in post_state_a['interactive_elements']}
				values_b = {el.get('value', '') for el in post_state_b['interactive_elements']}

				assert any('agent-a' in v for v in values_a), (
					f'Tab A should have agent-a value in elements, got values: {values_a}'
				)
				assert any('agent-b' in v for v in values_b), (
					f'Tab B should have agent-b value in elements, got values: {values_b}'
				)
				# Confirm the values did NOT bleed across tabs
				assert not any('agent-b' in v for v in values_a), f'agent-b value leaked into Tab A: {values_a}'
				assert not any('agent-a' in v for v in values_b), f'agent-a value leaked into Tab B: {values_b}'

			finally:
				await server_b._shutdown()

		finally:
			await primary_session.kill()
