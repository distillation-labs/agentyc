import json
import logging
from typing import Any

from pydantic import BaseModel

from agentyc.actions import ActionResult
from agentyc.browser import BrowserSession
from agentyc.dom.service import EnhancedDOMTreeNode
from agentyc.filesystem.file_system import FileSystem
from agentyc.tools.extraction.router import (
	get_deterministic_extraction_strategy,
	maybe_extract_deterministic_content,
)
from agentyc.tools.extraction.strategy import _build_no_route_error
from agentyc.tools.views import ExtractAction
from agentyc.utils import sanitize_surrogates

logger = logging.getLogger(__name__)


def register_extraction_actions(tools: Any) -> None:
	@tools.registry.action(
		"""Deterministically extract structured data from page markdown. Use when you know exactly what to extract and cannot use interactive elements. Set extract_links=True for URLs. Set extract_images=True for image src URLs. Use start_from_char if previous extraction was truncated to extract data further down the page. When paginating across pages, pass already_collected with item identifiers (names/URLs) from prior pages to avoid duplicates.""",
		param_model=ExtractAction,
	)
	async def extract(
		params: ExtractAction,
		browser_session: BrowserSession,
		file_system: FileSystem | None = None,
		extraction_schema: dict | None = None,
	):
		MAX_CHAR_LIMIT = 100000
		query = params['query'] if isinstance(params, dict) else params.query
		extract_links = params['extract_links'] if isinstance(params, dict) else params.extract_links
		extract_images = params.get('extract_images', False) if isinstance(params, dict) else params.extract_images
		start_from_char = params['start_from_char'] if isinstance(params, dict) else params.start_from_char
		output_schema: dict | None = params.get('output_schema') if isinstance(params, dict) else params.output_schema
		already_collected: list[str] = (
			params.get('already_collected', []) if isinstance(params, dict) else params.already_collected
		)

		if file_system is None:
			raise RuntimeError('Extract action requires a filesystem for overflow content storage.')

		image_keywords = ['image', 'photo', 'picture', 'thumbnail', 'img url', 'image url', 'photo url', 'product image']
		if not extract_images and any(keyword in query.lower() for keyword in image_keywords):
			extract_images = True

		deterministic_strategy = get_deterministic_extraction_strategy(
			query=query,
			extract_links=extract_links,
			output_schema=output_schema,
		)
		if deterministic_strategy == 'deterministic-link-collections':
			extract_links = True

		if output_schema is None and extraction_schema is not None:
			output_schema = extraction_schema

		structured_model: type[BaseModel] | None = None
		if output_schema is not None:
			try:
				from agentyc.tools.extraction.schema_utils import schema_dict_to_pydantic_model

				structured_model = schema_dict_to_pydantic_model(output_schema)
			except (ValueError, TypeError) as error:
				return ActionResult(error=f'Invalid output_schema for deterministic extraction: {error}')

		try:
			from agentyc.dom.markdown_extractor import extract_clean_markdown

			content, content_stats = await extract_clean_markdown(
				browser_session=browser_session,
				extract_links=extract_links,
				extract_images=extract_images,
			)
		except Exception as error:
			raise RuntimeError(f'Could not extract clean markdown: {type(error).__name__}')

		final_filtered_length = content_stats['final_filtered_chars']

		from agentyc.dom.markdown_extractor import chunk_markdown_by_structure

		chunks = chunk_markdown_by_structure(content, max_chunk_chars=MAX_CHAR_LIMIT, start_from_char=start_from_char)
		if not chunks:
			return ActionResult(
				error=f'start_from_char ({start_from_char}) exceeds content length {final_filtered_length} characters.'
			)
		chunk = chunks[0]
		content = chunk.content
		truncated = chunk.has_more

		if chunk.overlap_prefix:
			content = chunk.overlap_prefix + '\n' + content

		if start_from_char > 0:
			content_stats['started_from_char'] = start_from_char
		if truncated:
			content_stats['truncated_at_char'] = chunk.char_offset_end
			content_stats['next_start_char'] = chunk.char_offset_end
			content_stats['chunk_index'] = chunk.chunk_index
			content_stats['total_chunks'] = chunk.total_chunks

		content = sanitize_surrogates(content)
		query = sanitize_surrogates(query)

		current_url = await browser_session.get_current_page_url()
		selector_map: dict[int, EnhancedDOMTreeNode] | None = None
		if deterministic_strategy == 'deterministic-form-fields':
			selector_map = await browser_session.get_selector_map()
			if not selector_map:
				await browser_session.get_browser_state_summary(include_screenshot=False)
				selector_map = await browser_session.get_selector_map()

		deterministic_result = maybe_extract_deterministic_content(
			query=query,
			markdown=content,
			current_url=current_url,
			extract_links=extract_links,
			output_schema=output_schema,
			truncated=truncated,
			content_stats=content_stats,
			selector_map=selector_map,
			already_collected=already_collected,
		)
		if deterministic_result is not None:
			if deterministic_result.structured_data is not None:
				if structured_model is None or output_schema is None:
					return ActionResult(error='Deterministic structured extraction requires a valid output_schema.')
				try:
					validated_structured = structured_model.model_validate(deterministic_result.structured_data)
				except Exception as error:
					return ActionResult(error=f'Deterministic structured extraction did not match the provided schema: {error}')

				deterministic_result_data: dict[str, Any] = validated_structured.model_dump(mode='json')
				extracted_content = (
					f'<url>\n{current_url}\n</url>\n<query>\n{query}\n</query>\n'
					f'<structured_result>\n{json.dumps(deterministic_result_data)}\n</structured_result>'
				)

				from agentyc.tools.extraction.views import ExtractionResult

				extraction_meta = ExtractionResult(
					data=deterministic_result_data,
					schema_used=output_schema,
					is_partial=bool(deterministic_result.metadata.get('is_partial', False)),
					source_url=current_url,
					content_stats=content_stats,
				)

				max_memory_length = 10000
				if len(extracted_content) < max_memory_length:
					memory = extracted_content
					include_extracted_content_only_once = False
				else:
					file_name = await file_system.save_extracted_content(extracted_content)
					memory = f'Query: {query}\nContent in {file_name} and once in <read_state>.'
					include_extracted_content_only_once = True

				logger.info(f'📄 {memory}')
				return ActionResult(
					extracted_content=extracted_content,
					include_extracted_content_only_once=include_extracted_content_only_once,
					long_term_memory=memory,
					metadata={
						'route': deterministic_result.metadata.get('strategy'),
						'llm_used': False,
						'is_partial': bool(deterministic_result.metadata.get('is_partial', False)),
						'structured_extraction': True,
						'deterministic_extraction': True,
						**deterministic_result.metadata,
						'extraction_result': extraction_meta.model_dump(mode='json'),
					},
				)

			logger.info(f'📄 {deterministic_result.content}')
			return ActionResult(
				extracted_content=deterministic_result.content,
				include_extracted_content_only_once=False,
				long_term_memory=deterministic_result.content,
				metadata={
					'route': deterministic_result.metadata.get('strategy'),
					'llm_used': False,
					'is_partial': bool(deterministic_result.metadata.get('is_partial', False)),
					'structured_extraction': False,
					'deterministic_extraction': True,
					**deterministic_result.metadata,
				},
			)

		if structured_model is not None:
			return ActionResult(
				error=(
					'Deterministic structured extraction is not available for this query. '
					'Use a supported table, list, key-value, link-collection, form-field, or image query.'
				)
			)

		return ActionResult(error=_build_no_route_error(query))
