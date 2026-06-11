//! Inspection tools: extract_content, find_elements, search_page,
#![allow(clippy::too_many_arguments, clippy::collapsible_if, clippy::collapsible_match)]
//! wait_for_element, get_focused_element, get_attribute, evaluate.

use anyhow::{anyhow, Result};
use serde_json::{json, Value};
use rmcp::model::CallToolResult;

use crate::tools::{ok_json, ok_text, parse_ref, SharedState};

async fn cdp(state: &SharedState, method: &str, params: Value) -> Result<Value> {
    let g = state.lock().await;
    let cdp = g.cdp()?;
    let sid = g.session_id.clone();
    cdp.send::<Value>(method, params, sid.as_deref()).await
}

pub async fn browser_extract_content(
    state: &SharedState,
    query: String,
    extract_links: Option<bool>,
    _output_schema: Option<Value>,
) -> Result<CallToolResult> {
    // Get page HTML first for deterministic Rust-based extraction
    let html_resp = cdp(state, "Runtime.evaluate", json!({
        "expression": "document.documentElement.outerHTML",
        "returnByValue": true,
    })).await.ok();
    let html = html_resp
        .as_ref()
        .and_then(|v| v["result"]["value"].as_str())
        .unwrap_or("");

    // Adjust query if extract_links flag is set
    let effective_query = if extract_links.unwrap_or(false) && !query.to_lowercase().contains("link") {
        format!("links {query}")
    } else {
        query.clone()
    };

    match agentyc_tools::extract(html, &effective_query) {
        Ok(result) => Ok(ok_json(&result)),
        Err(e) => Err(anyhow::anyhow!("{}", e)),
    }
}

pub async fn browser_find_elements(
    state: &SharedState,
    selector: String,
    attributes: Option<Vec<String>>,
    max_results: Option<u32>,
) -> Result<CallToolResult> {
    let max = max_results.unwrap_or(50);
    let attrs = attributes.unwrap_or_default();
    let attrs_js = attrs.iter().map(|a| format!("{a:?}: el.getAttribute({a:?})")).collect::<Vec<_>>().join(",");
    let js = format!(
        r#"Array.from(document.querySelectorAll({:?})).slice(0,{max}).map(el=>({{
            tag: el.tagName.toLowerCase(),
            text: (el.innerText||el.textContent||'').trim().substring(0,200),
            {attrs_js}
        }}))"#,
        selector
    );
    let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
    Ok(ok_json(&resp["result"]["value"]))
}

pub async fn browser_search_page(
    state: &SharedState,
    pattern: String,
    regex: Option<bool>,
    max_results: Option<u32>,
) -> Result<CallToolResult> {
    let max = max_results.unwrap_or(25);
    let is_regex = regex.unwrap_or(false);
    let js = if is_regex {
        format!(
            r#"(function(){{
                const re = new RegExp({:?}, 'gi');
                const text = document.body.innerText;
                const results = [];
                let m;
                while((m=re.exec(text))!==null && results.length<{max}) {{
                    const start = Math.max(0, m.index-50);
                    const end = Math.min(text.length, m.index+m[0].length+50);
                    results.push({{match:m[0],context:text.slice(start,end),index:m.index}});
                }}
                return results;
            }})()"#,
            pattern
        )
    } else {
        format!(
            r#"(function(){{
                const pattern = {:?}.toLowerCase();
                const text = document.body.innerText;
                const results = [];
                let idx = 0;
                while(results.length<{max}) {{
                    const pos = text.toLowerCase().indexOf(pattern, idx);
                    if(pos===-1) break;
                    const start = Math.max(0, pos-50);
                    const end = Math.min(text.length, pos+pattern.length+50);
                    results.push({{match:text.slice(pos,pos+pattern.length),context:text.slice(start,end),index:pos}});
                    idx = pos+1;
                }}
                return results;
            }})()"#,
            pattern
        )
    };
    let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
    Ok(ok_json(&resp["result"]["value"]))
}

pub async fn browser_wait_for_element(
    state: &SharedState,
    text: Option<String>,
    r#ref: Option<String>,
    appear: Option<bool>,
    timeout_seconds: Option<f64>,
) -> Result<CallToolResult> {
    let timeout = std::time::Duration::from_secs_f64(timeout_seconds.unwrap_or(10.0));
    let should_appear = appear.unwrap_or(true);
    let deadline = tokio::time::Instant::now() + timeout;

    loop {
        let found = if let Some(t) = &text {
            let js = format!(
                "document.body.innerText.toLowerCase().includes({:?}.toLowerCase())",
                t
            );
            let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await.ok();
            resp.and_then(|v| v["result"]["value"].as_bool()).unwrap_or(false)
        } else if let Some(r) = &r#ref {
            let id = parse_ref(r).unwrap_or(0);
            let js = format!("document.querySelector('[data-backend-node-id=\"{id}\"]') !== null");
            let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await.ok();
            resp.and_then(|v| v["result"]["value"].as_bool()).unwrap_or(false)
        } else {
            false
        };

        if found == should_appear {
            return Ok(ok_text(format!("Element {}",
                if should_appear { "appeared" } else { "disappeared" })));
        }
        if tokio::time::Instant::now() >= deadline {
            return Err(anyhow!("Timeout waiting for element to {}",
                if should_appear { "appear" } else { "disappear" }));
        }
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
    }
}

pub async fn browser_get_focused_element(state: &SharedState) -> Result<CallToolResult> {
    let js = r#"(function(){
        const el = document.activeElement;
        if(!el) return null;
        return {
            tag: el.tagName.toLowerCase(),
            type: el.type||null,
            value: el.value||null,
            placeholder: el.placeholder||null,
            id: el.id||null,
            name: el.name||null,
        };
    })()"#;
    let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
    Ok(ok_json(&resp["result"]["value"]))
}

pub async fn browser_get_attribute(
    state: &SharedState,
    name: String,
    r#ref: Option<String>,
    index: Option<u64>,
) -> Result<CallToolResult> {
    let id = if let Some(r) = &r#ref { parse_ref(r)? } else { index.unwrap_or(0) };
    let js = if id > 0 {
        format!("document.querySelectorAll('*')[{id}]?.getAttribute({:?})", name)
    } else {
        format!("document.querySelector('*')?.getAttribute({:?})", name)
    };
    let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
    let val = &resp["result"]["value"];
    Ok(ok_text(val.as_str().unwrap_or("null")))
}

pub async fn browser_evaluate(state: &SharedState, code: String) -> Result<CallToolResult> {
    let resp = cdp(state, "Runtime.evaluate", json!({
        "expression": code,
        "returnByValue": true,
        "awaitPromise": true,
    })).await?;
    if let Some(exc) = resp.get("exceptionDetails") {
        return Err(anyhow!("JS exception: {exc}"));
    }
    let result = &resp["result"]["value"];
    Ok(ok_text(serde_json::to_string(result).unwrap_or_else(|_| "null".into())))
}
