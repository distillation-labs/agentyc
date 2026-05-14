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
							'url': '__BASE_URL__/search-results/auth-quickstart',
						},
						{
							'title': 'Webhook retries',
							'url': '__BASE_URL__/search-results/webhook-retries',
						},
					],
				},
			),
			focus_text='Search documentation',
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
	]


def dogfood_fixture_slugs() -> list[str]:
	return [
		'dense-catalog',
		'long-docs',
		'workflow-form',
		'pricing-table',
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
				<li><a href="/search-results/auth-quickstart">Auth quickstart</a> - Learn how to issue session tokens.</li>
				<li><a href="/search-results/webhook-retries">Webhook retries</a> - Tune retry windows for failed deliveries.</li>
				<li><a href="/search-results/cache-tags">Cache tags</a> - Invalidate stale cached pages safely.</li>
			</ol>
			<nav aria-label="Pagination">
				<a href="/search-results?page=2">Next page</a>
			</nav>
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


def serve_fixture_pack(fixtures: list[BenchmarkFixture]) -> ServedFixturePack:
	root = tempfile.TemporaryDirectory(prefix='agentyc-benchmark-')
	root_path = Path(root.name)
	for fixture in fixtures:
		(root_path / f'{fixture.slug}.html').write_text(fixture.html, encoding='utf-8')
		for relative_path, content in fixture.extra_files.items():
			(root_path / relative_path).write_text(content, encoding='utf-8')
	(root_path / 'release-notes.html').write_text(make_release_notes_html(), encoding='utf-8')

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
	tabs = json.loads(await server._list_tabs())
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
		expected_json = replace_base_url(fixture.deterministic_query.expected_json, base_url)
		deterministic_extract, deterministic_ms = await benchmark_call(
			lambda: server._extract_content(
				fixture.deterministic_query.query,
				extract_links=fixture.deterministic_query.extract_links,
				output_schema=fixture.deterministic_query.output_schema,
			)
		)
		match = (
			calculate_json_subset_match(
				extract_structured_json(deterministic_extract) or {},
				expected_json or {},
			)
			if fixture.deterministic_query.output_schema is not None
			else calculate_text_matches(deterministic_extract, fixture.deterministic_query.expected_terms)
		)
		extract_results['deterministic'] = {
			'latency_ms': round(deterministic_ms, 1),
			'payload_chars': len(deterministic_extract),
			'match': match,
			'structured': fixture.deterministic_query.output_schema is not None,
		}

	result = {
		'title': fixture.title,
		'interactive_element_count': full_state['interactive_element_count'],
		'state': {
			'full': {
				'latency_ms': round(full_state_ms, 1),
				'payload_chars': len(full_state_json),
				'selected_elements': len(full_state['interactive_elements']),
			},
			'auto': {
				'latency_ms': round(auto_state_ms, 1),
				'payload_chars': len(auto_state_json),
				'selected_elements': len(auto_state['interactive_elements']),
				'effective_mode': auto_state.get('effective_mode'),
				'payload_reduction_pct': reduction_percent(len(full_state_json), len(auto_state_json)),
				'recall': calculate_recall(auto_state, fixture.expected_compact_texts),
				'state_hash': auto_state['state_hash'],
			},
			'min': {
				'latency_ms': round(min_state_ms, 1),
				'payload_chars': len(min_state_json),
				'selected_elements': len(min_state['interactive_elements']),
				'payload_reduction_pct': reduction_percent(len(full_state_json), len(min_state_json)),
				'recall': calculate_recall(min_state, fixture.expected_compact_texts),
			},
			'unchanged_delta': {
				'latency_ms': round(unchanged_state_ms, 1),
				'payload_chars': len(unchanged_state_json),
				'changed': unchanged_state['changed'],
			},
		},
	}
	if focus_result is not None:
		result['state']['focus'] = focus_result
	if extract_results:
		result['extract_content'] = extract_results
	if fixture.action_scenario:
		result['action_reliability'] = await run_action_scenario(server, fixture, base_url)
	return result


def summarize_results(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
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
	return {
		'fixture_count': len(results),
		'auto_compacted_pages': compacted_pages,
		'avg_auto_payload_reduction_pct': round(mean(auto_reductions), 1) if auto_reductions else 0.0,
		'avg_min_payload_reduction_pct': round(mean(min_reductions), 1) if min_reductions else 0.0,
		'avg_auto_recall': round(mean(auto_recalls), 3) if auto_recalls else 0.0,
		'avg_min_recall': round(mean(min_recalls), 3) if min_recalls else 0.0,
		'deterministic_case_count': len(deterministic_matches),
		'avg_deterministic_recall': round(mean(deterministic_matches), 3) if deterministic_matches else 0.0,
		'zero_llm_deterministic_cases': zero_llm_deterministic,
		'structured_case_count': len(structured_matches),
		'avg_structured_recall': round(mean(structured_matches), 3) if structured_matches else 0.0,
		'action_case_count': len(action_scores),
		'avg_action_success': round(mean(action_scores), 3) if action_scores else 0.0,
		'failing_action_cases': failing_action_cases,
	}


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

		output = {
			'import_ms': import_ms,
			'session_init_ms': round(session_init_ms, 1),
			'summary': summarize_results(results),
			'fixtures': results,
		}

		regressions = detect_regressions(results)
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
		if args.open_issue and regressions:
			raise SystemExit(1)
	finally:
		await server._shutdown()
		fixture_pack.close()


if __name__ == '__main__':
	asyncio.run(main())
