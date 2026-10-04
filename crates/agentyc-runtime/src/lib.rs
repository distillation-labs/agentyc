//! Explicit runtime facades for the host-backed and legacy browser paths.
//!
//! [`HostClient`] is the Phase 3 canonical local-broker path. The direct
//! [`LegacyBrowserRuntime`] facade remains only for explicit legacy/test CDP
//! compatibility and is not a fallback when the host bridge is unavailable.

pub mod host_client;
pub use host_client::HostClient;

use std::{collections::HashMap, sync::Arc, time::Duration};

use anyhow::{Result, anyhow};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tokio::time::{Instant, sleep_until};

use agentyc_browser::{BrowserProfile, BrowserSession, PageSession, TabInfo, tab_id_from};
use agentyc_host::{CancellationToken, DownloadState, ElementState, NavigationKind, TextMatcher};

#[derive(Debug, Clone, Copy)]
enum NavigationTrigger {
    Url,
    History,
    Reload,
}

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

/// Options shared by event-driven runtime waits.
#[derive(Debug, Clone)]
pub struct WaitOptions {
    /// Absolute wait budget starting when the operation is armed.
    pub timeout: Duration,
    /// Cooperative cancellation shared with the caller.
    pub cancellation: CancellationToken,
}

impl Default for WaitOptions {
    fn default() -> Self {
        Self {
            timeout: Duration::from_secs(30),
            cancellation: CancellationToken::new(),
        }
    }
}

impl WaitOptions {
    /// Construct options with a timeout and a fresh cancellation token.
    pub fn with_timeout(timeout: Duration) -> Self {
        Self {
            timeout,
            ..Self::default()
        }
    }

    fn deadline(&self) -> Instant {
        Instant::now() + self.timeout
    }
}

/// Network request/response filter used by compatibility adapters.
#[derive(Debug, Clone, Default)]
pub struct NetworkWaitFilter {
    /// Optional URL substring.
    pub url_substring: Option<String>,
    /// Optional exact URL.
    pub url_exact: Option<String>,
    /// Optional HTTP method.
    pub method: Option<String>,
    /// Optional CDP resource type.
    pub resource_type: Option<String>,
    /// Optional response status.
    pub status: Option<u32>,
    /// Include request/response headers in the result.
    pub include_headers: bool,
}

/// Result returned by a request or response wait.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NetworkWaitResult {
    /// Matching URL.
    pub url: String,
    /// HTTP method when known.
    pub method: String,
    /// CDP resource type.
    pub resource_type: String,
    /// Request or response identity.
    pub request_id: String,
    /// Response status when present.
    pub status: Option<u32>,
    /// Optional headers requested by the caller.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub headers: Option<Value>,
}

/// Result returned by a CDP download lifecycle wait.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DownloadWaitResult {
    /// Browser-assigned download identity.
    pub guid: String,
    /// Suggested filename when Chrome supplied one.
    pub filename: String,
    /// Current CDP download state.
    pub state: String,
    /// Bytes received when reported by Chrome.
    pub received_bytes: Option<u64>,
    /// Expected total bytes when reported by Chrome.
    pub total_bytes: Option<u64>,
}

/// Explicit legacy direct-CDP runtime.
///
/// This facade is retained for compatibility and test harnesses. It may launch
/// or connect to a managed browser only when a caller explicitly selects this
/// legacy API; it is not the existing-Chrome host path.
#[derive(Clone)]
pub struct LegacyBrowserRuntime {
    session: Arc<BrowserSession>,
    allowed_domains: Option<Vec<String>>,
}

impl std::fmt::Debug for LegacyBrowserRuntime {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LegacyBrowserRuntime")
            .field("session", &self.session)
            .field("allowed_domains", &self.allowed_domains)
            .finish()
    }
}

fn url_matches(url: &str, filter: &NetworkWaitFilter) -> bool {
    filter
        .url_exact
        .as_ref()
        .is_none_or(|expected| url == expected)
        && filter
            .url_substring
            .as_ref()
            .is_none_or(|expected| url.contains(expected))
}

fn network_event_matches(
    params: &Value,
    filter: &NetworkWaitFilter,
    method: Option<&str>,
    resource_type: Option<&str>,
    status: Option<u32>,
) -> bool {
    let url = params["response"]["url"]
        .as_str()
        .or_else(|| params["request"]["url"].as_str())
        .or_else(|| params["url"].as_str())
        .unwrap_or_default();
    if !url_matches(url, filter) {
        return false;
    }
    if let Some(expected) = &filter.method
        && !method.is_some_and(|actual| actual.eq_ignore_ascii_case(expected))
    {
        return false;
    }
    let actual_resource_type = resource_type
        .or_else(|| params["type"].as_str())
        .unwrap_or_default();
    if let Some(expected) = &filter.resource_type
        && !actual_resource_type.eq_ignore_ascii_case(expected)
    {
        return false;
    }
    if let Some(expected) = filter.status
        && status != Some(expected)
    {
        return false;
    }
    true
}

fn network_result(
    params: &Value,
    filter: &NetworkWaitFilter,
    method: impl Into<String>,
    resource_type: impl Into<String>,
    status: Option<u32>,
) -> NetworkWaitResult {
    let url = params["response"]["url"]
        .as_str()
        .or_else(|| params["request"]["url"].as_str())
        .or_else(|| params["url"].as_str())
        .unwrap_or_default()
        .to_owned();
    let headers = if filter.include_headers {
        params["response"]["headers"]
            .as_object()
            .map(|_| params["response"]["headers"].clone())
            .or_else(|| {
                params["request"]["headers"]
                    .as_object()
                    .map(|_| params["request"]["headers"].clone())
            })
    } else {
        None
    };
    NetworkWaitResult {
        url,
        method: method.into(),
        resource_type: resource_type.into(),
        request_id: params["requestId"]
            .as_str()
            .or_else(|| params["request_id"].as_str())
            .unwrap_or_default()
            .to_owned(),
        status,
        headers,
    }
}

fn excluded_network_request(params: &Value) -> bool {
    let resource_type = params["type"]
        .as_str()
        .or_else(|| params["resourceType"].as_str())
        .or_else(|| params["resource_type"].as_str())
        .or_else(|| params["request"]["resourceType"].as_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    if matches!(
        resource_type.as_str(),
        "websocket" | "eventsource" | "webtransport" | "download"
    ) {
        return true;
    }
    if params["excluded"].as_bool() == Some(true)
        || params["long_lived"].as_bool() == Some(true)
        || params["websocket"].as_bool() == Some(true)
        || params["download"].as_bool() == Some(true)
        || params["analytics"].as_bool() == Some(true)
        || params["request"]["isDownload"].as_bool() == Some(true)
        || params["isDownload"].as_bool() == Some(true)
        || params["longLived"].as_bool() == Some(true)
        || params["isLongLived"].as_bool() == Some(true)
        || params["request"]["longLived"].as_bool() == Some(true)
    {
        return true;
    }
    let url = params["request"]["url"]
        .as_str()
        .or_else(|| params["url"].as_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    if url.starts_with("ws://") || url.starts_with("wss://") {
        return true;
    }
    [
        "google-analytics",
        "googletagmanager",
        "doubleclick",
        "segment.io",
        "mixpanel",
        "amplitude",
        "hotjar",
        "plausible.io",
        "/analytics",
        "/collect",
    ]
    .iter()
    .any(|marker| url.contains(marker))
}

fn event_belongs_to_session(event_session: Option<&str>, session_id: &str) -> bool {
    event_session == Some(session_id)
}

fn element_state_name(state: ElementState) -> &'static str {
    match state {
        ElementState::Present => "present",
        ElementState::Absent => "absent",
        ElementState::Visible => "visible",
        ElementState::Hidden => "hidden",
    }
}

fn download_state_matches(expected: DownloadState, actual: &str) -> bool {
    match expected {
        DownloadState::Started => {
            actual.eq_ignore_ascii_case("started")
                || actual.eq_ignore_ascii_case("inprogress")
                || actual.eq_ignore_ascii_case("in_progress")
        }
        DownloadState::Completed => actual.eq_ignore_ascii_case("completed"),
        DownloadState::Failed => {
            actual.eq_ignore_ascii_case("canceled") || actual.eq_ignore_ascii_case("failed")
        }
    }
}

fn navigation_event_kind(params: &Value) -> NavigationKind {
    if params.get("frameId").is_some() && params["frameId"].as_str().is_some() {
        NavigationKind::History
    } else if params["type"].as_str().is_some_and(|value| {
        value.eq_ignore_ascii_case("reload")
            || value.eq_ignore_ascii_case("backforwardcacherestore")
    }) {
        NavigationKind::Reload
    } else {
        NavigationKind::Url
    }
}

fn navigation_kind_matches(actual: NavigationKind, expected: Option<NavigationKind>) -> bool {
    match expected {
        None | Some(NavigationKind::Any) => true,
        Some(expected) => actual == expected,
    }
}

fn target_info_to_tab(info: &Value) -> Option<TabInfo> {
    if info["type"].as_str() != Some("page") {
        return None;
    }
    let target_id = info["targetId"].as_str()?.to_owned();
    Some(TabInfo {
        tab_id: tab_id_from(&target_id),
        target_id,
        url: info["url"].as_str().unwrap_or_default().to_owned(),
        title: info["title"].as_str().unwrap_or_default().to_owned(),
    })
}

impl LegacyBrowserRuntime {
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
        self.navigate_with_options(url, new_tab, WaitOptions::default())
            .await
    }

    /// Navigate after arming page lifecycle events before the trigger.
    pub async fn navigate_with_options(
        &self,
        url: &str,
        new_tab: bool,
        options: WaitOptions,
    ) -> Result<NavigationResult> {
        self.check_allowed_url(url)?;
        // Create a blank target first so the page event subscriptions are armed
        // before the URL trigger. This preserves legacy new-tab ownership while
        // preventing the initial navigation from racing the waiter.
        let page = if new_tab {
            self.session.new_tab(None).await?
        } else {
            self.session.ensure_active_page().await?
        };
        let deadline = options.deadline();
        let cancellation = options.cancellation.clone();
        self.wait_for_navigation_trigger(
            &page,
            "Page.navigate",
            json!({"url": url}),
            NavigationTrigger::Url,
            deadline,
            cancellation.clone(),
        )
        .await?;
        self.navigation_result(&page, url, deadline, cancellation)
            .await
    }

    /// Navigate backward in history and wait for the resulting transition.
    pub async fn go_back(&self, options: WaitOptions) -> Result<PageInfo> {
        self.history_navigation("back", options).await
    }

    /// Navigate forward in history and wait for the resulting transition.
    pub async fn go_forward(&self, options: WaitOptions) -> Result<PageInfo> {
        self.history_navigation("forward", options).await
    }

    /// Reload the active document and wait for its new generation.
    pub async fn reload(&self, options: WaitOptions) -> Result<PageInfo> {
        let page = self.session.ensure_active_page().await?;
        let deadline = options.deadline();
        let cancellation = options.cancellation.clone();
        self.wait_for_navigation_trigger(
            &page,
            "Page.reload",
            json!({}),
            NavigationTrigger::Reload,
            deadline,
            cancellation.clone(),
        )
        .await?;
        self.page_info_until(&page, deadline, cancellation).await
    }

    async fn navigation_result(
        &self,
        page: &PageSession,
        fallback_url: &str,
        deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<NavigationResult> {
        let info = self
            .evaluate_until(
                page,
                "({url:location.href,title:document.title})".to_owned(),
                deadline,
                cancellation,
                "navigation result",
            )
            .await?;
        Ok(NavigationResult {
            url: info["result"]["value"]["url"]
                .as_str()
                .unwrap_or(fallback_url)
                .to_owned(),
            title: info["result"]["value"]["title"]
                .as_str()
                .unwrap_or_default()
                .to_owned(),
            tab_id: page.tab_id.clone(),
        })
    }

    async fn history_navigation(&self, direction: &str, options: WaitOptions) -> Result<PageInfo> {
        let page = self.session.ensure_active_page().await?;
        let deadline = options.deadline();
        let cancellation = options.cancellation.clone();
        self.wait_for_navigation_trigger(
            &page,
            "Runtime.evaluate",
            json!({
                "expression": format!("history.{direction}()"),
                "returnByValue": true
            }),
            NavigationTrigger::History,
            deadline,
            cancellation.clone(),
        )
        .await?;
        self.page_info_until(&page, deadline, cancellation).await
    }

    async fn wait_for_navigation_trigger(
        &self,
        page: &PageSession,
        method: &str,
        params: Value,
        trigger: NavigationTrigger,
        deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<()> {
        if cancellation.is_cancelled() {
            return Err(anyhow!("wait cancelled"));
        }
        if Instant::now() >= deadline {
            return Err(anyhow!("navigation wait deadline exceeded"));
        }
        let client = self.session.client();
        // Subscribe before issuing the trigger. CDP events are buffered by the
        // transport while the command response is in flight.
        let mut navigated_rx = client.subscribe_with_session("Page.frameNavigated").await;
        let mut within_document_rx = client
            .subscribe_with_session("Page.navigatedWithinDocument")
            .await;
        let mut load_rx = client.subscribe_with_session("Page.loadEventFired").await;
        let mut lifecycle_rx = client.subscribe_with_session("Page.lifecycleEvent").await;
        let mut detached_rx = client.subscribe_with_session("Page.frameDetached").await;
        let mut closed = Box::pin(client.wait_closed());
        let mut cancelled = Box::pin(cancellation.wait_cancelled());
        let mut deadline_sleep = Box::pin(sleep_until(deadline));
        let mut command = Box::pin(self.session.send_page_with_session::<Value>(
            &page.session_id,
            method,
            params,
        ));

        // The trigger itself is deadline- and cancellation-bound. Once its
        // response is consumed, the future is never polled again.
        let response = tokio::select! {
            result = &mut command => result?,
            _ = &mut cancelled => return Err(anyhow!("wait cancelled")),
            _ = &mut deadline_sleep => return Err(anyhow!("navigation wait deadline exceeded")),
            _ = &mut closed => return Err(anyhow!("page session disconnected during navigation wait")),
        };
        let mut frame_id = response["frameId"].as_str().map(str::to_owned);
        let mut saw_navigation = false;
        let mut saw_load = false;
        let mut same_document = false;

        loop {
            if same_document && !matches!(trigger, NavigationTrigger::Reload) {
                return Ok(());
            }
            if saw_navigation && saw_load {
                return Ok(());
            }
            tokio::select! {
                _ = &mut cancelled => return Err(anyhow!("wait cancelled")),
                _ = &mut deadline_sleep => return Err(anyhow!("navigation wait deadline exceeded")),
                _ = &mut closed => return Err(anyhow!("page session disconnected during navigation wait")),
                event = navigated_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("navigation event stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    let frame = &event.params["frame"];
                    // A frameNavigated event for a child frame has parentId;
                    // only the main frame completes a page navigation.
                    if frame["parentId"].as_str().is_some() {
                        continue;
                    }
                    let event_frame = frame["id"].as_str();
                    if frame_id.as_deref().is_some_and(|expected| Some(expected) != event_frame) {
                        continue;
                    }
                    if frame_id.is_none() {
                        frame_id = event_frame.map(str::to_owned);
                    }
                    saw_navigation = true;

                }
                event = within_document_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("same-document event stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    let event_frame = event.params["frameId"].as_str();
                    if frame_id.as_deref().is_some_and(|expected| Some(expected) != event_frame) {
                        continue;
                    }
                    if frame_id.is_none() {
                        frame_id = event_frame.map(str::to_owned);
                    }
                    same_document = true;
                }
                event = load_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("load event stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    // Page.loadEventFired is main-frame scoped in CDP and
                    // commonly has no frameId. If it does, honor it.
                    if event.params["frameId"].as_str().is_none_or(|actual| {
                        frame_id.as_deref().is_none_or(|expected| expected == actual)
                    }) {
                        saw_load = true;
                    }
                }
                event = lifecycle_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("lifecycle event stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    if event.params["name"].as_str() == Some("load")
                        && event.params["frameId"].as_str().is_none_or(|actual| {
                            frame_id.as_deref().is_none_or(|expected| expected == actual)
                        })
                    {
                        saw_load = true;
                    }
                }
                event = detached_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("frame event stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    if event.params["parentFrameId"].as_str().is_some() {
                        continue;
                    }
                    let event_frame = event.params["frameId"].as_str();
                    if frame_id.as_deref().is_none_or(|expected| {
                        event_frame.is_none_or(|actual| expected == actual)
                    }) {
                        return Err(anyhow!("page generation was replaced during navigation wait"));
                    }
                }
            }
        }
    }

    async fn evaluate_until(
        &self,
        page: &PageSession,
        expression: String,
        deadline: Instant,
        cancellation: CancellationToken,
        label: &str,
    ) -> Result<Value> {
        if cancellation.is_cancelled() {
            return Err(anyhow!("{label} wait cancelled"));
        }
        if Instant::now() >= deadline {
            return Err(anyhow!("{label} wait deadline exceeded"));
        }
        let client = self.session.client();
        let mut closed = Box::pin(client.wait_closed());
        let mut cancelled = Box::pin(cancellation.wait_cancelled());
        let mut deadline_sleep = Box::pin(sleep_until(deadline));
        let mut command = Box::pin(self.session.send_page_with_session::<Value>(
            &page.session_id,
            "Runtime.evaluate",
            json!({
                "expression": expression,
                "awaitPromise": true,
                "returnByValue": true,
            }),
        ));
        let response = tokio::select! {
            result = &mut command => result?,
            _ = &mut cancelled => return Err(anyhow!("{label} wait cancelled")),
            _ = &mut deadline_sleep => return Err(anyhow!("{label} wait deadline exceeded")),
            _ = &mut closed => return Err(anyhow!("page session disconnected during {label} wait")),
        };
        if let Some(details) = response.get("exceptionDetails") {
            return Err(anyhow!("{label} wait failed: {details}"));
        }
        Ok(response)
    }

    async fn list_tabs_until(
        &self,
        deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<Vec<TabInfo>> {
        if cancellation.is_cancelled() {
            return Err(anyhow!("page-info wait cancelled"));
        }
        if Instant::now() >= deadline {
            return Err(anyhow!("page-info wait deadline exceeded"));
        }
        let client = self.session.client();
        let mut closed = Box::pin(client.wait_closed());
        let mut cancelled = Box::pin(cancellation.wait_cancelled());
        let mut deadline_sleep = Box::pin(sleep_until(deadline));
        let mut command = Box::pin(self.session.list_tabs());
        tokio::select! {
            result = &mut command => result,
            _ = &mut cancelled => Err(anyhow!("page-info wait cancelled")),
            _ = &mut deadline_sleep => Err(anyhow!("page-info wait deadline exceeded")),
            _ = &mut closed => Err(anyhow!("browser disconnected during page-info wait")),
        }
    }

    async fn page_info_until(
        &self,
        page: &PageSession,
        deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<PageInfo> {
        let info = self
            .evaluate_until(
                page,
                "({url:location.href,title:document.title})".to_owned(),
                deadline,
                cancellation.clone(),
                "page info",
            )
            .await?;
        let tabs = self.list_tabs_until(deadline, cancellation).await?;
        Ok(PageInfo {
            url: info["result"]["value"]["url"]
                .as_str()
                .unwrap_or_default()
                .to_owned(),
            title: info["result"]["value"]["title"]
                .as_str()
                .unwrap_or_default()
                .to_owned(),
            tab_id: Some(page.tab_id.clone()),
            tabs,
        })
    }

    /// Wait for a URL event in the active page.
    pub async fn wait_for_url(
        &self,
        matcher: TextMatcher,
        navigation: Option<NavigationKind>,
        options: WaitOptions,
    ) -> Result<String> {
        let page = self.session.ensure_active_page().await?;
        let deadline = options.deadline();
        let client = self.session.client();
        let mut navigated_rx = client.subscribe_with_session("Page.frameNavigated").await;
        let mut within_document_rx = client
            .subscribe_with_session("Page.navigatedWithinDocument")
            .await;
        let mut detached_rx = client.subscribe_with_session("Page.frameDetached").await;
        let mut closed = Box::pin(client.wait_closed());
        let cancellation = options.cancellation.clone();
        let mut cancelled = Box::pin(cancellation.wait_cancelled());
        let mut deadline_sleep = Box::pin(sleep_until(deadline));

        // Preserve the old wait-for-URL behavior when the requested URL was
        // already reached before the caller started waiting. Event listeners
        // are still armed first so a trigger cannot race registration.
        if navigation.is_none() || matches!(navigation, Some(NavigationKind::Any)) {
            let current = self
                .evaluate_until(
                    &page,
                    "location.href".to_owned(),
                    deadline,
                    cancellation.clone(),
                    "URL",
                )
                .await?;
            if let Some(url) = current["result"]["value"].as_str()
                && matcher.matches(url)
            {
                return Ok(url.to_owned());
            }
        }

        loop {
            tokio::select! {
                _ = &mut cancelled => return Err(anyhow!("URL wait cancelled")),
                _ = &mut deadline_sleep => return Err(anyhow!("URL wait deadline exceeded")),
                _ = &mut closed => return Err(anyhow!("page session disconnected during URL wait")),
                event = navigated_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("URL event stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    let frame = &event.params["frame"];
                    if frame["parentId"].as_str().is_some() {
                        continue;
                    }
                    let Some(url) = frame["url"].as_str() else {
                        continue;
                    };
                    if navigation_kind_matches(navigation_event_kind(&event.params), navigation)
                        && matcher.matches(url)
                    {
                        return Ok(url.to_owned());
                    }
                }
                event = within_document_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("same-document event stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    let Some(url) = event.params["url"].as_str() else {
                        continue;
                    };
                    if navigation_kind_matches(NavigationKind::History, navigation)
                        && matcher.matches(url)
                    {
                        return Ok(url.to_owned());
                    }
                }
                event = detached_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("frame event stream closed: {error}"))?;
                    if event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        return Err(anyhow!("page generation was replaced during URL wait"));
                    }
                }
            }
        }
    }

    /// Wait until the page has no tracked short-lived network requests for a
    /// semantic quiet interval. WebSockets, long-lived streams, downloads, and
    /// analytics traffic are deliberately excluded from the active set.
    pub async fn wait_for_network_idle(
        &self,
        quiet_for: Duration,
        options: WaitOptions,
    ) -> Result<()> {
        let page = self.session.ensure_active_page().await?;
        let deadline = options.deadline();
        if options.cancellation.is_cancelled() {
            return Err(anyhow!("network-idle wait cancelled"));
        }
        if Instant::now() >= deadline {
            return Err(anyhow!("network-idle wait deadline exceeded"));
        }
        let client = self.session.client();
        let mut request_rx = client
            .subscribe_with_session("Network.requestWillBeSent")
            .await;
        let mut finished_rx = client
            .subscribe_with_session("Network.loadingFinished")
            .await;
        let mut failed_rx = client.subscribe_with_session("Network.loadingFailed").await;
        let mut websocket_rx = client
            .subscribe_with_session("Network.webSocketCreated")
            .await;
        let mut download_rx = client
            .subscribe_with_session("Page.downloadWillBegin")
            .await;
        let mut closed = Box::pin(client.wait_closed());
        let cancellation = options.cancellation.clone();
        let mut cancelled = Box::pin(cancellation.wait_cancelled());
        let mut deadline_sleep = Box::pin(sleep_until(deadline));
        let mut active = HashMap::<String, String>::new();
        let mut quiet_since = Instant::now();

        loop {
            if Instant::now() >= deadline {
                return Err(anyhow!("network-idle wait deadline exceeded"));
            }
            if active.is_empty() && quiet_for.is_zero() {
                return Ok(());
            }
            let quiet_deadline = if active.is_empty() {
                quiet_since + quiet_for
            } else {
                deadline
            };
            let wake_at = if quiet_deadline < deadline {
                quiet_deadline
            } else {
                deadline
            };
            let mut idle_sleep = Box::pin(sleep_until(wake_at));
            tokio::select! {
                _ = &mut cancelled => return Err(anyhow!("network-idle wait cancelled")),
                _ = &mut deadline_sleep => return Err(anyhow!("network-idle wait deadline exceeded")),
                _ = &mut closed => return Err(anyhow!("page session disconnected during network-idle wait")),
                _ = &mut idle_sleep => {
                    if active.is_empty() && Instant::now() >= quiet_deadline {
                        if Instant::now() < deadline {
                            return Ok(());
                        }
                        return Err(anyhow!("network-idle wait deadline exceeded"));
                    }
                }
                event = request_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("request event stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    if excluded_network_request(&event.params) {
                        continue;
                    }
                    let Some(request_id) = event.params["requestId"].as_str() else {
                        continue;
                    };
                    let url = event.params["request"]["url"].as_str().unwrap_or_default().to_owned();
                    active.insert(request_id.to_owned(), url);
                    quiet_since = Instant::now();
                }
                event = finished_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("network finish stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    if let Some(request_id) = event.params["requestId"].as_str() {
                        active.remove(request_id);
                    }
                    if active.is_empty() {
                        quiet_since = Instant::now();
                    }
                }
                event = failed_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("network failure stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    if let Some(request_id) = event.params["requestId"].as_str() {
                        active.remove(request_id);
                    }
                    if active.is_empty() {
                        quiet_since = Instant::now();
                    }
                }
                event = websocket_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("WebSocket event stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    if let Some(request_id) = event.params["requestId"].as_str() {
                        active.remove(request_id);
                    }
                    if active.is_empty() {
                        quiet_since = Instant::now();
                    }
                }
                event = download_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("download event stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    if let Some(url) = event.params["url"].as_str() {
                        active.retain(|_, active_url| active_url != url);
                    }
                    if active.is_empty() {
                        quiet_since = Instant::now();
                    }
                }
            }
        }
    }

    /// Wait for a matching network request emitted by the active page.
    pub async fn wait_for_request(
        &self,
        filter: NetworkWaitFilter,
        options: WaitOptions,
    ) -> Result<NetworkWaitResult> {
        let page = self.session.ensure_active_page().await?;
        let deadline = options.deadline();
        let client = self.session.client();
        let mut rx = client
            .subscribe_with_session("Network.requestWillBeSent")
            .await;
        let mut closed = Box::pin(client.wait_closed());
        let cancellation = options.cancellation.clone();
        let mut cancelled = Box::pin(cancellation.wait_cancelled());
        let mut deadline_sleep = Box::pin(sleep_until(deadline));

        loop {
            tokio::select! {
                _ = &mut cancelled => return Err(anyhow!("request wait cancelled")),
                _ = &mut deadline_sleep => return Err(anyhow!("request wait deadline exceeded")),
                _ = &mut closed => return Err(anyhow!("page session disconnected during request wait")),
                event = rx.recv() => {
                    let event = event.map_err(|error| anyhow!("request event stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    let method = event.params["request"]["method"].as_str().unwrap_or_default();
                    let resource_type = event.params["type"].as_str().unwrap_or_default();
                    if network_event_matches(&event.params, &filter, Some(method), Some(resource_type), None) {
                        return Ok(network_result(
                            &event.params,
                            &filter,
                            method,
                            resource_type,
                            None,
                        ));
                    }
                }
            }
        }
    }

    /// Wait for a matching network response, correlating request metadata even
    /// when the response notification arrives first.
    pub async fn wait_for_response(
        &self,
        filter: NetworkWaitFilter,
        options: WaitOptions,
    ) -> Result<NetworkWaitResult> {
        let page = self.session.ensure_active_page().await?;
        let deadline = options.deadline();
        let client = self.session.client();
        let mut request_rx = client
            .subscribe_with_session("Network.requestWillBeSent")
            .await;
        let mut response_rx = client
            .subscribe_with_session("Network.responseReceived")
            .await;
        let mut closed = Box::pin(client.wait_closed());
        let cancellation = options.cancellation.clone();
        let mut cancelled = Box::pin(cancellation.wait_cancelled());
        let mut deadline_sleep = Box::pin(sleep_until(deadline));
        let mut metadata = HashMap::<String, (String, String)>::new();
        let mut pending = HashMap::<String, Value>::new();

        loop {
            tokio::select! {
                _ = &mut cancelled => return Err(anyhow!("response wait cancelled")),
                _ = &mut deadline_sleep => return Err(anyhow!("response wait deadline exceeded")),
                _ = &mut closed => return Err(anyhow!("page session disconnected during response wait")),
                event = request_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("request event stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    let Some(request_id) = event.params["requestId"].as_str() else {
                        continue;
                    };
                    let method = event.params["request"]["method"].as_str().unwrap_or_default().to_owned();
                    let resource_type = event.params["type"].as_str().unwrap_or_default().to_owned();
                    metadata.insert(request_id.to_owned(), (method.clone(), resource_type.clone()));
                    if let Some(response) = pending.remove(request_id)
                        && network_event_matches(
                            &response,
                            &filter,
                            Some(&method),
                            Some(&resource_type),
                            response["response"]["status"].as_u64().map(|status| status as u32),
                        )
                    {
                        return Ok(network_result(
                            &response,
                            &filter,
                            method,
                            resource_type,
                            response["response"]["status"].as_u64().map(|status| status as u32),
                        ));
                    }
                }
                event = response_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("response event stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    let Some(request_id) = event.params["requestId"].as_str() else {
                        continue;
                    };
                    let status = event.params["response"]["status"].as_u64().map(|status| status as u32);
                    if let Some((method, resource_type)) = metadata.get(request_id).cloned() {
                        if network_event_matches(
                            &event.params,
                            &filter,
                            Some(&method),
                            Some(&resource_type),
                            status,
                        ) {
                            return Ok(network_result(
                                &event.params,
                                &filter,
                                method,
                                resource_type,
                                status,
                            ));
                        }
                    } else if filter.method.is_none() && filter.resource_type.is_none()
                        && network_event_matches(&event.params, &filter, None, None, status)
                    {
                        return Ok(network_result(&event.params, &filter, "", "", status));
                    } else {
                        pending.insert(request_id.to_owned(), event.params);
                    }
                }
            }
        }
    }

    /// Wait for mutation quiet and geometry stability in the active page.
    pub async fn wait_for_stable_dom(
        &self,
        quiet_for: Duration,
        geometry_quiet_for: Duration,
        options: WaitOptions,
    ) -> Result<()> {
        let page = self.session.ensure_active_page().await?;
        let deadline = options.deadline();
        let timeout_ms = deadline
            .saturating_duration_since(Instant::now())
            .as_millis()
            .min(u128::from(u64::MAX));
        let expression = format!(
            r#"(function(){{
                const mutationQuiet = {quiet_ms};
                const geometryQuiet = {geometry_ms};
                const timeoutMs = {timeout_ms};
                return new Promise((resolve, reject) => {{
                    const root = document.documentElement;
                    if (!root) {{ resolve(true); return; }}
                    const geometry = () => [root, document.body]
                        .filter(Boolean)
                        .map((element) => {{
                            const rect = element.getBoundingClientRect();
                            return [rect.x, rect.y, rect.width, rect.height, element.scrollWidth, element.scrollHeight].join(',');
                        }}).join(';');
                    let lastMutation = performance.now();
                    let lastGeometry = performance.now();
                    let previousGeometry = geometry();
                    let finished = false;
                    const mutationObserver = new MutationObserver(() => {{
                        lastMutation = performance.now();
                    }});
                    mutationObserver.observe(root, {{subtree:true, childList:true, attributes:true, characterData:true}});
                    const resizeObserver = typeof ResizeObserver === 'function'
                        ? new ResizeObserver(() => {{ lastGeometry = performance.now(); }})
                        : null;
                    resizeObserver?.observe(root);
                    if (document.body) resizeObserver?.observe(document.body);
                    const finish = (error) => {{
                        if (finished) return;
                        finished = true;
                        mutationObserver.disconnect();
                        resizeObserver?.disconnect();
                        clearTimeout(timeout);
                        error ? reject(error) : resolve(true);
                    }};
                    const check = () => {{
                        const now = performance.now();
                        const currentGeometry = geometry();
                        if (currentGeometry !== previousGeometry) {{
                            previousGeometry = currentGeometry;
                            lastGeometry = now;
                        }}
                        if (now - lastMutation >= mutationQuiet && now - lastGeometry >= geometryQuiet) {{
                            finish();
                        }} else if (now < start + timeoutMs) {{
                            requestAnimationFrame(check);
                        }} else {{
                            finish('stable DOM timeout');
                        }}
                    }};
                    const start = performance.now();
                    const timeout = setTimeout(() => finish('stable DOM timeout'), timeoutMs);
                    requestAnimationFrame(check);
                }});
            }})()"#,
            quiet_ms = quiet_for.as_millis(),
            geometry_ms = geometry_quiet_for.as_millis(),
        );
        self.evaluate_until(
            &page,
            expression,
            deadline,
            options.cancellation,
            "stable DOM",
        )
        .await
        .map(|_| ())
    }

    /// Wait for an element or text condition using a mutation observer.
    pub async fn wait_for_element(
        &self,
        selector: Option<&str>,
        text: Option<&str>,
        state: ElementState,
        options: WaitOptions,
    ) -> Result<()> {
        let page = self.session.ensure_active_page().await?;
        let deadline = options.deadline();
        let selector = serde_json::to_string(&selector)?;
        let text = serde_json::to_string(&text)?;
        let state = serde_json::to_string(element_state_name(state))?;
        let timeout_ms = deadline
            .saturating_duration_since(Instant::now())
            .as_millis()
            .min(u128::from(u64::MAX));
        let expression = format!(
            r#"(function(){{
                const selector = {selector};
                const text = {text};
                const expected = {state};
                const timeoutMs = {timeout_ms};
                return new Promise((resolve, reject) => {{
                    const matches = () => {{
                        const element = selector ? document.querySelector(selector) : null;
                        const visible = (candidate) => {{
                            if (!candidate || !candidate.isConnected) return false;
                            const style = getComputedStyle(candidate);
                            const rect = candidate.getBoundingClientRect();
                            return style.display !== 'none' && style.visibility !== 'hidden'
                                && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
                        }};
                        const present = element ? true : !!text
                            && (document.body?.innerText || '').toLowerCase().includes(text.toLowerCase());
                        switch (expected) {{
                            case 'present': return present;
                            case 'absent': return !present;
                            case 'visible': return visible(element);
                            case 'hidden': return !!element && !visible(element);
                            default: return false;
                        }}
                    }};
                    let finished = false;
                    const observer = new MutationObserver(() => check());
                    const finish = (error) => {{
                        if (finished) return;
                        finished = true;
                        observer.disconnect();
                        clearTimeout(timeout);
                        error ? reject(error) : resolve(true);
                    }};
                    const check = () => {{
                        if (matches()) finish();
                    }};
                    if (document.documentElement) observer.observe(document.documentElement, {{subtree:true, childList:true, attributes:true, characterData:true}});
                    const timeout = setTimeout(() => finish('element wait timeout'), timeoutMs);
                    check();
                }});
            }})()"#,
        );
        self.evaluate_until(&page, expression, deadline, options.cancellation, "element")
            .await
            .map(|_| ())
    }

    /// Wait for a page lifecycle event such as `load` or `DOMContentLoaded`.
    pub async fn wait_for_page(
        &self,
        lifecycle: Option<&str>,
        options: WaitOptions,
    ) -> Result<PageInfo> {
        let page = self.session.ensure_active_page().await?;
        let deadline = options.deadline();
        if options.cancellation.is_cancelled() {
            return Err(anyhow!("page wait cancelled"));
        }
        if Instant::now() >= deadline {
            return Err(anyhow!("page wait deadline exceeded"));
        }
        let Some(expected) = lifecycle.map(str::to_ascii_lowercase) else {
            return self
                .page_info_until(&page, deadline, options.cancellation.clone())
                .await;
        };
        let client = self.session.client();
        let mut lifecycle_rx = client.subscribe_with_session("Page.lifecycleEvent").await;
        let mut load_rx = client.subscribe_with_session("Page.loadEventFired").await;
        let mut detached_rx = client.subscribe_with_session("Page.frameDetached").await;
        let current = self
            .evaluate_until(
                &page,
                "document.readyState".to_owned(),
                deadline,
                options.cancellation.clone(),
                "page",
            )
            .await?;
        let ready = current["result"]["value"].as_str().unwrap_or_default();
        if (expected == "complete" && ready == "complete")
            || (expected == "interactive" && matches!(ready, "interactive" | "complete"))
        {
            return self
                .page_info_until(&page, deadline, options.cancellation.clone())
                .await;
        }
        let mut closed = Box::pin(client.wait_closed());
        let cancellation = options.cancellation.clone();
        let mut cancelled = Box::pin(cancellation.wait_cancelled());
        let mut deadline_sleep = Box::pin(sleep_until(deadline));
        loop {
            tokio::select! {
                _ = &mut cancelled => return Err(anyhow!("page wait cancelled")),
                _ = &mut deadline_sleep => return Err(anyhow!("page wait deadline exceeded")),
                _ = &mut closed => return Err(anyhow!("page session disconnected during page wait")),
                event = lifecycle_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("page lifecycle stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    let actual = event.params["name"].as_str().unwrap_or_default();
                    if actual.eq_ignore_ascii_case(&expected)
                        || (expected == "complete" && actual.eq_ignore_ascii_case("load"))
                    {
                        return self
                            .page_info_until(&page, deadline, options.cancellation.clone())
                            .await;
                    }
                }
                event = load_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("page load stream closed: {error}"))?;
                    if event_belongs_to_session(event.session_id.as_deref(), &page.session_id)
                        && matches!(expected.as_str(), "load" | "complete")
                    {
                        return self
                            .page_info_until(&page, deadline, options.cancellation.clone())
                            .await;
                    }
                }
                event = detached_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("page frame stream closed: {error}"))?;
                    if event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        return Err(anyhow!("page generation was replaced during page wait"));
                    }
                }
            }
        }
    }

    /// Wait for a browser download lifecycle state.
    pub async fn wait_for_download(
        &self,
        name: Option<TextMatcher>,
        state: DownloadState,
        options: WaitOptions,
    ) -> Result<DownloadWaitResult> {
        let page = self.session.ensure_active_page().await?;
        let deadline = options.deadline();
        if options.cancellation.is_cancelled() {
            return Err(anyhow!("download wait cancelled"));
        }
        if Instant::now() >= deadline {
            return Err(anyhow!("download wait deadline exceeded"));
        }
        let client = self.session.client();
        let mut begin_rx = client
            .subscribe_with_session("Page.downloadWillBegin")
            .await;
        let mut progress_rx = client.subscribe_with_session("Page.downloadProgress").await;
        let mut closed = Box::pin(client.wait_closed());
        let cancellation = options.cancellation.clone();
        let mut cancelled = Box::pin(cancellation.wait_cancelled());
        let mut deadline_sleep = Box::pin(sleep_until(deadline));
        let mut downloads = HashMap::<String, DownloadWaitResult>::new();

        loop {
            tokio::select! {
                _ = &mut cancelled => return Err(anyhow!("download wait cancelled")),
                _ = &mut deadline_sleep => return Err(anyhow!("download wait deadline exceeded")),
                _ = &mut closed => return Err(anyhow!("page session disconnected during download wait")),
                event = begin_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("download start stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    let Some(guid) = event.params["guid"].as_str() else { continue };
                    let entry = downloads.entry(guid.to_owned()).or_insert_with(|| DownloadWaitResult {
                        guid: guid.to_owned(),
                        filename: String::new(),
                        state: "started".to_owned(),
                        received_bytes: None,
                        total_bytes: None,
                    });
                    if let Some(filename) = event.params["suggestedFilename"].as_str() {
                        entry.filename = filename.to_owned();
                    }
                    if entry.state.is_empty()
                        || (!entry.state.eq_ignore_ascii_case("completed")
                            && !entry.state.eq_ignore_ascii_case("canceled")
                            && !entry.state.eq_ignore_ascii_case("failed"))
                    {
                        entry.state = "started".to_owned();
                    }
                    if download_state_matches(state, &entry.state)
                        && name.as_ref().is_none_or(|matcher| matcher.matches(&entry.filename))
                    {
                        return Ok(entry.clone());
                    }
                }
                event = progress_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("download progress stream closed: {error}"))?;
                    if !event_belongs_to_session(event.session_id.as_deref(), &page.session_id) {
                        continue;
                    }
                    let Some(guid) = event.params["guid"].as_str() else { continue };
                    let entry = downloads.entry(guid.to_owned()).or_insert_with(|| DownloadWaitResult {
                        guid: guid.to_owned(),
                        filename: String::new(),
                        state: String::new(),
                        received_bytes: None,
                        total_bytes: None,
                    });
                    if let Some(download_state) = event.params["state"].as_str() {
                        entry.state = download_state.to_owned();
                    }
                    entry.received_bytes = event.params["receivedBytes"].as_u64();
                    entry.total_bytes = event.params["totalBytes"].as_u64();
                    if download_state_matches(state, &entry.state)
                        && name.as_ref().is_none_or(|matcher| matcher.matches(&entry.filename))
                    {
                        return Ok(entry.clone());
                    }
                }
            }
        }
    }

    /// Wait for a new page target using Target lifecycle events.
    pub async fn wait_for_new_tab(
        &self,
        matcher: Option<TextMatcher>,
        options: WaitOptions,
    ) -> Result<TabInfo> {
        let deadline = options.deadline();
        if options.cancellation.is_cancelled() {
            return Err(anyhow!("new-tab wait cancelled"));
        }
        if Instant::now() >= deadline {
            return Err(anyhow!("new-tab wait deadline exceeded"));
        }
        let client = self.session.client();
        let mut created_rx = client.subscribe("Target.targetCreated").await;
        let mut changed_rx = client.subscribe("Target.targetInfoChanged").await;
        let cancellation = options.cancellation.clone();
        let mut cancelled = Box::pin(cancellation.wait_cancelled());
        let mut closed = Box::pin(client.wait_closed());
        let mut deadline_sleep = Box::pin(sleep_until(deadline));
        let mut snapshot = Box::pin(self.session.list_tabs());
        let existing = tokio::select! {
            result = &mut snapshot => result?,
            _ = &mut cancelled => return Err(anyhow!("new-tab wait cancelled")),
            _ = &mut deadline_sleep => return Err(anyhow!("new-tab wait deadline exceeded")),
            _ = &mut closed => return Err(anyhow!("browser disconnected during new-tab wait")),
        };
        let existing = existing
            .into_iter()
            .map(|tab| (tab.target_id, ()))
            .collect::<HashMap<_, _>>();
        let mut created = HashMap::<String, ()>::new();
        let mut latest = HashMap::<String, Value>::new();

        loop {
            tokio::select! {
                _ = &mut cancelled => return Err(anyhow!("new-tab wait cancelled")),
                _ = &mut deadline_sleep => return Err(anyhow!("new-tab wait deadline exceeded")),
                _ = &mut closed => return Err(anyhow!("browser disconnected during new-tab wait")),
                event = created_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("target-created stream closed: {error}"))?;
                    let Some(info) = event["targetInfo"].as_object().map(|_| event["targetInfo"].clone()) else { continue };
                    let Some(target_id) = info["targetId"].as_str().map(str::to_owned) else { continue };
                    latest.insert(target_id.clone(), info);
                    created.insert(target_id.clone(), ());
                    let Some(tab) = latest.get(&target_id).and_then(target_info_to_tab) else { continue };
                    if matcher.as_ref().is_none_or(|matcher| matcher.matches(&tab.url)) {
                        return Ok(tab);
                    }
                }
                event = changed_rx.recv() => {
                    let event = event.map_err(|error| anyhow!("target-info stream closed: {error}"))?;
                    let Some(info) = event["targetInfo"].as_object().map(|_| event["targetInfo"].clone()) else { continue };
                    let Some(target_id) = info["targetId"].as_str().map(str::to_owned) else { continue };
                    latest.insert(target_id.clone(), info);
                    if existing.contains_key(&target_id) && !created.contains_key(&target_id) {
                        continue;
                    }
                    let Some(tab) = latest.get(&target_id).and_then(target_info_to_tab) else { continue };
                    if matcher.as_ref().is_none_or(|matcher| matcher.matches(&tab.url)) {
                        return Ok(tab);
                    }
                }
            }
        }
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

/// Compatibility alias for callers that have not migrated to [`HostClient`].
/// New code must use [`HostClient`] for the existing-Chrome path or name the
/// legacy mode explicitly with [`LegacyBrowserRuntime`].
pub type BrowserRuntime = LegacyBrowserRuntime;
