from google.genai import types
from google.genai.types import MediaModality

from agentyc.llm.views import ChatInvokeUsage


def get_stop_reason(response: types.GenerateContentResponse) -> str | None:
	"""Extract stop_reason from Google response."""
	if hasattr(response, 'candidates') and response.candidates:
		return str(response.candidates[0].finish_reason) if hasattr(response.candidates[0], 'finish_reason') else None
	return None


def get_usage(response: types.GenerateContentResponse) -> ChatInvokeUsage | None:
	usage: ChatInvokeUsage | None = None

	if response.usage_metadata is not None:
		image_tokens = 0
		if response.usage_metadata.prompt_tokens_details is not None:
			image_tokens = sum(
				detail.token_count or 0
				for detail in response.usage_metadata.prompt_tokens_details
				if detail.modality == MediaModality.IMAGE
			)

		usage = ChatInvokeUsage(
			prompt_tokens=response.usage_metadata.prompt_token_count or 0,
			completion_tokens=(response.usage_metadata.candidates_token_count or 0)
			+ (response.usage_metadata.thoughts_token_count or 0),
			total_tokens=response.usage_metadata.total_token_count or 0,
			prompt_cached_tokens=response.usage_metadata.cached_content_token_count,
			prompt_cache_creation_tokens=None,
			prompt_image_tokens=image_tokens,
		)

	return usage
