"""Shared dropdown helpers for the default action watchdog."""


async def resolve_dropdown_object_id(cdp_session, backend_node_id: int | None) -> str:
	try:
		object_result = await cdp_session.cdp_client.send.DOM.resolveNode(
			params={'backendNodeId': backend_node_id}, session_id=cdp_session.session_id
		)
		remote_object = object_result.get('object', {})
		object_id = remote_object.get('objectId')
		if not object_id:
			raise ValueError('Could not get object ID from resolved node')
		return object_id
	except Exception as error:
		raise ValueError(f'Failed to resolve node to object: {error}') from error
