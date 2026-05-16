"""Test-suite-level Chrome process cleanup.

Any BrowserSession started during CI tests must be cleaned up even when a test
crashes or is interrupted.  We track every Chrome PID that agentyc spawns by
hooking into LocalBrowserWatchdog's launch event, then terminate survivors at
the end of the test session.
"""

from __future__ import annotations

import atexit
import os
import signal

import pytest

# Module-level set that accumulates every Chrome PID agentyc starts.
_tracked_pids: set[int] = set()


def _register_pid(pid: int) -> None:
	_tracked_pids.add(pid)


def _kill_tracked_pids() -> None:
	"""Best-effort SIGTERM (then SIGKILL) for every tracked Chrome PID."""
	for pid in list(_tracked_pids):
		try:
			os.kill(pid, signal.SIGTERM)
		except ProcessLookupError:
			pass  # already gone
		except Exception:
			pass
	_tracked_pids.clear()


# Register the cleanup so it also fires when pytest exits via sys.exit().
atexit.register(_kill_tracked_pids)


@pytest.fixture(scope='session', autouse=True)
def _track_chrome_pids(monkeypatch_session):
	"""Patch LocalBrowserWatchdog to record every Chrome PID it launches."""
	try:
		from agentyc.browser.watchdogs.local_browser_watchdog import LocalBrowserWatchdog

		original_launch = LocalBrowserWatchdog._launch_browser

		async def _patched_launch(self, *args, **kwargs):
			process, cdp_url = await original_launch(self, *args, **kwargs)
			if process is not None:
				_register_pid(process.pid)
			return process, cdp_url

		monkeypatch_session.setattr(LocalBrowserWatchdog, '_launch_browser', _patched_launch)
	except Exception:
		pass

	yield

	_kill_tracked_pids()


@pytest.fixture(scope='session')
def monkeypatch_session():
	"""Session-scoped monkeypatch fixture (pytest only ships function-scoped by default)."""
	from _pytest.monkeypatch import MonkeyPatch

	mp = MonkeyPatch()
	yield mp
	mp.undo()
