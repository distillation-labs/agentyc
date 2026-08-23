//! Frontend-neutral browser operations built on the canonical `BrowserSession`.
//!
//! MCP, the command-line interface, and the REPL all use this facade for their
//! shared lifecycle and common agent-facing operations. It intentionally
//! returns typed/JSON values rather than frontend-specific result objects.

use std::sync::Arc;
use std::time::Duration;

use anyhow::{Result, anyhow};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tokio::time::sleep;

use agentyc_browser::{BrowserProfile, BrowserSession, PageSession, TabInfo};

/// Configuration for a frontend-neutral browser runtime.
#[derive(Debug, Clone, Default)]
pub struct RuntimeConfig {
    pub cdp_url: Option<String>,
    pub profile: BrowserProfile,
}

/// Result of a navigation operation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NavigationResult {
    pub url: String,
    pub title: String,
    pub tab_id: String,
}

/// Small, stable page snapshot intended for CLI/REPL and coding-agent use.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PageInfo {
    pub url: String,
    pub title: String,
    pub tab_id: Option<String>,
    pub tabs: Vec<TabInfo>,
}

/// Shared browser runtime used by all public frontends.
#[derive(Clone)]
pub struct BrowserRuntime {
    session: Arc<BrowserSession>,
    allowed_domains: Option<Vec<String>>,
}

impl std::fmt::Debug for BrowserRuntime {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BrowserRuntime")
            .field("session", &self.session)
            .field("allowed_domains", &self.allowed_domains)
            .finish()
    }
}

impl BrowserRuntime {
    /// Launch a local browser using the supplied profile.
    pub async fn launch(profile: BrowserProfile) -> Result<Self> {
        let allowed_domains = profile.allowed_domains.clone();
        let session = BrowserSession::launch(&profile).await?;
        Ok(Self {
            session: Arc::new(session),
            allowed_domains,
        })
    }

    /// Connect to an existing browser over CDP.
    pub async fn connect(cdp_url: &str) -> Result<Self> {
        let session = BrowserSession::connect(cdp_url).await?;
        Ok(Self {
            session: Arc::new(session),
            allowed_domains: BrowserProfile::default().allowed_domains,
        })
    }

    /// Construct from the standard CLI/environment configuration.
    pub async fn open(config: RuntimeConfig) -> Result<Self> {
        if let Some(cdp_url) = config.cdp_url {
            let mut runtime = Self::connect(&cdp_url).await?;
            runtime.allowed_domains = config.profile.allowed_domains;
            Ok(runtime)
        } else {
            Self::launch(config.profile).await
        }
    }

    /// Access the canonical lifecycle owner for advanced operations.
    pub fn session(&self) -> Arc<BrowserSession> {
        Arc::clone(&self.session)
    }

    /// Navigate the active tab or create a new tab first.
    pub async fn navigate(&self, url: &str, new_tab: bool) -> Result<NavigationResult> {
        self.check_allowed_url(url)?;
        let page = if new_tab {
            self.session.new_tab(Some(url)).await?
        } else {
            let page = self.session.ensure_active_page().await?;
            self.session
                .send_page::<Value>("Page.navigate", json!({"url": url}))
                .await?;
            page
        };

        // Let the document title update without making navigation depend on a
        // fixed sleep for correctness. The short delay is only a best-effort
        // presentation improvement for one-shot CLI output.
        sleep(Duration::from_millis(50)).await;
        let info: Value = self
            .session
            .send_page(
                "Runtime.evaluate",
                json!({"expression": "({url:location.href,title:document.title})", "returnByValue": true}),
            )
            .await
            .unwrap_or(Value::Null);
        let current_url = info["result"]["value"]["url"]
            .as_str()
            .unwrap_or(url)
            .to_string();
        let title = info["result"]["value"]["title"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        Ok(NavigationResult {
            url: current_url,
            title,
            tab_id: page.tab_id,
        })
    }

    /// Evaluate JavaScript in the active page and return its JSON value.
    pub async fn evaluate(&self, code: &str) -> Result<Value> {
        self.session.ensure_active_page().await?;
        let response: Value = self
            .session
            .send_page(
                "Runtime.evaluate",
                json!({"expression": code, "returnByValue": true, "awaitPromise": true}),
            )
            .await?;
        if let Some(details) = response.get("exceptionDetails") {
            return Err(anyhow!("JavaScript exception: {details}"));
        }
        Ok(response["result"]["value"].clone())
    }

    /// Read current URL/title and all open page tabs.
    pub async fn page_info(&self) -> Result<PageInfo> {
        let page = self.session.ensure_active_page().await?;
        let info: Value = self
            .session
            .send_page(
                "Runtime.evaluate",
                json!({"expression": "({url:location.href,title:document.title})", "returnByValue": true}),
            )
            .await?;
        Ok(PageInfo {
            url: info["result"]["value"]["url"]
                .as_str()
                .unwrap_or_default()
                .to_string(),
            title: info["result"]["value"]["title"]
                .as_str()
                .unwrap_or_default()
                .to_string(),
            tab_id: Some(page.tab_id),
            tabs: self.session.list_tabs().await?,
        })
    }

    pub async fn list_tabs(&self) -> Result<Vec<TabInfo>> {
        self.session.list_tabs().await
    }

    pub async fn new_tab(&self, url: Option<&str>) -> Result<PageSession> {
        if let Some(url) = url {
            self.check_allowed_url(url)?;
        }
        self.session.new_tab(url).await
    }

    pub async fn switch_tab(&self, tab_id: &str) -> Result<PageSession> {
        self.session.switch_tab(tab_id).await
    }

    pub async fn close_tab(&self, tab_id: &str) -> Result<()> {
        self.session.close_tab(tab_id).await
    }

    pub async fn close(&self) -> Result<()> {
        self.session.close().await
    }

    pub async fn close_all(&self) -> Result<()> {
        self.session.close_all().await
    }

    fn check_allowed_url(&self, url: &str) -> Result<()> {
        let Some(domains) = self.allowed_domains.as_ref().filter(|d| !d.is_empty()) else {
            return Ok(());
        };
        let host = url::Url::parse(url)
            .ok()
            .and_then(|parsed| parsed.host_str().map(str::to_string))
            .unwrap_or_default();
        if domains.iter().any(|domain| {
            let domain = domain.trim().trim_start_matches("*.");
            host == domain || host.ends_with(&format!(".{domain}"))
        }) {
            return Ok(());
        }
        Err(anyhow!(
            "Navigation to {url:?} blocked: host {host:?} is not in AGENTYC_ALLOWED_DOMAINS ({})",
            domains.join(", ")
        ))
    }
}
