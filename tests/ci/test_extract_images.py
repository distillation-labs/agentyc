"""Tests for extract_images support in extract_clean_markdown.

Root cause of AGI-101: markdownify strips img src URLs when images appear inside table cells
(<td>/<th>) or heading elements, because those contexts set the _inline flag. The extract_images
parameter fixes this by adding those tags to keep_inline_images_in.

Block-level images (direct children of <div>, <figure>, etc.) are ALWAYS included in markdown
regardless of extract_images. The parameter only matters for images inside <td>, <th>, <h1>-<h6>.
"""

import asyncio

import pytest
from pytest_httpserver import HTTPServer

from agentyc.browser import BrowserProfile, BrowserSession
from agentyc.dom.markdown_extractor import extract_clean_markdown

# --- Fixtures ---


@pytest.fixture(scope='session')
def http_server():
	"""Test HTTP server serving pages with product images."""
	server = HTTPServer()
	server.start()

	# Table-layout products — images are in <td>, the actual bug scenario.
	# With extract_images=False (default), img in td becomes just alt text (no URL).
	# With extract_images=True, img in td becomes ![alt](url) with the real URL.
	server.expect_request('/products-table').respond_with_data(
		"""
		<!DOCTYPE html>
		<html>
		<head><title>Products Table</title></head>
		<body>
			<h1>Product Catalog</h1>
			<table>
				<thead>
					<tr><th>Image</th><th>Name</th><th>Price</th></tr>
				</thead>
				<tbody>
					<tr>
						<td><img src="http://localhost/images/widget-a.jpg" alt="Widget A"></td>
						<td>Widget A</td>
						<td>$29.99</td>
					</tr>
					<tr>
						<td><img src="http://localhost/images/widget-b.jpg" alt="Widget B"></td>
						<td>Widget B</td>
						<td>$49.99</td>
					</tr>
					<tr>
						<td><img src="http://localhost/images/gadget-c.png" alt="Gadget C"></td>
						<td>Gadget C</td>
						<td>$19.50</td>
					</tr>
				</tbody>
			</table>
		</body>
		</html>
		""",
		content_type='text/html',
	)

	# Block-level products — images in <div>/<figure> are ALWAYS included in markdown,
	# regardless of extract_images value.
	server.expect_request('/products-block').respond_with_data(
		"""
		<!DOCTYPE html>
		<html>
		<head><title>Products Block</title></head>
		<body>
			<div class="product">
				<img src="http://localhost/images/widget-a.jpg" alt="Widget A">
				<p>Widget A - $29.99</p>
			</div>
		</body>
		</html>
		""",
		content_type='text/html',
	)

	server.expect_request('/text-only').respond_with_data(
		"""
		<!DOCTYPE html>
		<html>
		<head><title>Text Only</title></head>
		<body>
			<h1>No Images Here</h1>
			<p>Just some text content with no images at all.</p>
		</body>
		</html>
		""",
		content_type='text/html',
	)

	yield server
	server.stop()


@pytest.fixture(scope='session')
def base_url(http_server):
	return f'http://{http_server.host}:{http_server.port}'


@pytest.fixture(scope='module')
async def browser_session():
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			keep_alive=True,
		)
	)
	await session.start()
	yield session
	await session.kill()


# --- Helper ---


async def _navigate(browser_session, url: str):
	"""Navigate to URL and wait for page load."""
	await browser_session.navigate_to(url)
	await asyncio.sleep(0.5)


# --- Tests ---


class TestExtractCleanMarkdown:
	"""Tests for extract_clean_markdown with extract_images parameter."""

	async def test_table_images_excluded_by_default(self, browser_session, base_url):
		"""Images inside <td> lose their URL with extract_images=False (default).

		This is the AGI-101 root cause: markdownify strips img src in _inline contexts
		(td/th/headings) when keep_inline_images_in=[]. The alt text is kept but not the URL.
		"""
		await _navigate(browser_session, f'{base_url}/products-table')

		content, _ = await extract_clean_markdown(browser_session=browser_session, extract_images=False)

		# Alt text (product names) should still be present
		assert 'Widget A' in content
		assert 'Widget B' in content
		# But image URLs should NOT appear — img in td is stripped to alt text
		assert 'widget-a.jpg' not in content
		assert 'widget-b.jpg' not in content
		assert 'gadget-c.png' not in content
		# No markdown image syntax
		assert '![' not in content

	async def test_table_images_included_when_enabled(self, browser_session, base_url):
		"""Images inside <td> include their URL with extract_images=True."""
		await _navigate(browser_session, f'{base_url}/products-table')

		content, _ = await extract_clean_markdown(browser_session=browser_session, extract_images=True)

		# Image markdown syntax SHOULD be present for td-context images
		assert '![' in content
		# At least one product image URL should appear
		assert 'widget-a.jpg' in content or 'widget-b.jpg' in content or 'gadget-c.png' in content

	async def test_block_images_always_included(self, browser_session, base_url):
		"""Block-level images (in <div>, <figure>) are always included, extract_images has no effect."""
		await _navigate(browser_session, f'{base_url}/products-block')

		content_false, _ = await extract_clean_markdown(browser_session=browser_session, extract_images=False)
		content_true, _ = await extract_clean_markdown(browser_session=browser_session, extract_images=True)

		# Block-level images are always converted to ![alt](src) regardless
		assert '![' in content_false
		assert 'widget-a.jpg' in content_false
		assert '![' in content_true
		assert 'widget-a.jpg' in content_true

	async def test_false_is_default(self, browser_session, base_url):
		"""Calling extract_clean_markdown without extract_images behaves same as extract_images=False."""
		await _navigate(browser_session, f'{base_url}/products-table')

		content_default, _ = await extract_clean_markdown(browser_session=browser_session)
		content_false, _ = await extract_clean_markdown(browser_session=browser_session, extract_images=False)

		assert content_default == content_false

	async def test_no_images_on_text_only_page(self, browser_session, base_url):
		"""extract_images=True on a page with no images returns no image markdown."""
		await _navigate(browser_session, f'{base_url}/text-only')

		content, _ = await extract_clean_markdown(browser_session=browser_session, extract_images=True)

		assert '![' not in content
		assert 'No Images Here' in content or 'text content' in content


class TestExtractImagesAutoDetection:
	"""Tests for auto-detection of image-related queries in the extract action."""

	async def test_auto_detect_image_url_query(self, browser_session, base_url, tmp_path):
		"""Query containing 'image url' auto-enables extract_images and returns deterministic image URLs."""
		from agentyc.filesystem.file_system import FileSystem
		from agentyc.tools.service import Tools

		await _navigate(browser_session, f'{base_url}/products-table')

		tools = Tools()
		result = await tools.extract(
			query='get image url for each product',
			browser_session=browser_session,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		assert result.error is None
		assert result.metadata['strategy'] == 'deterministic-images'
		assert result.metadata['deterministic_extraction'] is True
		assert 'widget-a.jpg' in result.extracted_content or 'widget-b.jpg' in result.extracted_content

	async def test_no_auto_detect_without_image_keyword(self, browser_session, base_url, tmp_path):
		"""Query without image keywords keeps table images out of the deterministic table result."""
		from agentyc.filesystem.file_system import FileSystem
		from agentyc.tools.service import Tools

		await _navigate(browser_session, f'{base_url}/products-table')

		tools = Tools()
		result = await tools.extract(
			query='extract the table on the page',
			browser_session=browser_session,
			file_system=FileSystem(base_dir=str(tmp_path)),
		)

		assert result.error is None
		assert result.metadata['strategy'] == 'deterministic-tables'
		assert result.metadata['deterministic_extraction'] is True
		assert 'widget-a.jpg' not in result.extracted_content
		assert 'widget-b.jpg' not in result.extracted_content
