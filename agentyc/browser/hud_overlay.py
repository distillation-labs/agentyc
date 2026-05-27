from __future__ import annotations

import os
import queue
import sys
from multiprocessing import get_context
from typing import Any

from agentyc.browser.hud_stream import HudEvent, HudStream


def _run_overlay_process(command_queue: Any, status_queue: Any) -> None:
	try:
		import tkinter as tk
	except Exception:
		status_queue.put({'type': 'unavailable'})
		return

	try:
		root = tk.Tk()
	except Exception:
		status_queue.put({'type': 'unavailable'})
		return
	root.title('Agentyc HUD')
	root.configure(bg='#0a0a0c')
	root.attributes('-topmost', True)
	try:
		root.attributes('-alpha', 0.88)
	except Exception:
		pass
	try:
		root.overrideredirect(True)
	except Exception:
		pass

	width = 340
	height = 220
	x_offset = max(root.winfo_screenwidth() - width - 24, 0)
	root.geometry(f'{width}x{height}+{x_offset}+24')

	header = tk.Frame(root, bg='#0a0a0c', highlightbackground='#2a2a2e', highlightthickness=1)
	header.pack(fill='x', padx=8, pady=(8, 0))
	title = tk.Label(
		header,
		text='AGENTYC MCP',
		bg='#0a0a0c',
		fg='#f4f4f5',
		font=('Menlo', 10, 'bold'),
		anchor='w',
	)
	title.pack(fill='x', padx=8, pady=(6, 0))
	status = tk.Label(
		header,
		text='Live',
		bg='#0a0a0c',
		fg='#a1a1aa',
		font=('Menlo', 9),
		anchor='w',
	)
	status.pack(fill='x', padx=8, pady=(0, 6))

	current = tk.Label(
		root,
		text='Waiting for the next action',
		bg='#111114',
		fg='#fafafa',
		font=('Menlo', 10),
		anchor='w',
		justify='left',
		wraplength=300,
		highlightbackground='#2a2a2e',
		highlightthickness=1,
		padx=8,
		pady=8,
	)
	current.pack(fill='x', padx=8, pady=8)

	feed = tk.Listbox(
		root,
		bg='#0f0f12',
		fg='#e4e4e7',
		highlightbackground='#2a2a2e',
		highlightthickness=1,
		borderwidth=0,
		font=('Menlo', 9),
		selectbackground='#0f0f12',
		selectforeground='#e4e4e7',
		activestyle='none',
	)
	feed.pack(fill='both', expand=True, padx=8, pady=(0, 8))
	feed.insert('end', 'No visible actions yet.')

	rows: list[str] = []

	def format_row(event: dict[str, Any]) -> str:
		kind = event.get('kind')
		kind_value = kind if isinstance(kind, str) else ''
		icon = {
			'browser_event': 'o',
			'intent': '>',
			'tool_done': '+',
			'tool_error': '!',
			'tool_start': '-',
		}.get(kind_value, '-')
		duration_ms = event.get('duration_ms')
		duration = ''
		if isinstance(duration_ms, (int, float)):
			if duration_ms >= 1000:
				duration = f' ({duration_ms / 1000:.1f}s)'
			else:
				duration = f' ({round(duration_ms)}ms)'
		return f'{icon} {event.get("label", "")}{duration}'

	def pump() -> None:
		while True:
			try:
				item = command_queue.get_nowait()
			except queue.Empty:
				break
			if not isinstance(item, dict):
				continue
			item_type = item.get('type')
			if item_type == 'stop':
				root.destroy()
				return
			if item_type != 'event':
				continue

			raw_event = item.get('event')
			event = raw_event if isinstance(raw_event, dict) else {}
			kind = event.get('kind')
			kind_value = kind if isinstance(kind, str) else ''
			status_text = 'Live'
			if kind_value == 'tool_error':
				status_text = 'Attention needed'
			elif kind_value == 'intent':
				status_text = 'Intent updated'
			elif kind_value == 'tool_done':
				status_text = 'Updated'
			status.config(text=status_text)
			label = event.get('label')
			current.config(text=label if isinstance(label, str) and label else 'Waiting for the next action')

			rows.insert(0, format_row(event))
			del rows[8:]
			feed.delete(0, 'end')
			for row in rows:
				feed.insert('end', row)

		root.after(120, pump)

	status_queue.put({'type': 'ready'})
	root.after(120, pump)
	root.mainloop()


class HudOverlay:
	"""Small always-on-top desktop HUD for local operator visibility."""

	def __init__(self) -> None:
		self._ctx = get_context('spawn')
		self._command_queue: Any = None
		self._status_queue: Any = None
		self._process: Any = None
		self._available = True
		self._running = False

	def start(self) -> bool:
		if self._running:
			return self._available
		if not self._display_available():
			self._available = False
			return False

		self._command_queue = self._ctx.Queue()
		self._status_queue = self._ctx.Queue()
		self._process = self._ctx.Process(
			target=_run_overlay_process,
			args=(self._command_queue, self._status_queue),
			daemon=True,
		)
		self._process.start()
		try:
			status = self._status_queue.get(timeout=1.0)
		except queue.Empty:
			status = None
		if not isinstance(status, dict) or status.get('type') != 'ready':
			self._available = False
			self._teardown_process()
			return False

		self._running = True
		HudStream.get().subscribe(self._on_event)
		return True

	def stop(self) -> None:
		if not self._running:
			return
		HudStream.get().unsubscribe(self._on_event)
		self._running = False
		if self._command_queue is not None:
			self._command_queue.put({'type': 'stop'})
		self._teardown_process()

	def _display_available(self) -> bool:
		if sys.platform == 'linux':
			return bool(os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY'))
		return True

	def _on_event(self, event: HudEvent) -> None:
		if not self._running or self._command_queue is None:
			return
		self._command_queue.put(
			{
				'type': 'event',
				'event': {
					'kind': event.kind,
					'label': event.label,
					'duration_ms': event.duration_ms,
				},
			}
		)

	def _teardown_process(self) -> None:
		if self._process and self._process.is_alive():
			self._process.join(timeout=1.0)
		if self._process and self._process.is_alive():
			self._process.terminate()
		self._command_queue = None
		self._status_queue = None
		self._process = None


__all__ = ['HudOverlay']
