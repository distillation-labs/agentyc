"""Logging/bootstrap helpers for the MCP server."""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import platform
import sys

from agentyc.logging_config import setup_logging

try:
	import psutil

	PSUTIL_AVAILABLE = True
except ImportError:
	PSUTIL_AVAILABLE = False


def _try_preload_allocator() -> str | None:
	"""Try to preload mimalloc or jemalloc for lower RSS fragmentation.

	On macOS, the default allocator holds partially-freed spans resident.
	mimalloc's background purging keeps RSS closer to actual live data.
	Returns the name of the loaded allocator, or None if none found.
	"""
	if os.environ.get('AGENTYC_SKIP_ALLOCATOR_PRELOAD'):
		return None

	system = platform.system()
	if system == 'Darwin':
		candidates = [
			('mimalloc', ['libmimalloc.dylib', 'libmimalloc.2.dylib', '/opt/homebrew/lib/libmimalloc.dylib', '/usr/local/lib/libmimalloc.dylib']),
			('jemalloc', ['libjemalloc.dylib', '/opt/homebrew/lib/libjemalloc.dylib', '/usr/local/lib/libjemalloc.dylib']),
		]
	elif system == 'Linux':
		candidates = [
			('mimalloc', ['libmimalloc.so.2', 'libmimalloc.so', '/usr/lib/libmimalloc.so.2']),
			('jemalloc', ['libjemalloc.so.2', 'libjemalloc.so', '/usr/lib/x86_64-linux-gnu/libjemalloc.so.2']),
		]
	else:
		return None

	for name, paths in candidates:
		# Try ctypes.util.find_library first
		found = ctypes.util.find_library(name)
		if found:
			paths = [found] + paths
		for path in paths:
			try:
				ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
				return name
			except OSError:
				continue
	return None


def _configure_mcp_server_logging() -> None:
	"""Configure logging for MCP server mode without polluting stdout."""
	os.environ['AGENTYC_LOGGING_LEVEL'] = 'warning'
	os.environ['AGENTYC_SETUP_LOGGING'] = 'false'

	setup_logging(stream=sys.stderr, log_level='warning', force_setup=True)

	logging.root.handlers = []
	stderr_handler = logging.StreamHandler(sys.stderr)
	stderr_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
	logging.root.addHandler(stderr_handler)
	logging.root.setLevel(logging.CRITICAL)

	for name in list(logging.root.manager.loggerDict.keys()):
		logger_obj = logging.getLogger(name)
		logger_obj.handlers = []
		logger_obj.setLevel(logging.CRITICAL)
		logger_obj.addHandler(stderr_handler)
		logger_obj.propagate = False


def _ensure_all_loggers_use_stderr() -> None:
	"""Ensure all existing loggers only write to stderr."""
	stderr_handler = None
	for handler in logging.root.handlers:
		if hasattr(handler, 'stream') and handler.stream == sys.stderr:  # type: ignore[attr-defined]
			stderr_handler = handler
			break

	if not stderr_handler:
		stderr_handler = logging.StreamHandler(sys.stderr)
		stderr_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

	logging.root.handlers = [stderr_handler]
	logging.root.setLevel(logging.CRITICAL)

	for name in list(logging.root.manager.loggerDict.keys()):
		logger_obj = logging.getLogger(name)
		logger_obj.handlers = [stderr_handler]
		logger_obj.setLevel(logging.CRITICAL)
		logger_obj.propagate = False


__all__ = ['_configure_mcp_server_logging', '_ensure_all_loggers_use_stderr', '_try_preload_allocator']
