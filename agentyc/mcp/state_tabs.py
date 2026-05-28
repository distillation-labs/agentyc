"""Tab serialization helpers for MCP state payloads."""

from __future__ import annotations

from typing import Any, cast


def _serialize_tab_id(target_id: Any) -> str | None:
	if target_id is None:
		return None
	target = str(target_id)
	return target[-4:] if target else None


def _serialize_optional_model(value: Any, *, by_alias: bool = False) -> Any:
	if value is None:
		return None
	if hasattr(value, 'model_dump'):
		payload = cast(Any, value).model_dump(mode='json', by_alias=by_alias, exclude_none=True)
		return payload
	if isinstance(value, dict):
		return value
	return value


def _serialize_runtime_metadata(runtime: Any) -> dict[str, Any] | None:
	payload = _serialize_optional_model(runtime)
	if not isinstance(payload, dict):
		return None
	serialized: dict[str, Any] = {}
	for key in ('runtime_id', 'runtime_label', 'runtime_role', 'parent_runtime_id'):
		value = payload.get(key)
		if value is None:
			continue
		if key == 'runtime_role' and value == 'primary':
			continue
		serialized[key] = value
	return serialized or None


def _serialize_ownership_metadata(ownership: Any) -> dict[str, Any] | None:
	payload = _serialize_optional_model(ownership)
	if not isinstance(payload, dict):
		return None
	serialized: dict[str, Any] = {}
	for key in ('owner_kind', 'source', 'display_label', 'title_prefix_applied'):
		value = payload.get(key)
		if value is None:
			continue
		if key == 'title_prefix_applied' and value is False:
			continue
		serialized[key] = value
	runtime_payload = _serialize_runtime_metadata(payload.get('runtime'))
	if runtime_payload is not None:
		serialized['runtime'] = runtime_payload
	return serialized or None


def serialize_tab_info(tab: Any) -> dict[str, Any]:
	if hasattr(tab, 'model_dump'):
		payload = cast(Any, tab).model_dump(mode='json', by_alias=True, exclude_none=True)
	else:
		payload: dict[str, Any] = {
			'url': getattr(tab, 'url', ''),
			'title': getattr(tab, 'title', '') or '',
		}
		tab_id = _serialize_tab_id(getattr(tab, 'target_id', None))
		if tab_id is not None:
			payload['tab_id'] = tab_id
		parent_tab_id = _serialize_tab_id(getattr(tab, 'parent_target_id', None))
		if parent_tab_id is not None:
			payload['parent_tab_id'] = parent_tab_id
		display_title = getattr(tab, 'display_title', None)
		if display_title is not None:
			payload['display_title'] = display_title
		ownership = _serialize_ownership_metadata(getattr(tab, 'ownership', None))
		if ownership is not None:
			payload['ownership'] = ownership
		window_bounds = _serialize_optional_model(getattr(tab, 'window_bounds', None), by_alias=True)
		if window_bounds is not None:
			payload['window_bounds'] = window_bounds

	ownership = _serialize_ownership_metadata(payload.get('ownership'))
	if ownership is not None:
		payload['ownership'] = ownership
	else:
		payload.pop('ownership', None)

	if payload.get('title') is None:
		payload['title'] = ''
	return payload


def _tab_group_sort_key(group: dict[str, Any]) -> tuple[int, str]:
	owner_kind = str(group.get('owner_kind') or '')
	runtime_label = str(group.get('runtime_label') or group.get('display_label') or group.get('group_label') or '')
	priority = 2
	if owner_kind == 'agent':
		priority = 0
	elif owner_kind == 'runtime':
		priority = 1
	elif owner_kind == 'human':
		priority = 3
	return (priority, runtime_label.lower())


def build_tab_groups_payload(
	serialized_tabs: list[dict[str, Any]],
	*,
	current_tab_id: str | None = None,
) -> list[dict[str, Any]]:
	grouped: dict[str, dict[str, Any]] = {}
	for tab in serialized_tabs:
		ownership = tab.get('ownership') if isinstance(tab.get('ownership'), dict) else None
		runtime = ownership.get('runtime') if isinstance(ownership, dict) and isinstance(ownership.get('runtime'), dict) else None
		if runtime is not None:
			runtime_id = str(runtime.get('runtime_id') or '')
			ownership_display_label = str(ownership.get('display_label') or '') if ownership is not None else ''
			owner_kind = str(ownership.get('owner_kind') or 'runtime') if ownership is not None else 'runtime'
			runtime_label = str(runtime.get('runtime_label') or ownership_display_label or 'Runtime')
			group_key = f'runtime:{runtime_id or ownership_display_label or "unknown"}'
			group = grouped.setdefault(
				group_key,
				{
					'group_id': group_key,
					'owner_kind': owner_kind,
					'display_label': ownership_display_label or runtime_label,
					'runtime_id': runtime_id or None,
					'runtime_label': runtime_label,
					'tab_count': 0,
					'tab_ids': [],
				},
			)
			runtime_role = runtime.get('runtime_role') or 'primary'
			if runtime_role != 'primary':
				group['runtime_role'] = runtime_role
			parent_runtime_id = runtime.get('parent_runtime_id')
			if parent_runtime_id:
				group['parent_runtime_id'] = parent_runtime_id
		else:
			display_label = ownership.get('display_label') if isinstance(ownership, dict) else None
			owner_kind = ownership.get('owner_kind') if isinstance(ownership, dict) else 'unknown'
			if owner_kind == 'human':
				group_key = 'human'
				group_label = display_label or 'Human'
			else:
				group_key = f'ungrouped:{display_label or owner_kind or "unknown"}'
				group_label = display_label or 'Ungrouped'
			group = grouped.setdefault(
				group_key,
				{
					'group_id': group_key,
					'owner_kind': owner_kind,
					'display_label': group_label,
					'tab_count': 0,
					'tab_ids': [],
				},
			)

		group['tab_ids'].append(tab.get('tab_id') or '')
		group['tab_count'] += 1
		if current_tab_id is not None and tab.get('tab_id') == current_tab_id:
			group['current_tab_id'] = current_tab_id

	ordered_groups = sorted(grouped.values(), key=_tab_group_sort_key)
	return ordered_groups


def _build_current_tab_payload(tab_payload: dict[str, Any], *, include_page_identity: bool = True) -> dict[str, Any] | None:
	current_tab: dict[str, Any] = {}
	keys = ('tab_id', 'parent_tab_id', 'display_title', 'ownership', 'window_bounds')
	if include_page_identity:
		keys = keys + ('url', 'title')
	for key in keys:
		value = tab_payload.get(key)
		if value is not None:
			current_tab[key] = value
	return current_tab or None


def _resolve_current_tab_payload(
	*,
	tabs: list[Any],
	serialized_tabs: list[dict[str, Any]],
	current_tab_id: str | None,
	current_url: str,
	current_title: str,
	include_page_identity: bool = True,
) -> dict[str, Any] | None:
	if current_tab_id is not None:
		for tab, tab_payload in zip(tabs, serialized_tabs):
			if str(getattr(tab, 'target_id', '')) == current_tab_id:
				return _build_current_tab_payload(tab_payload, include_page_identity=include_page_identity)

	matching_tabs = [
		_build_current_tab_payload(tab_payload, include_page_identity=include_page_identity)
		for tab, tab_payload in zip(tabs, serialized_tabs)
		if getattr(tab, 'url', None) == current_url and getattr(tab, 'title', None) == current_title
	]
	matching_tabs = [payload for payload in matching_tabs if payload is not None]
	if len(matching_tabs) == 1:
		return matching_tabs[0]
	if len(serialized_tabs) == 1:
		return _build_current_tab_payload(serialized_tabs[0], include_page_identity=include_page_identity)
	return None


__all__ = ['serialize_tab_info', 'build_tab_groups_payload']
