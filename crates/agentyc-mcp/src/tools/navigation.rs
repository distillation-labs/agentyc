//! Navigation tools: navigate, back, forward, refresh, wait, wait_for_url,
//! wait_for_network_idle, wait_for_request, wait_for_response, wait_for_stable_dom.

#![allow(clippy::too_many_arguments, clippy::collapsible_if)]

use anyhow::Result;
use rmcp::model::CallToolResult;
use serde_json::{Value, json};

use crate::tools::{SharedState, ok_text};

async fn cdp_send(state: &SharedState, method: &str, params: Value) -> Result<Value> {
    let g = state.lock().await;
    let cdp = g.cdp()?;
    let sid = g.sid().map(str::to_string);
    cdp.send::<Value>(method, params, sid.as_deref()).await
}

/// Launch Chrome and connect if no CDP client exists. Reconnects if Chrome has crashed.
pub async fn ensure_browser(state: &SharedState) -> Result<()> {
    // Check if existing CDP connection is still alive
    {
        let g = state.lock().await;
        if let Some(cdp) = &g.cdp {
            // Ping with a lightweight CDP call
            let alive = cdp
                .send::<serde_json::Value>("Target.getTargets", json!({}), None)
                .await
                .is_ok();
            if alive {
                return Ok(());
            }
            // CDP is dead — fall through to relaunch
            drop(g);
        } else {
            drop(g);
        }
    }

    // Clear stale state before relaunching
    {
        let mut g = state.lock().await;
        g.cdp = None;
        g.session_id = None;
        g.current_tab_id = None;
        g.launched_browser = None;
    }

    // Launch Chrome
    let profile = agentyc_browser::BrowserProfile::default();
    let launched = agentyc_browser::launch_browser(&profile).await?;
    let cdp = agentyc_cdp::CdpClient::connect(&launched.ws_url).await?;

    // Enable network, runtime, page domains
    {
        cdp.send::<Value>("Network.enable", json!({}), None)
            .await
            .ok();
        cdp.send::<Value>("Runtime.enable", json!({}), None)
            .await
            .ok();
        cdp.send::<Value>("Page.enable", json!({}), None).await.ok();
    }

    // Get first page target and attach
    let targets_resp = cdp
        .send::<Value>("Target.getTargets", json!({}), None)
        .await?;
    let mut session_id = None;
    let mut tab_id = None;
    if let Some(targets) = targets_resp["targetInfos"].as_array() {
        if let Some(page) = targets.iter().find(|t| t["type"].as_str() == Some("page")) {
            let tid = page["targetId"].as_str().unwrap_or("").to_string();
            if let Ok(r) = cdp
                .send::<Value>(
                    "Target.attachToTarget",
                    json!({"targetId": tid, "flatten": true}),
                    None,
                )
                .await
            {
                let sid = r["sessionId"].as_str().unwrap_or("").to_string();
                cdp.send::<Value>("Network.enable", json!({}), Some(&sid))
                    .await
                    .ok();
                cdp.send::<Value>("Runtime.enable", json!({}), Some(&sid))
                    .await
                    .ok();
                cdp.send::<Value>("Page.enable", json!({}), Some(&sid))
                    .await
                    .ok();
                session_id = Some(sid);
                tab_id = Some(crate::tools::tab_id_from(&tid));
            }
        }
    }

    let mut g = state.lock().await;
    g.cdp = Some(cdp);
    g.session_id = session_id;
    g.current_tab_id = tab_id;
    g.launched_browser = Some(launched);
    Ok(())
}

/// Check URL against AGENTYC_ALLOWED_DOMAINS. Skip check if env not set.
async fn check_allowed_url(url: &str, _state: &SharedState) -> Result<()> {
    let allowed = match std::env::var("AGENTYC_ALLOWED_DOMAINS") {
        Ok(v) if !v.trim().is_empty() => v,
        _ => return Ok(()),
    };
    let domains: Vec<&str> = allowed
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .collect();
    let host = url::Url::parse(url)
        .ok()
        .and_then(|u| u.host_str().map(str::to_string))
        .unwrap_or_default();
    let allowed = domains
        .iter()
        .any(|d| host == *d || host.ends_with(&format!(".{d}")));
    if !allowed {
        return Err(anyhow::anyhow!(
            "Navigation to {url:?} blocked: host {host:?} is not in AGENTYC_ALLOWED_DOMAINS ({allowed_list})",
            allowed_list = domains.join(", ")
        ));
    }
    Ok(())
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

    // If new_tab requested, create target first
    if new_tab.unwrap_or(false) {
        let resp = {
            let g = state.lock().await;
            let cdp = g.cdp()?;
            cdp.send::<Value>("Target.createTarget", json!({"url": "about:blank"}), None)
                .await?
        };
        let target_id = resp["targetId"].as_str().unwrap_or("").to_string();
        // Get session for new target
        let resp2 = {
            let g = state.lock().await;
            let cdp = g.cdp()?;
            cdp.send::<Value>(
                "Target.attachToTarget",
                json!({"targetId": target_id, "flatten": true}),
                None,
            )
            .await?
        };
        let sid = resp2["sessionId"].as_str().unwrap_or("").to_string();
        {
            let mut g = state.lock().await;
            g.session_id = Some(sid.clone());
            g.current_tab_id = Some(crate::tools::tab_id_from(&target_id));
        }
        let sid_opt = Some(sid.as_str());
        let g = state.lock().await;
        let cdp = g.cdp()?;
        cdp.send::<Value>("Page.navigate", json!({"url": url}), sid_opt)
            .await?;
        return Ok(ok_text(format!("Navigated to {url} in new tab")));
    }

    // Auto-connect: if no session, try to get the first target
    {
        let mut g = state.lock().await;
        if g.cdp.is_some() && g.session_id.is_none() {
            // list targets and attach to the first page
            let cdp = g.cdp.as_ref().unwrap();
            if let Ok(resp) = cdp
                .send::<Value>("Target.getTargets", json!({}), None)
                .await
            {
                if let Some(targets) = resp["targetInfos"].as_array() {
                    let page = targets.iter().find(|t| t["type"].as_str() == Some("page"));
                    if let Some(p) = page {
                        let tid = p["targetId"].as_str().unwrap_or("").to_string();
                        if let Ok(r2) = cdp
                            .send::<Value>(
                                "Target.attachToTarget",
                                json!({"targetId": tid, "flatten": true}),
                                None,
                            )
                            .await
                        {
                            g.session_id = Some(r2["sessionId"].as_str().unwrap_or("").to_string());
                            g.current_tab_id = Some(crate::tools::tab_id_from(&tid));
                        }
                    }
                }
            }
        }
    }

    let sid_str = state.lock().await.session_id.clone();
    let resp = {
        let g = state.lock().await;
        let cdp = g.cdp()?;
        let timeout = crate::tools::action_timeout();
        tokio::time::timeout(
            timeout,
            cdp.send::<Value>("Page.navigate", json!({"url": url}), sid_str.as_deref()),
        )
        .await
        .map_err(|_| {
            anyhow::anyhow!(
                "browser_navigate timed out after {:.0}s",
                timeout.as_secs_f64()
            )
        })??
    };
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
