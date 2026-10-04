//! Navigation tools: navigate, back, forward, refresh, wait, wait_for_url,
//! wait_for_network_idle, wait_for_request, wait_for_response, wait_for_stable_dom.

#![allow(clippy::too_many_arguments, clippy::collapsible_if)]

use std::sync::Arc;

use agentyc_browser::BrowserProfile;
use agentyc_host::{NavigationKind, TextMatcher};
use agentyc_runtime::{BrowserRuntime, NetworkWaitFilter, WaitOptions};
use anyhow::Result;
use rmcp::model::CallToolResult;
use serde_json::json;

use crate::tools::{SharedState, ok_text, runtime_handle};

/// Launch or reconnect the canonical browser session exactly once.
pub async fn ensure_browser(state: &SharedState) -> Result<()> {
    let initialization_lock = state.lock().await.initialization_lock.clone();
    let _initialization = initialization_lock.lock().await;

    let existing = { state.lock().await.runtime.clone() };
    if let Some(runtime) = existing {
        if runtime.session().is_alive().await && runtime.session().active_page().await.is_ok() {
            crate::tools::ensure_dialog_handler(state).await;
            crate::tools::ensure_capture(state).await;
            return Ok(());
        }
        runtime.close().await.ok();
        let mut g = state.lock().await;
        g.runtime = None;
        g.dialog_handler_started = false;
        g.capture_started = false;
        g.clear_browser_scoped_state();
    }

    let cdp_url = { state.lock().await.cdp_url.clone() };
    let runtime = if let Some(cdp_url) = cdp_url {
        BrowserRuntime::connect(&cdp_url).await?
    } else {
        BrowserRuntime::launch(BrowserProfile::default()).await?
    };
    {
        let mut g = state.lock().await;
        g.clear_browser_scoped_state();
        g.runtime = Some(Arc::new(runtime));
        g.dialog_handler_started = false;
        g.capture_started = false;
    }
    crate::tools::ensure_dialog_handler(state).await;
    crate::tools::ensure_capture(state).await;
    Ok(())
}

/// Check URL against AGENTYC_ALLOWED_DOMAINS. Skip check if env not set.
async fn check_allowed_url(url: &str, _state: &SharedState) -> Result<()> {
    let host = url::Url::parse(url)
        .ok()
        .and_then(|u| u.host_str().map(str::to_string))
        .unwrap_or_default();
    let Some(raw_allowed) = std::env::var("AGENTYC_ALLOWED_DOMAINS")
        .ok()
        .filter(|v| !v.trim().is_empty())
    else {
        return Ok(());
    };
    let domains: Vec<&str> = raw_allowed
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .collect();
    if domains.iter().any(|d| {
        let d = d.trim_start_matches("*.");
        host == d || host.ends_with(&format!(".{d}"))
    }) {
        return Ok(());
    }
    Err(anyhow::anyhow!(
        "Navigation to {url:?} blocked: host {host:?} is not in AGENTYC_ALLOWED_DOMAINS ({})",
        domains.join(", ")
    ))
}

pub async fn browser_navigate(
    state: &SharedState,
    url: String,
    new_tab: Option<bool>,
) -> Result<CallToolResult> {
    // Auto-launch Chrome if no CDP client exists
    ensure_browser(state).await?;

    // Check allowed domains
    check_allowed_url(&url, state).await?;

    let runtime = runtime_handle(state).await?;
    let result = runtime
        .navigate_with_options(&url, new_tab.unwrap_or(false), WaitOptions::default())
        .await?;
    let msg = if new_tab.unwrap_or(false) {
        format!(
            "Navigated to: {} in new tab ({})",
            result.url, result.tab_id
        )
    } else if result.title.is_empty() {
        format!("Navigated to: {}", result.url)
    } else {
        format!("Navigated to: {} | \"{}\"", result.url, result.title)
    };
    Ok(ok_text(msg))
}

pub async fn browser_go_back(state: &SharedState) -> Result<CallToolResult> {
    runtime_handle(state)
        .await?
        .go_back(WaitOptions::default())
        .await?;
    Ok(ok_text("Went back"))
}

pub async fn browser_go_forward(state: &SharedState) -> Result<CallToolResult> {
    runtime_handle(state)
        .await?
        .go_forward(WaitOptions::default())
        .await?;
    Ok(ok_text("Went forward"))
}

pub async fn browser_refresh(state: &SharedState) -> Result<CallToolResult> {
    runtime_handle(state)
        .await?
        .reload(WaitOptions::default())
        .await?;
    Ok(ok_text("Page reloaded"))
}

pub async fn browser_wait(seconds: Option<f64>) -> Result<CallToolResult> {
    let secs = seconds.unwrap_or(2.0).clamp(0.1, 30.0);
    tokio::time::sleep(std::time::Duration::from_secs_f64(secs)).await;
    Ok(ok_text(format!("Waited {secs:.1}s")))
}

pub async fn browser_wait_for_url(
    state: &SharedState,
    url_substring: Option<String>,
    url_regex: Option<String>,
    timeout_seconds: Option<f64>,
) -> Result<CallToolResult> {
    let timeout = std::time::Duration::from_secs_f64(timeout_seconds.unwrap_or(10.0));
    if let Some(pattern) = url_regex {
        let regex = regex::Regex::new(&pattern)?;
        let runtime = runtime_handle(state).await?;
        let deadline = tokio::time::Instant::now() + timeout;
        loop {
            let current = runtime
                .evaluate("location.href")
                .await?
                .as_str()
                .unwrap_or_default()
                .to_owned();
            if regex.is_match(&current) {
                return Ok(ok_text(format!("URL matched: {current}")));
            }
            if tokio::time::Instant::now() >= deadline {
                return Err(anyhow::anyhow!("Timeout waiting for URL match"));
            }
            let next =
                (tokio::time::Instant::now() + std::time::Duration::from_millis(50)).min(deadline);
            tokio::time::sleep_until(next).await;
        }
    }
    let matcher = TextMatcher::Contains(url_substring.unwrap_or_default());
    let current = runtime_handle(state)
        .await?
        .wait_for_url(
            matcher,
            Some(NavigationKind::Any),
            WaitOptions::with_timeout(timeout),
        )
        .await?;
    Ok(ok_text(format!("URL matched: {current}")))
}

pub async fn browser_wait_for_network_idle(
    state: &SharedState,
    timeout_seconds: Option<f64>,
    idle_duration_ms: Option<u64>,
) -> Result<CallToolResult> {
    let timeout = std::time::Duration::from_secs_f64(timeout_seconds.unwrap_or(10.0));
    let idle = std::time::Duration::from_millis(idle_duration_ms.unwrap_or(500));
    runtime_handle(state)
        .await?
        .wait_for_network_idle(idle, WaitOptions::with_timeout(timeout))
        .await?;
    Ok(ok_text("Network idle"))
}

pub async fn browser_wait_for_request(
    state: &SharedState,
    url_substring: Option<String>,
    url_regex: Option<String>,
    method: Option<String>,
    resource_type: Option<String>,
    timeout_seconds: Option<f64>,
    include_headers: Option<bool>,
) -> Result<CallToolResult> {
    let timeout = std::time::Duration::from_secs_f64(timeout_seconds.unwrap_or(10.0));
    let re = url_regex
        .as_ref()
        .map(|r| regex::Regex::new(r))
        .transpose()?;
    if url_regex.is_none() {
        let result = runtime_handle(state)
            .await?
            .wait_for_request(
                NetworkWaitFilter {
                    url_substring,
                    method,
                    resource_type,
                    include_headers: include_headers.unwrap_or(false),
                    ..NetworkWaitFilter::default()
                },
                WaitOptions::with_timeout(timeout),
            )
            .await?;
        return Ok(ok_text(serde_json::to_string(&result)?));
    }
    let resource_filter = resource_type.map(|value| value.to_ascii_lowercase());
    let include_headers = include_headers.unwrap_or(false);
    let deadline = tokio::time::Instant::now() + timeout;
    let filter_description = url_substring.clone().or(url_regex.clone());

    let runtime = runtime_handle(state).await?;
    let page = runtime.session().active_page().await?;
    let cdp = runtime.session().client();
    let mut rx = cdp
        .subscribe_with_session("Network.requestWillBeSent")
        .await;
    let mut closed = Box::pin(cdp.wait_closed());
    let deadline_sleep = tokio::time::sleep_until(deadline);
    tokio::pin!(deadline_sleep);

    loop {
        tokio::select! {
            _ = &mut deadline_sleep => {
                return Err(anyhow::anyhow!("Timeout waiting for request matching {:?}", filter_description));
            }
            _ = &mut closed => return Err(anyhow::anyhow!("Network event stream closed")),
            event = rx.recv() => {
                let event = event.map_err(|_| anyhow::anyhow!("Network event stream closed"))?;
                if event.session_id.as_deref() != Some(page.session_id.as_str()) {
                    continue;
                }
                let params = event.params;
                let url = params["request"]["url"].as_str().unwrap_or("");
                let req_method = params["request"]["method"].as_str().unwrap_or("");
                let event_resource_type = params["type"].as_str().unwrap_or("");

                let url_match = if let Some(sub) = &url_substring {
                    url.contains(sub.as_str())
                } else if let Some(r) = &re {
                    r.is_match(url)
                } else {
                    true
                };
                if !url_match {
                    continue;
                }
                if let Some(filter) = &method
                    && !req_method.eq_ignore_ascii_case(filter)
                {
                    continue;
                }
                if let Some(filter) = &resource_filter
                    && !event_resource_type.eq_ignore_ascii_case(filter)
                {
                    continue;
                }

                let request_id = params["requestId"].as_str().unwrap_or("");
                let mut result = json!({
                    "url": url,
                    "method": req_method,
                    "resource_type": event_resource_type,
                    "request_id": request_id,
                });
                if include_headers {
                    result["headers"] = params["request"]["headers"].clone();
                }
                return Ok(ok_text(result.to_string()));
            }
        }
    }
}

pub async fn browser_wait_for_response(
    state: &SharedState,
    url_substring: Option<String>,
    url_regex: Option<String>,
    method: Option<String>,
    resource_type: Option<String>,
    status: Option<u32>,
    timeout_seconds: Option<f64>,
    include_headers: Option<bool>,
) -> Result<CallToolResult> {
    let timeout = std::time::Duration::from_secs_f64(timeout_seconds.unwrap_or(10.0));
    let re = url_regex
        .as_ref()
        .map(|r| regex::Regex::new(r))
        .transpose()?;
    if url_regex.is_none() {
        let result = runtime_handle(state)
            .await?
            .wait_for_response(
                NetworkWaitFilter {
                    url_substring,
                    method,
                    resource_type,
                    status,
                    include_headers: include_headers.unwrap_or(false),
                    ..NetworkWaitFilter::default()
                },
                WaitOptions::with_timeout(timeout),
            )
            .await?;
        return Ok(ok_text(serde_json::to_string(&result)?));
    }
    let resource_filter = resource_type.map(|value| value.to_ascii_lowercase());
    let include_headers = include_headers.unwrap_or(false);
    let deadline = tokio::time::Instant::now() + timeout;
    let filter_description = url_substring.clone().or(url_regex.clone());

    // Response events do not carry the HTTP method. Correlate them with the
    // request event by requestId while retaining the CDP resource type.
    let runtime = runtime_handle(state).await?;
    let page = runtime.session().active_page().await?;
    let cdp = runtime.session().client();
    let mut request_rx = cdp
        .subscribe_with_session("Network.requestWillBeSent")
        .await;
    let mut response_rx = cdp.subscribe_with_session("Network.responseReceived").await;
    let mut closed = Box::pin(cdp.wait_closed());
    let mut request_meta = std::collections::HashMap::<String, (String, String)>::new();
    let mut pending_responses = std::collections::HashMap::<String, serde_json::Value>::new();
    let deadline_sleep = tokio::time::sleep_until(deadline);
    tokio::pin!(deadline_sleep);

    loop {
        tokio::select! {
            _ = &mut deadline_sleep => {
                return Err(anyhow::anyhow!("Timeout waiting for response matching {:?}", filter_description));
            }
            _ = &mut closed => return Err(anyhow::anyhow!("Network event stream closed")),
            event = request_rx.recv() => {
                let event = event.map_err(|_| anyhow::anyhow!("Network event stream closed"))?;
                if event.session_id.as_deref() != Some(page.session_id.as_str()) {
                    continue;
                }
                let params = event.params;
                if let Some(request_id) = params["requestId"].as_str() {
                    let method_value = params["request"]["method"].as_str().unwrap_or("").to_owned();
                    let resource_value = params["type"].as_str().unwrap_or("").to_owned();
                    request_meta.insert(
                        request_id.to_owned(),
                        (method_value.clone(), resource_value.clone()),
                    );
                    if let Some(params) = pending_responses.remove(request_id) {
                        let url = params["response"]["url"].as_str().unwrap_or("");
                        let response_status = params["response"]["status"]
                            .as_u64()
                            .map(|value| value as u32);
                        let matches = url_substring.as_ref().is_none_or(|sub| url.contains(sub))
                            && re.as_ref().is_none_or(|regex| regex.is_match(url))
                            && method
                                .as_ref()
                                .is_none_or(|expected| method_value.eq_ignore_ascii_case(expected))
                            && resource_filter.as_ref().is_none_or(|expected| {
                                resource_value.eq_ignore_ascii_case(expected)
                            })
                            && status.is_none_or(|expected| response_status == Some(expected));
                        if matches {
                            let mut result = json!({
                                "url": url,
                                "method": method_value,
                                "resource_type": resource_value,
                                "status": response_status,
                                "request_id": request_id,
                            });
                            if include_headers {
                                result["headers"] = params["response"]["headers"].clone();
                            }
                            return Ok(ok_text(result.to_string()));
                        }
                    }
                }
            }
            event = response_rx.recv() => {
                let event = event.map_err(|_| anyhow::anyhow!("Network event stream closed"))?;
                if event.session_id.as_deref() != Some(page.session_id.as_str()) {
                    continue;
                }
                let params = event.params;
                let url = params["response"]["url"].as_str().unwrap_or("");
                let resp_status = params["response"]["status"].as_u64().map(|value| value as u32);
                let request_id = params["requestId"].as_str().unwrap_or("");
                let Some((req_method, request_resource_type)) = request_meta
                    .get(request_id)
                    .cloned()
                    .or_else(|| {
                        if method.is_some() || resource_filter.is_some() {
                            pending_responses.insert(request_id.to_owned(), params.clone());
                            None
                        } else {
                            Some((String::new(), String::new()))
                        }
                    }) else {
                    continue;
                };
                let event_resource_type = params["type"].as_str().unwrap_or("");
                let resource_kind = if request_resource_type.is_empty() {
                    event_resource_type
                } else {
                    request_resource_type.as_str()
                };

                let url_match = if let Some(sub) = &url_substring {
                    url.contains(sub.as_str())
                } else if let Some(r) = &re {
                    r.is_match(url)
                } else {
                    true
                };
                if !url_match {
                    continue;
                }
                if let Some(filter) = &method
                    && !req_method.eq_ignore_ascii_case(filter)
                {
                    continue;
                }
                if let Some(filter) = &resource_filter
                    && !resource_kind.eq_ignore_ascii_case(filter)
                {
                    continue;
                }
                if let Some(expected_status) = status
                    && resp_status != Some(expected_status)
                {
                    continue;
                }

                let mut result = json!({
                    "url": url,
                    "method": req_method,
                    "resource_type": resource_kind,
                    "status": resp_status,
                    "request_id": request_id,
                });
                if include_headers {
                    result["headers"] = params["response"]["headers"].clone();
                }
                return Ok(ok_text(result.to_string()));
            }
        }
    }
}

pub async fn browser_wait_for_stable_dom(
    state: &SharedState,
    timeout_seconds: Option<f64>,
    quiet_ms: Option<u64>,
) -> Result<CallToolResult> {
    let timeout = std::time::Duration::from_secs_f64(timeout_seconds.unwrap_or(10.0));
    let quiet = std::time::Duration::from_millis(quiet_ms.unwrap_or(500));
    runtime_handle(state)
        .await?
        .wait_for_stable_dom(quiet, quiet, WaitOptions::with_timeout(timeout))
        .await?;
    Ok(ok_text("DOM stable"))
}
