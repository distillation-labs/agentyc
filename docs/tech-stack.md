# Tech Stack

## Runtime Requirements

| Requirement | Value |
|-------------|-------|
| Rust | `>= 1.80` (workspace uses edition 2024) |
| Build tool | `cargo` |
| Browser | Local Chrome or Chromium, or an existing browser exposed over CDP |
| Protocol | Chrome DevTools Protocol (CDP) over WebSocket |

The runtime is a single native binary. There is no interpreter, virtualenv, or
package install step — and no Playwright in the automation path.

## Core Dependencies

### Async Runtime And MCP

| Crate | Role |
|-------|------|
| `tokio` | Async runtime (full features) |
| `rmcp` | MCP server SDK (stdio + Streamable HTTP transports, macros) |
| `axum` | HTTP server for `agentyc serve` |
| `schemars` | JSON Schema generation for tool input parameters |

### Browser And Protocol

| Crate | Role |
|-------|------|
| `chromiumoxide_cdp` | CDP command/event type definitions |
| `tokio-tungstenite` | WebSocket transport to the CDP endpoint (rustls) |
| `reqwest` | HTTP client for CDP attach and version probing (rustls, no OpenSSL) |

### Serialization And Utilities

| Crate | Role |
|-------|------|
| `serde` / `serde_json` | (De)serialization of MCP and CDP payloads |
| `clap` | CLI argument parsing |
| `anyhow` / `thiserror` | Error handling |
| `uuid` | Identifier generation |
| `regex` / `url` | Text and URL handling |
| `base64` | Screenshot / binary payload encoding |
| `tempfile` / `dirs` | Temp profiles and platform paths |
| `futures` / `tokio-stream` / `async-trait` | Async plumbing |
| `glob` / `md5` | File matching and hashing |

### Content And DOM

| Crate | Role |
|-------|------|
| `scraper` | HTML parsing for deterministic extraction |
| `htmd` | HTML→Markdown conversion |
| `image` | Screenshot encoding (jpeg, png, webp) |

### Observability And Performance

| Crate | Role |
|-------|------|
| `tracing` / `tracing-subscriber` | Structured logging to stderr (`AGENTYC_LOGGING_LEVEL`) |
| `sysinfo` | Process / system inspection |
| `mimalloc` | Global allocator for low idle memory and fast startup |

## Language And Style

| Attribute | Choice |
|-----------|--------|
| Edition | Rust 2024 |
| Async model | `async` / `.await` throughout on Tokio |
| Error model | `anyhow::Result` at boundaries, structured tool-error codes to clients |
| Transport | stdio MCP (default) or Streamable HTTP; CDP WebSocket to Chrome/Chromium |
| Formatting | `cargo fmt` (rustfmt) |
| Linting | `cargo clippy` with `-D warnings` |

## Build Profile

The release profile (root `Cargo.toml`) is tuned for a small, fast binary:

```toml
[profile.release]
opt-level = 3
lto = "thin"
codegen-units = 1
strip = true
panic = "abort"
```

## Testing And Quality

| Tool | Role |
|------|------|
| `cargo test` | Unit and integration tests |
| `agentyc-tests` | Integration harness that spawns the binary over stdio (`tests/*.rs`) |
| `cargo fmt --check` | Formatting gate |
| `cargo clippy -D warnings` | Lint gate |
| `codespell` | Spelling checks (pre-commit) |
| `pre-commit` | Local hook runner (wraps the cargo gates) |

Integration tests that need the public internet or a visible display are marked
`#[ignore]` and run with `cargo test -- --ignored`. Stress loops scale with the
`AGENTYC_TEST_SCALE` environment variable.

## Infrastructure

| Tool | Role |
|------|------|
| `cargo` | Dependency resolution and builds |
| GitHub Actions | CI (build, test with Chrome, fmt, clippy) |
| GitHub Releases | Distribution of prebuilt per-platform binaries |
