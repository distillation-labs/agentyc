"""AI/content helpers for Page."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from agentyc.dom.serializer.serializer import DOMTreeSerializer
from agentyc.dom.service import DomService
from agentyc.llm.messages import SystemMessage, UserMessage

T = TypeVar('T', bound=BaseModel)

if TYPE_CHECKING:
	from agentyc.browser.session import BrowserSession
	from agentyc.llm.base import BaseChatModel

	from .element import Element


class PageAIMixin:
	"""AI/content helpers mixed into Page."""

	if TYPE_CHECKING:
		_browser_session: BrowserSession
		_target_id: str
		_session_id: str | None
		_llm: BaseChatModel | None

		async def _ensure_session(self) -> str: ...

	@property
	def dom_service(self) -> DomService:
		"""Get the DOM service for this target."""
		return DomService(self._browser_session)

	async def get_element_by_prompt(self, prompt: str, llm: 'BaseChatModel | None' = None) -> 'Element | None':
		"""Get an element by a prompt."""
		await self._ensure_session()
		llm = llm or self._llm

		if not llm:
			raise ValueError('LLM not provided')

		dom_service = self.dom_service
		enhanced_dom_tree, _ = await dom_service.get_dom_tree(target_id=self._target_id, all_frames=None)

		session_id = self._browser_session.id
		serialized_dom_state, _ = DOMTreeSerializer(
			enhanced_dom_tree, None, paint_order_filtering=True, session_id=session_id
		).serialize_accessible_elements()

		llm_representation = serialized_dom_state.llm_representation()

		system_message = SystemMessage(
			content="""You are an AI created to find an element on a page by a prompt.

<browser_state>
Interactive Elements: All interactive elements will be provided in format as [index]<type>text</type> where
- index: Numeric identifier for interaction
- type: HTML element type (button, input, etc.)
- text: Element description

Examples:
[33]<div>User form</div>
[35]<button aria-label='Submit form'>Submit</button>

Note that:
- Only elements with numeric indexes in [] are interactive
- (stacked) indentation (with \t) is important and means that the element is a (html) child of the element above (with a lower index)
- Pure text elements without [] are not interactive.
</browser_state>

Your task is to find an element index (if any) that matches the prompt (written in <prompt> tag).

If non of the elements matches the, return None.

Before you return the element index, reason about the state and elements for a sentence or two."""
		)

		state_message = UserMessage(
			content=f"""
			<browser_state>
			{llm_representation}
			</browser_state>

			<prompt>
			{prompt}
			</prompt>
			"""
		)

		class ElementResponse(BaseModel):
			element_highlight_index: int | None

		llm_response = await llm.ainvoke(
			[
				system_message,
				state_message,
			],
			output_format=ElementResponse,
		)

		element_highlight_index = llm_response.completion.element_highlight_index

		if element_highlight_index is None or element_highlight_index not in serialized_dom_state.selector_map:
			return None

		element = serialized_dom_state.selector_map[element_highlight_index]

		from .element import Element as Element_

		return Element_(self._browser_session, element.backend_node_id, self._session_id)

	async def must_get_element_by_prompt(self, prompt: str, llm: 'BaseChatModel | None' = None) -> 'Element':
		"""Get an element by a prompt.

		@dev LLM can still return None, this just raises an error if the element is not found.
		"""
		element = await self.get_element_by_prompt(prompt, llm)
		if element is None:
			raise ValueError(f'No element found for prompt: {prompt}')

		return element

	async def extract_content(self, prompt: str, structured_output: type[T], llm: 'BaseChatModel | None' = None) -> T:
		"""Extract structured content from the current page using LLM."""
		llm = llm or self._llm

		if not llm:
			raise ValueError('LLM not provided')

		try:
			content, _content_stats = await self._extract_clean_markdown()
		except Exception as e:
			raise RuntimeError(f'Could not extract clean markdown: {type(e).__name__}')

		system_prompt = """
You are an expert at extracting structured data from the markdown of a webpage.

<input>
You will be given a query and the markdown of a webpage that has been filtered to remove noise and advertising content.
</input>

<instructions>
- You are tasked to extract information from the webpage that is relevant to the query.
- You should ONLY use the information available in the webpage to answer the query. Do not make up information or provide guess from your own knowledge.
- If the information relevant to the query is not available in the page, your response should mention that.
- If the query asks for all items, products, etc., make sure to directly list all of them.
- Return the extracted content in the exact structured format specified.
</instructions>

<output>
- Your output should present ALL the information relevant to the query in the specified structured format.
- Do not answer in conversational format - directly output the relevant information in the structured format.
</output>
""".strip()

		prompt_content = f'<query>\n{prompt}\n</query>\n\n<webpage_content>\n{content}\n</webpage_content>'

		try:
			response = await asyncio.wait_for(
				llm.ainvoke(
					[SystemMessage(content=system_prompt), UserMessage(content=prompt_content)], output_format=structured_output
				),
				timeout=120.0,
			)
			return response.completion
		except Exception as e:
			raise RuntimeError(str(e))

	async def _extract_clean_markdown(self, extract_links: bool = False) -> tuple[str, dict]:
		"""Extract clean markdown from the current page using enhanced DOM tree."""
		from agentyc.dom.markdown_extractor import extract_clean_markdown

		dom_service = self.dom_service
		return await extract_clean_markdown(dom_service=dom_service, target_id=self._target_id, extract_links=extract_links)
