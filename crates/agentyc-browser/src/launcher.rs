//! Chrome binary discovery and subprocess launcher.

use std::{
    path::{Path, PathBuf},
    time::Duration,
};

use anyhow::{Context, Result, anyhow};
use serde_json::Value;
use tokio::{process::Child, time::sleep};
use tracing::{debug, warn};

use crate::profile::{BrowserProfile, OwnedUserDataDir};

// ── Binary discovery ──────────────────────────────────────────────────────────

/// Find a usable Chrome/Chromium executable.
///
/// Search order (macOS):
/// 1. System Chrome stable
/// 2. Playwright Chromium cache (newest version wins)
/// 3. System Chromium
/// 4. Chrome Canary / Brave / Edge
///
/// Search order (Linux):
/// 1. System google-chrome-stable
/// 2. Playwright Chromium cache
/// 3. Chromium from various distro paths
pub fn find_chrome_binary() -> Option<PathBuf> {
    let os = std::env::consts::OS;

    // Check PLAYWRIGHT_BROWSERS_PATH override first
    let playwright_base: Option<PathBuf> = std::env::var("PLAYWRIGHT_BROWSERS_PATH")
        .ok()
        .map(PathBuf::from);

    match os {
        "macos" => find_chrome_macos(playwright_base),
        "linux" => find_chrome_linux(playwright_base),
        _ => None,
    }
}

fn playwright_chromium_paths(base: Option<PathBuf>, suffix: &str) -> Vec<PathBuf> {
    let default_base = if cfg!(target_os = "macos") {
        dirs::cache_dir().map(|d| d.join("ms-playwright"))
    } else {
        dirs::home_dir().map(|d| d.join(".cache/ms-playwright"))
    };
    let base = base.or(default_base);
    let Some(base) = base else { return vec![] };

    // Glob chromium-* directories and sort; last = highest version.
    let pattern = base.join(format!("chromium-*/{suffix}"));
    let Ok(entries) = glob::glob(pattern.to_str().unwrap_or("")) else {
        return vec![];
    };
    let mut paths: Vec<PathBuf> = entries.flatten().collect();
    paths.sort();
    paths
}

fn find_chrome_macos(playwright_base: Option<PathBuf>) -> Option<PathBuf> {
    // 1. System Chrome
    let system_chrome =
        PathBuf::from("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
    if system_chrome.is_file() {
        return Some(system_chrome);
    }

    // 2. Playwright Chromium (newest)
    let playwright_suffix = "chrome-mac/Chromium.app/Contents/MacOS/Chromium";
    if let Some(p) = playwright_chromium_paths(playwright_base.clone(), playwright_suffix)
        .into_iter()
        .last()
        && p.is_file()
    {
        return Some(p);
    }

    // 3. Standalone Chromium
    let chromium = PathBuf::from("/Applications/Chromium.app/Contents/MacOS/Chromium");
    if chromium.is_file() {
        return Some(chromium);
    }

    // 4. Canary / Brave / Edge
    for alt in &[
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ] {
        let p = PathBuf::from(alt);
        if p.is_file() {
            return Some(p);
        }
    }

    // 5. Playwright headless-shell fallback
    let shell_suffix = "chrome-mac/Chromium.app/Contents/MacOS/Chromium";
    if let Some(p) = playwright_chromium_paths(playwright_base, shell_suffix)
        .into_iter()
        .last()
        && p.is_file()
    {
        return Some(p);
    }

    None
}

fn find_chrome_linux(playwright_base: Option<PathBuf>) -> Option<PathBuf> {
    // 1. System Chrome stable
    for p in &[
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/usr/local/bin/google-chrome",
    ] {
        if Path::new(p).is_file() {
            return Some(PathBuf::from(p));
        }
    }

    // 2. Playwright Chromium
    let playwright_suffix = "chrome-linux/chrome";
    if let Some(p) = playwright_chromium_paths(playwright_base.clone(), playwright_suffix)
        .into_iter()
        .last()
        && p.is_file()
    {
        return Some(p);
    }

    // 3. Distro chromium
    for p in &[
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/local/bin/chromium",
        "/snap/bin/chromium",
    ] {
        if Path::new(p).is_file() {
            return Some(PathBuf::from(p));
        }
    }

    // 4. Brave / Edge
    for p in &[
        "/usr/bin/brave-browser",
        "/usr/bin/microsoft-edge-stable",
        "/usr/bin/microsoft-edge",
    ] {
        if Path::new(p).is_file() {
            return Some(PathBuf::from(p));
        }
    }

    None
}

// ── Port helper ───────────────────────────────────────────────────────────────

/// Find an available TCP port.
fn find_free_port() -> Result<u16> {
    let listener =
        std::net::TcpListener::bind("127.0.0.1:0").context("Failed to bind to find a free port")?;
    Ok(listener.local_addr()?.port())
}

// ── Launcher ─────────────────────────────────────────────────────────────────

/// A running Chrome instance managed by agentyc.
pub struct LaunchedBrowser {
    pub cdp_url: String,
    pub ws_url: String,
    process: Child,
    /// Keeps the temp dir alive until this struct is dropped.
    pub user_data_dir: OwnedUserDataDir,
}

impl LaunchedBrowser {
    /// Kill the subprocess and clean up.
    pub async fn kill(mut self) {
        if let Err(e) = self.process.kill().await {
            warn!("Failed to kill Chrome subprocess: {e}");
        }
        // user_data_dir drops here → TempDir cleaned up automatically
    }
}

impl Drop for LaunchedBrowser {
    fn drop(&mut self) {
        // Tokio's Child does not terminate the process when dropped. The
        // synchronous kill keeps failed launch/replacement paths from leaking
        // Chrome; explicit async `kill` remains the preferred shutdown path.
        if let Err(error) = self.process.start_kill() {
            debug!("Failed to start Chrome cleanup on drop: {error}");
        }
    }
}

impl std::fmt::Debug for LaunchedBrowser {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "LaunchedBrowser(cdp_url={})", self.cdp_url)
    }
}

/// Launch Chrome with a CDP debugging port.
///
/// If `profile.executable_path` is `None`, the binary is discovered automatically.
/// A fresh `TempDir` is always used as `--user-data-dir` for isolation.
pub async fn launch_browser(profile: &BrowserProfile) -> Result<LaunchedBrowser> {
    let binary = if let Some(ref p) = profile.executable_path {
        PathBuf::from(p)
    } else {
        find_chrome_binary().ok_or_else(|| {
            anyhow!(
                "No Chrome/Chromium binary found. Install Google Chrome or Chromium and try again, \
                 or set PLAYWRIGHT_BROWSERS_PATH to an existing Chromium cache."
            )
        })?
    };

    let port = find_free_port()?;
    let user_data_dir = OwnedUserDataDir::temp()?;

    let mut args = profile.build_args();
    args.push(format!("--remote-debugging-port={port}"));
    args.push(format!("--user-data-dir={}", user_data_dir.path.display()));
    args.push("--profile-directory=Default".to_string());

    debug!(binary = %binary.display(), port, "Launching Chrome");

    let mut cmd = tokio::process::Command::new(&binary);
    cmd.args(&args);

    // Redirect stdout/stderr to avoid polluting agent stdio.
    cmd.stdout(std::process::Stdio::null());
    cmd.stderr(std::process::Stdio::null());

    let process = cmd
        .spawn()
        .with_context(|| format!("Failed to spawn Chrome from {}", binary.display()))?;

    let cdp_url = format!("http://127.0.0.1:{port}/");
    let ws_url = match wait_for_cdp(&cdp_url, Duration::from_secs(55)).await {
        Ok(ws_url) => ws_url,
        Err(error) => {
            // `Child` does not kill the process when dropped. Clean up a
            // process that started but never exposed a usable CDP endpoint.
            process.kill().await.ok();
            return Err(error);
        }
    };

    debug!(%ws_url, "Chrome CDP ready");

    Ok(LaunchedBrowser {
        cdp_url,
        ws_url,
        process,
        user_data_dir,
    })
}

/// Poll `http://host/json/version` until Chrome reports ready.
/// Returns the `webSocketDebuggerUrl`.
pub async fn wait_for_cdp(base_url: &str, timeout: Duration) -> Result<String> {
    let url = format!(
        "{}json/version",
        base_url.trim_end_matches('/').to_string() + "/"
    );
    let client = reqwest::Client::new();
    let deadline = std::time::Instant::now() + timeout;

    loop {
        match client.get(&url).send().await {
            Ok(resp) if resp.status().is_success() => {
                let body: Value = resp.json().await.context("Failed to parse /json/version")?;
                if let Some(ws) = body.get("webSocketDebuggerUrl").and_then(Value::as_str) {
                    return Ok(ws.to_string());
                }
            }
            _ => {}
        }

        if std::time::Instant::now() >= deadline {
            return Err(anyhow!(
                "Chrome did not become ready within {}s",
                timeout.as_secs()
            ));
        }
        sleep(Duration::from_millis(100)).await;
    }
}
