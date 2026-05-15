"""Minimal CLI entrypoint for MCP server mode."""

from __future__ import annotations

import argparse
import asyncio
import json

from agentyc.mcp.server import main as mcp_main


def _cmd_mcp(args: argparse.Namespace) -> None:
	window_bounds = json.loads(args.shared_browser_window_bounds) if args.shared_browser_window_bounds else None
	asyncio.run(
		mcp_main(
			session_timeout_minutes=args.session_timeout_minutes,
			cdp_url=args.cdp_url,
			runtime_label=args.runtime_label,
			runtime_role=args.runtime_role,
			parent_runtime_id=args.parent_runtime_id,
			shared_browser_mode=args.shared_browser_mode,
			shared_browser_window_bounds=window_bounds,
			shared_browser_focus_policy=args.shared_browser_focus_policy,
		)
	)


def _cmd_browser(args: argparse.Namespace) -> None:
	"""Launch Chrome with remote debugging enabled and print the CDP WebSocket URL."""
	import json
	import subprocess
	import sys
	import time
	import urllib.request

	port = args.port

	# Build chrome launch command
	import shutil

	chrome_candidates = [
		'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
		'/Applications/Chromium.app/Contents/MacOS/Chromium',
		shutil.which('google-chrome'),
		shutil.which('google-chrome-stable'),
		shutil.which('chromium'),
		shutil.which('chromium-browser'),
	]
	chrome_bin = next((c for c in chrome_candidates if c and __import__('os').path.isfile(c)), None)
	if not chrome_bin:
		print('Could not find Chrome or Chromium. Install Chrome and try again.', file=sys.stderr)
		sys.exit(1)

	cmd = [
		chrome_bin,
		f'--remote-debugging-port={port}',
		'--no-first-run',
		'--no-default-browser-check',
		'--disable-background-networking',
	]
	if args.headless:
		cmd.append('--headless=new')

	proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

	# Poll until DevTools is ready
	deadline = time.time() + 15
	cdp_url = None
	while time.time() < deadline:
		try:
			resp = urllib.request.urlopen(f'http://localhost:{port}/json/version', timeout=1)
			data = json.loads(resp.read())
			cdp_url = data.get('webSocketDebuggerUrl')
			if cdp_url:
				break
		except Exception:
			time.sleep(0.2)

	if not cdp_url:
		proc.kill()
		print(f'Chrome did not start within 15 seconds on port {port}', file=sys.stderr)
		sys.exit(1)

	print(cdp_url, flush=True)

	if not args.detach:
		# Keep running so Chrome stays alive; forward Ctrl-C
		try:
			proc.wait()
		except KeyboardInterrupt:
			proc.terminate()


def main() -> None:
	"""Run the MCP server over stdio, or launch a shared browser."""
	parser = argparse.ArgumentParser(description='agentyc CLI')
	sub = parser.add_subparsers(dest='command')

	# Default: run MCP server (backward compatible — no subcommand required)
	mcp_parser = sub.add_parser('mcp', help='Run the MCP server over stdio (default)')
	mcp_parser.add_argument(
		'--session-timeout-minutes',
		type=int,
		default=10,
		help='Idle timeout for managed browser sessions',
	)
	mcp_parser.add_argument(
		'--cdp-url',
		type=str,
		default=None,
		help='CDP WebSocket URL of a shared Chrome browser. When provided, attaches to the existing browser and creates a collaboration target (tab by default, or a separate window in window mode) instead of launching a separate browser process. Use `agentyc browser` to get this URL.',
	)
	mcp_parser.add_argument('--runtime-label', type=str, default=None, help='Collaboration label for this runtime.')
	mcp_parser.add_argument('--runtime-role', type=str, default='primary', help='Collaboration role for this runtime.')
	mcp_parser.add_argument('--parent-runtime-id', type=str, default=None, help='Optional parent runtime identifier.')
	mcp_parser.add_argument(
		'--shared-browser-mode',
		choices=('tab', 'window'),
		default='tab',
		help='When attaching to a shared browser, create a tab or a separate window.',
	)
	mcp_parser.add_argument(
		'--shared-browser-window-bounds',
		type=str,
		default=None,
		help='Optional JSON object for shared-browser window bounds, e.g. {"left":0,"top":0,"width":1280,"height":900}.',
	)
	mcp_parser.add_argument(
		'--shared-browser-focus-policy',
		choices=('preserve', 'activate'),
		default='preserve',
		help='Preserve human focus by default for internal attach/new-tab flows, or activate the runtime target.',
	)

	# browser subcommand: start Chrome with remote debugging
	browser_parser = sub.add_parser('browser', help='Start Chrome with remote debugging and print the CDP WebSocket URL')
	browser_parser.add_argument('--port', type=int, default=9222, help='Remote debugging port (default: 9222)')
	browser_parser.add_argument('--headless', action='store_true', help='Run Chrome in headless mode')
	browser_parser.add_argument('--detach', action='store_true', help='Print URL and exit, leaving Chrome running in background')

	args = parser.parse_args()

	if args.command == 'browser':
		_cmd_browser(args)
	elif args.command == 'mcp':
		_cmd_mcp(args)
	else:
		# No subcommand: backward-compatible MCP server mode
		# Re-parse with the flat mcp args for backward compat
		flat_parser = argparse.ArgumentParser(description='agentyc MCP server')
		flat_parser.add_argument('--session-timeout-minutes', type=int, default=10)
		flat_parser.add_argument('--cdp-url', type=str, default=None)
		flat_parser.add_argument('--runtime-label', type=str, default=None)
		flat_parser.add_argument('--runtime-role', type=str, default='primary')
		flat_parser.add_argument('--parent-runtime-id', type=str, default=None)
		flat_parser.add_argument('--shared-browser-mode', choices=('tab', 'window'), default='tab')
		flat_parser.add_argument('--shared-browser-window-bounds', type=str, default=None)
		flat_parser.add_argument('--shared-browser-focus-policy', choices=('preserve', 'activate'), default='preserve')
		flat_args = flat_parser.parse_args()
		window_bounds = json.loads(flat_args.shared_browser_window_bounds) if flat_args.shared_browser_window_bounds else None
		asyncio.run(
			mcp_main(
				session_timeout_minutes=flat_args.session_timeout_minutes,
				cdp_url=flat_args.cdp_url,
				runtime_label=flat_args.runtime_label,
				runtime_role=flat_args.runtime_role,
				parent_runtime_id=flat_args.parent_runtime_id,
				shared_browser_mode=flat_args.shared_browser_mode,
				shared_browser_window_bounds=window_bounds,
				shared_browser_focus_policy=flat_args.shared_browser_focus_policy,
			)
		)
