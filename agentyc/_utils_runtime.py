import os
from functools import cache
from pathlib import Path

import httpx

from agentyc._utils_core import logger


def check_env_variables(keys: list[str], any_or_all=all) -> bool:
	"""Check if all required environment variables are set."""
	return any_or_all(os.getenv(key, '').strip() for key in keys)


def merge_dicts(a: dict, b: dict, path: tuple[str, ...] = ()):
	for key in b:
		if key in a:
			if isinstance(a[key], dict) and isinstance(b[key], dict):
				merge_dicts(a[key], b[key], path + (str(key),))
			elif isinstance(a[key], list) and isinstance(b[key], list):
				a[key] = a[key] + b[key]
			elif a[key] != b[key]:
				raise Exception('Conflict at ' + '.'.join(path + (str(key),)))
		else:
			a[key] = b[key]
	return a


@cache
def get_agentyc_version() -> str:
	"""Get the agentyc package version using the same logic as Agent._set_agentyc_version_and_source."""
	try:
		package_root = Path(__file__).parent.parent
		pyproject_path = package_root / 'pyproject.toml'

		if pyproject_path.exists():
			import re

			with open(pyproject_path, encoding='utf-8') as f:
				content = f.read()
				match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
				if match:
					version = f'{match.group(1)}'
					os.environ['LIBRARY_VERSION'] = version
					return version

		from importlib.metadata import version as get_version

		version = str(get_version('agentyc'))
		os.environ['LIBRARY_VERSION'] = version
		return version
	except Exception as e:
		logger.debug(f'Error detecting agentyc version: {type(e).__name__}: {e}')
		return 'unknown'


async def check_latest_agentyc_version() -> str | None:
	"""Check the latest version of agentyc from PyPI asynchronously."""
	try:
		async with httpx.AsyncClient(timeout=3.0) as client:
			response = await client.get('https://pypi.org/pypi/agentyc/json')
			if response.status_code == 200:
				data = response.json()
				return data['info']['version']
	except Exception:
		pass
	return None


@cache
def get_git_info() -> dict[str, str] | None:
	"""Get git information if installed from git repository."""
	try:
		import subprocess

		package_root = Path(__file__).parent.parent
		git_dir = package_root / '.git'
		if not git_dir.exists():
			return None

		commit_hash = (
			subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=package_root, stderr=subprocess.DEVNULL).decode().strip()
		)
		branch = (
			subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=package_root, stderr=subprocess.DEVNULL)
			.decode()
			.strip()
		)
		remote_url = (
			subprocess.check_output(['git', 'config', '--get', 'remote.origin.url'], cwd=package_root, stderr=subprocess.DEVNULL)
			.decode()
			.strip()
		)
		commit_timestamp = (
			subprocess.check_output(['git', 'show', '-s', '--format=%ci', 'HEAD'], cwd=package_root, stderr=subprocess.DEVNULL)
			.decode()
			.strip()
		)

		return {
			'commit_hash': commit_hash,
			'branch': branch,
			'remote_url': remote_url,
			'commit_timestamp': commit_timestamp,
		}
	except Exception as e:
		logger.debug(f'Error getting git info: {type(e).__name__}: {e}')
		return None
