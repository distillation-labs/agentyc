"""Browser-side collaboration metadata and marker helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from agentyc.browser.session_models import RuntimeOwnershipMetadata

TITLE_PREFIX_RE = re.compile(r'^\[(?P<runtime>[^\]]{1,64})\]\s*')


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
	include_title_prefix: bool = True,
) -> str:
	"""Create an init/eval script that writes runtime metadata to window and maintains the title prefix."""
	marker_payload: dict[str, Any] = {
		'runtime': runtime.model_dump(mode='json'),
		'targetId': target_id,
		'includeTitlePrefix': include_title_prefix,
	}
	payload_json = json.dumps(marker_payload, ensure_ascii=True)
	return rf"""
	(function() {{
		const payload = {payload_json};
		const runtime = payload.runtime;

		function stripPrefix(title) {{
			return String(title || '').replace(/^\[[^\]]{{1,64}}\]\s*/, '');
		}}

		function ensureState() {{
			window.__agentycCollaboration = {{
				runtime,
				targetId: payload.targetId,
				version: 1,
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

		ensureState();
		applyTitlePrefix();

		if (!window.__agentycCollaborationObserver) {{
			window.__agentycCollaborationObserver = true;
			const observer = new MutationObserver(() => applyTitlePrefix());
			observer.observe(document.documentElement, {{ childList: true, subtree: true }});
			window.addEventListener('pageshow', applyTitlePrefix);
		}}
	}})();
	"""


def build_runtime_metadata_probe_script() -> str:
	return """
	(function() {
		return window.__agentycCollaboration || null;
	})();
	"""
