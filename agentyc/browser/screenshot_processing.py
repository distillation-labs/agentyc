"""Helpers for compact LLM-targeted screenshot encoding."""

from __future__ import annotations

import io


def resize_screenshot_for_llm(
	data: bytes,
	target_size: tuple[int, int] | None,
	target_format: str = 'png',
	quality: int = 85,
	grayscale: bool = False,
) -> bytes:
	"""Resize/convert screenshot bytes for LLM consumption.

	Returns the original bytes on any processing failure.
	"""
	try:
		from PIL import Image

		img = Image.open(io.BytesIO(data))
		if img.mode == 'RGBA':
			img = img.convert('RGB')

		if grayscale:
			img = img.convert('L')

		if target_size:
			# Bilinear is cheaper than Lanczos here and produced smaller WebP outputs in our benchmark.
			img = img.resize(target_size, Image.Resampling.BILINEAR)

		buf = io.BytesIO()
		if target_format == 'jpeg':
			img.save(buf, format='JPEG', quality=quality, optimize=True)
		elif target_format == 'webp':
			img.save(buf, format='WEBP', quality=quality, optimize=True)
		else:
			if img.mode == 'L':
				img = img.convert('RGB')
			img.save(buf, format='PNG', optimize=True)
		return buf.getvalue()
	except Exception:
		return data
