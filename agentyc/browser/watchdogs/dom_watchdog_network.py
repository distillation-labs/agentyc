"""Network and event-history helpers for the DOM watchdog."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from agentyc.browser.views import NetworkRequest


def get_recent_events_str(watchdog, limit: int = 10) -> str | None:
	"""Get the most recent events from the event bus as JSON."""
	try:
		all_events = sorted(
			watchdog.browser_session.event_bus.event_history.values(),
			key=lambda event: event.event_created_at.timestamp(),
			reverse=True,
		)

		recent_events_data = []
		for event in all_events[:limit]:
			event_data = {
				'event_type': event.event_type,
				'timestamp': event.event_created_at.isoformat(),
			}
			if hasattr(event, 'url'):
				event_data['url'] = getattr(event, 'url')
			if hasattr(event, 'error_message'):
				event_data['error_message'] = getattr(event, 'error_message')
			if hasattr(event, 'target_id'):
				event_data['target_id'] = getattr(event, 'target_id')
			recent_events_data.append(event_data)

		return json.dumps(recent_events_data)
	except Exception as error:
		watchdog.logger.debug(f'Failed to get recent events: {error}')

	return json.dumps([])


async def get_pending_network_requests(watchdog) -> list['NetworkRequest']:
	"""Get list of currently pending network requests."""
	from agentyc.browser.views import NetworkRequest

	try:
		cdp_session = await watchdog.browser_session.get_or_create_cdp_session(focus=False)
		js_code = """
(function() {
	const now = performance.now();
	const resources = performance.getEntriesByType('resource');
	const pending = [];

	const docLoading = document.readyState !== 'complete';

	const adDomains = [
		'doubleclick.net', 'googlesyndication.com', 'googletagmanager.com',
		'facebook.net', 'analytics', 'ads', 'tracking', 'pixel',
		'hotjar.com', 'clarity.ms', 'mixpanel.com', 'segment.com',
		'demdex.net', 'omtrdc.net', 'adobedtm.com', 'ensighten.com',
		'newrelic.com', 'nr-data.net', 'google-analytics.com',
		'connect.facebook.net', 'platform.twitter.com', 'platform.linkedin.com',
		'.cloudfront.net/image/', '.akamaized.net/image/',
		'/tracker/', '/collector/', '/beacon/', '/telemetry/', '/log/',
		'/events/', '/eventBatch', '/track.', '/metrics/'
	];

	let totalResourcesChecked = 0;
	let filteredByResponseEnd = 0;
	const allDomains = new Set();

	for (const entry of resources) {
		totalResourcesChecked++;

		try {
			const hostname = new URL(entry.name).hostname;
			if (hostname) allDomains.add(hostname);
		} catch (e) {}

		if (entry.responseEnd === 0) {
			filteredByResponseEnd++;
			const url = entry.name;

			const isAd = adDomains.some(domain => url.includes(domain));
			if (isAd) continue;

			if (url.startsWith('data:') || url.length > 500) continue;

			const loadingDuration = now - entry.startTime;
			if (loadingDuration > 10000) continue;

			const resourceType = entry.initiatorType || 'unknown';

			const nonCriticalTypes = ['img', 'image', 'icon', 'font'];
			if (nonCriticalTypes.includes(resourceType) && loadingDuration > 3000) continue;

			const isImageUrl = /\\.(jpg|jpeg|png|gif|webp|svg|ico)(\\?|$)/i.test(url);
			if (isImageUrl && loadingDuration > 3000) continue;

			pending.push({
				url: url,
				method: 'GET',
				loading_duration_ms: Math.round(loadingDuration),
				resource_type: resourceType
			});
		}
	}

	return {
		pending_requests: pending,
		document_loading: docLoading,
		document_ready_state: document.readyState,
		debug: {
			total_resources: totalResourcesChecked,
			with_response_end_zero: filteredByResponseEnd,
			after_all_filters: pending.length,
			all_domains: Array.from(allDomains)
		}
	};
})()
"""

		result = await cdp_session.cdp_client.send.Runtime.evaluate(
			params={'expression': js_code, 'returnByValue': True},
			session_id=cdp_session.session_id,
		)

		if result.get('result', {}).get('type') == 'object':
			data = result['result'].get('value', {})
			pending = data.get('pending_requests', [])
			doc_state = data.get('document_ready_state', 'unknown')
			doc_loading = data.get('document_loading', False)
			debug_info = data.get('debug', {})

			all_domains = debug_info.get('all_domains', [])
			all_domains_str = ', '.join(sorted(all_domains)[:5]) if all_domains else 'none'
			if len(all_domains) > 5:
				all_domains_str += f' +{len(all_domains) - 5} more'

			watchdog.logger.debug(
				f'🔍 Network check: document.readyState={doc_state}, loading={doc_loading}, '
				f'total_resources={debug_info.get("total_resources", 0)}, '
				f'responseEnd=0: {debug_info.get("with_response_end_zero", 0)}, '
				f'after_filters={len(pending)}, domains=[{all_domains_str}]'
			)

			return [
				NetworkRequest(
					url=request['url'],
					method=request.get('method', 'GET'),
					loading_duration_ms=request.get('loading_duration_ms', 0.0),
					resource_type=request.get('resource_type'),
				)
				for request in pending[:20]
			]
	except Exception as error:
		watchdog.logger.debug(f'Failed to get pending network requests: {error}')

	return []
