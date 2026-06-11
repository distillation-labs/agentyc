//! BrowserProfile: Chrome launch configuration.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};
use tempfile::TempDir;

/// Proxy settings for Chrome.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ProxySettings {
    pub server: Option<String>,
    pub bypass: Option<String>,
    pub username: Option<String>,
    pub password: Option<String>,
}

/// Viewport size.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ViewportSize {
    pub width: u32,
    pub height: u32,
}

impl Default for ViewportSize {
    fn default() -> Self {
        Self { width: 1280, height: 720 }
    }
}

/// Owned per-session user-data directory.
///
/// When `Some`, the `TempDir` will be deleted on drop, giving per-agent isolation.
pub struct OwnedUserDataDir {
    pub path: std::path::PathBuf,
    _dir: Option<TempDir>,
}

impl OwnedUserDataDir {
    /// Use a provided path without cleanup.
    pub fn from_path(p: impl Into<std::path::PathBuf>) -> Self {
        Self { path: p.into(), _dir: None }
    }
    /// Create a fresh temp dir owned by this value.
    pub fn temp() -> anyhow::Result<Self> {
        let dir = tempfile::Builder::new().prefix("agentyc-tmp-").tempdir()?;
        let path = dir.path().to_path_buf();
        Ok(Self { path, _dir: Some(dir) })
    }
}

impl std::fmt::Debug for OwnedUserDataDir {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "OwnedUserDataDir({:?})", self.path)
    }
}

/// Static template for a browser launch/connect.
#[derive(Debug, Clone)]
pub struct BrowserProfile {
    /// Run in headless mode. `None` = follow env / default (`false`).
    pub headless: bool,
    /// Explicit Chrome binary path. `None` = auto-discover.
    pub executable_path: Option<String>,
    /// Proxy settings.
    pub proxy: Option<ProxySettings>,
    /// Navigation allow-list.
    pub allowed_domains: Option<Vec<String>>,
    /// Viewport hint (used when creating new pages).
    pub viewport: ViewportSize,
    /// Extra CLI args.
    pub extra_args: Vec<String>,
    /// Downloads directory.
    pub downloads_path: Option<String>,
    /// Extra environment variables.
    pub env: Option<HashMap<String, String>>,
    /// Disable sandbox (useful in Docker).
    pub no_sandbox: bool,
    /// Disable security features.
    pub disable_security: bool,
    /// User agent override.
    pub user_agent: Option<String>,
    /// Window size when not headless.
    pub window_size: Option<(u32, u32)>,
}

impl Default for BrowserProfile {
    fn default() -> Self {
        let headless = std::env::var("AGENTYC_HEADLESS")
            .ok()
            .map(|v| !matches!(v.to_lowercase().as_str(), "0" | "false" | "no" | "off"))
            .unwrap_or(false);
        let allowed_domains = std::env::var("AGENTYC_ALLOWED_DOMAINS").ok().map(|v| {
            v.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect()
        });
        let proxy = std::env::var("AGENTYC_PROXY_URL").ok().map(|server| ProxySettings {
            server: Some(server),
            bypass: std::env::var("AGENTYC_PROXY_BYPASS").ok(),
            username: std::env::var("AGENTYC_PROXY_USERNAME").ok(),
            password: std::env::var("AGENTYC_PROXY_PASSWORD").ok(),
        });
        Self {
            headless,
            executable_path: None,
            proxy,
            allowed_domains,
            viewport: ViewportSize::default(),
            extra_args: vec![],
            downloads_path: None,
            env: None,
            no_sandbox: false,
            disable_security: false,
            user_agent: None,
            window_size: None,
        }
    }
}

// Chrome disabled features — mirror of `_profile_constants.py`
const CHROME_DISABLED_FEATURES: &str = "AcceptCHFrame,AutoExpandDetailsElement,\
AvoidUnnecessaryBeforeUnloadCheckSync,CertificateTransparencyComponentUpdater,\
DestroyProfileOnBrowserClose,DialMediaRouteProvider,ExtensionManifestV2Disabled,\
GlobalMediaControls,HttpsUpgrades,ImprovedCookieControls,LazyFrameLoading,LensOverlay,\
MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,Translate,AutomationControlled,\
BackForwardCache,OptimizationHints,ProcessPerSiteUpToMainFrameThreshold,\
InterestFeedContentSuggestions,CalculateNativeWinOcclusion,HeavyAdPrivacyMitigations,\
PrivacySandboxSettings4,AutofillServerCommunication,CrashReporting,\
OverscrollHistoryNavigation,InfiniteSessionRestore,ExtensionDisableUnsupportedDeveloper,\
ExtensionManifestV2Unsupported";

const CHROME_DEFAULT_ARGS: &[&str] = &[
    "--disable-field-trial-config",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-back-forward-cache",
    "--disable-breakpad",
    "--disable-client-side-phishing-detection",
    "--disable-component-update",
    "--no-default-browser-check",
    "--disable-dev-shm-usage",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding",
    "--metrics-recording-only",
    "--no-first-run",
    "--no-service-autorun",
    "--export-tagged-pdf",
    "--disable-search-engine-choice-screen",
    "--unsafely-disable-devtools-self-xss-warnings",
    "--enable-features=NetworkService,NetworkServiceInProcess",
    "--enable-network-information-downlink-max",
    "--disable-sync",
    "--allow-pre-commit-input",
    "--disable-blink-features=AutomationControlled",
    "--install-autogenerated-theme=0,0,0",
    "--log-level=2",
    "--disable-focus-on-load",
    "--disable-window-activation",
    "--generate-pdf-document-outline",
    "--no-pings",
    "--disable-infobars",
    "--suppress-message-center-popups",
    "--disable-domain-reliability",
    "--disable-speech-synthesis-api",
    "--disable-speech-api",
    "--disable-print-preview",
    "--safebrowsing-disable-auto-update",
    "--disable-external-intent-requests",
    "--disable-desktop-notifications",
    "--noerrdialogs",
    "--silent-debugger-extension-api",
    "--disable-extensions-http-throttling",
    "--extensions-on-chrome-urls",
    "--disable-default-apps",
];

const CHROME_DISABLE_SECURITY_ARGS: &[&str] = &[
    "--disable-site-isolation-trials",
    "--disable-web-security",
    "--disable-features=IsolateOrigins,site-per-process",
    "--allow-running-insecure-content",
    "--ignore-certificate-errors",
];

impl BrowserProfile {
    /// Compile all Chrome CLI args (excluding --remote-debugging-port and --user-data-dir,
    /// which are added by the launcher at runtime).
    pub fn build_args(&self) -> Vec<String> {
        let mut args: Vec<String> = CHROME_DEFAULT_ARGS.iter().map(|s| s.to_string()).collect();

        args.push(format!("--disable-features={CHROME_DISABLED_FEATURES}"));

        if self.headless {
            args.push("--headless=new".to_string());
        } else if let Some((w, h)) = self.window_size {
            args.push(format!("--window-size={w},{h}"));
        } else {
            args.push("--start-maximized".to_string());
        }

        if self.no_sandbox {
            args.extend([
                "--no-sandbox".to_string(),
                "--disable-gpu-sandbox".to_string(),
                "--disable-setuid-sandbox".to_string(),
                "--disable-dev-shm-usage".to_string(),
                "--no-zygote".to_string(),
            ]);
        }

        if self.disable_security {
            for a in CHROME_DISABLE_SECURITY_ARGS {
                args.push(a.to_string());
            }
        }

        if let Some(ref ua) = self.user_agent {
            args.push(format!("--user-agent={ua}"));
        }

        if let Some(ref proxy) = self.proxy
            && let Some(ref server) = proxy.server
        {
            args.push(format!("--proxy-server={server}"));
            if let Some(ref bypass) = proxy.bypass {
                args.push(format!("--proxy-bypass-list={bypass}"));
            }
        }

        args.extend(self.extra_args.clone());
        args
    }
}
