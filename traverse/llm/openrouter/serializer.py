from openai.types.chat import ChatCompletionMessageParam

from traverse.llm.messages import BaseMessage
from traverse.llm.openai.serializer import OpenAIMessageSerializer


class OpenRouterMessageSerializer:
	"""
	Serializer for converting between custom message types and OpenRouter message formats.

	OpenRouter uses the OpenAI-compatible API, so we can reuse the OpenAI serializer.
	"""

	@staticmethod
	def serialize_messages(messages: list[BaseMessage]) -> list[ChatCompletionMessageParam]:
		"""
		Serialize a list of traverse messages to OpenRouter-compatible messages.

		Args:
		    messages: List of traverse messages

		Returns:
		    List of OpenRouter-compatible messages (identical to OpenAI format)
		"""
		# OpenRouter uses the same message format as OpenAI
		return OpenAIMessageSerializer.serialize_messages(messages)
