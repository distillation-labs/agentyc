//! Observability tools: console logs, network log, mocks, conditions,
#![allow(
    clippy::too_many_arguments,
    clippy::collapsible_if,
    clippy::collapsible_match
)]
//! replay, debug bundle, downloads, trace, inspect_network_entry.

use anyhow::{Result, anyhow};
use base64::Engine;
use rmcp::model::CallToolResult;
use serde_json::{Value, json};
use uuid::Uuid;

use crate::tools::{NetworkMock, SharedState, browser_client, ok_json, ok_text, page_send};

async fn cdp_root(state: &SharedState, method: &str, params: Value) -> Result<Value> {
    browser_client(state)
        .await?
        .send::<Value>(method, params, None)
        .await
}

async fn cdp_session(state: &SharedState, method: &str, params: Value) -> Result<Value> {
    page_send(state, method, params).await
}

pub async fn browser_get_console_logs(
    state: &SharedState,
    level: Option<String>,
    max_entries: Option<usize>,
) -> Result<CallToolResult> {
    crate::tools::ensure_capture(state).await;
    let lvl = level.as_deref().unwrap_or("all");
    let max = max_entries.unwrap_or(50);
    let g = state.lock().await;
    let entries: Vec<Value> = g
        .console_logs
        .iter()
        .filter(|e| lvl == "all" || e.level == lvl)
        .rev()
        .take(max)
        .map(|e| json!({"level": e.level, "text": e.text, "timestamp": e.timestamp}))
        .collect();
    Ok(ok_json(&json!(entries)))
}

pub async fn browser_get_network_log(
    state: &SharedState,
    type_filter: Option<String>,
    status_filter: Option<String>,
    max_entries: Option<usize>,
    include_headers: Option<bool>,
) -> Result<CallToolResult> {
    crate::tools::ensure_capture(state).await;
    let tf = type_filter.as_deref().unwrap_or("all").to_lowercase();
    let sf = status_filter.as_deref().unwrap_or("all").to_lowercase();
    let max = max_entries.unwrap_or(50);
    let inc_headers = include_headers.unwrap_or(false);
    let g = state.lock().await;
    let entries: Vec<Value> = g
        .network_log
        .iter()
        .filter(|e| tf == "all" || e.resource_type.to_lowercase() == tf)
        .filter(|e| match sf.as_str() {
            "errors" => e.status.map(|s| s >= 400).unwrap_or(false),
            "success" => e.status.map(|s| s < 400).unwrap_or(false),
            _ => true,
        })
        .rev()
        .take(max)
        .map(|e| {
            let mut v = json!({
                "request_id": e.request_id,
                "url": e.url,
                "method": e.method,
                "status": e.status,
                "resource_type": e.resource_type,
                "timestamp": e.timestamp,
                "duration_ms": e.duration_ms,
            });
            if inc_headers {
                if let Some(rh) = &e.request_headers {
                    v["request_headers"] = rh.clone();
                }
                if let Some(rh) = &e.response_headers {
                    v["response_headers"] = rh.clone();
                }
            }
            v
        })
        .collect();
    Ok(ok_json(&json!(entries)))
}

pub async fn browser_inspect_network_entry(
    state: &SharedState,
    request_id: Option<String>,
    url_substring: Option<String>,
    url_regex: Option<String>,
    method: Option<String>,
    resource_type: Option<String>,
    status: Option<u32>,
    include_headers: Option<bool>,
    include_request_body: Option<bool>,
    include_response_body: Option<bool>,
    max_body_bytes: Option<usize>,
    decode_json: Option<bool>,
) -> Result<CallToolResult> {
    let re = url_regex
        .as_ref()
        .map(|r| regex::Regex::new(r))
        .transpose()?;
    let g = state.lock().await;
    let entry = g
        .network_log
        .iter()
        .rev()
        .find(|e| {
            if let Some(rid) = &request_id {
                if e.request_id != *rid {
                    return false;
                }
            }
            if let Some(sub) = &url_substring {
                if !e.url.contains(sub.as_str()) {
                    return false;
                }
            }
            if let Some(r) = &re {
                if !r.is_match(&e.url) {
                    return false;
                }
            }
            if let Some(m) = &method {
                if e.method.to_uppercase() != m.to_uppercase() {
                    return false;
                }
            }
            if let Some(rt) = &resource_type {
                if e.resource_type.to_lowercase() != rt.to_lowercase() {
                    return false;
                }
            }
            if let Some(s) = status {
                if e.status != Some(s) {
                    return false;
                }
            }
            true
        })
        .cloned();

    let e = entry.ok_or_else(|| anyhow!("No matching network entry found"))?;
    let max_bytes = max_body_bytes.unwrap_or(2048);

    let mut v = json!({
        "request_id": e.request_id,
        "url": e.url,
        "method": e.method,
        "status": e.status,
        "resource_type": e.resource_type,
        "duration_ms": e.duration_ms,
    });
    if include_headers.unwrap_or(false) {
        if let Some(rh) = &e.request_headers {
            v["request_headers"] = rh.clone();
        }
        if let Some(rh) = &e.response_headers {
            v["response_headers"] = rh.clone();
        }
    }
    if include_request_body.unwrap_or(true) {
        if let Some(body) = &e.request_body {
            let truncated = &body[..body.len().min(max_bytes)];
            v["request_body"] = if decode_json.unwrap_or(true) {
                serde_json::from_str(truncated).unwrap_or_else(|_| json!(truncated))
            } else {
                json!(truncated)
            };
        }
    }
    if include_response_body.unwrap_or(true) {
        if let Some(body) = &e.response_body {
            let truncated = &body[..body.len().min(max_bytes)];
            v["response_body"] = if decode_json.unwrap_or(true) {
                serde_json::from_str(truncated).unwrap_or_else(|_| json!(truncated))
            } else {
                json!(truncated)
            };
        }
    }
    Ok(ok_json(&v))
}

pub async fn browser_add_network_mock(
    state: &SharedState,
    url_substring: Option<String>,
    url_regex: Option<String>,
    method: Option<String>,
    resource_type: Option<String>,
    action: Option<String>,
    status: Option<u32>,
    headers: Option<Value>,
    body: Option<String>,
    error_reason: Option<String>,
) -> Result<CallToolResult> {
    let mock_id = Uuid::new_v4().to_string()[..8].to_string();
    let mock = NetworkMock {
        mock_id: mock_id.clone(),
        url_substring,
        url_regex,
        method,
        resource_type,
        action: action.unwrap_or_else(|| "fulfill".into()),
        status: status.unwrap_or(200),
        headers: headers.unwrap_or(json!({})),
        body: body.unwrap_or_default(),
        error_reason: error_reason.unwrap_or_else(|| "Failed".into()),
        match_count: 0,
    };

    // Enable Fetch interception and spawn listener if this is the first mock
    let is_first = state.lock().await.mocks.is_empty();
    cdp_session(
        state,
        "Fetch.enable",
        json!({"patterns": [{"urlPattern": "*"}]}),
    )
    .await
    .ok();
    state.lock().await.mocks.push(mock);

    if is_first {
        let state_clone = std::sync::Arc::clone(state);
        let runtime = crate::tools::runtime_handle(state).await.ok();
        if let Some(runtime) = runtime {
            let client = runtime.session().client();
            let session = runtime.session();
            tokio::spawn(async move {
                let mut rx = client.subscribe_with_session("Fetch.requestPaused").await;
                while let Ok(event) = rx.recv().await {
                    let Some(session_id) = event.session_id else {
                        continue;
                    };
                    let params = event.params;
                    let Some(request_id) = params["requestId"].as_str().map(str::to_string) else {
                        continue;
                    };
                    let url = params["request"]["url"].as_str().unwrap_or("");
                    let req_method = params["request"]["method"].as_str().unwrap_or("");
                    let resource_type = params["resourceType"].as_str().unwrap_or("");
                    let matched_mock = {
                        let g = state_clone.lock().await;
                        g.mocks
                            .iter()
                            .find(|m| {
                                let url_match = m
                                    .url_substring
                                    .as_ref()
                                    .map(|s| url.contains(s))
                                    .or_else(|| {
                                        m.url_regex.as_ref().and_then(|p| {
                                            regex::Regex::new(p).ok().map(|r| r.is_match(url))
                                        })
                                    })
                                    .unwrap_or(true);
                                let method_match = m
                                    .method
                                    .as_ref()
                                    .map(|v| v.eq_ignore_ascii_case(req_method))
                                    .unwrap_or(true);
                                let type_match = m
                                    .resource_type
                                    .as_ref()
                                    .map(|v| v.eq_ignore_ascii_case(resource_type))
                                    .unwrap_or(true);
                                url_match && method_match && type_match
                            })
                            .cloned()
                    };
                    let command = if let Some(mock) = matched_mock {
                        if let Some(m) = state_clone
                            .lock()
                            .await
                            .mocks
                            .iter_mut()
                            .find(|m| m.mock_id == mock.mock_id)
                        {
                            m.match_count += 1;
                        }
                        if mock.action == "abort" {
                            (
                                "Fetch.failRequest",
                                json!({"requestId": request_id, "errorReason": mock.error_reason}),
                            )
                        } else {
                            let headers: Vec<Value> = mock.headers.as_object().map(|obj| obj.iter()
                                .map(|(k, v)| json!({"name": k, "value": v.as_str().unwrap_or("")})).collect()).unwrap_or_default();
                            let body = base64::engine::general_purpose::STANDARD
                                .encode(mock.body.as_bytes());
                            (
                                "Fetch.fulfillRequest",
                                json!({"requestId": request_id, "responseCode": mock.status, "responseHeaders": headers, "body": body}),
                            )
                        }
                    } else {
                        ("Fetch.continueRequest", json!({"requestId": request_id}))
                    };
                    session
                        .send_page_with_session::<Value>(&session_id, command.0, command.1)
                        .await
                        .ok();
                }
            });
        }
    }

    Ok(ok_text(format!("Mock added: {mock_id}")))
}

pub async fn browser_remove_network_mock(
    state: &SharedState,
    mock_id: Option<String>,
) -> Result<CallToolResult> {
    let mut g = state.lock().await;
    if let Some(id) = mock_id {
        g.mocks.retain(|m| m.mock_id != id);
        Ok(ok_text(format!("Mock {id} removed")))
    } else {
        g.mocks.clear();
        Ok(ok_text("All mocks removed"))
    }
}

pub async fn browser_list_network_mocks(state: &SharedState) -> Result<CallToolResult> {
    let g = state.lock().await;
    let mocks: Vec<Value> = g
        .mocks
        .iter()
        .map(|m| {
            json!({
                "mock_id": m.mock_id,
                "url_substring": m.url_substring,
                "url_regex": m.url_regex,
                "method": m.method,
                "action": m.action,
                "status": m.status,
                "match_count": m.match_count,
            })
        })
        .collect();
    Ok(ok_json(&json!(mocks)))
}

pub async fn browser_set_network_conditions(
    state: &SharedState,
    offline: Option<bool>,
    latency_ms: Option<f64>,
    download_kbps: Option<f64>,
    upload_kbps: Option<f64>,
    connection_type: Option<String>,
    reset: Option<bool>,
) -> Result<CallToolResult> {
    if reset.unwrap_or(false) {
        cdp_session(
            state,
            "Network.emulateNetworkConditions",
            json!({
                "offline": false, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1,
            }),
        )
        .await?;
        return Ok(ok_text("Network conditions reset"));
    }
    cdp_session(
        state,
        "Network.emulateNetworkConditions",
        json!({
            "offline": offline.unwrap_or(false),
            "latency": latency_ms.unwrap_or(0.0),
            "downloadThroughput": download_kbps.map(|k| k * 1024.0 / 8.0).unwrap_or(-1.0),
            "uploadThroughput": upload_kbps.map(|k| k * 1024.0 / 8.0).unwrap_or(-1.0),
            "connectionType": connection_type.unwrap_or_default(),
        }),
    )
    .await?;
    Ok(ok_text("Network conditions set"))
}

pub async fn browser_get_network_conditions(_state: &SharedState) -> Result<CallToolResult> {
    Ok(ok_text(
        "No active network conditions (use browser_set_network_conditions to configure)",
    ))
}

pub async fn browser_replay_request(
    state: &SharedState,
    request_id: Option<String>,
    url_substring: Option<String>,
    url_regex: Option<String>,
    method: Option<String>,
    body: Option<String>,
    headers: Option<Value>,
) -> Result<CallToolResult> {
    let re = url_regex
        .as_ref()
        .map(|r| regex::Regex::new(r))
        .transpose()?;
    let entry = {
        let g = state.lock().await;
        g.network_log
            .iter()
            .rev()
            .find(|e| {
                if let Some(rid) = &request_id {
                    if e.request_id != *rid {
                        return false;
                    }
                }
                if let Some(sub) = &url_substring {
                    if !e.url.contains(sub.as_str()) {
                        return false;
                    }
                }
                if let Some(r) = &re {
                    if !r.is_match(&e.url) {
                        return false;
                    }
                }
                true
            })
            .cloned()
    };
    let e = entry.ok_or_else(|| anyhow!("No matching entry to replay"))?;
    let replay_method = method.as_deref().unwrap_or(&e.method);
    // Build headers object for the fetch call
    let headers_js = if let Some(h) = &headers {
        format!(
            ", headers: {}",
            serde_json::to_string(h).unwrap_or_else(|_| "{}".into())
        )
    } else {
        String::new()
    };
    let js = format!(
        r#"fetch({:?}, {{method:{:?}{}{}}})).then(r=>r.text())"#,
        e.url,
        replay_method,
        headers_js,
        body.as_deref()
            .map(|b| format!(", body:{b:?}"))
            .unwrap_or_default(),
    );
    let resp = cdp_session(
        state,
        "Runtime.evaluate",
        json!({
            "expression": js, "awaitPromise": true, "returnByValue": true,
        }),
    )
    .await?;
    Ok(ok_json(&resp["result"]["value"]))
}

pub async fn browser_export_debug_bundle(
    state: &SharedState,
    state_mode: Option<String>,
    focus_ref: Option<String>,
    since_hash: Option<String>,
    include_screenshot: Option<bool>,
    include_headers: Option<bool>,
    include_html: Option<bool>,
    html_selector: Option<String>,
    console_max_entries: Option<usize>,
    network_max_entries: Option<usize>,
    network_status_filter: Option<String>,
) -> Result<CallToolResult> {
    let mode = state_mode.unwrap_or_else(|| "min".into());
    let browser_state = crate::tools::state_tools::browser_get_state(
        state,
        Some(mode),
        focus_ref,
        since_hash,
        include_screenshot,
    )
    .await;

    let console = browser_get_console_logs(state, None, console_max_entries)
        .await
        .ok();
    let network = browser_get_network_log(
        state,
        None,
        network_status_filter,
        network_max_entries,
        include_headers,
    )
    .await
    .ok();

    let html = if include_html.unwrap_or(false) {
        crate::tools::state_tools::browser_get_html(state, html_selector)
            .await
            .ok()
            .map(|r| {
                r.content
                    .first()
                    .and_then(|c| {
                        if let rmcp::model::RawContent::Text(t) = &c.raw {
                            Some(t.text.clone())
                        } else {
                            None
                        }
                    })
                    .unwrap_or_default()
            })
    } else {
        None
    };

    let console_data = console.and_then(|r| {
        r.content.first().and_then(|c| {
            if let rmcp::model::RawContent::Text(t) = &c.raw {
                serde_json::from_str::<Value>(&t.text).ok()
            } else {
                None
            }
        })
    });
    let network_data = network.and_then(|r| {
        r.content.first().and_then(|c| {
            if let rmcp::model::RawContent::Text(t) = &c.raw {
                serde_json::from_str::<Value>(&t.text).ok()
            } else {
                None
            }
        })
    });
    let bundle = json!({
        "console": console_data,
        "network": network_data,
        "state": browser_state.as_ref().ok().and_then(|r| {
            r.content.first().and_then(|c| {
                if let rmcp::model::RawContent::Text(t) = &c.raw {
                    serde_json::from_str::<Value>(&t.text).ok()
                } else { None }
            })
        }),
        "html": html,
    });

    let mut contents = vec![rmcp::model::Content::text(
        serde_json::to_string_pretty(&bundle).unwrap_or_default(),
    )];
    // Append screenshot if included
    if let Ok(ref sr) = browser_state {
        for c in &sr.content {
            if let rmcp::model::RawContent::Image(_) = &c.raw {
                contents.push(c.clone());
            }
        }
    }

    Ok(CallToolResult::success(contents))
}

pub async fn browser_get_downloads(state: &SharedState) -> Result<CallToolResult> {
    let g = state.lock().await;
    let dl: Vec<Value> = g
        .downloads
        .iter()
        .map(|d| {
            json!({
                "filename": d.filename,
                "path": d.path,
                "size": d.size,
                "mime_type": d.mime_type,
                "completed": d.completed,
            })
        })
        .collect();
    Ok(ok_json(&json!(dl)))
}

pub async fn browser_wait_for_download(
    state: &SharedState,
    expected_name: Option<String>,
    timeout_seconds: Option<f64>,
) -> Result<CallToolResult> {
    let timeout = std::time::Duration::from_secs_f64(timeout_seconds.unwrap_or(10.0));
    let deadline = tokio::time::Instant::now() + timeout;
    loop {
        {
            let g = state.lock().await;
            let dl = g.downloads.iter().find(|d| {
                d.completed
                    && expected_name
                        .as_ref()
                        .map(|n| &d.filename == n)
                        .unwrap_or(true)
            });
            if let Some(d) = dl {
                return Ok(ok_json(
                    &json!({"filename": d.filename, "path": d.path, "size": d.size}),
                ));
            }
        }
        if tokio::time::Instant::now() >= deadline {
            return Err(anyhow!("Timeout waiting for download"));
        }
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
    }
}

#[allow(unused_variables)]
pub async fn browser_clear_logs(
    state: &SharedState,
    console: Option<bool>,
    network: Option<bool>,
) -> Result<CallToolResult> {
    let mut g = state.lock().await;
    if console.unwrap_or(true) {
        g.console_logs.clear();
    }
    if network.unwrap_or(true) {
        g.network_log.clear();
    }
    Ok(ok_text("Logs cleared"))
}

pub async fn browser_start_trace(
    state: &SharedState,
    categories: Option<String>,
) -> Result<CallToolResult> {
    let cats = categories.unwrap_or_else(|| {
        "-*,disabled-by-default-devtools.timeline,devtools.timeline,loading,net,network".into()
    });
    cdp_root(
        state,
        "Tracing.start",
        json!({"categories": cats, "transferMode": "ReturnAsStream"}),
    )
    .await?;
    state.lock().await.tracing = true;
    Ok(ok_text("Trace started"))
}

pub async fn browser_stop_trace(state: &SharedState) -> Result<CallToolResult> {
    cdp_root(state, "Tracing.end", json!({})).await?;
    let events = std::mem::take(&mut state.lock().await.trace_events);
    state.lock().await.tracing = false;
    Ok(ok_json(
        &json!({"events": events, "event_count": events.len()}),
    ))
}
