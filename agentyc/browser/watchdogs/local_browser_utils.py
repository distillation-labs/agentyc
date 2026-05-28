"""Leaf helpers for LocalBrowserWatchdog."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def _find_free_port() -> int:
	"""Find a free port for the debugging interface."""
	import socket

	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
		sock.bind(('127.0.0.1', 0))
		sock.listen(1)
		port = sock.getsockname()[1]
	return port


def _cleanup_temp_dir(logger: Any, temp_dir: Path | str) -> None:
	"""Clean up a temporary browser profile directory."""
	if not temp_dir:
		return

	try:
		temp_path = Path(temp_dir)
		if 'agentyc-tmp-' in str(temp_path):
			shutil.rmtree(temp_path, ignore_errors=True)
	except Exception as error:
		logger.debug(f'Failed to cleanup temp dir {temp_dir}: {error}')
