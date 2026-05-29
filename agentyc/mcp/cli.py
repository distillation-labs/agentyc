"""Minimal CLI entrypoint for MCP server mode."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from agentyc.mcp.server_main import main as mcp_main

_SKILL_FILE = Path(__file__).parent.parent / 'skills' / 'SKILL.md'


def _cmd_init(args: argparse.Namespace) -> None:
	"""Write the agentyc skills guide to a file or print it."""
	if not _SKILL_FILE.exists():
		print('Skills file not found in package. Please reinstall agentyc.', file=sys.stderr)
		sys.exit(1)

	content = _SKILL_FILE.read_text()

	if args.print:
		print(content)
		return

	dest = Path(args.output)
	if dest.exists() and not args.force:
		print(f'{dest} already exists. Use --force to overwrite.', file=sys.stderr)
		sys.exit(1)

	dest.parent.mkdir(parents=True, exist_ok=True)
	dest.write_text(content)
	print(f'Written to {dest}')
	print()
	print('Add this file to your coding agent context:')
	print(f'  Claude Code:  add "{dest}" to CLAUDE.md with @{dest}')
	print('  Cursor:       copy to .cursor/rules/agentyc.md')
	print('  Copilot:      append to .github/copilot-instructions.md')


def _cmd_mcp(args: argparse.Namespace) -> None:
	window_bounds = json.loads(args.shared_browser_window_bounds) if args.shared_browser_window_bounds else None
	asyncio.run(
		mcp_main(
			session_timeout_minutes=args.session_timeout_minutes,
			cdp_url=args.cdp_url,
			hud_overlay=args.hud_overlay,
			runtime_label=args.runtime_label,
			runtime_role=args.runtime_role,
			parent_runtime_id=args.parent_runtime_id,
			shared_browser_mode=args.shared_browser_mode,
			shared_browser_window_bounds=window_bounds,
			shared_browser_focus_policy=args.shared_browser_focus_policy,
			reuse_local_browser=args.reuse_local_browser,
		)
	)


def _cmd_browser(args: argparse.Namespace) -> None:
	"""Launch Chrome with remote debugging enabled and print the CDP WebSocket URL."""
	import json
	import subprocess
	import sys
	import time
	import urllib.request

	from agentyc.mcp.shared_browser_registry import register_local_shared_browser

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

	register_local_shared_browser(
		cdp_url=cdp_url,
		browser_pid=proc.pid,
		headless=args.headless,
		user_data_dir=None,
	)
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
		default=0,
		help='Idle timeout in minutes for managed browser sessions. 0 (default) disables automatic cleanup and keeps sessions alive indefinitely.',
	)
	mcp_parser.add_argument(
		'--hud-overlay',
		action=argparse.BooleanOptionalAction,
		default=None,
		help='Show a small transparent desktop HUD that mirrors sanitized MCP runtime activity.',
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
	mcp_parser.add_argument(
		'--reuse-local-browser',
		action=argparse.BooleanOptionalAction,
		default=None,
		help='Reuse a previously launched local Agentyc browser from the shared-browser registry. '
		'Defaults to AGENTYC_REUSE_LOCAL_BROWSER when unset.',
	)

	# init subcommand: write skills guide to a file
	init_parser = sub.add_parser('init', help='Write the agentyc skills guide to a file for your coding agent')
	init_parser.add_argument(
		'--output',
		type=str,
		default='agentyc-skill.md',
		help='Destination file path (default: agentyc-skill.md)',
	)
	init_parser.add_argument('--print', action='store_true', help='Print the skills guide to stdout instead of writing a file')
	init_parser.add_argument('--force', action='store_true', help='Overwrite the destination file if it already exists')

	# browser subcommand: start Chrome with remote debugging
	browser_parser = sub.add_parser('browser', help='Start Chrome with remote debugging and print the CDP WebSocket URL')
	browser_parser.add_argument('--port', type=int, default=9222, help='Remote debugging port (default: 9222)')
	browser_parser.add_argument('--headless', action='store_true', help='Run Chrome in headless mode')
	browser_parser.add_argument('--detach', action='store_true', help='Print URL and exit, leaving Chrome running in background')

	args = parser.parse_args()

	if args.command == 'init':
		_cmd_init(args)
	elif args.command == 'browser':
		_cmd_browser(args)
	elif args.command == 'mcp':
		_cmd_mcp(args)
	else:
		# No subcommand: backward-compatible MCP server mode
		# Re-parse with the flat mcp args for backward compat
		flat_parser = argparse.ArgumentParser(description='agentyc MCP server')
		flat_parser.add_argument('--session-timeout-minutes', type=int, default=0)
		flat_parser.add_argument('--hud-overlay', action=argparse.BooleanOptionalAction, default=None)
		flat_parser.add_argument('--cdp-url', type=str, default=None)
		flat_parser.add_argument('--runtime-label', type=str, default=None)
		flat_parser.add_argument('--runtime-role', type=str, default='primary')
		flat_parser.add_argument('--parent-runtime-id', type=str, default=None)
		flat_parser.add_argument('--shared-browser-mode', choices=('tab', 'window'), default='tab')
		flat_parser.add_argument('--shared-browser-window-bounds', type=str, default=None)
		flat_parser.add_argument('--shared-browser-focus-policy', choices=('preserve', 'activate'), default='preserve')
		flat_parser.add_argument('--reuse-local-browser', action=argparse.BooleanOptionalAction, default=None)
		flat_args = flat_parser.parse_args()
		window_bounds = json.loads(flat_args.shared_browser_window_bounds) if flat_args.shared_browser_window_bounds else None
		asyncio.run(
			mcp_main(
				session_timeout_minutes=flat_args.session_timeout_minutes,
				cdp_url=flat_args.cdp_url,
				hud_overlay=flat_args.hud_overlay,
				runtime_label=flat_args.runtime_label,
				runtime_role=flat_args.runtime_role,
				parent_runtime_id=flat_args.parent_runtime_id,
				shared_browser_mode=flat_args.shared_browser_mode,
				shared_browser_window_bounds=window_bounds,
				shared_browser_focus_policy=flat_args.shared_browser_focus_policy,
				reuse_local_browser=flat_args.reuse_local_browser,
			)
		)
