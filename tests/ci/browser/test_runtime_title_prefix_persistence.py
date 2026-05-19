from __future__ import annotations

import asyncio

from pytest_httpserver import HTTPServer

from agentyc.browser.profile import BrowserProfile
from agentyc.browser.session import BrowserSession


def _base_url(httpserver: HTTPServer) -> str:
	return f'http://127.0.0.1:{httpserver.port}'


async def test_runtime_title_prefix_persists_after_late_title_rewrite(httpserver: HTTPServer) -> None:
	httpserver.expect_request('/late-title').respond_with_data(
		"""
		<!DOCTYPE html>
		<html lang="en">
		<head>
			<title>Initial title</title>
		</head>
		<body>
			<main>Runtime title test</main>
			<script>
				setTimeout(() => {
					document.title = 'Late rewrite';
				}, 150);
			</script>
		</body>
		</html>
		""",
		content_type='text/html',
	)

	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			keep_alive=True,
		)
	)
	await session.start()

	try:
		await session.navigate_to(f'{_base_url(httpserver)}/late-title')
		await asyncio.sleep(0.4)

		title = await session.get_current_page_title()

		assert title == f'{session.runtime_metadata.title_prefix}Late rewrite'.rstrip()
	finally:
		await session.kill()
