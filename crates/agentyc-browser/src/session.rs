//! BrowserSession: owns a CdpClient + SessionManager for one browser instance.

use anyhow::Result;
use tracing::debug;

use agentyc_cdp::{CdpClient, SessionManager};

use crate::launcher::{launch_browser, LaunchedBrowser};
use crate::profile::BrowserProfile;

/// A live browser session: process + CDP connection + session manager.
pub struct BrowserSession {
    pub client: CdpClient,
    pub sessions: SessionManager,
    /// Holds the process + temp dir. `None` when connecting to an existing browser.
    launched: Option<LaunchedBrowser>,
}

impl BrowserSession {
    /// Launch a new Chrome process and connect via CDP.
    pub async fn launch(profile: &BrowserProfile) -> Result<Self> {
        let launched = launch_browser(profile).await?;
        debug!(ws_url = %launched.ws_url, "Connecting to launched Chrome");
        let client = CdpClient::connect(&launched.ws_url).await?;
        let sessions = SessionManager::new(client.clone());
        sessions.setup().await?;
        Ok(Self {
            client,
            sessions,
            launched: Some(launched),
        })
    }

    /// Connect to an already-running browser via its CDP HTTP URL or WebSocket URL.
    pub async fn connect(cdp_url: &str) -> Result<Self> {
        let client = if cdp_url.starts_with("ws://") || cdp_url.starts_with("wss://") {
            CdpClient::connect(cdp_url).await?
        } else {
            CdpClient::connect_via_http(cdp_url).await?
        };
        let sessions = SessionManager::new(client.clone());
        sessions.setup().await?;
        Ok(Self {
            client,
            sessions,
            launched: None,
        })
    }

    /// Close the session, kill the browser process if we own it, and clean up temp dirs.
    pub async fn close(self) {
        if let Some(launched) = self.launched {
            launched.kill().await;
        }
    }

    /// Returns true if this session launched (and owns) the browser process.
    pub fn owns_browser(&self) -> bool {
        self.launched.is_some()
    }
}
