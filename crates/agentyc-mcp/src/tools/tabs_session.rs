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

use crate::tools::{
    SharedState, browser_client, ok_json, ok_text, page_send, runtime_handle, tab_id_from,
};

async fn cdp_root(state: &SharedState, method: &str, params: Value) -> Result<Value> {
    let cdp = browser_client(state).await?;
    cdp.send::<Value>(method, params, None).await
}

async fn cdp_session(state: &SharedState, method: &str, params: Value) -> Result<Value> {
    page_send(state, method, params).await
}

pub async fn browser_new_tab(state: &SharedState, url: Option<String>) -> Result<CallToolResult> {
    let runtime = runtime_handle(state).await?;
    let page = runtime.new_tab(url.as_deref()).await?;
    Ok(ok_text(format!("New tab created: {}", page.tab_id)))
}

pub async fn browser_list_tabs(state: &SharedState) -> Result<CallToolResult> {
    let runtime = runtime_handle(state).await?;
    Ok(ok_json(&serde_json::to_value(runtime.list_tabs().await?)?))
}

pub async fn browser_switch_tab(state: &SharedState, tab_id: String) -> Result<CallToolResult> {
    let runtime = runtime_handle(state).await?;
    let page = runtime.switch_tab(&tab_id).await?;
    Ok(ok_text(format!("Switched to tab {}", page.tab_id)))
}

pub async fn browser_close_tab(state: &SharedState, tab_id: String) -> Result<CallToolResult> {
    let runtime = runtime_handle(state).await?;
    runtime.close_tab(&tab_id).await?;
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
            .map_or(&[][..], |targets| targets.as_slice())
            .iter()
            .filter(|t| t["type"].as_str() == Some("page"))
            .filter_map(|t| t["targetId"].as_str().map(str::to_string))
            .collect()
    };
    loop {
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
        let resp = cdp_root(state, "Target.getTargets", json!({})).await?;
        for target in resp["targetInfos"]
            .as_array()
            .map_or(&[][..], |targets| targets.as_slice())
        {
            if target["type"].as_str() != Some("page") {
                continue;
            }
            let target_id = target["targetId"].as_str().unwrap_or("").to_string();
            if initial_tabs.contains(&target_id) {
                continue;
            }
            let url = target["url"].as_str().unwrap_or("");
            let matches = if let Some(sub) = &url_substring {
                url.contains(sub.as_str())
            } else if let Some(regex) = &re {
                regex.is_match(url)
            } else {
                true
            };
            if matches {
                if switch_focus.unwrap_or(true) {
                    runtime_handle(state)
                        .await?
                        .switch_tab(&tab_id_from(&target_id))
                        .await?;
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
    // Try session-level first, fall back to root for older Chrome versions.
    if cdp_session(state, "Network.setCookies", json!({"cookies": cookies}))
        .await
        .is_err()
    {
        cdp_root(state, "Network.setCookies", json!({"cookies": cookies})).await?;
    }
    Ok(ok_text(format!("Set {} cookie(s)", cookies.len())))
}

pub async fn browser_clear_cookies(
    state: &SharedState,
    name: Option<String>,
) -> Result<CallToolResult> {
    if let Some(name) = name {
        if cdp_session(state, "Network.deleteCookies", json!({"name": name}))
            .await
            .is_err()
            && cdp_root(state, "Network.deleteCookies", json!({"name": name}))
                .await
                .is_err()
        {
            let expression = format!(
                "document.cookie = {:?} + '=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;'",
                name
            );
            cdp_session(state, "Runtime.evaluate", json!({"expression": expression}))
                .await
                .ok();
        }
        return Ok(ok_text(format!("Deleted cookie: {name}")));
    }

    if cdp_session(state, "Network.clearBrowserCookies", json!({}))
        .await
        .is_err()
        && cdp_root(state, "Network.clearBrowserCookies", json!({}))
            .await
            .is_err()
    {
        let expression = "document.cookie.split(';').forEach(c=>{const k=c.trim().split('=')[0];document.cookie=k+'=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/;';})";
        cdp_session(state, "Runtime.evaluate", json!({"expression": expression}))
            .await
            .ok();
    }
    Ok(ok_text("All cookies cleared"))
}

pub async fn browser_grant_permissions(
    state: &SharedState,
    permissions: Vec<String>,
    origin: Option<String>,
) -> Result<CallToolResult> {
    let mut params = json!({"permissions": permissions});
    if let Some(origin) = origin {
        params["origin"] = json!(origin);
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
    if let Some(language) = accept_language {
        params["acceptLanguage"] = json!(language);
    }
    if let Some(platform) = platform {
        params["platform"] = json!(platform);
    }
    cdp_session(state, "Network.setUserAgentOverride", params).await?;
    Ok(ok_text("User agent set"))
}

pub async fn browser_set_timezone(
    state: &SharedState,
    timezone_id: Option<String>,
) -> Result<CallToolResult> {
    let timezone = timezone_id.unwrap_or_default();
    cdp_session(
        state,
        "Emulation.setTimezoneOverride",
        json!({"timezoneId": timezone}),
    )
    .await?;
    Ok(ok_text(if timezone.is_empty() {
        "Timezone cleared".into()
    } else {
        format!("Timezone set to {timezone}")
    }))
}

pub async fn browser_set_locale(
    state: &SharedState,
    locale: Option<String>,
) -> Result<CallToolResult> {
    let locale = locale.unwrap_or_default();
    cdp_session(
        state,
        "Emulation.setLocaleOverride",
        json!({"locale": locale}),
    )
    .await?;
    Ok(ok_text(if locale.is_empty() {
        "Locale cleared".into()
    } else {
        format!("Locale set to {locale}")
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
    if let Some(value) = color_scheme {
        features.push(json!({"name":"prefers-color-scheme","value":value}));
    }
    if let Some(value) = reduced_motion {
        features.push(json!({"name":"prefers-reduced-motion","value":value}));
    }
    if let Some(value) = forced_colors {
        features.push(json!({"name":"forced-colors","value":value}));
    }
    cdp_session(
        state,
        "Emulation.setEmulatedMedia",
        json!({"media": media.unwrap_or_default(), "features": features}),
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
    let local_storage_resp = cdp_session(
        state,
        "Runtime.evaluate",
        json!({
            "expression": "(function(){const o={};for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);o[k]=localStorage.getItem(k);}return o;})()",
            "returnByValue": true,
        }),
    )
    .await
    .unwrap_or(Value::Null);
    let data = json!({
        "cookies": cookies_resp["cookies"],
        "localStorage": local_storage_resp["result"]["value"],
    });
    if let Some(parent) = std::path::Path::new(&save_path).parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&save_path, serde_json::to_string_pretty(&data)?)?;
    Ok(ok_text(format!("State saved to {save_path}")))
}

pub async fn browser_load_state(state: &SharedState, path: String) -> Result<CallToolResult> {
    let content = std::fs::read_to_string(&path)?;
    let data: Value = serde_json::from_str(&content)?;
    if let Some(cookies) = data["cookies"].as_array()
        && !cookies.is_empty()
    {
        cdp_root(state, "Network.setCookies", json!({"cookies": cookies}))
            .await
            .ok();
    }
    if let Some(local_storage) = data["localStorage"].as_object() {
        for (key, value) in local_storage {
            let expression = format!(
                "localStorage.setItem({key:?}, {:?})",
                value.as_str().unwrap_or("")
            );
            cdp_session(state, "Runtime.evaluate", json!({"expression": expression}))
                .await
                .ok();
        }
    }
    Ok(ok_text(format!("State loaded from {path}")))
}

pub async fn browser_list_sessions(state: &SharedState) -> Result<CallToolResult> {
    let runtime = { state.lock().await.runtime.clone() };
    let connected = runtime.is_some();
    let tab = if let Some(runtime) = runtime {
        runtime
            .session()
            .active_page()
            .await
            .ok()
            .map(|page| page.tab_id)
    } else {
        None
    };
    let tab = tab.as_deref().unwrap_or("none");
    Ok(ok_text(format!(
        "{{\"session_id\":\"default\",\"connected\":{connected},\"current_tab_id\":\"{tab}\"}}"
    )))
}

pub async fn browser_close_session(
    state: &SharedState,
    _session_id: String,
) -> Result<CallToolResult> {
    browser_close_all(state).await
}

pub async fn browser_close_all(state: &SharedState) -> Result<CallToolResult> {
    let runtime = { state.lock().await.runtime.clone() };
    if let Some(runtime) = runtime {
        runtime.close_all().await?;
    }
    let mut state = state.lock().await;
    state.runtime = None;
    state.dialog_handler_started = false;
    state.capture_started = false;
    state.clear_browser_scoped_state();
    Ok(ok_text("All sessions closed"))
}
