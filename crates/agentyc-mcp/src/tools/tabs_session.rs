//! Tabs & session state tools: new_tab, list_tabs, switch_tab, close_tab,
#![allow(
    clippy::too_many_arguments,
    clippy::collapsible_if,
    clippy::collapsible_match
)]
//! wait_for_tab, get/set/clear cookies, grant_permissions, set_geolocation,
//! set_extra_headers, set_user_agent, set_timezone, set_locale, emulate_media,
//! save_state, load_state, list_sessions, close_session, close_all.

use anyhow::{Result, anyhow};
use rmcp::model::CallToolResult;
use serde_json::{Value, json};

use crate::tools::{SharedState, ok_json, ok_text, tab_id_from};

async fn cdp_root(state: &SharedState, method: &str, params: Value) -> Result<Value> {
    let g = state.lock().await;
    let cdp = g.cdp()?;
    cdp.send::<Value>(method, params, None).await
}

async fn cdp_session(state: &SharedState, method: &str, params: Value) -> Result<Value> {
    let g = state.lock().await;
    let cdp = g.cdp()?;
    let sid = g.session_id.clone();
    cdp.send::<Value>(method, params, sid.as_deref()).await
}

pub async fn browser_new_tab(state: &SharedState, url: Option<String>) -> Result<CallToolResult> {
    let target_url = url.as_deref().unwrap_or("about:blank");
    let resp = cdp_root(state, "Target.createTarget", json!({"url": target_url})).await?;
    let target_id = resp["targetId"].as_str().unwrap_or("").to_string();
    let r2 = cdp_root(
        state,
        "Target.attachToTarget",
        json!({"targetId": target_id, "flatten": true}),
    )
    .await?;
    let sid = r2["sessionId"].as_str().unwrap_or("").to_string();
    // Enable domains on new session
    {
        let g = state.lock().await;
        if let Some(cdp) = &g.cdp {
            cdp.send::<serde_json::Value>("Network.enable", json!({}), Some(&sid))
                .await
                .ok();
            cdp.send::<serde_json::Value>("Runtime.enable", json!({}), Some(&sid))
                .await
                .ok();
            cdp.send::<serde_json::Value>("Page.enable", json!({}), Some(&sid))
                .await
                .ok();
        }
    }
    let tid = tab_id_from(&target_id);
    {
        let mut g = state.lock().await;
        g.session_id = Some(sid.clone());
        g.current_tab_id = Some(tid.clone());
    }
    Ok(ok_text(format!("New tab created: {tid}")))
}

pub async fn browser_list_tabs(state: &SharedState) -> Result<CallToolResult> {
    let resp = cdp_root(state, "Target.getTargets", json!({})).await?;
    let tabs: Vec<Value> = resp["targetInfos"]
        .as_array()
        .unwrap_or(&vec![])
        .iter()
        .filter(|t| t["type"].as_str() == Some("page"))
        .map(|t| {
            let tid = t["targetId"].as_str().unwrap_or("");
            json!({
                "tab_id": tab_id_from(tid),
                "url": t["url"],
                "title": t["title"],
            })
        })
        .collect();
    Ok(ok_json(&json!(tabs)))
}

pub async fn browser_switch_tab(state: &SharedState, tab_id: String) -> Result<CallToolResult> {
    // Find target by tab_id suffix
    let resp = cdp_root(state, "Target.getTargets", json!({})).await?;
    let target = resp["targetInfos"]
        .as_array()
        .unwrap_or(&vec![])
        .iter()
        .find(|t| {
            t["type"].as_str() == Some("page")
                && tab_id_from(t["targetId"].as_str().unwrap_or("")) == tab_id
        })
        .cloned();
    let target = target.ok_or_else(|| anyhow!("Tab {tab_id} not found"))?;
    let target_id = target["targetId"].as_str().unwrap_or("").to_string();
    let r2 = cdp_root(
        state,
        "Target.attachToTarget",
        json!({"targetId": target_id, "flatten": true}),
    )
    .await?;
    let sid = r2["sessionId"].as_str().unwrap_or("").to_string();
    {
        let mut g = state.lock().await;
        g.session_id = Some(sid);
        g.current_tab_id = Some(tab_id.clone());
    }
    cdp_session(
        state,
        "Target.activateTarget",
        json!({"targetId": target_id}),
    )
    .await
    .ok();
    Ok(ok_text(format!("Switched to tab {tab_id}")))
}

pub async fn browser_close_tab(state: &SharedState, tab_id: String) -> Result<CallToolResult> {
    let resp = cdp_root(state, "Target.getTargets", json!({})).await?;
    let target = resp["targetInfos"]
        .as_array()
        .unwrap_or(&vec![])
        .iter()
        .find(|t| {
            t["type"].as_str() == Some("page")
                && tab_id_from(t["targetId"].as_str().unwrap_or("")) == tab_id
        })
        .cloned();
    if let Some(t) = target {
        let tid = t["targetId"].as_str().unwrap_or("").to_string();
        cdp_root(state, "Target.closeTarget", json!({"targetId": tid})).await?;
    }
    Ok(ok_text(format!("Closed tab {tab_id}")))
}

pub async fn browser_wait_for_tab(
    state: &SharedState,
    url_substring: Option<String>,
    url_regex: Option<String>,
    timeout_seconds: Option<f64>,
    switch_focus: Option<bool>,
) -> Result<CallToolResult> {
    let timeout = std::time::Duration::from_secs_f64(timeout_seconds.unwrap_or(10.0));
    let re = url_regex
        .as_ref()
        .map(|r| regex::Regex::new(r))
        .transpose()?;
    let deadline = tokio::time::Instant::now() + timeout;
    let initial_tabs: Vec<String> = {
        let resp = cdp_root(state, "Target.getTargets", json!({})).await?;
        resp["targetInfos"]
            .as_array()
            .unwrap_or(&vec![])
            .iter()
            .filter(|t| t["type"].as_str() == Some("page"))
            .filter_map(|t| t["targetId"].as_str().map(str::to_string))
            .collect()
    };
    loop {
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
        let resp = cdp_root(state, "Target.getTargets", json!({})).await?;
        for t in resp["targetInfos"].as_array().unwrap_or(&vec![]) {
            if t["type"].as_str() != Some("page") {
                continue;
            }
            let tid = t["targetId"].as_str().unwrap_or("").to_string();
            if initial_tabs.contains(&tid) {
                continue;
            }
            let url = t["url"].as_str().unwrap_or("");
            let url_match = if let Some(sub) = &url_substring {
                url.contains(sub.as_str())
            } else if let Some(r) = &re {
                r.is_match(url)
            } else {
                true
            };
            if url_match {
                if switch_focus.unwrap_or(true) {
                    let r2 = cdp_root(
                        state,
                        "Target.attachToTarget",
                        json!({"targetId": tid, "flatten": true}),
                    )
                    .await?;
                    let sid = r2["sessionId"].as_str().unwrap_or("").to_string();
                    let new_tab_id = tab_id_from(&tid);
                    let mut g = state.lock().await;
                    g.session_id = Some(sid);
                    g.current_tab_id = Some(new_tab_id.clone());
                }
                return Ok(ok_text(format!("New tab found: {url}")));
            }
        }
        if tokio::time::Instant::now() >= deadline {
            return Err(anyhow!("Timeout waiting for new tab"));
        }
    }
}

pub async fn browser_get_cookies(state: &SharedState) -> Result<CallToolResult> {
    let resp = cdp_session(state, "Network.getCookies", json!({})).await?;
    Ok(ok_json(&resp["cookies"]))
}

pub async fn browser_set_cookies(
    state: &SharedState,
    cookies: Vec<Value>,
) -> Result<CallToolResult> {
    // Try session-level first, fall back to root
    let r = cdp_session(state, "Network.setCookies", json!({"cookies": cookies})).await;
    if r.is_err() {
        cdp_root(state, "Network.setCookies", json!({"cookies": cookies})).await?;
    }
    Ok(ok_text(format!("Set {} cookie(s)", cookies.len())))
}

pub async fn browser_clear_cookies(
    state: &SharedState,
    name: Option<String>,
) -> Result<CallToolResult> {
    if let Some(n) = name {
        let r1 = cdp_session(state, "Network.deleteCookies", json!({"name": n})).await;
        if r1.is_err() {
            let r2 = cdp_root(state, "Network.deleteCookies", json!({"name": n})).await;
            if r2.is_err() {
                let js = format!(
                    "document.cookie = {:?} + '=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;'",
                    n
                );
                let g = state.lock().await;
                let sid = g.session_id.clone();
                g.cdp()?
                    .send::<Value>(
                        "Runtime.evaluate",
                        json!({"expression": js}),
                        sid.as_deref(),
                    )
                    .await
                    .ok();
            }
        }
        Ok(ok_text(format!("Deleted cookie: {n}")))
    } else {
        let r1 = cdp_session(state, "Network.clearBrowserCookies", json!({})).await;
        if r1.is_err() {
            let r2 = cdp_root(state, "Network.clearBrowserCookies", json!({})).await;
            if r2.is_err() {
                let js = "document.cookie.split(';').forEach(c=>{const k=c.trim().split('=')[0];document.cookie=k+'=;expires=Thu,01 Jan 1970 00:00:00 UTC;path=/;';})";
                let g = state.lock().await;
                let sid = g.session_id.clone();
                g.cdp()?
                    .send::<Value>(
                        "Runtime.evaluate",
                        json!({"expression": js}),
                        sid.as_deref(),
                    )
                    .await
                    .ok();
            }
        }
        Ok(ok_text("All cookies cleared"))
    }
}

pub async fn browser_grant_permissions(
    state: &SharedState,
    permissions: Vec<String>,
    origin: Option<String>,
) -> Result<CallToolResult> {
    let mut params = json!({"permissions": permissions});
    if let Some(o) = origin {
        params["origin"] = json!(o);
    }
    cdp_root(state, "Browser.grantPermissions", params).await?;
    Ok(ok_text(format!("Granted permissions: {permissions:?}")))
}

pub async fn browser_set_geolocation(
    state: &SharedState,
    latitude: f64,
    longitude: f64,
    accuracy: Option<f64>,
) -> Result<CallToolResult> {
    cdp_session(
        state,
        "Emulation.setGeolocationOverride",
        json!({
            "latitude": latitude,
            "longitude": longitude,
            "accuracy": accuracy.unwrap_or(100.0),
        }),
    )
    .await?;
    Ok(ok_text(format!(
        "Geolocation set to ({latitude},{longitude})"
    )))
}

pub async fn browser_set_extra_headers(
    state: &SharedState,
    headers: std::collections::HashMap<String, String>,
) -> Result<CallToolResult> {
    cdp_session(
        state,
        "Network.setExtraHTTPHeaders",
        json!({"headers": headers}),
    )
    .await?;
    Ok(ok_text(format!("Set {} extra header(s)", headers.len())))
}

pub async fn browser_set_user_agent(
    state: &SharedState,
    user_agent: String,
    accept_language: Option<String>,
    platform: Option<String>,
) -> Result<CallToolResult> {
    let mut params = json!({"userAgent": user_agent});
    if let Some(al) = accept_language {
        params["acceptLanguage"] = json!(al);
    }
    if let Some(p) = platform {
        params["platform"] = json!(p);
    }
    cdp_session(state, "Network.setUserAgentOverride", params).await?;
    Ok(ok_text("User agent set"))
}

pub async fn browser_set_timezone(
    state: &SharedState,
    timezone_id: Option<String>,
) -> Result<CallToolResult> {
    let tz = timezone_id.unwrap_or_default();
    cdp_session(
        state,
        "Emulation.setTimezoneOverride",
        json!({"timezoneId": tz}),
    )
    .await?;
    Ok(ok_text(if tz.is_empty() {
        "Timezone cleared".into()
    } else {
        format!("Timezone set to {tz}")
    }))
}

pub async fn browser_set_locale(
    state: &SharedState,
    locale: Option<String>,
) -> Result<CallToolResult> {
    let lc = locale.unwrap_or_default();
    cdp_session(state, "Emulation.setLocaleOverride", json!({"locale": lc})).await?;
    Ok(ok_text(if lc.is_empty() {
        "Locale cleared".into()
    } else {
        format!("Locale set to {lc}")
    }))
}

pub async fn browser_emulate_media(
    state: &SharedState,
    media: Option<String>,
    color_scheme: Option<String>,
    reduced_motion: Option<String>,
    forced_colors: Option<String>,
) -> Result<CallToolResult> {
    let mut features = Vec::new();
    if let Some(cs) = color_scheme {
        features.push(json!({"name":"prefers-color-scheme","value":cs}));
    }
    if let Some(rm) = reduced_motion {
        features.push(json!({"name":"prefers-reduced-motion","value":rm}));
    }
    if let Some(fc) = forced_colors {
        features.push(json!({"name":"forced-colors","value":fc}));
    }
    cdp_session(
        state,
        "Emulation.setEmulatedMedia",
        json!({
            "media": media.unwrap_or_default(),
            "features": features,
        }),
    )
    .await?;
    Ok(ok_text("Media emulation set"))
}

pub async fn browser_save_state(
    state: &SharedState,
    path: Option<String>,
) -> Result<CallToolResult> {
    let save_path = path.unwrap_or_else(|| {
        dirs::home_dir()
            .unwrap_or_default()
            .join(".agentyc-mcp")
            .join("browser-state.json")
            .to_string_lossy()
            .to_string()
    });
    let cookies_resp = cdp_session(state, "Network.getCookies", json!({}))
        .await
        .unwrap_or(Value::Null);
    let cookies = &cookies_resp["cookies"];
    let ls_resp = cdp_session(state, "Runtime.evaluate", json!({
        "expression": "(function(){const o={};for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);o[k]=localStorage.getItem(k);}return o;})()",
        "returnByValue": true,
    })).await.unwrap_or(Value::Null);
    let data = json!({"cookies": cookies, "localStorage": ls_resp["result"]["value"]});
    if let Some(parent) = std::path::Path::new(&save_path).parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&save_path, serde_json::to_string_pretty(&data)?)?;
    Ok(ok_text(format!("State saved to {save_path}")))
}

pub async fn browser_load_state(state: &SharedState, path: String) -> Result<CallToolResult> {
    let content = std::fs::read_to_string(&path)?;
    let data: Value = serde_json::from_str(&content)?;
    if let Some(cookies) = data["cookies"].as_array() {
        if !cookies.is_empty() {
            cdp_root(state, "Network.setCookies", json!({"cookies": cookies}))
                .await
                .ok();
        }
    }
    if let Some(ls) = data["localStorage"].as_object() {
        for (k, v) in ls {
            let js = format!(
                "localStorage.setItem({:?}, {:?})",
                k,
                v.as_str().unwrap_or("")
            );
            cdp_session(state, "Runtime.evaluate", json!({"expression": js}))
                .await
                .ok();
        }
    }
    Ok(ok_text(format!("State loaded from {path}")))
}

pub async fn browser_list_sessions(state: &SharedState) -> Result<CallToolResult> {
    let g = state.lock().await;
    let connected = g.cdp.is_some();
    let tab = g.current_tab_id.clone();
    drop(g);
    let tab_str = tab.as_deref().unwrap_or("none");
    Ok(ok_text(format!(
        "{{\"session_id\":\"default\",\"connected\":{connected},\"current_tab_id\":\"{tab_str}\"}}"
    )))
}

pub async fn browser_close_session(
    state: &SharedState,
    _session_id: String,
) -> Result<CallToolResult> {
    // In single-session mode, this closes all tabs
    browser_close_all(state).await
}

pub async fn browser_close_all(state: &SharedState) -> Result<CallToolResult> {
    let resp = cdp_root(state, "Target.getTargets", json!({})).await?;
    if let Some(targets) = resp["targetInfos"].as_array() {
        for t in targets {
            if t["type"].as_str() == Some("page") {
                let tid = t["targetId"].as_str().unwrap_or("");
                cdp_root(state, "Target.closeTarget", json!({"targetId": tid}))
                    .await
                    .ok();
            }
        }
    }
    let mut g = state.lock().await;
    g.session_id = None;
    g.current_tab_id = None;
    g.cdp = None;
    if let Some(launched) = g.launched_browser.take() {
        launched.kill().await;
    }
    Ok(ok_text("All sessions closed"))
}
