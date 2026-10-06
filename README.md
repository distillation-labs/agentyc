# agentyc

<p align="center">
  <em>Deterministic, host-backed browser task spaces for coding agents.</em><br>
  No API key needed. No LLM fallback. Direct CLI/SDK is the primary interface; MCP is compatibility-only.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/rust-≥1.80-orange?style=flat&logo=rust&logoColor=white" alt="Rust ≥1.80">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT">
  <img src="https://img.shields.io/badge/CLI%2FSDK-primary-success?style=flat" alt="CLI/SDK Primary">
  <img src="https://img.shields.io/badge/MCP-compatibility-informational?style=flat&logo=modelcontextprotocol&logoColor=white" alt="MCP Compatibility">
</p>

---

## What It Is

`agentyc` is a native binary providing direct host-backed CLI and Node SDK access to logical browser task spaces and managed pages. The direct CLI/SDK is the primary interface; MCP is compatibility-only.

Key architectural boundaries:

- **Logical task spaces and pages:** The primary object model uses logical identifiers (`space_id`, `page_id`) and durable labels. Raw browser target IDs, session IDs, and tab IDs are never public identity and are not exposed.
- **Dedicated Chrome profile:** Agentyc uses a dedicated profile launched by the user with a loopback DevTools Protocol endpoint. Spaces share that profile's cookies, sessions, and storage; Agentyc does not copy data from the user's everyday profile.
- **Host-owned browser control:** The host controls pages over loopback CDP. The extension is used only to create tabs through Native Messaging; it has no popup or side panel, and clicking its icon does nothing.
- **No implicit Chrome launch or download:** The host connects only to the user-launched profile and never launches Chrome or creates/switches profiles.
- **Deterministic fake-host seam:** For testing and CI without a live browser, `--offline` (or `AGENTYC_FAKE_HOST=1`) executes host broker operations deterministically in-process.
- **Current CLI per-invocation model:** Direct CLI commands run per invocation against the host ledger. In offline mode, each process invocation starts a fresh broker instance, which fences active leases from previous runs. A long-lived host transport is the continuity mechanism for the SDK.
- **Live validation limits:** Live browser control requires the dedicated Chrome profile's loopback CDP endpoint. Creating tabs also requires the enrolled extension and Native Messaging host; when either required connection is unavailable, Agentyc returns a typed error instead of using another browser-control path.

Cold start: **~5ms**. Binary: **~8MB**. Idle RSS: **~3MB**.

```bash
# Direct host-backed CLI commands:
agentyc host status   # inspects host status and bridge capabilities
agentyc space list    # lists logical task spaces
agentyc init          # writes agentyc-skill.md — point your agent at it
# Portable agent plugin bundle:
# plugins/agentyc-browser-automation/plugin.json
```

---

## Quick Start

**Download the prebuilt binary (fastest — no Rust toolchain needed):**

```bash
# macOS arm64
curl -L https://github.com/distillation-labs/agentyc/releases/latest/download/agentyc-aarch64-apple-darwin.tar.gz | tar xz
# macOS x86_64
curl -L https://github.com/distillation-labs/agentyc/releases/latest/download/agentyc-x86_64-apple-darwin.tar.gz | tar xz
# Linux x86_64
curl -L https://github.com/distillation-labs/agentyc/releases/latest/download/agentyc-x86_64-unknown-linux-gnu.tar.gz | tar xz
# Move the binary onto your PATH
```

**Or build from source:**

```bash
cargo install --git https://github.com/distillation-labs/agentyc agentyc
```

### Runnable tested example (using deterministic offline seam)

You can execute direct task-space commands immediately without live Chrome using `--offline`:

```bash
# Inspect host lifecycle and capabilities:
agentyc --state-dir /tmp/agentyc-state --offline --json host status

# Create a task space with explicit shared-profile disclosure:
agentyc --state-dir /tmp/agentyc-state --offline --json space create \
  --label research \
  --accept-shared-profile-disclosure

# List logical task spaces:
agentyc --state-dir /tmp/agentyc-state --offline --json space list
```

### Bootstrap your agent with the skills guide

```bash
agentyc init                      # writes agentyc-skill.md
agentyc init --output .agent.md   # custom path
agentyc init --print              # print to stdout
agentyc init --force              # overwrite existing
```

Point your agent at that file. It teaches the read→ref→act→verify loop, supported direct operations, error reconciliation, and safety. The canonical installable skill and plugin metadata live under `.agents/skills/agentyc-browser-automation/` and `plugins/agentyc-browser-automation/`; see `docs/skills-and-plugins.md`.

### Optional host-backed MCP adapter (stdio only)

The direct host-backed CLI/SDK is the primary interface. MCP exposes logical host operations over stdio only; it does not expose the removed `browser_*` tool surface or an HTTP transport. The deterministic offline server lists 29 routes. The connected remote catalog declares 30, with 19 supported by the owner-host protocol and 11 returning typed `capability_unavailable` results. `host_lease_acknowledge_fence` retries a pending takeover fence at the same epoch with lease renewal. Historical existing-profile testing exercised MCP stdio, Native Messaging, and extension fence/rebind under the previous architecture; snapshot/action reconciliation and the full Phase 8 release workflows remain incomplete, so MCP is **not distribution-ready**. See [MCP compatibility](docs/mcp-compatibility.md).

```json
{
  "mcp": {
    "agentyc": {
      "type": "local",
      "command": ["agentyc", "mcp"]
    }
  }
}
```

---

## Existing-Chrome task spaces

The host/core contracts, MV3 extension package, Native Messaging host, direct CLI/SDK, context/reliability modules, rollout gates, and host-backed MCP adapter are present in the repository. Each logical space owns its managed pages; leases and epochs authorize mutations. Live product and release gates remain tracked across execution phases. See the [task-space plan](docs/exec-plans/active/agentyc-browser-task-spaces/README.md) and [release gate](docs/release-gate.md).

---

## How It Compares

|                       | agentyc                                           | browser-use                 | Playwright MCP           |
| --------------------- | ------------------------------------------------- | --------------------------- | ------------------------ |
| **Interface**         | Direct host-backed CLI / Node SDK (primary)       | Python script + custom loop | MCP wrapper over library |
| **LLM required**      | No                                                | Yes (planner)               | No                       |
| **Extraction**        | Deterministic (structured routes)                 | LLM-based                   | Raw page access          |
| **State snapshots**   | Token-aware, compact, normalized hash             | Full DOM dump               | Full DOM or AX tree      |
| **Element targeting** | Stable logical refs (`ref_`)                      | XPath/CSS selectors         | Playwright locators      |
| **Browser backend**   | Enrolled Chrome extension                         | Playwright                  | Playwright               |
| **Runtime**           | Native binary                                     | Python + many deps          | Node + Playwright        |
| **MCP interface**     | Host-backed logical stdio; not distribution-ready | ~15–20                      | ~20                      |

---

## Configuration

### Direct CLI options

| Flag                   | Description                                                                            |
| ---------------------- | -------------------------------------------------------------------------------------- |
| `--state-dir <PATH>`   | Durable state directory (defaults to `$AGENTYC_STATE_DIR` or `~/.agentyc/state`).      |
| `--principal <ID>`     | Logical principal identifier (defaults to `principal_cli`).                            |
| `--profile-binding-id` | Enrolled profile binding suffix or complete identity.                                  |
| `--offline`            | Use the deterministic in-process fake-host seam for testing/CI without live Chrome.    |
| `--json`               | Emit compact structured JSON on stdout (default emits pretty-printed structured JSON). |

### Environment variables

| Variable                  | Description                                                            |
| ------------------------- | ---------------------------------------------------------------------- |
| `AGENTYC_STATE_DIR`       | State directory for host ledger and leases.                            |
| `AGENTYC_PRINCIPAL`       | Logical principal identity override.                                   |
| `AGENTYC_FAKE_HOST`       | Set to `1` to select the deterministic fake-host test seam.            |
| `AGENTYC_ALLOWED_DOMAINS` | Comma-separated domain allowlist for constrained workflows.            |
| `AGENTYC_LOGGING_LEVEL`   | Log level for diagnostics emitted to stderr (`warn`, `info`, `debug`). |

### CLI scope

The CLI exposes the host-backed logical commands listed in [Direct CLI](docs/cli.md), plus `agentyc mcp` and `agentyc init`. Standalone direct-CDP `browser`, `run`, and `repl` commands have been removed; CDP-based installation or test harnesses are not user-facing CLI interfaces.

The Node SDK remains in `packages/agentyc-browser`; it is distinct from the removed Rust `agentyc-browser` crate. The extension is a create-tab bridge only; browser control stays in the host.

---

## Docs

- [Overview](docs/overview.md)
- [Features](docs/features.md)
- [Architecture](docs/architecture.md)
- [API Reference](docs/api.md)
- [Configuration](docs/configuration.md)
- [MCP compatibility](docs/mcp-compatibility.md)
- [Release Gate](docs/release-gate.md)

---

## License

MIT — see [LICENSE](LICENSE).
