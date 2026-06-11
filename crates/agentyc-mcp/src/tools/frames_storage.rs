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

pub async fn browser_get_frame_html(state: &SharedState, _frame_id: String) -> Result<CallToolResult> {
    let js = r#"(function(){
        for(const frame of document.querySelectorAll('iframe,frame')) {
            try { if(frame.contentDocument) return frame.contentDocument.documentElement.outerHTML; } catch(e) {}
        }
        return document.documentElement.outerHTML;
    })()"#;
    let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
    let html = resp["result"]["value"].as_str().unwrap_or("").to_string();
    Ok(ok_text(html))
}

pub async fn browser_get_storage(
    state: &SharedState,
    _origin: Option<String>,
    storage_type: Option<String>,
    key: Option<String>,
) -> Result<CallToolResult> {
    let mut result = json!({});
    let types = match storage_type.as_deref() {
        Some("localStorage") => vec!["localStorage"],
        Some("sessionStorage") => vec!["sessionStorage"],
        _ => vec!["localStorage", "sessionStorage"],
    };
    for st in types {
        let js = if let Some(k) = &key {
            format!("{st}.getItem({:?})", k)
        } else {
            format!(r#"(function(){{
                const o={{}};
                for(let i=0;i<{st}.length;i++){{
                    const k={st}.key(i); o[k]={st}.getItem(k);
                }}
                return o;
            }})()"#)
        };
        let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
        result[st] = resp["result"]["value"].clone();
    }
    Ok(ok_json(&result))
}

pub async fn browser_set_storage(
    state: &SharedState,
    _origin: String,
    storage_type: String,
    key: String,
    value: String,
) -> Result<CallToolResult> {
    let st = if storage_type == "sessionStorage" { "sessionStorage" } else { "localStorage" };
    let js = format!("{st}.setItem({:?}, {:?})", key, value);
    cdp(state, "Runtime.evaluate", json!({"expression": js})).await?;
    Ok(ok_text(format!("Set {st}[{key}]")))
}

pub async fn browser_clear_storage(
    state: &SharedState,
    _origin: String,
    storage_type: Option<String>,
    key: Option<String>,
) -> Result<CallToolResult> {
    let types: Vec<&str> = match storage_type.as_deref() {
        Some("localStorage") => vec!["localStorage"],
        Some("sessionStorage") => vec!["sessionStorage"],
        _ => vec!["localStorage", "sessionStorage"],
    };
    for st in types {
        let js = if let Some(k) = &key {
            format!("{st}.removeItem({:?})", k)
        } else {
            format!("{st}.clear()")
        };
        cdp(state, "Runtime.evaluate", json!({"expression": js})).await?;
    }
    Ok(ok_text("Storage cleared"))
}
