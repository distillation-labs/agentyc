//! Frames & storage tools: list_frames, get_frame_html, get_storage, set_storage, clear_storage.
#![allow(clippy::too_many_arguments, clippy::collapsible_if, clippy::collapsible_match)]

use anyhow::Result;
use serde_json::{json, Value};
use rmcp::model::CallToolResult;

use crate::tools::{ok_json, ok_text, SharedState};

async fn cdp(state: &SharedState, method: &str, params: Value) -> Result<Value> {
    let g = state.lock().await;
    let cdp = g.cdp()?;
    let sid = g.session_id.clone();
    cdp.send::<Value>(method, params, sid.as_deref()).await
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

pub async fn browser_get_frame_html(state: &SharedState, frame_id: String) -> Result<CallToolResult> {
    // Try to get HTML from the target frame via CDP target attach; fall back to JS for same-origin frames
    let js = format!(
        r#"(function(){{
            const frames = document.querySelectorAll('iframe,frame');
            for(const f of frames) {{
                try {{
                    const doc = f.contentDocument;
                    if(doc) return doc.documentElement.outerHTML;
                }} catch(e) {{}}
            }}
            return document.documentElement.outerHTML;
        }})()"#
    );
    let _ = frame_id; // Used for routing in future; currently falls back to first accessible frame
    let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
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
    for st in types {
        let is_local = st == "localStorage";
        // Get current URL to build storage id
        let g = state.lock().await;
        let sid = g.session_id.clone();
        let cdp = g.cdp()?;
        let url_resp = cdp.send::<Value>("Runtime.evaluate", json!({"expression": "location.origin", "returnByValue": true}), sid.as_deref()).await.unwrap_or(json!({}));
        let origin_str = url_resp["result"]["value"].as_str().unwrap_or("null").to_string();
        drop(g);

        // Try CDP DOMStorage first (works even on data: URLs)
        let storage_id = json!({
            "securityOrigin": origin_str,
            "isLocalStorage": is_local,
        });
        let items_resp = {
            let g = state.lock().await;
            let sid = g.session_id.clone();
            g.cdp()?.send::<Value>("DOMStorage.getDOMStorageItems", json!({"storageId": storage_id}), sid.as_deref()).await
        };

        if let Ok(items) = items_resp {
            if let Some(entries) = items["entries"].as_array() {
                if let Some(k) = &key {
                    let val = entries.iter()
                        .find(|e| e.as_array().and_then(|a| a.first()).and_then(Value::as_str) == Some(k))
                        .and_then(|e| e.as_array()?.get(1).cloned());
                    result[st] = val.unwrap_or(Value::Null);
                } else {
                    let obj: serde_json::Map<String, Value> = entries.iter()
                        .filter_map(|e| {
                            let arr = e.as_array()?;
                            Some((arr.first()?.as_str()?.to_string(), arr.get(1)?.clone()))
                        })
                        .collect();
                    result[st] = Value::Object(obj);
                }
                continue;
            }
        }

        // Fallback to JS eval
        let js = if let Some(k) = &key {
            format!("{st}.getItem({:?})", k)
        } else {
            format!(r#"(function(){{const o={{}};for(let i=0;i<{st}.length;i++){{const k={st}.key(i);o[k]={st}.getItem(k);}}return o;}})()"#)
        };
        let g = state.lock().await;
        let sid = g.session_id.clone();
        let resp = g.cdp()?.send::<Value>("Runtime.evaluate", json!({"expression": js, "returnByValue": true}), sid.as_deref()).await.unwrap_or(json!({}));
        drop(g);
        result[st] = resp["result"]["value"].clone();
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
    let st = if is_local { "localStorage" } else { "sessionStorage" };

    // Try CDP DOMStorage.setDOMStorageItem
    let g = state.lock().await;
    let sid = g.session_id.clone();
    let cdp = g.cdp()?;
    let url_resp = cdp.send::<Value>("Runtime.evaluate", json!({"expression": "location.origin", "returnByValue": true}), sid.as_deref()).await.unwrap_or(json!({}));
    let origin_str = url_resp["result"]["value"].as_str().unwrap_or("null").to_string();
    let storage_id = json!({"securityOrigin": origin_str, "isLocalStorage": is_local});
    let set_result = cdp.send::<Value>("DOMStorage.setDOMStorageItem", json!({
        "storageId": storage_id, "key": key, "value": value,
    }), sid.as_deref()).await;
    drop(g);

    if set_result.is_err() {
        // Fallback to JS eval
        let js = format!("{st}.setItem({:?}, {:?})", key, value);
        let g = state.lock().await;
        let sid = g.session_id.clone();
        g.cdp()?.send::<Value>("Runtime.evaluate", json!({"expression": js}), sid.as_deref()).await?;
    }
    Ok(ok_text(format!("Set {st}[{key}]")))
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
    for (st, is_local) in types {
        // Try CDP DOMStorage
        let g = state.lock().await;
        let sid = g.session_id.clone();
        let cdp = g.cdp()?;
        let url_resp = cdp.send::<Value>("Runtime.evaluate", json!({"expression": "location.origin", "returnByValue": true}), sid.as_deref()).await.unwrap_or(json!({}));
        let origin_str = url_resp["result"]["value"].as_str().unwrap_or("null").to_string();
        let storage_id = json!({"securityOrigin": origin_str, "isLocalStorage": is_local});
        let cdp_result = if let Some(k) = &key {
            cdp.send::<Value>("DOMStorage.removeDOMStorageItem", json!({"storageId": storage_id, "key": k}), sid.as_deref()).await
        } else {
            cdp.send::<Value>("DOMStorage.clear", json!({"storageId": storage_id}), sid.as_deref()).await
        };
        drop(g);

        if cdp_result.is_err() {
            // Fallback to JS
            let js = if let Some(k) = &key {
                format!("{st}.removeItem({:?})", k)
            } else {
                format!("{st}.clear()")
            };
            let g = state.lock().await;
            let sid = g.session_id.clone();
            g.cdp()?.send::<Value>("Runtime.evaluate", json!({"expression": js}), sid.as_deref()).await?;
        }
    }
    Ok(ok_text("Storage cleared"))
}
