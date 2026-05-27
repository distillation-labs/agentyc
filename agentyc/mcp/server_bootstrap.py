"""Logging/bootstrap helpers for the MCP server."""

from __future__ import annotations

import logging
import os
import sys

from agentyc.logging_config import setup_logging

try:
	import psutil

	PSUTIL_AVAILABLE = True
except ImportError:
	PSUTIL_AVAILABLE = False


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


def get_parent_process_cmdline() -> str | None:
	"""Get the command line of all parent processes up the chain."""
	if not PSUTIL_AVAILABLE:
		return None

	try:
		cmdlines: list[str] = []
		current_process = psutil.Process()
		parent = current_process.parent()

		while parent:
			try:
				cmdline = parent.cmdline()
				if cmdline:
					cmdlines.append(' '.join(cmdline))
			except (psutil.AccessDenied, psutil.NoSuchProcess):
				pass

			try:
				parent = parent.parent()
			except (psutil.AccessDenied, psutil.NoSuchProcess):
				break

		return ';'.join(cmdlines) if cmdlines else None
	except Exception:
		return None


__all__ = ['_configure_mcp_server_logging', '_ensure_all_loggers_use_stderr', 'get_parent_process_cmdline']
