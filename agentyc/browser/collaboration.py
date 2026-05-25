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
		const STATE_KEY = '__agentycCollaboration';
		const OBSERVER_KEY = '__agentycCollaborationObserver';
		const TITLE_PATCH_KEY = '__agentycCollaborationTitlePatched';
		const TITLE_VALUE_KEY = '__agentycCollaborationDesiredTitle';

		function stripPrefix(title) {{
			return String(title || '').replace(/^\[[^\]]{{1,64}}\]\s*/, '');
		}}

		function titleFallback() {{
			const href = String(document.location.href || '');
			if (
				href === 'about:blank' ||
				href.startsWith('chrome://') ||
				href.startsWith('chrome-error://') ||
				href.startsWith('devtools://') ||
				href.startsWith('edge://')
			) {{
				return href;
			}}
			return '';
		}}

		function getRawTitle() {{
			const rawTitle = stripPrefix(document.title || '').trim();
			return rawTitle || titleFallback();
		}}

		function getDesiredTitle() {{
			if (!payload.includeTitlePrefix) {{
				return getRawTitle().trim();
			}}
			return `${{runtime.title_prefix}}${{getRawTitle()}}`.trim();
		}}

		function ensureState() {{
			window[STATE_KEY] = {{
				runtime,
				targetId: payload.targetId,
				version: 1,
			}};
		}}

		function applyTitlePrefix() {{
			try {{
				const desiredTitle = getDesiredTitle();
				window[TITLE_VALUE_KEY] = desiredTitle;
				if (document.title !== desiredTitle) {{
					document.title = desiredTitle;
				}}
			}} catch (_error) {{}}
		}}

		function patchDocumentTitleSetter() {{
			if (window[TITLE_PATCH_KEY]) return;
			let proto = document;
			let descriptor = null;
			while (proto && !descriptor) {{
				descriptor = Object.getOwnPropertyDescriptor(proto, 'title');
				proto = Object.getPrototypeOf(proto);
			}}
			if (!descriptor || typeof descriptor.get !== 'function' || typeof descriptor.set !== 'function') return;
			Object.defineProperty(document, 'title', {{
				configurable: true,
				enumerable: descriptor.enumerable ?? true,
				get() {{
					return descriptor.get.call(document);
				}},
				set(value) {{
					const rawValue = stripPrefix(value || '').trim() || titleFallback();
					const nextValue = payload.includeTitlePrefix
						? `${{runtime.title_prefix}}${{rawValue}}`.trim()
						: rawValue.trim();
					window[TITLE_VALUE_KEY] = nextValue;
					descriptor.set.call(document, nextValue);
				}},
			}});
			window[TITLE_PATCH_KEY] = true;
		}}

		ensureState();
		patchDocumentTitleSetter();
		applyTitlePrefix();

		if (!window[OBSERVER_KEY]) {{
			window[OBSERVER_KEY] = true;
			const observer = new MutationObserver(() => applyTitlePrefix());
			const root = document.documentElement || document.body || document;
			if (root) {{
				observer.observe(root, {{ childList: true, subtree: true }});
			}}
			window.addEventListener('pageshow', applyTitlePrefix);
			window.addEventListener('focus', applyTitlePrefix);
			document.addEventListener('readystatechange', applyTitlePrefix);
		}}
	}})();
	"""


def build_runtime_metadata_probe_script() -> str:
	return """
	(function() {
		return window.__agentycCollaboration || null;
	})();
	"""
