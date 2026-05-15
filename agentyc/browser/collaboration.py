"""Browser-side collaboration metadata and marker helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from agentyc.browser.session_models import RuntimeOwnershipMetadata

TITLE_PREFIX_RE = re.compile(r'^\[agtyc:(?P<runtime>[A-Za-z0-9_-]{1,32})\]\s*')


def strip_title_prefix(title: str) -> str:
	return TITLE_PREFIX_RE.sub('', title or '', count=1)


def extract_title_prefix(title: str) -> str | None:
	match = TITLE_PREFIX_RE.match(title or '')
	return match.group(0) if match else None


def apply_title_prefix(title: str, metadata: RuntimeOwnershipMetadata) -> str:
	raw_title = strip_title_prefix(title)
	return f'{metadata.title_prefix}{raw_title}'.rstrip()


def build_runtime_marker_script(
	*,
	runtime: RuntimeOwnershipMetadata,
	target_id: str,
	include_overlay: bool = True,
	include_title_prefix: bool = True,
	exclude_session_id: str | None = None,
) -> str:
	"""Create an init/eval script that annotates a target with runtime metadata."""
	marker_payload: dict[str, Any] = {
		'runtime': runtime.model_dump(mode='json'),
		'targetId': target_id,
		'includeOverlay': include_overlay,
		'includeTitlePrefix': include_title_prefix,
		'excludeSessionId': exclude_session_id,
	}
	payload_json = json.dumps(marker_payload, ensure_ascii=True)
	return rf"""
	(function() {{
		const payload = {payload_json};
		const runtime = payload.runtime;
		const markerId = '__agentyc-collaboration-ribbon';
		const excludeAttr = payload.excludeSessionId ? `data-agentyc-exclude-${{payload.excludeSessionId}}` : null;

		function stripPrefix(title) {{
			return String(title || '').replace(/^\[agtyc:[A-Za-z0-9_-]{{1,32}}\]\s*/, '');
		}}

		function ensureState() {{
			window.__agentycCollaboration = {{
				runtime,
				targetId: payload.targetId,
				version: 1,
			}};
			window.__agentycSetCollaborationVisibility = function(visible) {{
				const marker = document.getElementById(markerId);
				if (marker) marker.style.display = visible ? 'flex' : 'none';
			}};
		}}

		function applyTitlePrefix() {{
			if (!payload.includeTitlePrefix) return;
			try {{
				const rawTitle = stripPrefix(document.title || document.location.href || '');
				const desiredTitle = `${{runtime.title_prefix}}${{rawTitle}}`.trim();
				if (document.title !== desiredTitle) {{
					document.title = desiredTitle;
				}}
			}} catch (_error) {{}}
		}}

		function renderRibbon() {{
			if (!payload.includeOverlay || !document.body) return;
			let marker = document.getElementById(markerId);
			if (!marker) {{
				marker = document.createElement('div');
				marker.id = markerId;
				marker.setAttribute('data-agentyc-collaboration-marker', 'true');
				marker.setAttribute('data-agentyc-highlight', 'collaboration-ribbon');
				marker.setAttribute('data-agentyc-exclude', 'true');
				if (excludeAttr) marker.setAttribute(excludeAttr, 'true');
				marker.style.cssText = [
					'position:fixed',
					'top:12px',
					'right:12px',
					'display:flex',
					'align-items:center',
					'gap:8px',
					'padding:6px 10px',
					'border-radius:999px',
					'background:rgba(17,24,39,0.72)',
					'backdrop-filter:blur(8px)',
					'color:white',
					'font:600 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif',
					'letter-spacing:0.01em',
					'pointer-events:none',
					'z-index:2147483647',
					'box-shadow:0 8px 24px rgba(0,0,0,0.24)',
				].join(';');
				const dot = document.createElement('span');
				dot.style.cssText = `width:9px;height:9px;border-radius:999px;background:${{runtime.accent_color}};flex:none;box-shadow:0 0 0 2px rgba(255,255,255,0.18)`;
				dot.setAttribute('data-agentyc-exclude', 'true');
				if (excludeAttr) dot.setAttribute(excludeAttr, 'true');
				const label = document.createElement('span');
				label.textContent = runtime.overlay_label;
				label.setAttribute('data-agentyc-exclude', 'true');
				if (excludeAttr) label.setAttribute(excludeAttr, 'true');
				marker.replaceChildren(dot, label);
				document.body.appendChild(marker);
			}} else {{
				marker.style.display = 'flex';
			}}
		}}

		ensureState();
		applyTitlePrefix();
		if (document.readyState === 'loading') {{
			document.addEventListener('DOMContentLoaded', renderRibbon, {{ once: true }});
		}} else {{
			renderRibbon();
		}}

		if (!window.__agentycCollaborationObserver) {{
			window.__agentycCollaborationObserver = true;
			const observer = new MutationObserver(() => applyTitlePrefix());
			observer.observe(document.documentElement, {{ childList: true, subtree: true }});
			window.addEventListener('pageshow', applyTitlePrefix);
			window.setInterval(() => {{
				if (payload.includeOverlay && document.body && !document.getElementById(markerId)) {{
					renderRibbon();
				}}
			}}, 750);
		}}
	}})();
	"""


def build_marker_visibility_script(visible: bool) -> str:
	state = 'true' if visible else 'false'
	return f"""
	(function() {{
		if (typeof window.__agentycSetCollaborationVisibility === 'function') {{
			window.__agentycSetCollaborationVisibility({state});
		}}
		return {{ visible: {state} }};
	}})();
	"""


def build_runtime_metadata_probe_script() -> str:
	return """
	(function() {
		return window.__agentycCollaboration || null;
	})();
	"""
