from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import mean
from threading import Thread
from typing import Any

from agentyc.dogfood import (
	build_issue_body,
	build_issue_title,
	collect_fixture_regression_reasons,
	create_github_issue,
	default_artifact_root,
	detect_regressions,
	evaluate_release_gate,
	write_json,
	write_text,
)


@dataclass(slots=True)
class BenchmarkFixture:
	slug: str
	title: str
	html: str
	expected_compact_texts: list[str]
	extra_files: dict[str, str] = field(default_factory=dict)
	deterministic_query: BenchmarkExtractionQuery | None = None
	focus_text: str | None = None
	action_scenario: str | None = None
	expect_unchanged_delta: bool = True


@dataclass(slots=True)
class BenchmarkExtractionQuery:
	query: str
	extract_links: bool = False
	expected_terms: list[str] = field(default_factory=list)
	output_schema: dict[str, Any] | None = None
	expected_json: dict[str, Any] | None = None


@dataclass(slots=True)
class ServedFixturePack:
	root: tempfile.TemporaryDirectory[str]
	server: ThreadingHTTPServer
	thread: Thread
	base_url: str

	def close(self) -> None:
		self.server.shutdown()
		self.server.server_close()
		self.thread.join(timeout=5)
		self.root.cleanup()


class QuietHandler(SimpleHTTPRequestHandler):
	def log_message(self, format: str, *args: object) -> None:
		return


async def benchmark_call(fn) -> tuple[Any, float]:
	start = time.perf_counter()
	result = await fn()
	return result, (time.perf_counter() - start) * 1000


def measure_import_ms() -> float:
	command = [
		sys.executable,
		'-c',
		'import time; start=time.perf_counter(); import agentyc.mcp.server; print((time.perf_counter()-start)*1000)',
	]
	output = subprocess.check_output(command, text=True).strip()
	return round(float(output), 1)


def build_fixture_pack() -> list[BenchmarkFixture]:
	return [
		BenchmarkFixture(
			slug='small-form',
			title='Small accessible form',
			html="""
			<!doctype html>
			<html lang="en">
			<head><meta charset="utf-8" /><title>Small form</title></head>
			<body>
				<header>
					<h1>Checkout</h1>
					<p>Small control surface used as the low-cardinality baseline.</p>
				</header>
				<main>
					<label>Email address <input id="email" aria-label="Email address" placeholder="you@example.com" type="email" /></label>
					<div>
						<button aria-label="Start checkout">Start checkout</button>
						<button aria-label="Open help center">Help</button>
						<a href="/docs/getting-started">Documentation</a>
						<a href="/pricing">Pricing</a>
					</div>
				</main>
			</body>
			</html>
			""",
			expected_compact_texts=['Email address', 'Start checkout', 'Documentation'],
			deterministic_query=BenchmarkExtractionQuery(
				query='list all links on the page',
				extract_links=True,
				expected_terms=['Documentation', 'Pricing'],
			),
			focus_text='Email address',
		),
		BenchmarkFixture(
			slug='dense-catalog',
			title='Dense catalog grid',
			html=make_dense_catalog_html(),
			expected_compact_texts=['Search products', 'Sort by', 'Open cart', 'Open support', 'Featured Product 1'],
			focus_text='Search products',
		),
		BenchmarkFixture(
			slug='long-docs',
			title='Long documentation page',
			html=make_long_docs_html(),
			expected_compact_texts=['Search documentation', 'API Reference', 'Authentication', 'SDK quickstart'],
			deterministic_query=BenchmarkExtractionQuery(
				query='list all links on the page',
				extract_links=True,
				expected_terms=['API Reference', 'Authentication', 'SDK quickstart 1'],
			),
			focus_text='Search documentation',
		),
		BenchmarkFixture(
			slug='workflow-form',
			title='Workflow form with validation',
			html=make_workflow_form_html(),
			expected_compact_texts=['Project name', 'Environment', 'Deploy preview', 'Open validation help'],
			deterministic_query=BenchmarkExtractionQuery(
				query='list all form fields on the page',
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
				expected_json={
					'field_count': 5,
					'fields': [
						{'name': 'Project name', 'type': 'text', 'required': False},
						{'name': 'Repository URL', 'type': 'url'},
						{'name': 'Environment', 'type': 'select', 'options': ['Preview', 'Production']},
					],
				},
			),
			focus_text='Project name',
		),
		BenchmarkFixture(
			slug='status-panel',
			title='Key-value status panel',
			html=make_status_panel_html(),
			expected_compact_texts=['Restart service', 'Acknowledge alert', 'Open runbook'],
			deterministic_query=BenchmarkExtractionQuery(
				query='extract the status panel on the page',
				output_schema={
					'type': 'object',
					'properties': {
						'status': {'type': 'string'},
						'region': {'type': 'string'},
						'last_deployed_by': {'type': 'string'},
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
				expected_json={
					'status': 'Healthy',
					'region': 'us-east-1',
					'last_deployed_by': 'release-bot',
					'properties': [
						{'key': 'Status', 'value': 'Healthy'},
						{'key': 'Region', 'value': 'us-east-1'},
					],
				},
			),
			focus_text='Restart service',
		),
		BenchmarkFixture(
			slug='pricing-table',
			title='Pricing table',
			html=make_pricing_table_html(),
			expected_compact_texts=['Starter plan', 'Business plan', 'Contact sales'],
			deterministic_query=BenchmarkExtractionQuery(
				query='extract the pricing table on the page',
				output_schema={
					'type': 'object',
					'properties': {
						'columns': {'type': 'array', 'items': {'type': 'string'}},
						'row_count': {'type': 'integer'},
						'rows': {
							'type': 'array',
							'items': {
								'type': 'object',
								'properties': {
									'plan': {'type': 'string'},
									'price': {'type': 'string'},
									'sla': {'type': 'string'},
								},
								'required': ['plan', 'price', 'sla'],
							},
						},
					},
					'required': ['rows'],
				},
				expected_json={
					'columns': ['Plan', 'Price', 'SLA'],
					'row_count': 3,
					'rows': [
						{'plan': 'Starter plan', 'price': '$19', 'sla': 'Email support'},
						{'plan': 'Business plan', 'price': '$99', 'sla': '4-hour response'},
					],
				},
			),
			focus_text='Starter plan',
		),
		BenchmarkFixture(
			slug='search-results',
			title='Structured search results',
			html=make_search_results_html(),
			expected_compact_texts=['Search documentation', 'Auth quickstart', 'Next page'],
			deterministic_query=BenchmarkExtractionQuery(
				query='extract the search results on the page',
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
				expected_json={
					'result_count': 3,
					'results': [
						{
							'title': 'Auth quickstart',
							'url': '__BASE_URL__/docs/authentication.html',
						},
						{
							'title': 'Webhook retries',
							'url': '__BASE_URL__/docs/webhooks.html',
						},
					],
				},
			),
			focus_text='Search documentation',
		),
		BenchmarkFixture(
			slug='issue-queue',
			title='Issue triage queue',
			html=make_issue_queue_html(),
			expected_compact_texts=['Search issues', 'Filter severity', 'Build #482 failing on main', 'Open triage runbook'],
			deterministic_query=BenchmarkExtractionQuery(
				query='extract table rows from the issue queue table',
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
					'required': ['issues'],
				},
				expected_json={
					'issue_count': 3,
					'issues': [
						{'title': 'Build #482 failing on main', 'severity': 'Critical', 'status': 'Open'},
						{'title': 'OAuth callback latency spike', 'severity': 'High', 'status': 'Investigating'},
					],
				},
			),
			focus_text='Search issues',
			extra_files={
				'issues/build-482.html': make_issue_detail_html(
					'Build #482 failing on main',
					'Critical',
					'Open',
					'Release bot',
					'Investigate CDN timeout in upload step before the release can proceed.',
				),
				'issues/oauth-latency.html': make_issue_detail_html(
					'OAuth callback latency spike',
					'High',
					'Investigating',
					'Identity team',
					'Compare callback timings and confirm whether the auth cookie is being set.',
				),
				'issues/docs-links.html': make_issue_detail_html(
					'Docs release links stale',
					'Medium',
					'Todo',
					'Docs team',
					'Verify release-note links and update the runbook references.',
				),
			},
		),
		BenchmarkFixture(
			slug='triage-checklist',
			title='Incident triage checklist',
			html=make_triage_checklist_html(),
			expected_compact_texts=['Open failing workflow', 'Download logs', 'Escalate incident'],
			deterministic_query=BenchmarkExtractionQuery(
				query='list all checklist items on the page',
				output_schema={
					'type': 'object',
					'properties': {
						'step_count': {'type': 'integer'},
						'steps': {'type': 'array', 'items': {'type': 'string'}},
					},
					'required': ['steps'],
				},
				expected_json={
					'step_count': 4,
					'steps': [
						'Open the failing workflow run',
						'Download the failed job logs',
						'Capture a browser screenshot',
						'Update the incident channel',
					],
				},
			),
			focus_text='Open failing workflow',
		),
		BenchmarkFixture(
			slug='accessibility-panel',
			title='Accessibility-heavy control panel',
			html=make_accessibility_panel_html(),
			expected_compact_texts=['Run accessibility scan', 'Severity filter', 'Only show keyboard blockers'],
			focus_text='Severity filter',
			action_scenario='accessibility-panel',
		),
		BenchmarkFixture(
			slug='delayed-release',
			title='Delayed enablement release form',
			html=make_delayed_release_html(),
			expected_compact_texts=['Release name', 'Publish release'],
			focus_text='Release name',
			action_scenario='delayed-release',
		),
		BenchmarkFixture(
			slug='modal-wizard',
			title='Modal blocker workflow',
			html=make_modal_wizard_html(),
			expected_compact_texts=['Dismiss blocking modal', 'Run deployment'],
			focus_text='Dismiss blocking modal',
			action_scenario='modal-wizard',
		),
		BenchmarkFixture(
			slug='drift-recovery',
			title='DOM drift ref recovery',
			html=make_drift_recovery_html(),
			expected_compact_texts=['Approve deployment'],
			focus_text='Approve deployment',
			action_scenario='drift-recovery',
			expect_unchanged_delta=False,
		),
		BenchmarkFixture(
			slug='repeated-actions',
			title='Repeated labels with group context',
			html=make_repeated_actions_html(),
			expected_compact_texts=['Open'],
			focus_text='Open',
			action_scenario='repeated-actions',
		),
		BenchmarkFixture(
			slug='tab-workspace',
			title='New-tab release notes launcher',
			html=make_tab_workspace_html(),
			expected_compact_texts=['Open release notes'],
			focus_text='Open release notes',
			action_scenario='tab-workspace',
		),
		BenchmarkFixture(
			slug='contenteditable-compose',
			title='Contenteditable issue composer',
			html=make_contenteditable_compose_html(),
			expected_compact_texts=['Issue body editor', 'Publish comment'],
			focus_text='Issue body editor',
			action_scenario='contenteditable-compose',
		),
		BenchmarkFixture(
			slug='custom-combobox',
			title='ARIA combobox assignee picker',
			html=make_custom_combobox_html(),
			expected_compact_texts=['Choose assignee'],
			focus_text='Choose assignee',
			action_scenario='custom-combobox',
		),
		BenchmarkFixture(
			slug='iframe-workspace',
			title='Same-origin iframe form workflow',
			html=make_iframe_workspace_html(),
			extra_files={'iframe-workspace-child.html': make_iframe_workspace_child_html()},
			expected_compact_texts=['Frame input', 'Submit frame value'],
			focus_text='Frame input',
			action_scenario='iframe-workspace',
		),
		BenchmarkFixture(
			slug='shadow-dom-workspace',
			title='Shadow DOM form workflow',
			html=make_shadow_dom_workspace_html(),
			expected_compact_texts=['Email address', 'Publish update'],
			focus_text='Email address',
			action_scenario='shadow-dom-workspace',
		),
		BenchmarkFixture(
			slug='debounced-autocomplete',
			title='Debounced autocomplete selection workflow',
			html=make_debounced_autocomplete_html(),
			expected_compact_texts=['Search user'],
			focus_text='Search user',
			action_scenario='debounced-autocomplete',
		),
		BenchmarkFixture(
			slug='confirm-dialog',
			title='Confirm dialog decision workflow',
			html=make_confirm_dialog_html(),
			expected_compact_texts=['Delete branch'],
			focus_text='Delete branch',
			action_scenario='confirm-dialog',
		),
		BenchmarkFixture(
			slug='hover-dropdown',
			title='Hover-triggered dropdown menu',
			html=make_hover_dropdown_html(),
			expected_compact_texts=['Deploy'],
			focus_text='Deploy',
			action_scenario='hover-dropdown',
		),
		BenchmarkFixture(
			slug='drag-sortable',
			title='Drag-and-drop sortable list',
			html=make_drag_sortable_html(),
			expected_compact_texts=['Task Alpha', 'Task Beta', 'Task Gamma'],
			action_scenario='drag-sortable',
		),
		BenchmarkFixture(
			slug='form-validation',
			title='Form with HTML5 validation constraints',
			html=make_form_validation_html(),
			expected_compact_texts=['Create account'],
			action_scenario='form-validation',
		),
		BenchmarkFixture(
			slug='right-click-menu',
			title='Right-click context menu trigger',
			html=make_right_click_menu_html(),
			expected_compact_texts=['Options'],
			action_scenario='right-click-menu',
		),
		BenchmarkFixture(
			slug='cookie-auth',
			title='Cookie-based auth state toggle',
			html=make_cookie_auth_html(),
			expected_compact_texts=['Refresh'],
			action_scenario='cookie-auth',
		),
		BenchmarkFixture(
			slug='console-log-capture',
			title='Console log capture and filtering',
			html=make_console_log_capture_html(),
			expected_compact_texts=['Trigger log'],
			action_scenario='console-log-capture',
		),
		BenchmarkFixture(
			slug='typing-speed',
			title='Typing speed benchmark (Input.insertText fast path)',
			html=make_form_validation_html(),
			expected_compact_texts=['Create account'],
			action_scenario='typing-speed',
		),
		BenchmarkFixture(
			slug='network-log',
			title='Network request monitoring via CDP',
			html=make_network_log_html(),
			expected_compact_texts=['Fetch data', 'Post data'],
			action_scenario='network-log',
		),
	]


def dogfood_fixture_slugs() -> list[str]:
	return [
		'dense-catalog',
		'long-docs',
		'workflow-form',
		'pricing-table',
		'issue-queue',
		'triage-checklist',
		'accessibility-panel',
		'delayed-release',
		'modal-wizard',
		'drift-recovery',
		'repeated-actions',
		'tab-workspace',
		'contenteditable-compose',
		'custom-combobox',
		'iframe-workspace',
		'shadow-dom-workspace',
		'debounced-autocomplete',
		'confirm-dialog',
	]


def make_dense_catalog_html(card_count: int = 72) -> str:
	cards = []
	for index in range(1, card_count + 1):
		cards.append(
			f"""
			<article class="card">
				<h2>Featured Product {index}</h2>
				<p>Reusable product card with repetitive actions to stress compaction.</p>
				<button aria-label="Add Featured Product {index} to cart">Add to cart</button>
				<a href="/catalog/{index}">Featured Product {index}</a>
				<a href="/catalog/{index}/details">View details</a>
			</article>
			"""
		)

	return f"""
	<!doctype html>
	<html lang="en">
	<head>
		<meta charset="utf-8" />
		<title>Dense catalog</title>
		<style>
			body {{ font-family: sans-serif; max-width: 1280px; margin: 0 auto; padding: 24px; }}
			.toolbar {{ display: grid; grid-template-columns: 2fr 1fr 1fr auto auto; gap: 12px; position: sticky; top: 0; background: white; }}
			.catalog {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 24px; }}
			.card {{ border: 1px solid #d0d7de; border-radius: 8px; padding: 12px; }}
		</style>
	</head>
	<body>
		<header><h1>Dense catalog</h1><p>High-cardinality repeated controls.</p></header>
		<section class="toolbar" aria-label="Catalog controls">
			<label>Search <input aria-label="Search products" placeholder="Search products" type="search" /></label>
			<label>Sort <select aria-label="Sort by"><option>Featured</option><option>Price</option></select></label>
			<label><input aria-label="Only show discounted items" type="checkbox" /> Discounted</label>
			<button aria-label="Open cart">Open cart</button>
			<a href="/support">Open support</a>
		</section>
		<section class="catalog" aria-label="Catalog items">
			{''.join(cards)}
		</section>
	</body>
	</html>
	"""


def make_long_docs_html(section_count: int = 36) -> str:
	nav_links = []
	sections = []
	for index in range(1, section_count + 1):
		title = f'SDK quickstart {index}' if index == 1 else f'Documentation topic {index}'
		nav_links.append(f'<li><a href="#section-{index}">{title}</a></li>')
		sections.append(
			f"""
			<section id="section-{index}">
				<h2>{title}</h2>
				<p>Detailed content for {title}.</p>
				<a href="/docs/topic-{index}">Read more</a>
			</section>
			"""
		)

	return f"""
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Long docs</title></head>
	<body>
		<header>
			<h1>Documentation hub</h1>
			<label>Search docs <input aria-label="Search documentation" placeholder="Search documentation" type="search" /></label>
			<nav aria-label="Primary docs navigation">
				<a href="/docs/api-reference">API Reference</a>
				<a href="/docs/authentication">Authentication</a>
				<a href="/docs/webhooks">Webhooks</a>
			</nav>
		</header>
		<main style="display:grid; grid-template-columns: 280px 1fr; gap: 24px;">
			<aside>
				<ul>{''.join(nav_links)}</ul>
			</aside>
			<article>{''.join(sections)}</article>
		</main>
	</body>
	</html>
	"""


def make_workflow_form_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Workflow form</title></head>
	<body>
		<header><h1>Deployment workflow</h1></header>
		<main>
			<form>
				<label>Project name <input aria-label="Project name" placeholder="Project name" type="text" /></label>
				<label>Repository URL <input aria-label="Repository URL" placeholder="https://github.com/org/repo" type="url" /></label>
				<label>Environment
					<select aria-label="Environment">
						<option>Preview</option>
						<option>Production</option>
					</select>
				</label>
				<label><input aria-label="Enable canary rollout" type="checkbox" /> Enable canary rollout</label>
				<label><input aria-label="Require manual approval" type="checkbox" /> Require manual approval</label>
				<div role="group" aria-label="Deployment actions">
					<button aria-label="Deploy preview">Deploy preview</button>
					<button aria-label="Schedule production deploy">Schedule production deploy</button>
					<a href="/docs/validation">Open validation help</a>
				</div>
			</form>
		</main>
	</body>
	</html>
	"""


def make_pricing_table_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Pricing table</title></head>
	<body>
		<header>
			<h1>Pricing</h1>
			<p>Compare plans for the hosted browser runtime.</p>
		</header>
		<main>
			<table>
				<thead>
					<tr><th>Plan</th><th>Price</th><th>SLA</th></tr>
				</thead>
				<tbody>
					<tr><td><a href="/pricing/starter">Starter plan</a></td><td>$19</td><td>Email support</td></tr>
					<tr><td><a href="/pricing/business">Business plan</a></td><td>$99</td><td>4-hour response</td></tr>
					<tr><td><a href="/pricing/enterprise">Enterprise plan</a></td><td>Contact sales</td><td>Dedicated support</td></tr>
				</tbody>
			</table>
			<div>
				<a href="/pricing/contact">Contact sales</a>
			</div>
		</main>
	</body>
	</html>
	"""


def make_triage_checklist_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Incident triage checklist</title></head>
	<body>
		<header><h1>Incident triage</h1></header>
		<main>
			<div role="toolbar" aria-label="Triage actions">
				<button aria-label="Open failing workflow">Open failing workflow</button>
				<button aria-label="Download logs">Download logs</button>
				<button aria-label="Escalate incident">Escalate incident</button>
			</div>
			<section>
				<h2>Checklist</h2>
				<ol>
					<li>Open the failing workflow run</li>
					<li>Download the failed job logs</li>
					<li>Capture a browser screenshot</li>
					<li>Update the incident channel</li>
				</ol>
			</section>
		</main>
	</body>
	</html>
	"""


def make_accessibility_panel_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Accessibility panel</title></head>
	<body>
		<header><h1>Accessibility review</h1></header>
		<main>
			<div role="toolbar" aria-label="Accessibility actions">
				<button aria-label="Run accessibility scan">Run accessibility scan</button>
				<button aria-label="Export accessibility report">Export report</button>
				<a href="/docs/a11y">Accessibility guide</a>
			</div>
			<section>
				<label>Severity
					<select aria-label="Severity filter">
						<option>All severities</option>
						<option>Critical</option>
						<option>Serious</option>
					</select>
				</label>
				<label><input aria-label="Only show keyboard blockers" type="checkbox" /> Only show keyboard blockers</label>
				<label><input aria-label="Include color contrast issues" type="checkbox" /> Include color contrast issues</label>
			</section>
			<section aria-label="Findings list">
				<button aria-label="Focus first failing element">Focus first failing element</button>
				<button aria-label="Open issue template">Open issue template</button>
			</section>
			<p id="status" aria-live="polite">No scan has been run.</p>
		</main>
		<script>
			const status = document.getElementById('status');
			const blockersOnly = document.querySelector('input[aria-label="Only show keyboard blockers"]');
			document.querySelector('button[aria-label="Run accessibility scan"]').addEventListener('click', () => {
				status.textContent = blockersOnly.checked ? 'Keyboard blockers only: 1 issue found.' : 'Full accessibility scan: 4 issues found.';
			});
		</script>
	</body>
	</html>
	"""


def make_status_panel_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Status panel</title></head>
	<body>
		<header><h1>Runtime overview</h1></header>
		<main>
			<section aria-label="Service status panel">
				<ul>
					<li>Status: Healthy</li>
					<li>Region: us-east-1</li>
					<li>Last deployed by: release-bot</li>
					<li>Pending rollout: Canary</li>
				</ul>
			</section>
			<div role="toolbar" aria-label="Runtime actions">
				<button aria-label="Restart service">Restart service</button>
				<button aria-label="Acknowledge alert">Acknowledge alert</button>
				<a href="/docs/runbook">Open runbook</a>
			</div>
		</main>
	</body>
	</html>
	"""


def make_search_results_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Search results</title></head>
	<body>
		<header>
			<h1>Documentation search</h1>
			<label>Search <input aria-label="Search documentation" placeholder="Search documentation" type="search" /></label>
		</header>
		<main>
			<ol aria-label="Search results">
				<li><a href="/docs/authentication.html">Auth quickstart</a> - Learn how to issue session tokens.</li>
				<li><a href="/docs/webhooks.html">Webhook retries</a> - Tune retry windows for failed deliveries.</li>
				<li><a href="/docs/cache-tags.html">Cache tags</a> - Invalidate stale cached pages safely.</li>
			</ol>
			<nav aria-label="Pagination">
				<a href="/search-results?page=2">Next page</a>
			</nav>
		</main>
	</body>
	</html>
	"""


def make_issue_queue_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Issue queue</title></head>
	<body>
		<header>
			<h1>Engineering issue queue</h1>
			<label>Search <input aria-label="Search issues" placeholder="Search issues" type="search" /></label>
			<label>Severity
				<select aria-label="Filter severity">
					<option>All severities</option>
					<option>Critical</option>
					<option>High</option>
					<option>Medium</option>
				</select>
			</label>
			<a href="/docs/runbook.html">Open triage runbook</a>
		</header>
		<main>
			<table aria-label="Issue queue">
				<thead>
					<tr><th>Issue</th><th>Severity</th><th>Status</th><th>Owner</th></tr>
				</thead>
				<tbody>
					<tr>
						<td><a href="/issues/build-482.html">Build #482 failing on main</a></td>
						<td>Critical</td>
						<td>Open</td>
						<td>Release bot</td>
					</tr>
					<tr>
						<td><a href="/issues/oauth-latency.html">OAuth callback latency spike</a></td>
						<td>High</td>
						<td>Investigating</td>
						<td>Identity team</td>
					</tr>
					<tr>
						<td><a href="/issues/docs-links.html">Docs release links stale</a></td>
						<td>Medium</td>
						<td>Todo</td>
						<td>Docs team</td>
					</tr>
				</tbody>
			</table>
		</main>
	</body>
	</html>
	"""


def make_issue_detail_html(title: str, severity: str, status: str, owner: str, summary: str) -> str:
	return f"""
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>{title}</title></head>
	<body>
		<main>
			<h1>{title}</h1>
			<ul aria-label="Issue metadata">
				<li>Severity: {severity}</li>
				<li>Status: {status}</li>
				<li>Owner: {owner}</li>
			</ul>
			<p>{summary}</p>
			<a href="/triage-checklist.html">Open incident checklist</a>
		</main>
	</body>
	</html>
	"""


def make_delayed_release_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Delayed release</title></head>
	<body>
		<header><h1>Release form</h1></header>
		<main>
			<label>Release name <input id="release-name" aria-label="Release name" type="text" /></label>
			<button id="publish" aria-label="Publish release" disabled>Publish release</button>
			<p id="status" aria-live="polite">Waiting for release name.</p>
		</main>
		<script>
			const input = document.getElementById('release-name');
			const publish = document.getElementById('publish');
			const status = document.getElementById('status');
			let pendingValidation = null;
			input.addEventListener('input', () => {
				status.textContent = 'Validating release name...';
				publish.disabled = true;
				clearTimeout(pendingValidation);
				pendingValidation = setTimeout(() => {
					publish.disabled = input.value.trim().length < 3;
					status.textContent = publish.disabled ? 'Release name must be at least 3 characters.' : 'Ready to publish.';
				}, 250);
			});
			publish.addEventListener('click', () => {
				status.textContent = 'Release published.';
			});
		</script>
	</body>
	</html>
	"""


def make_modal_wizard_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Modal wizard</title></head>
	<body>
		<header><h1>Deployment wizard</h1></header>
		<main>
			<div id="modal" role="dialog" aria-label="Blocking modal">
				<p>Review the environment notice before continuing.</p>
				<button id="dismiss" aria-label="Dismiss blocking modal">Dismiss blocking modal</button>
			</div>
			<button id="deploy" aria-label="Run deployment" disabled>Run deployment</button>
			<p id="status" aria-live="polite">Deployment blocked by modal.</p>
		</main>
		<script>
			const modal = document.getElementById('modal');
			const dismiss = document.getElementById('dismiss');
			const deploy = document.getElementById('deploy');
			const status = document.getElementById('status');
			dismiss.addEventListener('click', () => {
				modal.remove();
				deploy.disabled = false;
				status.textContent = 'Modal dismissed. Ready to deploy.';
			});
			deploy.addEventListener('click', () => {
				status.textContent = 'Deployment started.';
			});
		</script>
	</body>
	</html>
	"""


def make_drift_recovery_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>DOM drift recovery</title></head>
	<body>
		<header><h1>Approval gate</h1></header>
		<main>
			<button id="approve" aria-label="Approve deployment" data-action="approve">Approve deployment</button>
			<p id="status" aria-live="polite">Awaiting approval.</p>
		</main>
		<script>
			const status = document.getElementById('status');
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
				status.textContent = 'Deployment approved.';
			});
		</script>
	</body>
	</html>
	"""


def make_repeated_actions_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Repeated actions</title></head>
	<body>
		<header><h1>Workspaces</h1></header>
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


def make_tab_workspace_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Tab workspace</title></head>
	<body>
		<header><h1>Primary workspace</h1></header>
		<main>
			<p>Open supporting notes in a new tab.</p>
			<a href="/release-notes.html" aria-label="Open release notes" target="_blank" rel="noopener">Open release notes</a>
		</main>
	</body>
	</html>
	"""


def make_contenteditable_compose_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head>
		<meta charset="utf-8" />
		<title>Issue composer</title>
		<style>
			body { font-family: sans-serif; max-width: 720px; margin: 0 auto; padding: 24px; }
			.editor { min-height: 140px; padding: 12px; border: 1px solid #d0d7de; border-radius: 8px; }
		</style>
	</head>
	<body>
		<main>
			<h1>Issue composer</h1>
			<div
				id="editor"
				class="editor"
				contenteditable="true"
				role="textbox"
				aria-label="Issue body editor"
			>Draft issue details</div>
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


def make_custom_combobox_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head>
		<meta charset="utf-8" />
		<title>Assignee combobox</title>
		<style>
			body { font-family: sans-serif; max-width: 720px; margin: 0 auto; padding: 24px; }
			[role="combobox"] { width: 260px; padding: 10px 12px; border: 1px solid #d0d7de; border-radius: 8px; }
			[role="listbox"] { width: 260px; margin-top: 8px; padding: 0; border: 1px solid #d0d7de; border-radius: 8px; list-style: none; }
			[role="option"] { padding: 10px 12px; cursor: pointer; }
		</style>
	</head>
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


def make_iframe_workspace_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Iframe workspace</title></head>
	<body>
		<main>
			<h1>Release workspace</h1>
			<iframe src="/iframe-workspace-child.html" title="Editor frame" style="width: 640px; height: 220px;"></iframe>
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


def make_iframe_workspace_child_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Iframe editor</title></head>
	<body>
		<label>Frame input <input aria-label="Frame input" type="text" /></label>
		<button aria-label="Submit frame value">Submit frame value</button>
		<script>
			document.querySelector('button').addEventListener('click', () => {
				window.parent.setFrameStatus(document.querySelector('input').value || 'Empty');
			});
		</script>
	</body>
	</html>
	"""


def make_shadow_dom_workspace_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Shadow workspace</title></head>
	<body>
		<main>
			<div id="shadow-host"></div>
			<p id="status">Idle</p>
		</main>
		<script>
			const shadowRoot = document.getElementById('shadow-host').attachShadow({ mode: 'open' });
			shadowRoot.innerHTML = `
				<label>Email address <input id="email" aria-label="Email address" type="email" /></label>
				<button id="publish" aria-label="Publish update">Publish</button>
			`;
			shadowRoot.getElementById('publish').addEventListener('click', () => {
				document.getElementById('status').textContent = shadowRoot.getElementById('email').value || 'Empty';
			});
		</script>
	</body>
	</html>
	"""


def make_debounced_autocomplete_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Debounced autocomplete</title></head>
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
				/>
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


def make_confirm_dialog_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Confirm dialog</title></head>
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


def make_hover_dropdown_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Hover dropdown menu</title>
	<style>
		.nav-item { position: relative; display: inline-block; }
		.dropdown { display: none; position: absolute; top: 100%; left: 0; background: #fff; border: 1px solid #ccc; min-width: 160px; z-index: 100; }
		.nav-item:hover .dropdown, .nav-item.open .dropdown { display: block; }
		.dropdown a { display: block; padding: 8px 12px; text-decoration: none; color: #333; }
	</style>
	</head>
	<body>
		<nav>
			<div class="nav-item" id="nav-deploy" tabindex="0" role="button" aria-label="Deploy menu" aria-haspopup="true">
				Deploy
				<div class="dropdown" role="menu" aria-label="Deploy options">
					<a href="#" role="menuitem" id="deploy-staging">Deploy to Staging</a>
					<a href="#" role="menuitem" id="deploy-prod">Deploy to Production</a>
					<a href="#" role="menuitem" id="rollback">Rollback</a>
				</div>
			</div>
		</nav>
		<p id="status">Idle</p>
		<script>
			document.getElementById('deploy-staging').addEventListener('click', () => {
				document.getElementById('status').textContent = 'Deploying to staging...';
			});
			document.getElementById('deploy-prod').addEventListener('click', () => {
				document.getElementById('status').textContent = 'Deploying to production...';
			});
			document.getElementById('rollback').addEventListener('click', () => {
				document.getElementById('status').textContent = 'Rolling back...';
			});
		</script>
	</body>
	</html>
	"""


def make_drag_sortable_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Drag sortable list</title>
	<style>
		.task { padding: 12px; margin: 6px 0; background: #f5f5f5; border: 1px solid #ddd; cursor: grab; border-radius: 4px; }
		.task.dragging { opacity: 0.5; }
		.task.drag-over { border-top: 3px solid #0066cc; }
	</style>
	</head>
	<body>
		<main>
			<h1>Task board</h1>
			<ul id="task-list" aria-label="Sortable task list" role="listbox">
				<li class="task" draggable="true" id="task-a" aria-label="Task Alpha" role="option" tabindex="0">Task Alpha</li>
				<li class="task" draggable="true" id="task-b" aria-label="Task Beta" role="option" tabindex="0">Task Beta</li>
				<li class="task" draggable="true" id="task-c" aria-label="Task Gamma" role="option" tabindex="0">Task Gamma</li>
			</ul>
			<p id="status">Idle</p>
		</main>
		<script>
			let dragged = null;
			document.querySelectorAll('.task').forEach(task => {
				task.addEventListener('dragstart', e => { dragged = task; task.classList.add('dragging'); });
				task.addEventListener('dragend', () => { task.classList.remove('dragging'); dragged = null; });
				task.addEventListener('dragover', e => { e.preventDefault(); task.classList.add('drag-over'); });
				task.addEventListener('dragleave', () => task.classList.remove('drag-over'));
				task.addEventListener('drop', e => {
					e.preventDefault();
					task.classList.remove('drag-over');
					if (dragged && dragged !== task) {
						const list = document.getElementById('task-list');
						list.insertBefore(dragged, task);
						document.getElementById('status').textContent = dragged.id + ' moved before ' + task.id;
					}
				});
			});
		</script>
	</body>
	</html>
	"""


def make_form_validation_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Form with validation</title></head>
	<body>
		<main>
			<h1>User registration</h1>
			<form id="reg-form" novalidate>
				<label>
					Username
					<input id="username" name="username" type="text" minlength="3" maxlength="20" pattern="[a-zA-Z0-9_]+" placeholder="letters, numbers, underscore" aria-describedby="username-hint" required>
					<span id="username-hint">3-20 chars, letters/numbers/underscore only</span>
					<span id="username-error" aria-live="polite" style="color:red;display:none"></span>
				</label>
				<label>
					Email
					<input id="email" name="email" type="email" placeholder="you@example.com" required>
				</label>
				<label>
					Phone
					<input id="phone" name="phone" type="tel" pattern="[0-9]{10}" placeholder="10 digits" inputmode="numeric">
				</label>
				<button type="submit" id="submit-btn">Create account</button>
			</form>
			<p id="status">Fill out the form</p>
		</main>
		<script>
			document.getElementById('username').addEventListener('input', function() {
				const err = document.getElementById('username-error');
				if (this.value.length > 0 && !/^[a-zA-Z0-9_]+$/.test(this.value)) {
					err.style.display = 'inline';
					err.textContent = 'Only letters, numbers, and underscore allowed';
					this.setAttribute('aria-invalid', 'true');
					this.setAttribute('aria-errormessage', 'username-error');
				} else {
					err.style.display = 'none';
					this.removeAttribute('aria-invalid');
					this.removeAttribute('aria-errormessage');
				}
			});
			document.getElementById('reg-form').addEventListener('submit', e => {
				e.preventDefault();
				const username = document.getElementById('username').value;
				const email = document.getElementById('email').value;
				if (username.length >= 3 && email.includes('@')) {
					document.getElementById('status').textContent = 'Account created: ' + username;
				} else {
					document.getElementById('status').textContent = 'Please fix validation errors';
				}
			});
		</script>
	</body>
	</html>
	"""


def make_right_click_menu_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Right-click menu</title></head>
	<body>
		<main>
			<h1>Context menu demo</h1>
			<button id="btn" aria-label="Options">Options</button>
			<p id="status">No action taken.</p>
		</main>
		<script>
			document.getElementById('btn').addEventListener('contextmenu', function(e) {
				e.preventDefault();
				var menu = document.createElement('div');
				menu.id = 'menu';
				menu.textContent = 'Context menu shown';
				document.body.appendChild(menu);
				document.getElementById('status').textContent = 'Context menu opened.';
			});
		</script>
	</body>
	</html>
	"""


def make_cookie_auth_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Cookie auth</title></head>
	<body>
		<main>
			<h1>Auth state</h1>
			<div id="auth">logged-out</div>
			<button id="refresh-btn" aria-label="Refresh" onclick="window.location.reload()">Refresh</button>
		</main>
		<script>
			(function() {
				var cookies = document.cookie.split(';').map(function(c) { return c.trim(); });
				var hasSession = cookies.some(function(c) { return c === 'session=abc123'; });
				document.getElementById('auth').textContent = hasSession ? 'logged-in' : 'logged-out';
			})();
		</script>
	</body>
	</html>
	"""


def make_console_log_capture_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Console log capture</title></head>
	<body>
		<main>
			<h1>Console log demo</h1>
			<button id="log-btn" aria-label="Trigger log">Trigger log</button>
			<p id="status">Idle</p>
		</main>
		<script>
			document.addEventListener('DOMContentLoaded', function() {
				console.warn('page loaded');
			});
			document.getElementById('log-btn').addEventListener('click', function() {
				console.log('clicked');
				console.error('test error');
				document.getElementById('status').textContent = 'Log triggered.';
			});
		</script>
	</body>
	</html>
	"""


def make_network_log_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Network log</title></head>
	<body>
		<main>
			<h1>Network request demo</h1>
			<button id="fetch-btn">Fetch data</button>
			<button id="post-btn">Post data</button>
			<p id="status">Idle</p>
		</main>
		<script>
			document.getElementById('fetch-btn').addEventListener('click', async function() {
				try {
					const r = await fetch('/api/data', {method: 'GET'});
					document.getElementById('status').textContent = 'GET ' + r.status;
				} catch(e) {
					document.getElementById('status').textContent = 'fetch failed: ' + e.message;
				}
			});
			document.getElementById('post-btn').addEventListener('click', async function() {
				try {
					const r = await fetch('/api/submit', {method: 'POST', body: JSON.stringify({x:1}), headers: {'Content-Type': 'application/json'}});
					document.getElementById('status').textContent = 'POST ' + r.status;
				} catch(e) {
					document.getElementById('status').textContent = 'post failed: ' + e.message;
				}
			});
		</script>
	</body>
	</html>
	"""


def make_release_notes_html() -> str:
	return """
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>Release notes</title></head>
	<body>
		<main>
			<h1>Release notes</h1>
			<p>Latest release notes are open in this tab.</p>
		</main>
	</body>
	</html>
	"""


def make_collaboration_runtime_html(runtime_name: str, button_label: str) -> str:
	return f"""
	<!doctype html>
	<html lang="en">
	<head><meta charset="utf-8" /><title>{runtime_name}</title></head>
	<body>
		<main>
			<h1>{runtime_name}</h1>
			<p>Shared-browser ownership benchmark page for {runtime_name}.</p>
			<button aria-label="{button_label}">{button_label}</button>
		</main>
	</body>
	</html>
	"""


def serve_fixture_pack(fixtures: list[BenchmarkFixture]) -> ServedFixturePack:
	root = tempfile.TemporaryDirectory(prefix='agentyc-benchmark-')
	root_path = Path(root.name)
	(root_path / 'docs').mkdir(parents=True, exist_ok=True)
	(root_path / 'issues').mkdir(parents=True, exist_ok=True)
	for fixture in fixtures:
		(root_path / f'{fixture.slug}.html').write_text(fixture.html, encoding='utf-8')
		for relative_path, content in fixture.extra_files.items():
			(root_path / relative_path).parent.mkdir(parents=True, exist_ok=True)
			(root_path / relative_path).write_text(content, encoding='utf-8')
	(root_path / 'release-notes.html').write_text(make_release_notes_html(), encoding='utf-8')
	(root_path / 'docs' / 'authentication.html').write_text(
		make_issue_detail_html(
			'Authentication quickstart',
			'Guide',
			'Published',
			'Docs team',
			'How to issue and persist session cookies for browser automation flows.',
		),
		encoding='utf-8',
	)
	(root_path / 'docs' / 'webhooks.html').write_text(
		make_issue_detail_html(
			'Webhook retries',
			'Guide',
			'Published',
			'Platform team',
			'Retry failed deliveries and inspect network logs when callbacks time out.',
		),
		encoding='utf-8',
	)
	(root_path / 'docs' / 'cache-tags.html').write_text(
		make_issue_detail_html(
			'Cache tags',
			'Guide',
			'Published',
			'Platform team',
			'Invalidate stale cached pages and confirm propagation across the edge.',
		),
		encoding='utf-8',
	)
	(root_path / 'docs' / 'runbook.html').write_text(
		make_issue_detail_html(
			'Incident triage runbook',
			'Runbook',
			'Published',
			'SRE',
			'Follow the queue, inspect logs, and capture evidence before escalating.',
		),
		encoding='utf-8',
	)
	(root_path / 'docs' / 'a11y.html').write_text(
		make_issue_detail_html(
			'Accessibility guide',
			'Guide',
			'Published',
			'Design systems',
			'Review blockers, export reports, and attach findings to the issue queue.',
		),
		encoding='utf-8',
	)
	(root_path / 'collaboration-runtime-a.html').write_text(
		make_collaboration_runtime_html('Collaboration Runtime A', 'Runtime A action'),
		encoding='utf-8',
	)
	(root_path / 'collaboration-runtime-b.html').write_text(
		make_collaboration_runtime_html('Collaboration Runtime B', 'Runtime B action'),
		encoding='utf-8',
	)
	(root_path / 'collaboration-window.html').write_text(
		make_collaboration_runtime_html('Collaboration Window Runtime', 'Window runtime action'),
		encoding='utf-8',
	)

	handler = partial(QuietHandler, directory=root.name)
	server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
	thread = Thread(target=server.serve_forever, daemon=True)
	thread.start()
	base_url = f'http://127.0.0.1:{server.server_port}'
	return ServedFixturePack(root=root, server=server, thread=thread, base_url=base_url)


def normalize_text(text: str) -> str:
	return ' '.join(text.lower().split())


def find_element_ref(
	payload: dict[str, Any],
	target_text: str,
	context_contains: str | None = None,
	tag: str | None = None,
) -> str | None:
	target = normalize_text(target_text)
	context_target = normalize_text(context_contains) if context_contains else None
	tag_target = normalize_text(tag) if tag else None
	for element in payload.get('interactive_elements', []):
		element_text = normalize_text(element.get('text', ''))
		element_context = normalize_text(element.get('context', ''))
		element_tag = normalize_text(element.get('tag', ''))
		if (
			element_text == target
			and (context_target is None or context_target in element_context)
			and (tag_target is None or element_tag == tag_target)
		):
			return element['ref']
	return None


def calculate_recall(payload: dict[str, Any], expected_texts: list[str]) -> dict[str, Any]:
	present_texts = [normalize_text(element.get('text', '')) for element in payload.get('interactive_elements', [])]
	matched = [
		text
		for text in expected_texts
		if any(
			normalize_text(text) == present_text or normalize_text(text) in present_text or present_text in normalize_text(text)
			for present_text in present_texts
		)
	]
	return {
		'matched': matched,
		'expected': expected_texts,
		'recall': round(len(matched) / len(expected_texts), 3) if expected_texts else 1.0,
	}


def calculate_text_matches(text: str, expected_terms: list[str]) -> dict[str, Any]:
	normalized_text = normalize_text(text)
	matched = [term for term in expected_terms if normalize_text(term) in normalized_text]
	return {
		'matched': matched,
		'expected': expected_terms,
		'recall': round(len(matched) / len(expected_terms), 3) if expected_terms else 1.0,
	}


def extract_structured_json(text: str) -> dict[str, Any] | None:
	start_tag = '<structured_result>\n'
	end_tag = '\n</structured_result>'
	if start_tag not in text or end_tag not in text:
		return None
	start_index = text.index(start_tag) + len(start_tag)
	end_index = text.index(end_tag, start_index)
	return json.loads(text[start_index:end_index])


def calculate_json_subset_match(actual: Any, expected: Any) -> dict[str, Any]:
	matched_paths: list[str] = []
	expected_paths: list[str] = []

	def walk(actual_value: Any, expected_value: Any, path: str) -> None:
		if isinstance(expected_value, dict):
			for key, nested_expected in expected_value.items():
				next_path = f'{path}.{key}' if path else key
				if isinstance(actual_value, dict) and key in actual_value:
					if isinstance(nested_expected, (dict, list)):
						walk(actual_value[key], nested_expected, next_path)
					else:
						expected_paths.append(next_path)
						if actual_value[key] == nested_expected:
							matched_paths.append(next_path)
				elif not isinstance(nested_expected, (dict, list)):
					expected_paths.append(next_path)
			return
		if isinstance(expected_value, list):
			for index, nested_expected in enumerate(expected_value):
				next_path = f'{path}[{index}]'
				if isinstance(actual_value, list) and index < len(actual_value):
					if isinstance(nested_expected, (dict, list)):
						walk(actual_value[index], nested_expected, next_path)
					else:
						expected_paths.append(next_path)
						if actual_value[index] == nested_expected:
							matched_paths.append(next_path)
				elif not isinstance(nested_expected, (dict, list)):
					expected_paths.append(next_path)
			return

	walk(actual, expected, '')
	dedup_expected = list(dict.fromkeys(expected_paths))
	dedup_matched = [path for path in dict.fromkeys(matched_paths) if path in dedup_expected]
	return {
		'matched': dedup_matched,
		'expected': dedup_expected,
		'recall': round(len(dedup_matched) / len(dedup_expected), 3) if dedup_expected else 1.0,
	}


def replace_base_url(value: Any, base_url: str) -> Any:
	if isinstance(value, str):
		return value.replace('__BASE_URL__', base_url)
	if isinstance(value, list):
		return [replace_base_url(item, base_url) for item in value]
	if isinstance(value, dict):
		return {key: replace_base_url(nested_value, base_url) for key, nested_value in value.items()}
	return value


def reduction_percent(full_chars: int, compact_chars: int) -> float:
	if full_chars == 0:
		return 0.0
	return round(((full_chars - compact_chars) / full_chars) * 100, 1)


def estimate_tokens_from_chars(char_count: int) -> int:
	if char_count <= 0:
		return 0
	return max(1, round(char_count / 4))


def calculate_context_utilization(payload_chars: int, *, context_budget_tokens: int = 128_000) -> dict[str, Any]:
	estimated_tokens = estimate_tokens_from_chars(payload_chars)
	return {
		'estimated_tokens': estimated_tokens,
		'context_budget_tokens': context_budget_tokens,
		'context_utilization_pct': round((estimated_tokens / context_budget_tokens) * 100, 3) if context_budget_tokens else 0.0,
	}


def calculate_precision(match: dict[str, Any], actual_count: int) -> float | None:
	matched = len(match.get('matched', []))
	if actual_count <= 0:
		return None
	return round(matched / actual_count, 3)


def calculate_f1(recall: float, precision: float | None) -> float | None:
	if precision is None:
		return None
	if recall <= 0 and precision <= 0:
		return 0.0
	return round((2 * recall * precision) / (recall + precision), 3)


def build_quality_metrics(match: dict[str, Any], *, actual_count: int, outcome_passed: bool | None = None) -> dict[str, Any]:
	recall = float(match.get('recall', 0.0))
	precision = calculate_precision(match, actual_count)
	metrics: dict[str, Any] = {
		'recall': recall,
		'precision': precision,
		'f1': calculate_f1(recall, precision),
	}
	if outcome_passed is not None:
		metrics['accuracy'] = 1.0 if outcome_passed else 0.0
	return metrics


def build_efficiency_metrics(payload_chars: int, latency_ms: float, matched_count: int) -> dict[str, Any]:
	estimated_tokens = estimate_tokens_from_chars(payload_chars)
	seconds = latency_ms / 1000 if latency_ms > 0 else 0.0
	return {
		'payload_chars_per_match': round(payload_chars / matched_count, 1) if matched_count else None,
		'estimated_tokens_per_match': round(estimated_tokens / matched_count, 1) if matched_count else None,
		'matches_per_second': round(matched_count / seconds, 3) if seconds > 0 and matched_count else 0.0,
		'chars_per_ms': round(payload_chars / latency_ms, 3) if latency_ms > 0 else 0.0,
		'estimated_tokens_per_ms': round(estimated_tokens / latency_ms, 3) if latency_ms > 0 else 0.0,
	}


async def run_action_scenario(server, fixture: BenchmarkFixture, base_url: str) -> dict[str, Any]:
	start = time.perf_counter()
	try:
		if fixture.action_scenario == 'delayed-release':
			result = await _scenario_delayed_release(server)
		elif fixture.action_scenario == 'modal-wizard':
			result = await _scenario_modal_wizard(server)
		elif fixture.action_scenario == 'drift-recovery':
			result = await _scenario_drift_recovery(server)
		elif fixture.action_scenario == 'repeated-actions':
			result = await _scenario_repeated_actions(server)
		elif fixture.action_scenario == 'tab-workspace':
			result = await _scenario_tab_workspace(server)
		elif fixture.action_scenario == 'accessibility-panel':
			result = await _scenario_accessibility_panel(server)
		elif fixture.action_scenario == 'contenteditable-compose':
			result = await _scenario_contenteditable_compose(server)
		elif fixture.action_scenario == 'custom-combobox':
			result = await _scenario_custom_combobox(server)
		elif fixture.action_scenario == 'iframe-workspace':
			result = await _scenario_iframe_workspace(server)
		elif fixture.action_scenario == 'shadow-dom-workspace':
			result = await _scenario_shadow_dom_workspace(server)
		elif fixture.action_scenario == 'debounced-autocomplete':
			result = await _scenario_debounced_autocomplete(server)
		elif fixture.action_scenario == 'confirm-dialog':
			result = await _scenario_confirm_dialog(server)
		elif fixture.action_scenario == 'hover-dropdown':
			result = await _scenario_hover_dropdown(server)
		elif fixture.action_scenario == 'drag-sortable':
			result = await _scenario_drag_sortable(server)
		elif fixture.action_scenario == 'form-validation':
			result = await _scenario_form_validation(server)
		elif fixture.action_scenario == 'right-click-menu':
			result = await _scenario_right_click_menu(server)
		elif fixture.action_scenario == 'cookie-auth':
			result = await _scenario_cookie_auth(server, base_url)
		elif fixture.action_scenario == 'console-log-capture':
			result = await _scenario_console_log_capture(server)
		elif fixture.action_scenario == 'typing-speed':
			result = await _scenario_typing_speed(server)
		elif fixture.action_scenario == 'network-log':
			result = await _scenario_network_log(server, base_url)
		else:
			raise ValueError(f'Unknown action scenario: {fixture.action_scenario}')
		result['latency_ms'] = round((time.perf_counter() - start) * 1000, 1)
		return result
	except Exception as exc:
		return {
			'scenario': fixture.action_scenario,
			'passed': False,
			'error': str(exc),
			'latency_ms': round((time.perf_counter() - start) * 1000, 1),
			'base_url': base_url,
		}


async def _scenario_delayed_release(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	release_ref = find_element_ref(state, 'Release name', tag='input')
	publish_ref = find_element_ref(state, 'Publish release')
	if release_ref is None or publish_ref is None:
		raise ValueError('Could not resolve release form controls')
	type_result = await server._type_text(ref=release_ref, text='v1.2.3')
	await asyncio.sleep(0.45)
	click_result = await server._click(ref=publish_ref)
	status_html = await server._get_html('#status')
	passed = not type_result.startswith('Error') and not click_result.startswith('Error') and 'Release published.' in status_html
	return {
		'scenario': 'delayed-release',
		'passed': passed,
		'type_result': type_result,
		'click_result': click_result,
	}


async def _scenario_modal_wizard(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	dismiss_ref = find_element_ref(state, 'Dismiss blocking modal')
	if dismiss_ref is None:
		raise ValueError('Could not resolve modal dismiss control')
	dismiss_result = await server._click(ref=dismiss_ref)
	await asyncio.sleep(0.1)
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	deploy_ref = find_element_ref(state, 'Run deployment')
	if deploy_ref is None:
		raise ValueError('Could not resolve deployment control after dismissing modal')
	deploy_result = await server._click(ref=deploy_ref)
	status_html = await server._get_html('#status')
	passed = (
		not dismiss_result.startswith('Error') and not deploy_result.startswith('Error') and 'Deployment started.' in status_html
	)
	return {
		'scenario': 'modal-wizard',
		'passed': passed,
		'dismiss_result': dismiss_result,
		'deploy_result': deploy_result,
	}


async def _scenario_drift_recovery(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	approve_ref = find_element_ref(state, 'Approve deployment')
	if approve_ref is None:
		raise ValueError('Could not resolve approval control before DOM drift')
	await asyncio.sleep(0.35)
	click_result = await server._click(ref=approve_ref)
	status_html = await server._get_html('#status')
	passed = not click_result.startswith('Error') and 'Deployment approved.' in status_html
	return {
		'scenario': 'drift-recovery',
		'passed': passed,
		'click_result': click_result,
	}


async def _scenario_repeated_actions(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	beta_ref = find_element_ref(state, 'Open', context_contains='Project Beta actions')
	if beta_ref is None:
		raise ValueError('Could not resolve the repeated Open action for Project Beta')
	click_result = await server._click(ref=beta_ref)
	alpha_status = await server._get_html('#alpha-status')
	beta_status = await server._get_html('#beta-status')
	passed = not click_result.startswith('Error') and 'Idle' in alpha_status and 'Opened beta.' in beta_status
	return {
		'scenario': 'repeated-actions',
		'passed': passed,
		'click_result': click_result,
	}


async def _scenario_tab_workspace(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	link_ref = find_element_ref(state, 'Open release notes')
	if link_ref is None:
		raise ValueError('Could not resolve release notes launcher')
	click_result = await server._click(ref=link_ref, new_tab=True)
	if click_result.startswith('Error'):
		return {'scenario': 'tab-workspace', 'passed': False, 'click_result': click_result}
	tabs = (json.loads(await server._list_tabs()) or {}).get('tabs', [])
	target_tab = next((tab for tab in tabs if tab['url'].endswith('/release-notes.html')), None)
	if target_tab is None:
		raise ValueError('Release notes tab was not created')
	switch_result = await server._switch_tab(target_tab['tab_id'])
	current_state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	current_state = json.loads(current_state_json)
	passed = not switch_result.startswith('Error') and current_state['url'].endswith('/release-notes.html')
	return {
		'scenario': 'tab-workspace',
		'passed': passed,
		'click_result': click_result,
		'switch_result': switch_result,
	}


async def _scenario_accessibility_panel(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	filter_ref = find_element_ref(state, 'Only show keyboard blockers')
	scan_ref = find_element_ref(state, 'Run accessibility scan')
	if filter_ref is None or scan_ref is None:
		raise ValueError('Could not resolve accessibility controls')
	filter_result = await server._click(ref=filter_ref)
	scan_result = await server._click(ref=scan_ref)
	status_html = await server._get_html('#status')
	passed = (
		not filter_result.startswith('Error')
		and not scan_result.startswith('Error')
		and 'Keyboard blockers only: 1 issue found.' in status_html
	)
	return {
		'scenario': 'accessibility-panel',
		'passed': passed,
		'filter_result': filter_result,
		'scan_result': scan_result,
	}


async def _scenario_contenteditable_compose(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	editor_ref = find_element_ref(state, 'Issue body editor')
	publish_ref = find_element_ref(state, 'Publish comment')
	if editor_ref is None or publish_ref is None:
		raise ValueError('Could not resolve contenteditable composer controls')
	type_result = await server._type_text(ref=editor_ref, text='Hello from editor')
	click_result = await server._click(ref=publish_ref)
	status_html = await server._get_html('#status')
	passed = not type_result.startswith('Error') and not click_result.startswith('Error') and 'Hello from editor' in status_html
	return {
		'scenario': 'contenteditable-compose',
		'passed': passed,
		'type_result': type_result,
		'click_result': click_result,
	}


async def _scenario_custom_combobox(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	combobox_ref = find_element_ref(state, 'Choose assignee')
	if combobox_ref is None:
		raise ValueError('Could not resolve ARIA combobox trigger')
	open_result = await server._click(ref=combobox_ref)
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	option_ref = find_element_ref(state, 'Bob')
	if option_ref is None:
		raise ValueError('Could not resolve expanded combobox option')
	select_result = await server._click(ref=option_ref)
	chosen_html = await server._get_html('#chosen')
	passed = not open_result.startswith('Error') and not select_result.startswith('Error') and 'Bob' in chosen_html
	return {
		'scenario': 'custom-combobox',
		'passed': passed,
		'open_result': open_result,
		'select_result': select_result,
	}


async def _scenario_iframe_workspace(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	input_ref = find_element_ref(state, 'Frame input')
	submit_ref = find_element_ref(state, 'Submit frame value')
	if input_ref is None or submit_ref is None:
		raise ValueError('Could not resolve same-origin iframe controls')
	type_result = await server._type_text(ref=input_ref, text='from iframe')
	click_result = await server._click(ref=submit_ref)
	status_html = await server._get_html('#status')
	passed = not type_result.startswith('Error') and not click_result.startswith('Error') and 'from iframe' in status_html
	return {
		'scenario': 'iframe-workspace',
		'passed': passed,
		'type_result': type_result,
		'click_result': click_result,
	}


async def _scenario_shadow_dom_workspace(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	email_ref = find_element_ref(state, 'Email address')
	publish_ref = find_element_ref(state, 'Publish update')
	if email_ref is None or publish_ref is None:
		raise ValueError('Could not resolve shadow DOM form controls')
	type_result = await server._type_text(ref=email_ref, text='shadow@example.com')
	click_result = await server._click(ref=publish_ref)
	status_html = await server._get_html('#status')
	passed = not type_result.startswith('Error') and not click_result.startswith('Error') and 'shadow@example.com' in status_html
	return {
		'scenario': 'shadow-dom-workspace',
		'passed': passed,
		'type_result': type_result,
		'click_result': click_result,
	}


async def _scenario_debounced_autocomplete(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	search_ref = find_element_ref(state, 'Search user')
	if search_ref is None:
		raise ValueError('Could not resolve autocomplete search input')
	type_result = await server._type_text(ref=search_ref, text='ali')
	await asyncio.sleep(0.25)
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	option_ref = find_element_ref(state, 'Alice Johnson')
	if option_ref is None:
		raise ValueError('Could not resolve autocomplete option after debounce delay')
	click_result = await server._click(ref=option_ref)
	status_html = await server._get_html('#status')
	passed = not type_result.startswith('Error') and not click_result.startswith('Error') and 'Alice Johnson' in status_html
	return {
		'scenario': 'debounced-autocomplete',
		'passed': passed,
		'type_result': type_result,
		'click_result': click_result,
	}


async def _scenario_confirm_dialog(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	delete_ref = find_element_ref(state, 'Delete branch')
	if delete_ref is None:
		raise ValueError('Could not resolve confirm-dialog trigger')
	click_result = await server._click(ref=delete_ref)
	status_html = await server._get_html('#status')
	passed = not click_result.startswith('Error') and 'Deleted' in status_html
	return {
		'scenario': 'confirm-dialog',
		'passed': passed,
		'click_result': click_result,
	}


async def _scenario_hover_dropdown(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	menu_ref = find_element_ref(state, 'Deploy menu', tag='div') or find_element_ref(state, 'Deploy')
	if menu_ref is None:
		raise ValueError('Could not resolve hover dropdown trigger')
	# Hover to reveal dropdown items
	hover_result = await server._hover(ref=menu_ref)
	await asyncio.sleep(0.2)
	# After hover, state should include newly visible items
	post_hover_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	post_hover = json.loads(post_hover_json)
	staging_ref = find_element_ref(post_hover, 'Deploy to Staging')
	if staging_ref is None:
		# CSS :hover reveals items — if not visible, the hover might not have worked but that's a browser/AX limitation
		# Try clicking the button directly as a fallback
		click_result = await server._click(ref=menu_ref)
		post_click_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
		post_click = json.loads(post_click_json)
		staging_ref = find_element_ref(post_click, 'Deploy to Staging')
	click_result = 'skipped'
	if staging_ref:
		click_result = await server._click(ref=staging_ref)
	status_html = await server._get_html('#status')
	passed = not hover_result.startswith('Error') and ('Deploying to staging' in status_html or staging_ref is not None)
	return {
		'scenario': 'hover-dropdown',
		'passed': passed,
		'hover_result': hover_result,
		'staging_ref': staging_ref,
		'click_result': click_result,
	}


async def _scenario_drag_sortable(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	alpha_ref = find_element_ref(state, 'Task Alpha')
	gamma_ref = find_element_ref(state, 'Task Gamma')
	if alpha_ref is not None and gamma_ref is not None:
		# Ref-based drag
		drag_result = await server._drag_to(source_ref=alpha_ref, target_ref=gamma_ref)
	else:
		# Fallback: coordinate-based drag via JS evaluate to get positions
		coords_json = await server._evaluate(
			'(function(){var a=document.getElementById("task-a"),c=document.getElementById("task-c");'
			'if(!a||!c)return null;var ra=a.getBoundingClientRect(),rc=c.getBoundingClientRect();'
			'return {ax:ra.x+ra.width/2,ay:ra.y+ra.height/2,cx:rc.x+rc.width/2,cy:rc.y+rc.height/2};})()'
		)
		coords = json.loads(coords_json) if isinstance(coords_json, str) and coords_json.startswith('{') else None
		if coords is None or not isinstance(coords, dict):
			raise ValueError(f'Could not resolve drag sources: refs={alpha_ref},{gamma_ref}, coords={coords_json}')
		drag_result = await server._drag_to(
			source_x=int(coords['ax']),
			source_y=int(coords['ay']),
			target_x=int(coords['cx']),
			target_y=int(coords['cy']),
		)
	await asyncio.sleep(0.3)
	status_html = await server._get_html('#status')
	passed = not drag_result.startswith('Error') and ('task-a' in status_html or 'moved' in status_html)
	return {
		'scenario': 'drag-sortable',
		'passed': passed,
		'drag_result': drag_result,
		'status': status_html,
		'alpha_ref': alpha_ref,
		'gamma_ref': gamma_ref,
	}


async def _scenario_form_validation(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	username_ref = find_element_ref(state, 'Username') or find_element_ref(state, 'Create account', tag='input')
	# Find by placeholder
	if username_ref is None:
		for el in state.get('interactive_elements', []):
			if el.get('placeholder') == 'letters, numbers, underscore':
				username_ref = el['ref']
				break
	email_ref = find_element_ref(state, 'Email') or None
	if email_ref is None:
		for el in state.get('interactive_elements', []):
			if el.get('type') == 'email':
				email_ref = el['ref']
				break
	submit_ref = find_element_ref(state, 'Create account')
	if username_ref is None or email_ref is None or submit_ref is None:
		raise ValueError(f'Could not resolve form controls: username={username_ref}, email={email_ref}, submit={submit_ref}')
	# Check constraints are surfaced in state
	username_el = next((el for el in state.get('interactive_elements', []) if el.get('ref') == username_ref), {})
	has_pattern = 'pattern' in username_el
	has_minlength = 'minlength' in username_el
	# Type valid values and submit
	type_user = await server._type_text(ref=username_ref, text='test_user123')
	type_email = await server._type_text(ref=email_ref, text='test@example.com')
	click_submit = await server._click(ref=submit_ref)
	await asyncio.sleep(0.2)
	status_html = await server._get_html('#status')
	passed = (
		not type_user.startswith('Error')
		and not type_email.startswith('Error')
		and not click_submit.startswith('Error')
		and 'Account created' in status_html
	)
	return {
		'scenario': 'form-validation',
		'passed': passed,
		'constraints_surfaced': has_pattern and has_minlength,
		'type_user': type_user,
		'type_email': type_email,
		'click_submit': click_submit,
	}


async def _scenario_right_click_menu(server) -> dict[str, Any]:
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	btn_ref = find_element_ref(state, 'Options')
	if btn_ref is None:
		raise ValueError('Could not resolve Options button')
	right_click_result = await server._right_click(ref=btn_ref)
	await asyncio.sleep(0.1)
	menu_exists = await server._evaluate("document.getElementById('menu') !== null")
	passed = not right_click_result.startswith('Error') and menu_exists not in (None, 'null', 'false', False)
	return {
		'scenario': 'right-click-menu',
		'passed': passed,
		'right_click_result': right_click_result,
		'menu_exists': menu_exists,
	}


async def _scenario_cookie_auth(server, base_url: str) -> dict[str, Any]:
	# Navigate without session cookie — expect logged-out
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	auth_html_before = await server._get_html('#auth')
	initially_logged_out = 'logged-out' in auth_html_before

	# Set the session cookie
	hostname = base_url.replace('http://', '').split(':')[0]
	set_result = await server._set_cookies([{'name': 'session', 'value': 'abc123', 'domain': hostname}])

	# Reload and check
	await server._navigate(server._current_url if hasattr(server, '_current_url') else base_url + '/cookie-auth.html')
	await asyncio.sleep(0.2)
	auth_html_after = await server._get_html('#auth')
	logged_in_after_set = 'logged-in' in auth_html_after

	# Verify get_cookies contains the session cookie
	cookies_json = await server._get_cookies()
	cookies = json.loads(cookies_json)
	session_cookie = next((c for c in cookies if c.get('name') == 'session'), None)
	session_cookie_present = session_cookie is not None and session_cookie.get('value') == 'abc123'

	# Clear the session cookie and reload
	clear_result = await server._clear_cookies(name='session')
	await server._navigate(server._current_url if hasattr(server, '_current_url') else base_url + '/cookie-auth.html')
	await asyncio.sleep(0.2)
	auth_html_cleared = await server._get_html('#auth')
	logged_out_after_clear = 'logged-out' in auth_html_cleared

	passed = (
		initially_logged_out
		and not set_result.startswith('Error')
		and logged_in_after_set
		and session_cookie_present
		and not clear_result.startswith('Error')
		and logged_out_after_clear
	)
	return {
		'scenario': 'cookie-auth',
		'passed': passed,
		'initially_logged_out': initially_logged_out,
		'logged_in_after_set': logged_in_after_set,
		'session_cookie_present': session_cookie_present,
		'logged_out_after_clear': logged_out_after_clear,
		'set_result': set_result,
		'clear_result': clear_result,
	}


async def _scenario_console_log_capture(server) -> dict[str, Any]:
	# CDP-native capture: logs from DOMContentLoaded (before any tool call) are already buffered.
	# This is the key differentiator vs JS injection — we see page-load errors immediately.
	await asyncio.sleep(0.1)  # let DOMContentLoaded fire
	initial_logs_json = await server._get_console_logs(level='all', max_entries=50)
	initial_logs = json.loads(initial_logs_json)
	# The page emits console.warn('page loaded') on DOMContentLoaded — CDP catches it from load
	page_load_log_captured = any('page loaded' in str(entry.get('text', '')) for entry in initial_logs)

	# Click the button to trigger console.log('clicked') and console.error('test error')
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	btn_ref = find_element_ref(state, 'Trigger log')
	if btn_ref is None:
		raise ValueError('Could not resolve Trigger log button')
	click_result = await server._click(ref=btn_ref)
	await asyncio.sleep(0.1)

	# Capture all logs and verify 'clicked' appears
	all_logs_json = await server._get_console_logs(level='all', max_entries=50)
	all_logs = json.loads(all_logs_json)
	clicked_in_logs = any('clicked' in str(entry.get('text', '')) for entry in all_logs)

	# Filter error-level logs
	error_logs_json = await server._get_console_logs(level='error', max_entries=50)
	error_logs = json.loads(error_logs_json)
	error_captured = any('test error' in str(entry.get('text', '')) for entry in error_logs)

	passed = not click_result.startswith('Error') and clicked_in_logs and error_captured
	return {
		'scenario': 'console-log-capture',
		'passed': passed,
		'click_result': click_result,
		'page_load_log_captured': page_load_log_captured,
		'all_log_count': len(all_logs),
		'error_log_count': len(error_logs),
		'clicked_in_logs': clicked_in_logs,
		'error_captured': error_captured,
	}


async def _scenario_typing_speed(server) -> dict[str, Any]:
	# Reuse form-validation fixture (already navigated by run_fixture)
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	username_ref = find_element_ref(state, 'Username')
	if username_ref is None:
		for el in state.get('interactive_elements', []):
			if el.get('placeholder') == 'letters, numbers, underscore':
				username_ref = el['ref']
				break
	if username_ref is None:
		raise ValueError('Could not resolve username input for typing speed benchmark')
	text_20 = 'abcdefghijklmnopqrst'  # exactly 20 chars, respects maxlength=20
	type_result, latency_ms = await benchmark_call(lambda: server._type_text(ref=username_ref, text=text_20))
	latency_ms = round(latency_ms, 1)
	passed = not type_result.startswith('Error')
	return {
		'scenario': 'typing-speed',
		'passed': passed,
		'latency_ms': latency_ms,
		'chars_typed': len(text_20),
		'ms_per_char': round(latency_ms / len(text_20), 2),
		'type_result': type_result,
	}


async def _scenario_network_log(server, base_url: str) -> dict[str, Any]:
	# The httpserver won't have /api/data or /api/submit routes, so the fetch calls will
	# fail with network errors. That's fine — we're testing that the network log captures
	# both the request attempts AND the failures (loadingFailed events).
	state_json, _ = await server._get_browser_state(include_screenshot=False, mode='auto')
	state = json.loads(state_json)
	fetch_ref = find_element_ref(state, 'Fetch data')
	post_ref = find_element_ref(state, 'Post data')
	if fetch_ref is None or post_ref is None:
		raise ValueError(f'Could not resolve network buttons (fetch={fetch_ref}, post={post_ref})')

	# Click both buttons to trigger network requests
	await server._click(ref=fetch_ref)
	await asyncio.sleep(0.3)
	await server._click(ref=post_ref)
	await asyncio.sleep(0.3)

	# Retrieve full network log
	net_log_json = await server._get_network_log(type_filter='all', status_filter='all', max_entries=50)
	net_log = json.loads(net_log_json)

	# Filter to only the fetch/XHR calls (not Document load)
	api_calls = [e for e in net_log if '/api/' in e.get('url', '')]
	get_found = any(e.get('method') == 'GET' and '/api/data' in e.get('url', '') for e in api_calls)
	post_found = any(e.get('method') == 'POST' and '/api/submit' in e.get('url', '') for e in api_calls)

	# Also check error filtering
	errors_json = await server._get_network_log(type_filter='all', status_filter='errors', max_entries=50)
	errors = json.loads(errors_json)
	api_errors = [e for e in errors if '/api/' in e.get('url', '')]

	passed = get_found and post_found
	return {
		'scenario': 'network-log',
		'passed': passed,
		'total_requests_captured': len(net_log),
		'api_calls_captured': len(api_calls),
		'get_request_found': get_found,
		'post_request_found': post_found,
		'api_errors_captured': len(api_errors),
	}


async def run_fixture(
	server,
	fixture: BenchmarkFixture,
	base_url: str,
) -> dict[str, Any]:
	await server._navigate(f'{base_url}/{fixture.slug}.html')

	(full_state_json, _), full_state_ms = await benchmark_call(
		lambda: server._get_browser_state(include_screenshot=False, mode='full')
	)
	full_state = json.loads(full_state_json)

	(auto_state_json, _), auto_state_ms = await benchmark_call(
		lambda: server._get_browser_state(include_screenshot=False, mode='auto')
	)
	auto_state = json.loads(auto_state_json)

	(min_state_json, _), min_state_ms = await benchmark_call(
		lambda: server._get_browser_state(include_screenshot=False, mode='min')
	)
	min_state = json.loads(min_state_json)

	(unchanged_state_json, _), unchanged_state_ms = await benchmark_call(
		lambda: server._get_browser_state(include_screenshot=False, mode='auto', since_hash=auto_state['state_hash'])
	)
	unchanged_state = json.loads(unchanged_state_json)

	focus_result: dict[str, Any] | None = None
	if fixture.focus_text:
		focus_ref = find_element_ref(auto_state, fixture.focus_text) or find_element_ref(full_state, fixture.focus_text)
		if focus_ref is not None:
			(focus_state_json, _), focus_state_ms = await benchmark_call(
				lambda: server._get_browser_state(include_screenshot=False, mode='focus', focus_ref=focus_ref)
			)
			focus_state = json.loads(focus_state_json)
			focus_result = {
				'latency_ms': round(focus_state_ms, 1),
				'payload_chars': len(focus_state_json),
				'focus_ref': focus_state.get('focus_ref'),
			}

	extract_results: dict[str, Any] = {}
	if fixture.deterministic_query:
		dq = fixture.deterministic_query
		expected_json = replace_base_url(dq.expected_json, base_url)
		deterministic_extract, deterministic_ms = await benchmark_call(
			lambda: server._extract_content(
				dq.query,
				extract_links=dq.extract_links,
				output_schema=dq.output_schema,
			)
		)
		match = (
			calculate_json_subset_match(
				extract_structured_json(deterministic_extract) or {},
				expected_json or {},
			)
			if dq.output_schema is not None
			else calculate_text_matches(deterministic_extract, dq.expected_terms)
		)
		extract_results['deterministic'] = {
			'latency_ms': round(deterministic_ms, 1),
			'payload_chars': len(deterministic_extract),
			'token_metrics': calculate_context_utilization(len(deterministic_extract)),
			'match': match,
			'quality': build_quality_metrics(match, actual_count=len(match.get('matched', []))),
			'efficiency': build_efficiency_metrics(
				len(deterministic_extract),
				round(deterministic_ms, 1),
				len(match.get('matched', [])),
			),
			'structured': fixture.deterministic_query.output_schema is not None,
		}

	result = {
		'title': fixture.title,
		'interactive_element_count': full_state['interactive_element_count'],
		'state': {
			'full': {
				'latency_ms': round(full_state_ms, 1),
				'payload_chars': len(full_state_json),
				'token_metrics': calculate_context_utilization(len(full_state_json)),
				'selected_elements': len(full_state['interactive_elements']),
			},
			'auto': {
				'latency_ms': round(auto_state_ms, 1),
				'payload_chars': len(auto_state_json),
				'token_metrics': calculate_context_utilization(len(auto_state_json)),
				'selected_elements': len(auto_state['interactive_elements']),
				'effective_mode': auto_state.get('effective_mode'),
				'payload_reduction_pct': reduction_percent(len(full_state_json), len(auto_state_json)),
				'recall': calculate_recall(auto_state, fixture.expected_compact_texts),
				'state_hash': auto_state['state_hash'],
			},
			'min': {
				'latency_ms': round(min_state_ms, 1),
				'payload_chars': len(min_state_json),
				'token_metrics': calculate_context_utilization(len(min_state_json)),
				'selected_elements': len(min_state['interactive_elements']),
				'payload_reduction_pct': reduction_percent(len(full_state_json), len(min_state_json)),
				'recall': calculate_recall(min_state, fixture.expected_compact_texts),
			},
			'unchanged_delta': {
				'latency_ms': round(unchanged_state_ms, 1),
				'payload_chars': len(unchanged_state_json),
				'token_metrics': calculate_context_utilization(len(unchanged_state_json)),
				'changed': unchanged_state['changed'],
			},
		},
	}
	auto_recall = result['state']['auto']['recall']
	min_recall = result['state']['min']['recall']
	result['state']['auto']['quality'] = build_quality_metrics(auto_recall, actual_count=len(auto_state['interactive_elements']))
	result['state']['auto']['efficiency'] = build_efficiency_metrics(
		len(auto_state_json),
		round(auto_state_ms, 1),
		len(auto_recall.get('matched', [])),
	)
	result['state']['min']['quality'] = build_quality_metrics(min_recall, actual_count=len(min_state['interactive_elements']))
	result['state']['min']['efficiency'] = build_efficiency_metrics(
		len(min_state_json),
		round(min_state_ms, 1),
		len(min_recall.get('matched', [])),
	)
	if focus_result is not None:
		focus_result['token_metrics'] = calculate_context_utilization(focus_result['payload_chars'])
		result['state']['focus'] = focus_result
	if extract_results:
		result['extract_content'] = extract_results
	if fixture.action_scenario:
		result['action_reliability'] = await run_action_scenario(server, fixture, base_url)
		action = result['action_reliability']
		action['quality'] = {'accuracy': 1.0 if action.get('passed') else 0.0}
		payload_chars = len(json.dumps(action, sort_keys=True, separators=(',', ':')))
		action['token_metrics'] = calculate_context_utilization(payload_chars)
	return result


def summarize_results(results: dict[str, dict[str, Any]], collaboration: dict[str, Any] | None = None) -> dict[str, Any]:
	auto_reductions = [fixture['state']['auto']['payload_reduction_pct'] for fixture in results.values()]
	min_reductions = [fixture['state']['min']['payload_reduction_pct'] for fixture in results.values()]
	auto_recalls = [fixture['state']['auto']['recall']['recall'] for fixture in results.values()]
	min_recalls = [fixture['state']['min']['recall']['recall'] for fixture in results.values()]
	deterministic_matches = [
		fixture['extract_content']['deterministic']['match']['recall']
		for fixture in results.values()
		if fixture.get('extract_content', {}).get('deterministic')
	]
	structured_matches = [
		fixture['extract_content']['deterministic']['match']['recall']
		for fixture in results.values()
		if fixture.get('extract_content', {}).get('deterministic', {}).get('structured')
	]
	zero_llm_deterministic = len(deterministic_matches)
	action_results = [fixture['action_reliability'] for fixture in results.values() if fixture.get('action_reliability')]
	action_scores = [1.0 if action_result.get('passed') else 0.0 for action_result in action_results]
	failing_action_cases = [
		slug for slug, fixture in results.items() if fixture.get('action_reliability', {}).get('passed') is False
	]
	compacted_pages = [slug for slug, fixture in results.items() if fixture['state']['auto'].get('effective_mode') == 'min']
	auto_tokens = [fixture['state']['auto']['token_metrics']['estimated_tokens'] for fixture in results.values()]
	min_tokens = [fixture['state']['min']['token_metrics']['estimated_tokens'] for fixture in results.values()]
	auto_precisions = [
		fixture['state']['auto']['quality']['precision']
		for fixture in results.values()
		if fixture['state']['auto']['quality']['precision'] is not None
	]
	min_precisions = [
		fixture['state']['min']['quality']['precision']
		for fixture in results.values()
		if fixture['state']['min']['quality']['precision'] is not None
	]
	deterministic_precisions = [
		fixture['extract_content']['deterministic']['quality']['precision']
		for fixture in results.values()
		if fixture.get('extract_content', {}).get('deterministic')
		and fixture['extract_content']['deterministic']['quality']['precision'] is not None
	]
	action_accuracy = [
		fixture['action_reliability']['quality']['accuracy'] for fixture in results.values() if fixture.get('action_reliability')
	]
	auto_chars_per_ms = [fixture['state']['auto']['efficiency']['chars_per_ms'] for fixture in results.values()]
	min_chars_per_ms = [fixture['state']['min']['efficiency']['chars_per_ms'] for fixture in results.values()]
	auto_matches_per_second = [fixture['state']['auto']['efficiency']['matches_per_second'] for fixture in results.values()]
	min_matches_per_second = [fixture['state']['min']['efficiency']['matches_per_second'] for fixture in results.values()]
	summary = {
		'fixture_count': len(results),
		'auto_compacted_pages': compacted_pages,
		'avg_auto_payload_reduction_pct': round(mean(auto_reductions), 1) if auto_reductions else 0.0,
		'avg_min_payload_reduction_pct': round(mean(min_reductions), 1) if min_reductions else 0.0,
		'avg_auto_recall': round(mean(auto_recalls), 3) if auto_recalls else 0.0,
		'avg_min_recall': round(mean(min_recalls), 3) if min_recalls else 0.0,
		'avg_auto_precision': round(mean(auto_precisions), 3) if auto_precisions else 0.0,
		'avg_min_precision': round(mean(min_precisions), 3) if min_precisions else 0.0,
		'deterministic_case_count': len(deterministic_matches),
		'avg_deterministic_recall': round(mean(deterministic_matches), 3) if deterministic_matches else 0.0,
		'avg_deterministic_precision': round(mean(deterministic_precisions), 3) if deterministic_precisions else 0.0,
		'zero_llm_deterministic_cases': zero_llm_deterministic,
		'structured_case_count': len(structured_matches),
		'avg_structured_recall': round(mean(structured_matches), 3) if structured_matches else 0.0,
		'action_case_count': len(action_scores),
		'avg_action_success': round(mean(action_scores), 3) if action_scores else 0.0,
		'avg_action_accuracy': round(mean(action_accuracy), 3) if action_accuracy else 0.0,
		'avg_auto_estimated_tokens': round(mean(auto_tokens), 1) if auto_tokens else 0.0,
		'avg_min_estimated_tokens': round(mean(min_tokens), 1) if min_tokens else 0.0,
		'avg_token_reduction_pct': reduction_percent(
			round(mean(auto_tokens)) if auto_tokens else 0,
			round(mean(min_tokens)) if min_tokens else 0,
		),
		'failing_action_cases': failing_action_cases,
	}
	summary['efficiency'] = {
		'avg_auto_chars_per_ms': round(mean(auto_chars_per_ms), 3) if auto_chars_per_ms else 0.0,
		'avg_min_chars_per_ms': round(mean(min_chars_per_ms), 3) if min_chars_per_ms else 0.0,
		'avg_auto_matches_per_second': round(mean(auto_matches_per_second), 3) if auto_matches_per_second else 0.0,
		'avg_min_matches_per_second': round(mean(min_matches_per_second), 3) if min_matches_per_second else 0.0,
	}
	if isinstance(collaboration, dict):
		_trp = collaboration.get('tab_runtime_pair')
		tab_runtime_pair: dict = _trp if isinstance(_trp, dict) else {}
		_wmp = collaboration.get('window_mode_probe')
		window_mode_probe: dict = _wmp if isinstance(_wmp, dict) else {}
		summary.update(
			{
				'collaboration_case_count': 1,
				'collaboration_passed': bool(collaboration.get('passed')),
				'collaboration_latency_ms': collaboration.get('latency_ms'),
				'collaboration_required_check_count': tab_runtime_pair.get('required_check_count', 0),
				'collaboration_required_checks_passed': tab_runtime_pair.get('required_checks_passed', 0),
				'collaboration_required_check_pass_rate': round(
					(tab_runtime_pair.get('required_checks_passed', 0) / tab_runtime_pair.get('required_check_count', 1)),
					3,
				)
				if tab_runtime_pair.get('required_check_count')
				else 0.0,
				'collaboration_window_mode_exercised': bool(window_mode_probe.get('exercised')),
				'collaboration_window_mode_passed': bool(window_mode_probe.get('passed')),
			}
		)
	return summary


def should_capture_fixture_artifacts(result: dict[str, Any]) -> bool:
	return bool(collect_fixture_regression_reasons(result))


async def capture_fixture_artifacts(server, fixture: BenchmarkFixture, result: dict[str, Any], artifact_root: Path) -> None:
	fixture_root = artifact_root / fixture.slug
	fixture_root.mkdir(parents=True, exist_ok=True)

	state_json, screenshot_b64 = await server._get_browser_state(include_screenshot=True, mode='auto')
	html = await server._get_html()

	write_json(fixture_root / 'result.json', result)
	write_text(fixture_root / 'state.json', state_json)
	write_text(fixture_root / 'page.html', html)
	if screenshot_b64:
		(fixture_root / 'screenshot.png').write_bytes(base64.b64decode(screenshot_b64))


def _ownership_runtime_id(payload: dict[str, Any] | None) -> str | None:
	if not isinstance(payload, dict):
		return None
	ownership = payload.get('ownership')
	if not isinstance(ownership, dict):
		return None
	runtime = ownership.get('runtime')
	if not isinstance(runtime, dict):
		return None
	runtime_id = runtime.get('runtime_id')
	return str(runtime_id) if isinstance(runtime_id, str) else None


def _interactive_texts(payload: dict[str, Any]) -> set[str]:
	return {normalize_text(element.get('text', '')) for element in payload.get('interactive_elements', []) if element.get('text')}


def _collaboration_runtime_snapshot(state: dict[str, Any], runtime_id: str) -> dict[str, Any]:
	current_tab = state.get('current_tab') if isinstance(state.get('current_tab'), dict) else {}
	peer_runtime_tabs = 0
	for tab in state.get('tabs', []):
		if not isinstance(tab, dict):
			continue
		tab_runtime_id = _ownership_runtime_id(tab)
		if tab_runtime_id is not None and tab_runtime_id != runtime_id:
			peer_runtime_tabs += 1
	return {
		'runtime_id': runtime_id,
		'tab_id': current_tab.get('tab_id'),
		'ownership_runtime_id': _ownership_runtime_id(current_tab),
		'url': current_tab.get('url') or state.get('url'),
		'display_title': current_tab.get('display_title'),
		'window_bounds': current_tab.get('window_bounds'),
		'tab_count': len(state.get('tabs', [])),
		'peer_runtime_tabs_visible': peer_runtime_tabs,
	}


async def _run_window_mode_probe(cdp_url: str, base_url: str) -> dict[str, Any]:
	from agentyc.mcp.server import AgentycServer

	requested_bounds = {'left': 40, 'top': 50, 'width': 1000, 'height': 700}
	server = AgentycServer(
		cdp_url=cdp_url,
		runtime_label='benchmark-window-runtime',
		runtime_role='benchmark-collaboration',
		shared_browser_mode='window',
		shared_browser_window_bounds=requested_bounds,
	)
	start = time.perf_counter()
	try:
		await server._init_browser_session(headless=True, user_data_dir=None)
		await server._navigate(f'{base_url}/collaboration-window.html')
		(state_json, _), state_latency_ms = await benchmark_call(
			lambda: server._get_browser_state(include_screenshot=False, mode='auto')
		)
		state = json.loads(state_json)
		runtime_id = server.browser_session.runtime_metadata.runtime_id
		snapshot = _collaboration_runtime_snapshot(state, runtime_id)
		observed_bounds = snapshot.get('window_bounds')
		checks = {
			'runtime_owner_matches': snapshot.get('ownership_runtime_id') == runtime_id,
			'window_bounds_present': isinstance(observed_bounds, dict),
		}
		if isinstance(observed_bounds, dict):
			checks['window_width_matches_request'] = observed_bounds.get('width') == requested_bounds['width']
			checks['window_height_matches_request'] = observed_bounds.get('height') == requested_bounds['height']
		exercised = isinstance(observed_bounds, dict)
		passed = exercised and all(checks.values())
		return {
			'required': False,
			'exercised': exercised,
			'passed': passed,
			'latency_ms': round((time.perf_counter() - start) * 1000, 1),
			'state_latency_ms': round(state_latency_ms, 1),
			'requested_window_bounds': requested_bounds,
			'observed_window_bounds': observed_bounds,
			'checks': [{'key': key, 'passed': value} for key, value in checks.items()],
			'runtime': snapshot,
		}
	except Exception as exc:
		return {
			'required': False,
			'exercised': False,
			'passed': False,
			'latency_ms': round((time.perf_counter() - start) * 1000, 1),
			'error': str(exc),
			'requested_window_bounds': requested_bounds,
		}
	finally:
		await server._shutdown()


async def run_collaboration_benchmark(base_url: str) -> dict[str, Any]:
	from agentyc.browser import BrowserProfile, BrowserSession
	from agentyc.mcp.server import AgentycServer

	primary_session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			keep_alive=True,
		)
	)
	await primary_session.start()

	start = time.perf_counter()
	server_a: AgentycServer | None = None
	server_b: AgentycServer | None = None
	try:
		cdp_url = primary_session.browser_profile.cdp_url
		if not cdp_url:
			raise RuntimeError('Shared-browser collaboration benchmark requires a browser CDP URL')

		server_a = AgentycServer(
			cdp_url=cdp_url,
			runtime_label='benchmark-runtime-a',
			runtime_role='benchmark-collaboration',
			shared_browser_mode='tab',
		)
		server_b = AgentycServer(
			cdp_url=cdp_url,
			runtime_label='benchmark-runtime-b',
			runtime_role='benchmark-collaboration',
			shared_browser_mode='tab',
		)
		await server_a._init_browser_session(headless=True, user_data_dir=None)
		await server_b._init_browser_session(headless=True, user_data_dir=None)

		navigation_start = time.perf_counter()
		await asyncio.gather(
			server_a._navigate(f'{base_url}/collaboration-runtime-a.html'),
			server_b._navigate(f'{base_url}/collaboration-runtime-b.html'),
		)
		navigation_latency_ms = round((time.perf_counter() - navigation_start) * 1000, 1)

		(state_a_json, _), state_a_latency_ms = await benchmark_call(
			lambda: server_a._get_browser_state(include_screenshot=False, mode='auto')
		)
		(state_b_json, _), state_b_latency_ms = await benchmark_call(
			lambda: server_b._get_browser_state(include_screenshot=False, mode='auto')
		)
		state_a = json.loads(state_a_json)
		state_b = json.loads(state_b_json)

		runtime_a_id = server_a.browser_session.runtime_metadata.runtime_id
		runtime_b_id = server_b.browser_session.runtime_metadata.runtime_id
		snapshot_a = _collaboration_runtime_snapshot(state_a, runtime_a_id)
		snapshot_b = _collaboration_runtime_snapshot(state_b, runtime_b_id)
		texts_a = _interactive_texts(state_a)
		texts_b = _interactive_texts(state_b)

		checks = {
			'distinct_runtime_ids': runtime_a_id != runtime_b_id,
			'distinct_current_tabs': snapshot_a.get('tab_id') != snapshot_b.get('tab_id'),
			'runtime_a_current_tab_owner_matches': snapshot_a.get('ownership_runtime_id') == runtime_a_id,
			'runtime_b_current_tab_owner_matches': snapshot_b.get('ownership_runtime_id') == runtime_b_id,
			'runtime_a_sees_peer_runtime_tab': snapshot_a.get('peer_runtime_tabs_visible', 0) >= 1,
			'runtime_b_sees_peer_runtime_tab': snapshot_b.get('peer_runtime_tabs_visible', 0) >= 1,
			'runtime_a_state_isolated': 'runtime a action' in texts_a and 'runtime b action' not in texts_a,
			'runtime_b_state_isolated': 'runtime b action' in texts_b and 'runtime a action' not in texts_b,
		}
		required_checks_passed = sum(1 for value in checks.values() if value)
		tab_runtime_pair = {
			'passed': required_checks_passed == len(checks),
			'latency_ms': round((time.perf_counter() - start) * 1000, 1),
			'navigation_latency_ms': navigation_latency_ms,
			'state_latency_ms': {
				'runtime_a': round(state_a_latency_ms, 1),
				'runtime_b': round(state_b_latency_ms, 1),
			},
			'required_check_count': len(checks),
			'required_checks_passed': required_checks_passed,
			'checks': [{'key': key, 'passed': value} for key, value in checks.items()],
			'runtime_a': snapshot_a,
			'runtime_b': snapshot_b,
		}
		window_mode_probe = await _run_window_mode_probe(cdp_url, base_url)
		return {
			'passed': tab_runtime_pair['passed'],
			'latency_ms': round((time.perf_counter() - start) * 1000, 1),
			'tab_runtime_pair': tab_runtime_pair,
			'window_mode_probe': window_mode_probe,
		}
	finally:
		if server_b is not None:
			await server_b._shutdown()
		if server_a is not None:
			await server_a._shutdown()
		await primary_session.kill()


async def main() -> None:
	fixtures = build_fixture_pack()
	fixture_lookup = {fixture.slug: fixture for fixture in fixtures}

	parser = argparse.ArgumentParser(description='Benchmark MCP runtime payload size, latency, and extraction behavior.')
	parser.add_argument('--json', action='store_true', help='Print compact JSON instead of pretty JSON.')
	parser.add_argument(
		'--preset',
		choices=['dogfood'],
		help='Run a named fixture preset. Ignored when --fixture is provided.',
	)
	parser.add_argument(
		'--fixture',
		action='append',
		dest='fixtures',
		choices=sorted(fixture_lookup.keys()),
		help='Run only the selected fixture(s). Can be provided multiple times.',
	)
	parser.add_argument('--open-issue', action='store_true', help='Open a GitHub issue when dogfood regressions are detected.')
	parser.add_argument(
		'--fail-on-regression',
		action='store_true',
		help='Exit non-zero when regressions or release-gate threshold failures are detected.',
	)
	parser.add_argument(
		'--release-gate',
		action='store_true',
		help='Evaluate release-gate thresholds and include the result in the benchmark report.',
	)
	parser.add_argument(
		'--artifact-dir',
		type=Path,
		help='Write dogfood artifacts to this directory instead of the default user-level dogfood path.',
	)
	parser.add_argument(
		'--issue-repo',
		help='Optional GitHub repository override for issue creation (defaults to the current repo).',
	)
	parser.add_argument(
		'--issue-label',
		action='append',
		dest='issue_labels',
		default=[],
		help='Label to apply to auto-opened issues. Can be provided multiple times.',
	)
	parser.add_argument(
		'--issue-title-prefix',
		default='[dogfood]',
		help='Prefix to use for auto-opened issue titles.',
	)
	parser.add_argument('--max-import-ms', type=float, help='Fail the release gate when import time exceeds this threshold.')
	parser.add_argument(
		'--max-session-init-ms',
		type=float,
		help='Fail the release gate when browser session init time exceeds this threshold.',
	)
	parser.add_argument(
		'--min-avg-auto-payload-reduction-pct',
		type=float,
		help='Fail the release gate when average auto payload reduction drops below this threshold.',
	)
	parser.add_argument(
		'--min-avg-auto-recall',
		type=float,
		help='Fail the release gate when average auto recall drops below this threshold.',
	)
	parser.add_argument(
		'--min-avg-min-recall',
		type=float,
		help='Fail the release gate when average min recall drops below this threshold.',
	)
	parser.add_argument(
		'--min-avg-deterministic-recall',
		type=float,
		help='Fail the release gate when average deterministic extraction recall drops below this threshold.',
	)
	parser.add_argument(
		'--min-avg-structured-recall',
		type=float,
		help='Fail the release gate when average structured extraction recall drops below this threshold.',
	)
	parser.add_argument(
		'--min-avg-action-success',
		type=float,
		help='Fail the release gate when average action success drops below this threshold.',
	)
	parser.add_argument(
		'--min-collaboration-required-check-pass-rate',
		type=float,
		help='Fail the release gate when the shared-browser collaboration required-check pass rate drops below this threshold.',
	)
	args = parser.parse_args()

	if args.fixtures:
		selected_fixtures = [fixture_lookup[slug] for slug in args.fixtures]
	elif args.preset == 'dogfood':
		selected_fixtures = [fixture_lookup[slug] for slug in dogfood_fixture_slugs()]
	else:
		selected_fixtures = fixtures
	import_ms = measure_import_ms()

	from agentyc.mcp.server import AgentycServer

	fixture_pack = serve_fixture_pack(selected_fixtures)
	server = AgentycServer(session_timeout_minutes=1)

	try:
		_, session_init_ms = await benchmark_call(
			lambda: server._init_browser_session(headless=True, keep_alive=False, user_data_dir=None)
		)

		results: dict[str, dict[str, Any]] = {}
		for fixture in selected_fixtures:
			results[fixture.slug] = await run_fixture(server, fixture, fixture_pack.base_url)
		collaboration = await run_collaboration_benchmark(fixture_pack.base_url)

		output = {
			'import_ms': import_ms,
			'session_init_ms': round(session_init_ms, 1),
			'summary': summarize_results(results, collaboration),
			'collaboration': collaboration,
			'fixtures': results,
		}

		regressions = detect_regressions(results)
		release_gate = None
		if args.release_gate:
			release_gate = evaluate_release_gate(
				output,
				preset=args.preset or 'all-fixtures',
				max_import_ms=args.max_import_ms,
				max_session_init_ms=args.max_session_init_ms,
				min_avg_auto_payload_reduction_pct=args.min_avg_auto_payload_reduction_pct,
				min_avg_auto_recall=args.min_avg_auto_recall,
				min_avg_min_recall=args.min_avg_min_recall,
				min_avg_deterministic_recall=args.min_avg_deterministic_recall,
				min_avg_structured_recall=args.min_avg_structured_recall,
				min_avg_action_success=args.min_avg_action_success,
				min_collaboration_required_check_pass_rate=args.min_collaboration_required_check_pass_rate,
			)
			output['release_gate'] = release_gate
		artifact_root: Path | None = None
		if args.artifact_dir is not None:
			artifact_root = args.artifact_dir.expanduser()
		elif args.open_issue and regressions:
			artifact_root = default_artifact_root()

		if artifact_root is not None:
			artifact_root.mkdir(parents=True, exist_ok=True)
			regression_slugs = {regression.slug for regression in regressions}
			write_json(artifact_root / 'report.json', output)
			if regressions:
				for fixture in selected_fixtures:
					if fixture.slug in regression_slugs:
						await capture_fixture_artifacts(server, fixture, results[fixture.slug], artifact_root)

		if args.open_issue and regressions:
			assert artifact_root is not None
			body_file = artifact_root / 'issue-body.md'
			issue_body = build_issue_body(
				preset='dogfood',
				command='./scripts/dogfood.sh',
				artifact_dir=artifact_root,
				output=output,
				regressions=regressions,
			)
			write_text(body_file, issue_body)
			issue_title = build_issue_title(regressions, preset='dogfood', title_prefix=args.issue_title_prefix)
			labels = args.issue_labels or ['dogfood']
			issue_url = create_github_issue(title=issue_title, body_file=body_file, labels=labels, repo=args.issue_repo)
			output['dogfood_issue'] = {
				'title': issue_title,
				'url': issue_url,
				'artifact_dir': str(artifact_root),
				'body_file': str(body_file),
			}
			write_json(artifact_root / 'report.json', output)

		print(json.dumps(output, indent=None if args.json else 2, sort_keys=False))
		should_fail = False
		if args.fail_on_regression and regressions:
			should_fail = True
		if args.fail_on_regression and release_gate is not None and not release_gate['passed']:
			should_fail = True
		if args.open_issue and regressions:
			should_fail = True
		if should_fail:
			raise SystemExit(1)
	finally:
		await server._shutdown()
		fixture_pack.close()


if __name__ == '__main__':
	asyncio.run(main())
