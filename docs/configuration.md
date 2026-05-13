# Configuration

## Configuration Sources (Priority Order)

1. **Environment variables** — highest priority
2. **Config file** (`~/.config/traverse/config.json`) — DB-style saved profiles
3. **Code defaults** in `BrowserProfile`, `LLMEntry`, `AgentEntry`

---

## BrowserProfile

`BrowserProfile` (`traverse/browser/profile.py`) is the single object that controls how a browser session launches.

```python
from traverse import BrowserProfile, BrowserSession

profile = BrowserProfile(
    headless=False,
    window_width=1280,
    window_height=800,
    user_data_dir="/path/to/profile",
    proxy=ProxySettings(server="http://proxy:8080"),
    allowed_domains=["example.com", "*.trusted.org"],
)

async with BrowserSession(profile=profile) as session:
    ...
```

### Key Parameters

#### Display
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `headless` | `bool` | `False` | Run without visible UI |
| `window_width` | `int` | auto-detected | Browser window width in px |
| `window_height` | `int` | auto-detected | Browser window height in px |
| `device_scale_factor` | `float` | `1.0` | HiDPI scale factor |

Display size is auto-detected via `detect_display_configuration()`:
- macOS: `AppKit.NSScreen`
- Linux/Windows: `screeninfo`

#### Persistence
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `user_data_dir` | `Path \| None` | temp dir | Chrome user data directory for persistent sessions |
| `keep_user_data_dir` | `bool` | `False` | Don't delete user data dir on session close |

#### Proxy
| Parameter | Type | Description |
|-----------|------|-------------|
| `proxy` | `ProxySettings \| None` | Proxy config |
| `ProxySettings.server` | `str` | Proxy URL (e.g., `http://host:port`) |
| `ProxySettings.bypass` | `str` | Comma-separated bypass list |
| `ProxySettings.username` | `str \| None` | Auth username |
| `ProxySettings.password` | `str \| None` | Auth password |

#### Security
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `allowed_domains` | `list[str] \| None` | `None` (all allowed) | Allowlist; wildcards supported (`*.example.com`) |
| `blocked_domains` | `list[str]` | `[]` | Denylist |
| `block_private_ips` | `bool` | `True` | Block navigation to private/reserved IPs (SSRF protection) |
| `disable_security` | `bool` | `False` | Disable Chrome security features (use for testing only) |

#### Extensions
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `disable_extensions` | `bool` | `False` | Disable all extensions |
| `extension_whitelist` | `list[str]` | `[]` | Per-domain extension bypass |

Built-in extensions: uBlock Origin, cookie handler.

#### Browser Process
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `chrome_binary_path` | `Path \| None` | auto-detected | Path to Chrome/Chromium binary |
| `remote_debugging_port` | `int` | `9222` | CDP debug port |
| `extra_chromium_args` | `list[str]` | `[]` | Additional Chrome flags |
| `no_sandbox` | `bool` | `False` | Disable Chrome sandbox (auto-enabled in Docker) |

#### Cloud / Remote
| Parameter | Type | Description |
|-----------|------|-------------|
| `cdp_url` | `str \| None` | Connect to existing Chrome via CDP URL (e.g., Browserless) |
| `cloud_browser` | `CloudBrowserConfig \| None` | Traverse cloud browser config |

---

## Environment Variables

### Browser Behavior
| Variable | Description | Default |
|----------|-------------|---------|
| `TRAVERSE_HEADLESS` | `true`/`false` headless mode | `false` |
| `TRAVERSE_ALLOWED_DOMAINS` | Comma-separated domain allowlist | (none) |
| `TRAVERSE_BLOCKED_DOMAINS` | Comma-separated domain denylist | (none) |
| `TRAVERSE_PROXY_URL` | Proxy server URL | (none) |
| `TRAVERSE_DISABLE_EXTENSIONS` | `true`/`false` | `false` |

### LLM
| Variable | Description |
|----------|-------------|
| `TRAVERSE_LLM_MODEL` | Default LLM model string |
| `OPENAI_API_KEY` | OpenAI API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `GOOGLE_API_KEY` | Google Gemini API key |
| `GROQ_API_KEY` | Groq API key |
| `MISTRAL_API_KEY` | Mistral API key |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI key |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `GITHUB_TOKEN` | GitHub Copilot token |

### Logging and Debugging
| Variable | Description | Values |
|----------|-------------|--------|
| `TRAVERSE_LOGGING_LEVEL` | Log verbosity | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Action Behavior
| Variable | Description | Default |
|----------|-------------|---------|
| `TRAVERSE_ACTION_TIMEOUT_S` | Per-action timeout in seconds | `180` |

---

## Config File (`~/.config/traverse/config.json`)

DB-style JSON file storing named profiles. Managed via CLI or the config API.

Structure:
```json
{
  "browser_profiles": [
    {
      "id": "<uuid7>",
      "name": "my-profile",
      "headless": false,
      "window_width": 1280,
      "user_data_dir": "/path/to/data"
    }
  ],
  "llm_configs": [
    {
      "id": "<uuid7>",
      "name": "default",
      "model": "gpt-4o",
      "api_key": "sk-...",
      "temperature": 0.0
    }
  ],
  "agent_configs": [
    {
      "id": "<uuid7>",
      "name": "default",
      "max_steps": 100,
      "use_vision": true,
      "system_prompt_override": null
    }
  ]
}
```

---

## MCP Server Configuration

When running as MCP server, these parameters are accepted via CLI or MCP initialization:

| Parameter | Description |
|-----------|-------------|
| `--headless` | Run browser headless |
| `--model` | LLM model for extraction (e.g., `gpt-4o`) |
| `--allowed-domains` | Domain allowlist |
| `--session-timeout` | Idle session cleanup timeout |
| `--mcp` | Start in MCP server mode |

CLI entry points: `traverse`, `traverse`, `bu`, `browser`

```bash
# Start as MCP server
uvx traverse[cli] --mcp

# With options
uvx traverse[cli] --mcp --headless --allowed-domains example.com,*.trusted.org
```

---

## Iframe Limits

Configurable in `BrowserSession`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_iframe_depth` | `2` | How deep to traverse nested iframes |
| `max_iframes_per_page` | `5` | Max iframes to process per page |
| `include_cross_origin_iframes` | `True` | Whether to attach sub-sessions for cross-origin frames |
