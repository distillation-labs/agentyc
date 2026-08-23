//! Frames & storage tools: list_frames, get_frame_html, get_storage, set_storage, clear_storage.
#![allow(
    clippy::too_many_arguments,
    clippy::collapsible_if,
    clippy::collapsible_match
)]

use anyhow::Result;
use rmcp::model::CallToolResult;
use serde_json::{Value, json};

use crate::tools::{SharedState, ok_json, ok_text, page_client, page_send};

async fn cdp(state: &SharedState, method: &str, params: Value) -> Result<Value> {
    page_send(state, method, params).await
}

async fn current_origin(state: &SharedState) -> String {
    cdp(
        state,
        "Runtime.evaluate",
        json!({"expression": "location.origin", "returnByValue": true}),
    )
    .await
    .ok()
    .and_then(|response| response["result"]["value"].as_str().map(str::to_string))
    .unwrap_or_else(|| "null".to_string())
}

async fn dom_storage_command(
    state: &SharedState,
    method: &str,
    params: Value,
) -> Result<Value> {
    let (client, session_id) = page_client(state).await?;
    client
        .send::<Value>(method, params, Some(&session_id))
        .await
}

pub async fn browser_list_frames(state: &SharedState) -> Result<CallToolResult> {
    let resp = cdp(state, "Page.getFrameTree", json!({})).await?;
    let frames = collect_frames(&resp["frameTree"]);
    Ok(ok_json(&json!(frames)))
}

fn collect_frames(node: &Value) -> Vec<Value> {
    let mut result = Vec::new();
    if let Some(frame) = node.get("frame") {
        result.push(json!({
            "frame_id": frame["id"],
            "parent_id": frame["parentId"],
            "url": frame["url"],
            "name": frame["name"],
            "cross_origin": frame["securityOrigin"] != frame["url"],
        }));
    }
    if let Some(children) = node["childFrames"].as_array() {
        for child in children {
            result.extend(collect_frames(child));
        }
    }
    result
}

pub async fn browser_get_frame_html(
    state: &SharedState,
    frame_id: String,
) -> Result<CallToolResult> {
    // Try to get HTML from the target frame via CDP target attach; fall back to JS for same-origin frames.
    let js = r#"(function(){
            const frames = document.querySelectorAll('iframe,frame');
            for(const f of frames) {
                try {
                    const doc = f.contentDocument;
                    if(doc) return doc.documentElement.outerHTML;
                } catch(e) {}
            }
            return document.documentElement.outerHTML;
        })()"#
        .to_string();
    let _ = frame_id; // Used for routing in future; currently falls back to first accessible frame.
    let resp = cdp(
        state,
        "Runtime.evaluate",
        json!({"expression": js, "returnByValue": true}),
    )
    .await?;
    let html = resp["result"]["value"].as_str().unwrap_or("").to_string();
    Ok(ok_text(html))
}

pub async fn browser_get_storage(
    state: &SharedState,
    origin: Option<String>,
    storage_type: Option<String>,
    key: Option<String>,
) -> Result<CallToolResult> {
    let _ = origin;
    let types = match storage_type.as_deref() {
        Some("localStorage") => vec!["localStorage"],
        Some("sessionStorage") => vec!["sessionStorage"],
        _ => vec!["localStorage", "sessionStorage"],
    };
    let mut result = json!({});
    for storage_name in types {
        let is_local = storage_name == "localStorage";
        let origin_str = current_origin(state).await;
        let storage_id = json!({
            "securityOrigin": origin_str,
            "isLocalStorage": is_local,
        });

        // Try CDP DOMStorage first (works even on data: URLs).
        let items_resp = dom_storage_command(
            state,
            "DOMStorage.getDOMStorageItems",
            json!({"storageId": storage_id}),
        )
        .await;
        if let Ok(items) = items_resp {
            if let Some(entries) = items["entries"].as_array() {
                if let Some(key) = &key {
                    let value = entries
                        .iter()
                        .find(|entry| {
                            entry
                                .as_array()
                                .and_then(|array| array.first())
                                .and_then(Value::as_str)
                                == Some(key)
                        })
                        .and_then(|entry| entry.as_array()?.get(1).cloned());
                    result[storage_name] = value.unwrap_or(Value::Null);
                } else {
                    let object: serde_json::Map<String, Value> = entries
                        .iter()
                        .filter_map(|entry| {
                            let array = entry.as_array()?;
                            Some((
                                array.first()?.as_str()?.to_string(),
                                array.get(1)?.clone(),
                            ))
                        })
                        .collect();
                    result[storage_name] = Value::Object(object);
                }
                continue;
            }
        }

        // Fallback to JavaScript evaluation.
        let expression = if let Some(key) = &key {
            format!("{storage_name}.getItem({key:?})")
        } else {
            format!(
                r#"(function(){{const o={{}};for(let i=0;i<{storage_name}.length;i++){{const k={storage_name}.key(i);o[k]={storage_name}.getItem(k);}}return o;}})()"#
            )
        };
        let response = cdp(
            state,
            "Runtime.evaluate",
            json!({"expression": expression, "returnByValue": true}),
        )
        .await
        .unwrap_or_else(|_| json!({}));
        result[storage_name] = response["result"]["value"].clone();
    }
    Ok(ok_json(&result))
}

pub async fn browser_set_storage(
    state: &SharedState,
    origin: String,
    storage_type: String,
    key: String,
    value: String,
) -> Result<CallToolResult> {
    let _ = origin;
    let is_local = storage_type != "sessionStorage";
    let storage_name = if is_local {
        "localStorage"
    } else {
        "sessionStorage"
    };
    let origin_str = current_origin(state).await;
    let storage_id = json!({"securityOrigin": origin_str, "isLocalStorage": is_local});
    let set_result = dom_storage_command(
        state,
        "DOMStorage.setDOMStorageItem",
        json!({"storageId": storage_id, "key": key, "value": value}),
    )
    .await;

    if set_result.is_err() {
        let expression = format!("{storage_name}.setItem({key:?}, {value:?})");
        cdp(state, "Runtime.evaluate", json!({"expression": expression})).await?;
    }
    Ok(ok_text(format!("Set {storage_name}[{key}]")))
}

pub async fn browser_clear_storage(
    state: &SharedState,
    origin: String,
    storage_type: Option<String>,
    key: Option<String>,
) -> Result<CallToolResult> {
    let _ = origin;
    let types: Vec<(&str, bool)> = match storage_type.as_deref() {
        Some("localStorage") => vec![("localStorage", true)],
        Some("sessionStorage") => vec![("sessionStorage", false)],
        _ => vec![("localStorage", true), ("sessionStorage", false)],
    };
    for (storage_name, is_local) in types {
        let origin_str = current_origin(state).await;
        let storage_id = json!({"securityOrigin": origin_str, "isLocalStorage": is_local});
        let cdp_result = if let Some(key) = &key {
            dom_storage_command(
                state,
                "DOMStorage.removeDOMStorageItem",
                json!({"storageId": storage_id, "key": key}),
            )
            .await
        } else {
            dom_storage_command(
                state,
                "DOMStorage.clear",
                json!({"storageId": storage_id}),
            )
            .await
        };

        if cdp_result.is_err() {
            let expression = if let Some(key) = &key {
                format!("{storage_name}.removeItem({key:?})")
            } else {
                format!("{storage_name}.clear()")
            };
            cdp(state, "Runtime.evaluate", json!({"expression": expression})).await?;
        }
    }
    Ok(ok_text("Storage cleared"))
}
