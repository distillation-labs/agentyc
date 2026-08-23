//! Navigation tools: navigate, back, forward, refresh, wait, wait_for_url,
//! wait_for_network_idle, wait_for_request, wait_for_response, wait_for_stable_dom.

#![allow(clippy::too_many_arguments, clippy::collapsible_if)]

use std::sync::Arc;

use agentyc_browser::BrowserProfile;
use agentyc_runtime::BrowserRuntime;
use anyhow::Result;
use rmcp::model::CallToolResult;
use serde_json::{Value, json};

use crate::tools::{SharedState, ok_text};

async fn cdp_send(state: &SharedState, method: &str, params: Value) -> Result<Value> {
    let (cdp, sid) = crate::tools::page_client(state).await?;
    cdp.send::<Value>(method, params, Some(&sid)).await
}

/// Launch or reconnect the canonical browser session exactly once.
pub async fn ensure_browser(state: &SharedState) -> Result<()> {
    let initialization_lock = state.lock().await.initialization_lock.clone();
    let _initialization = initialization_lock.lock().await;

    if let Some(runtime) = state.lock().await.runtime.clone() {
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
    }

    let runtime = BrowserRuntime::launch(BrowserProfile::default()).await?;
    {
        let mut g = state.lock().await;
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

    // If new_tab requested, use the canonical target owner.
    if new_tab.unwrap_or(false) {
        let runtime = state.lock().await.runtime()?;
        let page = runtime.new_tab(Some(&url)).await?;
        return Ok(ok_text(format!("Navigated to {url} in new tab ({})", page.tab_id)));
    }

    let runtime = state.lock().await.runtime()?;
    runtime.session().ensure_active_page().await?;
    let resp = cdp_send(state, "Page.navigate", json!({"url": url})).await?;
    // Wait briefly for page title to populate
    tokio::time::sleep(std::time::Duration::from_millis(150)).await;
    let title_resp = cdp_send(
        state,
        "Runtime.evaluate",
        json!({
            "expression": "document.title", "returnByValue": true
        }),
    )
    .await
    .unwrap_or(json!({}));
    let title = title_resp["result"]["value"]
        .as_str()
        .unwrap_or("")
        .to_string();
    let nav_url = resp["url"].as_str().unwrap_or(&url);
    let msg = if title.is_empty() {
        format!("Navigated to: {nav_url}")
    } else {
        format!("Navigated to: {nav_url} | \"{title}\"")
    };
    Ok(ok_text(msg))
}

pub async fn browser_go_back(state: &SharedState) -> Result<CallToolResult> {
    cdp_send(
        state,
        "Runtime.evaluate",
        json!({"expression": "history.back()", "returnByValue": true}),
    )
    .await?;
    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    Ok(ok_text("Went back"))
}

pub async fn browser_go_forward(state: &SharedState) -> Result<CallToolResult> {
    cdp_send(
        state,
        "Runtime.evaluate",
        json!({"expression": "history.forward()", "returnByValue": true}),
    )
    .await?;
    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    Ok(ok_text("Went forward"))
}

pub async fn browser_refresh(state: &SharedState) -> Result<CallToolResult> {
    let r = cdp_send(state, "Page.reload", json!({})).await;
    if r.is_err() {
        cdp_send(
            state,
            "Runtime.evaluate",
            json!({"expression": "location.reload()", "returnByValue": true}),
        )
        .await?;
    }
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
    let re = url_regex
        .as_ref()
        .map(|r| regex::Regex::new(r))
        .transpose()?;
    let deadline = tokio::time::Instant::now() + timeout;
    loop {
        let current = {
            let g = state.lock().await;
            let sid = g.session_id.clone();
            if let Some(cdp) = &g.cdp {
                cdp.send::<Value>(
                    "Runtime.evaluate",
                    json!({"expression": "location.href", "returnByValue": true}),
                    sid.as_deref(),
                )
                .await
                .ok()
                .and_then(|v| v["result"]["value"].as_str().map(str::to_string))
                .unwrap_or_default()
            } else {
                String::new()
            }
        };
        let matched = if let Some(sub) = &url_substring {
            current.contains(sub.as_str())
        } else if let Some(r) = &re {
            r.is_match(&current)
        } else {
            false
        };
        if matched {
            return Ok(ok_text(format!("URL matched: {current}")));
        }
        if tokio::time::Instant::now() >= deadline {
            return Err(anyhow::anyhow!("Timeout waiting for URL match"));
        }
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
    }
}

pub async fn browser_wait_for_network_idle(
    state: &SharedState,
    timeout_seconds: Option<f64>,
    idle_duration_ms: Option<u64>,
) -> Result<CallToolResult> {
    let timeout = std::time::Duration::from_secs_f64(timeout_seconds.unwrap_or(10.0));
    let idle_ms = idle_duration_ms.unwrap_or(500);
    // Use JS Performance API to detect network quiet
    let js = format!(
        r#"new Promise((resolve) => {{
            let timer = setTimeout(() => resolve('idle'), {idle_ms});
            const observer = new PerformanceObserver(() => {{
                clearTimeout(timer);
                timer = setTimeout(() => resolve('idle'), {idle_ms});
            }});
            observer.observe({{ entryTypes: ['resource'] }});
            setTimeout(() => resolve('idle'), {timeout_ms});
        }})"#,
        timeout_ms = timeout.as_millis()
    );
    cdp_send(
        state,
        "Runtime.evaluate",
        json!({
            "expression": js, "awaitPromise": true, "returnByValue": true,
        }),
    )
    .await
    .ok();
    Ok(ok_text("Network idle"))
}

pub async fn browser_wait_for_request(
    state: &SharedState,
    url_substring: Option<String>,
    url_regex: Option<String>,
    method: Option<String>,
    _resource_type: Option<String>,
    timeout_seconds: Option<f64>,
    _include_headers: Option<bool>,
) -> Result<CallToolResult> {
    let timeout = std::time::Duration::from_secs_f64(timeout_seconds.unwrap_or(10.0));
    let re = url_regex
        .as_ref()
        .map(|r| regex::Regex::new(r))
        .transpose()?;
    let deadline = tokio::time::Instant::now() + timeout;

    // Subscribe to Network.requestWillBeSent via CDP event channel
    let mut rx = {
        let g = state.lock().await;
        let cdp = g.cdp()?;
        cdp.subscribe("Network.requestWillBeSent").await
    };

    loop {
        match tokio::time::timeout_at(deadline, rx.recv()).await {
            Ok(Ok(params)) => {
                let url = params["request"]["url"].as_str().unwrap_or("");
                let req_method = params["request"]["method"].as_str().unwrap_or("");

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
                if let Some(m) = &method {
                    if req_method.to_uppercase() != m.to_uppercase() {
                        continue;
                    }
                }
                return Ok(ok_text(
                    serde_json::json!({
                        "url": url,
                        "method": req_method,
                        "request_id": params["requestId"].as_str().unwrap_or(""),
                    })
                    .to_string(),
                ));
            }
            Ok(Err(_)) | Err(_) => {
                return Err(anyhow::anyhow!(
                    "Timeout waiting for request matching {:?}",
                    url_substring.or(url_regex)
                ));
            }
        }
    }
}

pub async fn browser_wait_for_response(
    state: &SharedState,
    url_substring: Option<String>,
    url_regex: Option<String>,
    method: Option<String>,
    _resource_type: Option<String>,
    status: Option<u32>,
    timeout_seconds: Option<f64>,
    _include_headers: Option<bool>,
) -> Result<CallToolResult> {
    let timeout = std::time::Duration::from_secs_f64(timeout_seconds.unwrap_or(10.0));
    let re = url_regex
        .as_ref()
        .map(|r| regex::Regex::new(r))
        .transpose()?;
    let deadline = tokio::time::Instant::now() + timeout;

    // Subscribe to Network.responseReceived via CDP event channel
    let mut rx = {
        let g = state.lock().await;
        let cdp = g.cdp()?;
        cdp.subscribe("Network.responseReceived").await
    };

    loop {
        match tokio::time::timeout_at(deadline, rx.recv()).await {
            Ok(Ok(params)) => {
                let url = params["response"]["url"].as_str().unwrap_or("");
                let resp_status = params["response"]["status"].as_u64().map(|s| s as u32);
                let req_method = params["type"].as_str().unwrap_or("");

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
                if let Some(m) = &method {
                    if req_method.to_uppercase() != m.to_uppercase() {
                        continue;
                    }
                }
                if let Some(s) = status {
                    if resp_status != Some(s) {
                        continue;
                    }
                }
                return Ok(ok_text(
                    serde_json::json!({
                        "url": url,
                        "status": resp_status,
                        "request_id": params["requestId"].as_str().unwrap_or(""),
                    })
                    .to_string(),
                ));
            }
            Ok(Err(_)) | Err(_) => {
                return Err(anyhow::anyhow!(
                    "Timeout waiting for response matching {:?}",
                    url_substring.or(url_regex)
                ));
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
    let qms = quiet_ms.unwrap_or(500);
    let js = format!(
        r#"new Promise((resolve, reject) => {{
            let timer = null;
            const obs = new MutationObserver(() => {{
                clearTimeout(timer);
                timer = setTimeout(() => {{ obs.disconnect(); resolve('stable'); }}, {qms});
            }});
            obs.observe(document.documentElement, {{subtree:true,childList:true,attributes:true}});
            timer = setTimeout(() => {{ obs.disconnect(); resolve('stable'); }}, {qms});
            setTimeout(() => {{ obs.disconnect(); reject('timeout'); }}, {timeout_ms});
        }})"#,
        timeout_ms = (timeout.as_millis())
    );
    let result = cdp_send(
        state,
        "Runtime.evaluate",
        json!({
            "expression": js,
            "awaitPromise": true,
            "returnByValue": true,
        }),
    )
    .await;
    match result {
        Ok(_) => Ok(ok_text("DOM stable")),
        Err(_) => Err(anyhow::anyhow!("Timeout waiting for stable DOM")),
    }
}
