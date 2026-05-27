from __future__ import annotations

import argparse
import asyncio
import base64
import io
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import median
from threading import Thread

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from agentyc.browser import BrowserSession
from agentyc.browser.profile import BrowserProfile

REPORT_WIDTH = 110


@dataclass
class Config:
	label: str
	format: str  # 'png' or 'jpeg'
	quality: int | None  # JPEG quality 1-100, None for PNG
	resize: tuple[int, int] | None  # (width, height) or None for original


@dataclass
class Result:
	config: Config
	size_bytes: int
	size_b64_bytes: int
	latency_ms: float
	ssim_score: float | None = None


TEST_HTML = """<!DOCTYPE html>
<html><head><style>
  body { font-family: sans-serif; margin: 40px; background: #f5f5f5; }
  h1 { color: #333; font-size: 28px; }
  .card { background: white; border-radius: 8px; padding: 20px; margin: 16px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
  button { background: #0066cc; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; }
  input { padding: 8px; border: 1px solid #ccc; border-radius: 4px; width: 300px; }
  table { width: 100%; border-collapse: collapse; }
  td, th { border: 1px solid #ddd; padding: 8px; text-align: left; }
  img { max-width: 100%; }
  .grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
</style></head><body>
  <h1>ACME Corp Dashboard</h1>
  <div class="card">
    <h2>Welcome back, Jane!</h2>
    <p>You have <strong>3</strong> pending tasks and <strong>2</strong> unread messages.</p>
    <button>View Tasks</button>
    <button style="background:#666">Messages</button>
  </div>
  <div class="card">
    <h2>Quick Actions</h2>
    <input type="text" placeholder="Search..." />
    <button style="background:green">Go</button>
  </div>
  <div class="card grid">
    <div><h3>Revenue</h3><p style="font-size:24px;color:green;">$128,430</p><small>+12.3%</small></div>
    <div><h3>Users</h3><p style="font-size:24px;color:blue;">2,847</p><small>+8.1%</small></div>
    <div><h3>Orders</h3><p style="font-size:24px;color:orange;">431</p><small>-2.4%</small></div>
  </div>
  <div class="card">
    <h2>Recent Orders</h2>
    <table><tr><th>ID</th><th>Customer</th><th>Status</th><th>Total</th></tr>
    <tr><td>#1024</td><td>Alice Johnson</td><td><span style="color:green">Shipped</span></td><td>$249.99</td></tr>
    <tr><td>#1023</td><td>Bob Smith</td><td><span style="color:orange">Pending</span></td><td>$129.50</td></tr>
    <tr><td>#1022</td><td>Carol Williams</td><td><span style="color:green">Shipped</span></td><td>$599.00</td></tr>
    <tr><td>#1021</td><td>Dave Brown</td><td><span style="color:red">Cancelled</span></td><td>$49.99</td></tr>
    <tr><td>#1020</td><td>Eve Davis</td><td><span style="color:green">Shipped</span></td><td>$1,299.00</td></tr></table>
  </div>
  <div class="card">
    <h2>Product Catalog</h2>
    <div class="grid">
      <div><h4>Laptop Pro</h4><p>$1,299</p><button>Buy</button></div>
      <div><h4>Wireless Mouse</h4><p>$49</p><button>Buy</button></div>
      <div><h4>USB-C Hub</h4><p>$79</p><button>Buy</button></div>
    </div>
  </div>
</body></html>"""


class QuietHandler(SimpleHTTPRequestHandler):
	def log_message(self, format: str, *args: object) -> None:
		return


def serve_fixture(html: str):
	tmp = tempfile.TemporaryDirectory()
	Path(tmp.name, 'index.html').write_text(html)
	os.chdir(tmp.name)
	server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
	thread = Thread(target=server.serve_forever, daemon=True)
	thread.start()
	url = f'http://127.0.0.1:{server.server_address[1]}/'
	return thread, server, url, tmp


async def capture_one(
	session: BrowserSession,
	page_url: str,
	config: Config,
) -> bytes:
	raw_bytes = await session.take_screenshot(full_page=False)

	if config.label.startswith('via pipeline'):
		resized = BrowserSession.resize_screenshot_for_llm(
			raw_bytes,
			target_size=config.resize,
			target_format=config.format,
			quality=config.quality or 85,
		)
		return resized

	img = Image.open(io.BytesIO(raw_bytes)).convert('RGB')

	if config.resize:
		img = img.resize(config.resize, Image.Resampling.LANCZOS)

	buf = io.BytesIO()
	f = config.format
	if f == 'jpeg':
		img.save(buf, format='JPEG', quality=config.quality, optimize=True)
	elif f == 'webp':
		img.save(buf, format='WEBP', quality=config.quality or 80, optimize=True)
	elif f == 'jpeg_gray':
		img_gray = img.convert('L')
		buf = io.BytesIO()
		img_gray.save(buf, format='JPEG', quality=config.quality, optimize=True)
	elif f == 'png8':
		img_q = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
		img_q.save(buf, format='PNG', optimize=True)
	else:
		img.save(buf, format='PNG', optimize=True)
	return buf.getvalue()


async def run_config(
	session: BrowserSession,
	page_url: str,
	config: Config,
	reference_bytes: bytes | None,
	n_runs: int = 3,
) -> Result:
	latencies: list[float] = []
	final_bytes: bytes | None = None

	for i in range(n_runs):
		t0 = time.perf_counter()
		data = await capture_one(session, page_url, config)
		latencies.append((time.perf_counter() - t0) * 1000)
		final_bytes = data

	assert final_bytes is not None
	b64_str = base64.b64encode(final_bytes).decode()
	size_b64 = len(b64_str)

	ssim_score = None

	return Result(
		config=config,
		size_bytes=len(final_bytes),
		size_b64_bytes=size_b64,
		latency_ms=median(latencies),
		ssim_score=ssim_score,
	)


def print_header(text: str):
	print(f'\n{"=" * REPORT_WIDTH}')
	print(f'  {text}')
	print(f'{"=" * REPORT_WIDTH}')


def estimate_tokens(b64_bytes: int, format: str) -> dict[str, int | str]:
	return {
		'openai_low': int(b64_bytes / 2.5),
		'openai_high': int(b64_bytes / 0.625),
		'anthropic_low': int(b64_bytes / 15),
		'notes': 'OpenAI: low/high res. Anthropic: 128px tiles.',
	}


def print_results(results: list[Result]):
	print(f'\n{"Config":<40} {"Bytes":>10} {"Base64":>10} {"Latency":>8} {"SSIM":>8} {"Est.Tokens":>12}')
	print('-' * REPORT_WIDTH)

	baseline = None
	for r in results:
		if r.config.label == 'PNG (baseline)':
			baseline = r
			break

	for r in results:
		tokens = estimate_tokens(r.size_b64_bytes, r.config.format)
		token_str = f'{tokens["openai_low"]:>12,}'
		ssim_str = f'{r.ssim_score:.4f}' if r.ssim_score is not None else 'N/A'
		print(
			f'{r.config.label:<40} {r.size_bytes:>10,} {r.size_b64_bytes:>10,} {r.latency_ms:>7.1f}ms {ssim_str:>8} {token_str}'
		)

	print()
	if baseline:
		print(f'{"Improvement vs PNG baseline":}')
		print('-' * REPORT_WIDTH)
		for r in results:
			if r.config.label == 'PNG (baseline)':
				continue
			ratio = r.size_b64_bytes / baseline.size_b64_bytes if baseline.size_b64_bytes else 1
			ssim_str = f'{r.ssim_score:.4f}' if r.ssim_score is not None else 'N/A'
			print(f'{r.config.label:<40}  {ratio:>7.2%} of baseline size  (SSIM: {ssim_str})')


async def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('--headless', action='store_true', default=True)
	parser.add_argument('--n-runs', type=int, default=3)
	parser.add_argument('--view-port', type=str, default='1280x720')
	args = parser.parse_args()

	vp = {'width': 1280, 'height': 720}
	if args.view_port:
		parts = args.view_port.split('x')
		vp = {'width': int(parts[0]), 'height': int(parts[1])}

	print_header(f'Screenshot Token Benchmark — Viewport {vp["width"]}x{vp["height"]}')

	thread, server, url, tmp = serve_fixture(TEST_HTML)
	try:
		profile = BrowserProfile(
			headless=True,
			viewport={'width': vp['width'], 'height': vp['height']},
			cdp_url=None,
		)
		session = BrowserSession(browser_profile=profile)
		await session.start()

		await session.navigate_to(url)
		await asyncio.sleep(1)

		print_header('Establishing Baseline')

		# Optimized configs to test
		configs: list[Config] = [
			Config(label='PNG (baseline)', format='png', quality=None, resize=None),
			Config(label='JPEG q=95', format='jpeg', quality=95, resize=None),
			Config(label='JPEG q=85', format='jpeg', quality=85, resize=None),
			Config(label='JPEG q=75', format='jpeg', quality=75, resize=None),
			Config(label='JPEG q=60', format='jpeg', quality=60, resize=None),
			Config(label='JPEG q=40', format='jpeg', quality=40, resize=None),
			Config(label='PNG resize 800x450', format='png', quality=None, resize=(800, 450)),
			Config(label='PNG resize 640x360', format='png', quality=None, resize=(640, 360)),
			Config(label='PNG resize 480x270', format='png', quality=None, resize=(480, 270)),
			Config(label='JPEG q=85 800x450', format='jpeg', quality=85, resize=(800, 450)),
			Config(label='JPEG q=85 640x360', format='jpeg', quality=85, resize=(640, 360)),
			Config(label='JPEG q=85 480x270', format='jpeg', quality=85, resize=(480, 270)),
			Config(label='JPEG q=60 800x450', format='jpeg', quality=60, resize=(800, 450)),
			Config(label='JPEG q=60 640x360', format='jpeg', quality=60, resize=(640, 360)),
			Config(label='JPEG q=60 480x270', format='jpeg', quality=60, resize=(480, 270)),
			Config(label='via pipeline: 800x450 PNG', format='png', quality=None, resize=(800, 450)),
			Config(label='via pipeline: 640x360 JPEG q=85', format='jpeg', quality=85, resize=(640, 360)),
			Config(label='via pipeline: 480x270 JPEG q=60', format='jpeg', quality=60, resize=(480, 270)),
			# Grayscale exploration
			Config(label='Gray JPEG q=85 640x360', format='jpeg_gray', quality=85, resize=(640, 360)),
			Config(label='Gray JPEG q=75 640x360', format='jpeg_gray', quality=75, resize=(640, 360)),
			Config(label='Gray JPEG q=60 640x360', format='jpeg_gray', quality=60, resize=(640, 360)),
			Config(label='Gray JPEG q=85 480x270', format='jpeg_gray', quality=85, resize=(480, 270)),
			Config(label='Gray JPEG q=60 480x270', format='jpeg_gray', quality=60, resize=(480, 270)),
			# PNG8 (256 colors)
			Config(label='PNG8 640x360', format='png8', quality=None, resize=(640, 360)),
			Config(label='PNG8 480x270', format='png8', quality=None, resize=(480, 270)),
			# WebP
			Config(label='WebP q=85 640x360', format='webp', quality=85, resize=(640, 360)),
			Config(label='WebP q=75 640x360', format='webp', quality=75, resize=(640, 360)),
			Config(label='WebP q=85 480x270', format='webp', quality=85, resize=(480, 270)),
			# Aggressive resize
			Config(label='JPEG q=85 320x180', format='jpeg', quality=85, resize=(320, 180)),
			Config(label='JPEG q=75 320x180', format='jpeg', quality=75, resize=(320, 180)),
			Config(label='JPEG q=60 320x180', format='jpeg', quality=60, resize=(320, 180)),
			Config(label='Gray JPEG q=85 320x180', format='jpeg_gray', quality=85, resize=(320, 180)),
			Config(label='Gray JPEG q=60 320x180', format='jpeg_gray', quality=60, resize=(320, 180)),
			Config(label='Gray JPEG q=85 240x135', format='jpeg_gray', quality=85, resize=(240, 135)),
		]

		png_baseline = await capture_one(session, url, Config(label='', format='png', quality=None, resize=None))

		results: list[Result] = []
		for config in configs:
			print(f'  Testing: {config.label}... ', end='', flush=True)
			result = await run_config(session, url, config, png_baseline, n_runs=args.n_runs)
			results.append(result)
			tokens = estimate_tokens(result.size_b64_bytes, config.format)
			print(f'base64={result.size_b64_bytes:,}B, latency={result.latency_ms:.1f}ms, ~{tokens["openai_low"]:,} tokens')

		print_results(results)

		print_header('Recommendations')
		best = min(results, key=lambda r: r.size_b64_bytes)
		print(f'  Smallest:  {best.config.label} ({best.size_b64_bytes:,}B base64)')
		if baseline_result := next((r for r in results if r.config.label == 'PNG (baseline)'), None):
			ratio = best.size_b64_bytes / baseline_result.size_b64_bytes
			print(f'  Reduction: {ratio:.2%} of baseline ({1 / ratio:.1f}x smaller)')

		high_quality = [r for r in results if r.ssim_score is None or r.ssim_score > 0.95]
		if high_quality:
			smallest_good = min(high_quality, key=lambda r: r.size_b64_bytes)
			print(f'\n  Best (SSIM>0.95): {smallest_good.config.label} ({smallest_good.size_b64_bytes:,}B base64)')

	finally:
		await session.stop()
		server.shutdown()
		tmp.cleanup()

	print()


if __name__ == '__main__':
	asyncio.run(main())
