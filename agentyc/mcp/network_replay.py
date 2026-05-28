"""Pure request-replay helpers for MCP network tools."""

from __future__ import annotations

import json


def _build_replay_request_expression(
	*,
	request_url: str,
	request_method: str,
	request_headers: dict[str, str],
	request_body: str,
) -> str:
	request_init_parts = [
		f'method: {json.dumps(request_method)}',
		f'headers: {json.dumps(request_headers)}',
	]
	if request_method not in {'GET', 'HEAD'} or request_body:
		request_init_parts.append(f'body: {json.dumps(request_body)}')
	request_init = ', '.join(request_init_parts)
	return (
		'(async function(){'
		'try {'
		f'const response = await fetch({json.dumps(request_url)}, {{{request_init}}});'
		'const text = await response.text();'
		'return JSON.stringify({status: response.status, ok: response.ok, body: text});'
		'} catch (error) {'
		'return JSON.stringify({'
		'ok: false,'
		'error: error && error.message ? error.message : String(error)'
		'});'
		'}'
		'})()'
	)
