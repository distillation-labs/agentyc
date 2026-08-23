//! Canonical browser/session runtime boundary.
//!
//! `BrowserSession` owns the CDP connection, the optional browser process, and
//! the currently selected page target. Frontends should use this type instead
//! of managing target/session IDs themselves.

use std::sync::Arc;

use anyhow::{Context, Result, anyhow};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tokio::sync::Mutex;
use tracing::debug;

use agentyc_cdp::CdpClient;

use crate::launcher::{LaunchedBrowser, launch_browser};
use crate::profile::BrowserProfile;

/// The page target currently selected by a browser session.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PageSession {
    pub target_id: String,
    pub session_id: String,
    pub tab_id: String,
}

/// Public information about a page target.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TabInfo {
    pub target_id: String,
    pub tab_id: String,
    pub url: String,
    pub title: String,
}

/// Derive the compatibility tab ID used by the existing MCP API.
///
/// The short ID remains part of the public contract for now. Callers that
/// resolve a tab must still compare the complete target list and reject
/// collisions rather than silently selecting an arbitrary target.
pub fn tab_id_from(target_id: &str) -> String {
    if target_id.len() >= 4 {
        target_id[target_id.len() - 4..].to_string()
    } else {
        target_id.to_string()
    }
}

/// Owns one CDP connection and its browser/page lifecycle.
///
/// The type is intentionally frontend-neutral: it returns transport-neutral
/// values and never depends on MCP, CLI, or REPL result types. All mutable
/// lifecycle state is private so there is one source of truth for the active
/// target and browser ownership.
pub struct BrowserSession {
    client: CdpClient,
    active_page: Mutex<Option<PageSession>>,
    launched_browser: Mutex<Option<LaunchedBrowser>>,
}

impl std::fmt::Debug for BrowserSession {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BrowserSession")
            .field("client", &"<cdp>")
            .finish_non_exhaustive()
    }
}

impl BrowserSession {
    /// Launch an isolated local browser and attach to its first page.
    pub async fn launch(profile: &BrowserProfile) -> Result<Self> {
        let launched = launch_browser(profile).await?;
        debug!(ws_url = %launched.ws_url, "Connecting to launched Chrome");

        let client = match CdpClient::connect(&launched.ws_url).await {
            Ok(client) => client,
            Err(error) => {
                // Do not leak a process if the websocket connection fails after
                // Chrome has already started.
                launched.kill().await;
                return Err(error);
            }
        };

        let session = Self {
            client,
            active_page: Mutex::new(None),
            launched_browser: Mutex::new(Some(launched)),
        };
        if let Err(error) = session.initialize().await {
            session.close().await.ok();
            return Err(error);
        }
        Ok(session)
    }

    /// Attach to an existing browser exposed through an HTTP or WebSocket CDP URL.
    pub async fn connect(cdp_url: &str) -> Result<Self> {
        let client = if cdp_url.starts_with("ws://") || cdp_url.starts_with("wss://") {
            CdpClient::connect(cdp_url).await?
        } else {
            CdpClient::connect_via_http(cdp_url).await?
        };

        let session = Self {
            client,
            active_page: Mutex::new(None),
            launched_browser: Mutex::new(None),
        };
        session.initialize().await?;
        Ok(session)
    }

    async fn initialize(&self) -> Result<()> {
        // Enable browser-level domains where Chrome accepts them. Page-level
        // domains are enabled after the target is attached.
        self.send_browser::<Value>("Network.enable", json!({}))
            .await
            .ok();
        self.send_browser::<Value>("Runtime.enable", json!({}))
            .await
            .ok();
        self.send_browser::<Value>("Page.enable", json!({}))
            .await
            .ok();

        // A browser may be connected before it has a page. Keep the session
        // valid and attach lazily when the first page operation is requested.
        let _ = self.attach_first_page().await?;
        Ok(())
    }

    /// Clone the underlying client for event subscriptions or advanced callers.
    /// Prefer `send_page`, `send_browser`, and tab methods for normal use.
    pub fn client(&self) -> CdpClient {
        self.client.clone()
    }

    /// Return the selected page, if one is attached.
    pub async fn active_page(&self) -> Result<PageSession> {
        self.active_page
            .lock()
            .await
            .clone()
            .ok_or_else(|| anyhow!("No page target is attached"))
    }

    /// Send a command to the currently selected page without holding lifecycle locks.
    pub async fn send_page<T: serde::de::DeserializeOwned>(
        &self,
        method: &str,
        params: Value,
    ) -> Result<T> {
        let page = self.active_page().await?;
        self.client
            .send(method, params, Some(&page.session_id))
            .await
    }

    /// Send a browser-level command without holding lifecycle locks.
    pub async fn send_browser<T: serde::de::DeserializeOwned>(
        &self,
        method: &str,
        params: Value,
    ) -> Result<T> {
        self.client.send(method, params, None).await
    }

    /// Return all page targets currently visible to CDP.
    pub async fn list_tabs(&self) -> Result<Vec<TabInfo>> {
        let response: Value = self.send_browser("Target.getTargets", json!({})).await?;
        Ok(response["targetInfos"]
            .as_array()
            .unwrap_or(&[])
            .iter()
            .filter(|target| target["type"].as_str() == Some("page"))
            .map(|target| {
                let target_id = target["targetId"].as_str().unwrap_or_default().to_string();
                TabInfo {
                    tab_id: tab_id_from(&target_id),
                    target_id,
                    url: target["url"].as_str().unwrap_or_default().to_string(),
                    title: target["title"].as_str().unwrap_or_default().to_string(),
                }
            })
            .collect())
    }

    /// Attach to a new page target and make it active.
    pub async fn new_tab(&self, url: Option<&str>) -> Result<PageSession> {
        let target_url = url.unwrap_or("about:blank");
        let response: Value = self
            .send_browser("Target.createTarget", json!({"url": target_url}))
            .await?;
        let target_id = response["targetId"]
            .as_str()
            .ok_or_else(|| anyhow!("Target.createTarget returned no targetId"))?;
        self.attach_target(target_id).await
    }

    /// Switch to a unique compatibility tab ID.
    pub async fn switch_tab(&self, tab_id: &str) -> Result<PageSession> {
        let tab = self.resolve_tab(tab_id).await?;
        let page = self.attach_target(&tab.target_id).await?;
        self.send_browser::<Value>("Target.activateTarget", json!({"targetId": tab.target_id}))
            .await
            .ok();
        Ok(page)
    }

    /// Close a tab and select another live page if the closed tab was active.
    pub async fn close_tab(&self, tab_id: &str) -> Result<()> {
        let tab = self.resolve_tab(tab_id).await?;
        self.send_browser::<Value>("Target.closeTarget", json!({"targetId": tab.target_id}))
            .await?;

        let active_target = self.active_page.lock().await.clone();
        if active_target.as_ref().map(|page| page.target_id.as_str()) == Some(tab.target_id.as_str()) {
            let replacement = self.list_tabs().await?.into_iter().next();
            if let Some(next) = replacement {
                self.attach_target(&next.target_id).await?;
            } else {
                *self.active_page.lock().await = None;
            }
        }
        Ok(())
    }

    /// Attach to the first available page, if the session has no active page.
    pub async fn ensure_active_page(&self) -> Result<PageSession> {
        if let Ok(page) = self.active_page().await {
            return Ok(page);
        }
        self.attach_first_page()
            .await?
            .ok_or_else(|| anyhow!("No page target is available"))
    }

    /// Check whether the underlying browser connection still responds.
    pub async fn is_alive(&self) -> bool {
        self.send_browser::<Value>("Target.getTargets", json!({}))
            .await
            .is_ok()
    }

    /// Close the owned browser process and clear the active page.
    ///
    /// Attached external browsers are never killed. This method is idempotent.
    pub async fn close(&self) -> Result<()> {
        *self.active_page.lock().await = None;
        let launched = self.launched_browser.lock().await.take();
        if let Some(launched) = launched {
            launched.kill().await;
        }
        Ok(())
    }

    /// Close all page targets and then release any locally owned browser.
    pub async fn close_all(&self) -> Result<()> {
        let tabs = self.list_tabs().await.unwrap_or_default();
        for tab in tabs {
            self.send_browser::<Value>("Target.closeTarget", json!({"targetId": tab.target_id}))
                .await
                .ok();
        }
        self.close().await
    }

    async fn attach_first_page(&self) -> Result<Option<PageSession>> {
        let tabs = self.list_tabs().await?;
        if let Some(tab) = tabs.first() {
            return self.attach_target(&tab.target_id).await.map(Some);
        }
        Ok(None)
    }

    async fn attach_target(&self, target_id: &str) -> Result<PageSession> {
        let response: Value = self
            .send_browser(
                "Target.attachToTarget",
                json!({"targetId": target_id, "flatten": true}),
            )
            .await?;
        let session_id = response["sessionId"]
            .as_str()
            .ok_or_else(|| anyhow!("Target.attachToTarget returned no sessionId"))?
            .to_string();

        for domain in ["Network.enable", "Runtime.enable", "Page.enable"] {
            self.client
                .send::<Value>(domain, json!({}), Some(&session_id))
                .await
                .with_context(|| format!("failed to enable {domain} for target {target_id}"))?;
        }

        let page = PageSession {
            target_id: target_id.to_string(),
            session_id,
            tab_id: tab_id_from(target_id),
        };
        *self.active_page.lock().await = Some(page.clone());
        Ok(page)
    }

    async fn resolve_tab(&self, tab_id: &str) -> Result<TabInfo> {
        let matches: Vec<TabInfo> = self
            .list_tabs()
            .await?
            .into_iter()
            .filter(|tab| tab.tab_id == tab_id)
            .collect();
        match matches.as_slice() {
            [] => Err(anyhow!("Tab {tab_id} not found")),
            [tab] => Ok(tab.clone()),
            _ => Err(anyhow!("Tab {tab_id} is ambiguous; use browser_list_tabs to refresh")),
        }
    }
}

impl Drop for BrowserSession {
    fn drop(&mut self) {
        // `LaunchedBrowser` performs best-effort synchronous process cleanup in
        // its own Drop implementation. Async callers should prefer `close`.
    }
}
