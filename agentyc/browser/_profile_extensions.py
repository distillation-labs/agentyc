import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from agentyc.config import CONFIG
from agentyc.utils import logger

from ._profile_constants import (
	CHROME_DEFAULT_ARGS,
	CHROME_DETERMINISTIC_RENDERING_ARGS,
	CHROME_DISABLE_SECURITY_ARGS,
	CHROME_DOCKER_ARGS,
	CHROME_HEADLESS_ARGS,
)
from ._profile_types import BrowserChannel

DEFAULT_EXTENSIONS = [
	{
		'name': 'uBlock Origin Lite',
		'id': 'ddkjiahejlhfcafbddmgiahcphecmpfh',
		'url': 'https://clients2.google.com/service/update2/crx?response=redirect&prodversion=133&acceptformat=crx3&x=id%3Dddkjiahejlhfcafbddmgiahcphecmpfh%26uc',
	},
	{
		'name': "I still don't care about cookies",
		'id': 'edibdbjcniadpccecjdfdjjppcpchdlm',
		'url': 'https://clients2.google.com/service/update2/crx?response=redirect&prodversion=133&acceptformat=crx3&x=id%3Dedibdbjcniadpccecjdfdjjppcpchdlm%26uc',
	},
	{
		'name': 'Force Background Tab',
		'id': 'gidlfommnbibbmegmgajdbikelkdcmcl',
		'url': 'https://clients2.google.com/service/update2/crx?response=redirect&prodversion=133&acceptformat=crx3&x=id%3Dgidlfommnbibbmegmgajdbikelkdcmcl%26uc',
	},
]


def copy_profile_to_temp_dir(profile: Any) -> None:
	"""Copy Chrome-family profiles into an isolated temp user-data-dir."""
	if profile.user_data_dir is None:
		return

	user_data_str = str(profile.user_data_dir)
	if 'agentyc-user-data-dir-' in user_data_str.lower():
		return

	is_chrome = (
		'chrome' in user_data_str.lower()
		or ('chrome' in str(profile.executable_path).lower())
		or profile.channel
		in (BrowserChannel.CHROME, BrowserChannel.CHROME_BETA, BrowserChannel.CHROME_DEV, BrowserChannel.CHROME_CANARY)
	)
	if not is_chrome:
		return

	temp_dir = tempfile.mkdtemp(prefix='agentyc-user-data-dir-')
	path_original_user_data = Path(profile.user_data_dir)
	path_original_profile = path_original_user_data / profile.profile_directory
	path_temp_profile = Path(temp_dir) / profile.profile_directory

	if path_original_profile.exists():
		shutil.copytree(path_original_profile, path_temp_profile)
		local_state_src = path_original_user_data / 'Local State'
		local_state_dst = Path(temp_dir) / 'Local State'
		if local_state_src.exists():
			shutil.copy(local_state_src, local_state_dst)
		logger.info(f'Copied profile ({profile.profile_directory}) and Local State to temp directory: {temp_dir}')
	else:
		Path(temp_dir).mkdir(parents=True, exist_ok=True)
		path_temp_profile.mkdir(parents=True, exist_ok=True)
		logger.info(f'Created new profile ({profile.profile_directory}) in temp directory: {temp_dir}')

	profile.user_data_dir = temp_dir


def build_profile_args(profile: Any) -> list[str]:
	"""Compile Chrome CLI launch args from defaults and profile configuration."""
	if isinstance(profile.ignore_default_args, list):
		default_args: list[str] | set[str] = set(CHROME_DEFAULT_ARGS) - set(profile.ignore_default_args)
	elif profile.ignore_default_args is True:
		default_args = []
	else:
		default_args = CHROME_DEFAULT_ARGS

	assert profile.user_data_dir is not None, 'user_data_dir must be set to a non-default path'

	pre_conversion_args = [
		*default_args,
		*profile.args,
		f'--user-data-dir={profile.user_data_dir}',
		f'--profile-directory={profile.profile_directory}',
		*(CHROME_DOCKER_ARGS if (CONFIG.IN_DOCKER or not profile.chromium_sandbox) else []),
		*(CHROME_HEADLESS_ARGS if profile.headless else []),
		*(CHROME_DISABLE_SECURITY_ARGS if profile.disable_security else []),
		*(CHROME_DETERMINISTIC_RENDERING_ARGS if profile.deterministic_rendering else []),
		*(
			[f'--window-size={profile.window_size["width"]},{profile.window_size["height"]}']
			if profile.window_size
			else (['--start-maximized'] if not profile.headless else [])
		),
		*(
			[f'--window-position={profile.window_position["width"]},{profile.window_position["height"]}']
			if profile.window_position
			else []
		),
		*(get_extension_args(profile) if profile.enable_default_extensions else []),
	]

	proxy_server = profile.proxy.server if profile.proxy else None
	proxy_bypass = profile.proxy.bypass if profile.proxy else None
	if proxy_server:
		pre_conversion_args.append(f'--proxy-server={proxy_server}')
		if proxy_bypass:
			pre_conversion_args.append(f'--proxy-bypass-list={proxy_bypass}')

	if profile.user_agent:
		pre_conversion_args.append(f'--user-agent={profile.user_agent}')

	disable_features_values: list[str] = []
	non_disable_features_args: list[str] = []
	for arg in pre_conversion_args:
		if arg.startswith('--disable-features='):
			disable_features_values.extend(arg.split('=', 1)[1].split(','))
		else:
			non_disable_features_args.append(arg)

	if disable_features_values:
		unique_features: list[str] = []
		seen: set[str] = set()
		for feature in disable_features_values:
			feature = feature.strip()
			if feature and feature not in seen:
				unique_features.append(feature)
				seen.add(feature)
		non_disable_features_args.append(f'--disable-features={",".join(unique_features)}')

	return profile.args_as_list(profile.args_as_dict(non_disable_features_args))


def get_extension_args(profile: Any) -> list[str]:
	"""Get Chrome args for enabling default extensions."""
	extension_paths = ensure_default_extensions_downloaded(profile)
	args = [
		'--enable-extensions',
		'--disable-extensions-file-access-check',
		'--disable-extensions-http-throttling',
		'--enable-extension-activity-logging',
	]
	if extension_paths:
		args.append(f'--load-extension={",".join(extension_paths)}')
	return args


def check_extension_manifest_version(ext_dir: Path, ext_name: str) -> bool:
	"""Check that an extension uses Manifest V3."""
	manifest_path = ext_dir / 'manifest.json'
	if not manifest_path.exists():
		return False
	try:
		with open(manifest_path, encoding='utf-8') as f:
			manifest = json.load(f)
		manifest_version = manifest.get('manifest_version', 2)
		if manifest_version < 3:
			logger.warning(f'Skipping {ext_name} extension: Manifest V{manifest_version} is no longer supported by Chrome')
			return False
		return True
	except Exception:
		return False


def ensure_default_extensions_downloaded(profile: Any) -> list[str]:
	"""Ensure default extensions are downloaded and cached locally."""
	cache_dir = CONFIG.AGENTYC_EXTENSIONS_DIR
	cache_dir.mkdir(parents=True, exist_ok=True)

	extension_paths: list[str] = []
	loaded_extension_names: list[str] = []

	for ext in DEFAULT_EXTENSIONS:
		ext_dir = cache_dir / ext['id']
		crx_file = cache_dir / f'{ext["id"]}.crx'

		if ext_dir.exists() and (ext_dir / 'manifest.json').exists():
			if not check_extension_manifest_version(ext_dir, ext['name']):
				continue
			extension_paths.append(str(ext_dir))
			loaded_extension_names.append(ext['name'])
			continue

		try:
			if not crx_file.exists():
				logger.info(f'📦 Downloading {ext["name"]} extension...')
				download_extension(ext['url'], crx_file)
			else:
				logger.debug(f'📦 Found cached {ext["name"]} .crx file')

			logger.info(f'📂 Extracting {ext["name"]} extension...')
			extract_extension(crx_file, ext_dir)

			if not check_extension_manifest_version(ext_dir, ext['name']):
				continue

			extension_paths.append(str(ext_dir))
			loaded_extension_names.append(ext['name'])
		except Exception as e:
			logger.warning(f'⚠️ Failed to setup {ext["name"]} extension: {e}')
			continue

	for i, path in enumerate(extension_paths):
		if loaded_extension_names[i] == "I still don't care about cookies":
			apply_minimal_extension_patch(Path(path), profile.cookie_whitelist_domains)

	if extension_paths:
		logger.debug(f'[BrowserProfile] 🧩 Extensions loaded ({len(extension_paths)}): [{", ".join(loaded_extension_names)}]')
	else:
		logger.warning('[BrowserProfile] ⚠️ No default extensions could be loaded')

	return extension_paths


def apply_minimal_extension_patch(ext_dir: Path, whitelist_domains: list[str]) -> None:
	"""Pre-populate chrome.storage.local with configurable domain whitelist."""
	try:
		bg_path = ext_dir / 'data' / 'background.js'
		if not bg_path.exists():
			return

		with open(bg_path, encoding='utf-8') as f:
			content = f.read()

		whitelist_entries = [f'        "{domain}": true' for domain in whitelist_domains]
		whitelist_js = '{\n' + ',\n'.join(whitelist_entries) + '\n      }'

		old_init = """async function initialize(checkInitialized, magic) {
  if (checkInitialized && initialized) {
    return;
  }
  loadCachedRules();
  await updateSettings();
  await recreateTabList(magic);
  initialized = true;
}"""

		new_init = f"""// Pre-populate storage with configurable domain whitelist if empty
async function ensureWhitelistStorage() {{
  const result = await chrome.storage.local.get({{ settings: null }});
  if (!result.settings) {{
    const defaultSettings = {{
      statusIndicators: true,
      whitelistedDomains: {whitelist_js}
    }};
    await chrome.storage.local.set({{ settings: defaultSettings }});
  }}
}}

async function initialize(checkInitialized, magic) {{
  if (checkInitialized && initialized) {{
    return;
  }}
  loadCachedRules();
  await ensureWhitelistStorage(); // Add storage initialization
  await updateSettings();
  await recreateTabList(magic);
  initialized = true;
}}"""

		if old_init in content:
			content = content.replace(old_init, new_init)
			with open(bg_path, 'w', encoding='utf-8') as f:
				f.write(content)
			logger.info(f'[BrowserProfile] ✅ Cookie extension: {", ".join(whitelist_domains)} pre-populated in storage')
		else:
			logger.debug('[BrowserProfile] Initialize function not found for patching')
	except Exception as e:
		logger.debug(f'[BrowserProfile] Could not patch extension storage: {e}')


def download_extension(url: str, output_path: Path) -> None:
	"""Download extension .crx file."""
	try:
		with urllib.request.urlopen(url) as response:
			with open(output_path, 'wb') as f:
				f.write(response.read())
	except Exception as e:
		raise Exception(f'Failed to download extension: {e}')


def extract_extension(crx_path: Path, extract_dir: Path) -> None:
	"""Extract .crx file to directory."""
	if extract_dir.exists():
		shutil.rmtree(extract_dir)

	extract_dir.mkdir(parents=True, exist_ok=True)

	try:
		with zipfile.ZipFile(crx_path, 'r') as zip_ref:
			zip_ref.extractall(extract_dir)
		if not (extract_dir / 'manifest.json').exists():
			raise Exception('No manifest.json found in extension')
	except zipfile.BadZipFile:
		with open(crx_path, 'rb') as f:
			magic = f.read(4)
			if magic != b'Cr24':
				raise Exception('Invalid CRX file format')

			version = int.from_bytes(f.read(4), 'little')
			if version == 2:
				pubkey_len = int.from_bytes(f.read(4), 'little')
				sig_len = int.from_bytes(f.read(4), 'little')
				f.seek(16 + pubkey_len + sig_len)
			elif version == 3:
				header_len = int.from_bytes(f.read(4), 'little')
				f.seek(12 + header_len)

			zip_data = f.read()

		with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_zip:
			temp_zip.write(zip_data)
			temp_zip.flush()
			with zipfile.ZipFile(temp_zip.name, 'r') as zip_ref:
				zip_ref.extractall(extract_dir)
			os.unlink(temp_zip.name)
