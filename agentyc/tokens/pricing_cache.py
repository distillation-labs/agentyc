import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import httpx

from agentyc.config import CONFIG
from agentyc.tokens.views import CachedPricingData

if TYPE_CHECKING:
	from agentyc.tokens.service import TokenCost


logger = logging.getLogger('agentyc.tokens.service')


def xdg_cache_home() -> Path:
	default = Path.home() / '.cache'
	if CONFIG.XDG_CACHE_HOME and (path := Path(CONFIG.XDG_CACHE_HOME)).is_absolute():
		return path
	return default


async def initialize(token_cost: 'TokenCost') -> None:
	"""Initialize the service by loading pricing data."""
	if not token_cost._initialized:
		if token_cost.include_cost:
			await _load_pricing_data(token_cost)
		token_cost._initialized = True


async def _load_pricing_data(token_cost: 'TokenCost') -> None:
	"""Load pricing data from cache or fetch from GitHub."""
	cache_file = await _find_valid_cache(token_cost)
	if cache_file:
		await _load_from_cache(token_cost, cache_file)
	else:
		await _fetch_and_cache_pricing_data(token_cost)


async def _find_valid_cache(token_cost: 'TokenCost') -> Path | None:
	"""Find the most recent valid cache file."""
	try:
		token_cost._cache_dir.mkdir(parents=True, exist_ok=True)
		cache_files = list(token_cost._cache_dir.glob('*.json'))

		if not cache_files:
			return None

		cache_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

		for cache_file in cache_files:
			is_valid, should_delete = await _get_cache_status(token_cost, cache_file)
			if is_valid:
				return cache_file
			if should_delete:
				try:
					os.remove(cache_file)
				except Exception:
					pass

		return None
	except Exception:
		return None


async def _get_cache_status(token_cost: 'TokenCost', cache_file: Path) -> tuple[bool, bool]:
	"""Return whether a cache file is usable and whether it should be deleted."""
	try:
		if not cache_file.exists():
			return False, False

		cached = CachedPricingData.model_validate_json(await anyio.Path(cache_file).read_text())
		if token_cost._cache_expired(cached):
			return False, True
		return _cache_source_matches(token_cost, cached), False
	except Exception:
		return False, True


def _cache_source_matches(token_cost: 'TokenCost', cached: CachedPricingData) -> bool:
	"""Only use cached pricing files from the same source URL."""
	if cached.source_url is None:
		return token_cost.pricing_url == token_cost.DEFAULT_PRICING_URL

	return cached.source_url == token_cost.pricing_url


async def _load_from_cache(token_cost: 'TokenCost', cache_file: Path) -> None:
	"""Load pricing data from a specific cache file."""
	try:
		content = await anyio.Path(cache_file).read_text()
		cached = CachedPricingData.model_validate_json(content)
		token_cost._pricing_data = cached.data
	except Exception as e:
		logger.debug(f'Error loading cached pricing data from {cache_file}: {e}')
		await _fetch_and_cache_pricing_data(token_cost)


async def _fetch_and_cache_pricing_data(token_cost: 'TokenCost') -> None:
	"""Fetch pricing data from LiteLLM GitHub and cache it with timestamp."""
	try:
		async with httpx.AsyncClient() as client:
			response = await client.get(token_cost.pricing_url, timeout=30)
			response.raise_for_status()
			token_cost._pricing_data = response.json()

		cached = CachedPricingData(
			timestamp=token_cost._now(),
			source_url=token_cost.pricing_url,
			data=token_cost._pricing_data or {},
		)
		token_cost._cache_dir.mkdir(parents=True, exist_ok=True)
		timestamp_str = token_cost._now().strftime('%Y%m%d_%H%M%S')
		cache_file = token_cost._cache_dir / f'pricing_{timestamp_str}.json'
		await anyio.Path(cache_file).write_text(cached.model_dump_json(indent=2))
	except Exception as e:
		logger.debug(f'Error fetching pricing data: {e}')
		token_cost._pricing_data = {}


async def refresh_pricing_data(token_cost: 'TokenCost') -> None:
	"""Force refresh of pricing data from GitHub."""
	if token_cost.include_cost:
		await _fetch_and_cache_pricing_data(token_cost)


async def clean_old_caches(token_cost: 'TokenCost', keep_count: int = 3) -> None:
	"""Clean up old cache files, keeping only the most recent ones from this source URL."""
	try:
		cache_files = list(token_cost._cache_dir.glob('*.json'))
		if not cache_files:
			return

		own_files: list[Path] = []
		for cache_file in cache_files:
			try:
				cached = CachedPricingData.model_validate_json(cache_file.read_text())
				if _cache_source_matches(token_cost, cached):
					own_files.append(cache_file)
			except Exception:
				pass

		if len(own_files) <= keep_count:
			return

		own_files.sort(key=lambda f: f.stat().st_mtime)
		for cache_file in own_files[:-keep_count]:
			try:
				os.remove(cache_file)
			except Exception:
				pass
	except Exception as e:
		logger.debug(f'Error cleaning old cache files: {e}')


async def ensure_pricing_loaded(token_cost: 'TokenCost') -> None:
	"""Ensure pricing data is loaded in the background."""
	if not token_cost._initialized and token_cost.include_cost:
		await initialize(token_cost)
