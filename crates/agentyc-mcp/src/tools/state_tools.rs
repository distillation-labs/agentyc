//! State tools: get_state, get_html, screenshot, save_as_pdf, set_viewport.
#![allow(clippy::too_many_arguments, clippy::collapsible_if, clippy::collapsible_match)]

use anyhow::Result;
use base64::Engine;
use serde_json::{json, Value};
use rmcp::model::{CallToolResult, Content};

use crate::tools::{ok_text, SharedState};
use crate::state::{ElemSummary, StateBuilder, DEFAULT_MIN_ELEMENTS};

async fn cdp(state: &SharedState, method: &str, params: Value) -> Result<Value> {
    let g = state.lock().await;
    let cdp = g.cdp()?;
    let sid = g.session_id.clone();
    cdp.send::<Value>(method, params, sid.as_deref()).await
}

pub async fn browser_get_state(
    state: &SharedState,
    mode: Option<String>,
    focus_ref: Option<String>,
    since_hash: Option<String>,
    include_screenshot: Option<bool>,
) -> Result<CallToolResult> {
    let mode = mode.unwrap_or_else(|| "auto".into());

    // Get current URL + title
    let url_resp = cdp(state, "Runtime.evaluate", json!({
        "expression": "({url:location.href,title:document.title})",
        "returnByValue": true
    })).await?;
    let url = url_resp["result"]["value"]["url"].as_str().unwrap_or("").to_string();
    let title = url_resp["result"]["value"]["title"].as_str().unwrap_or("").to_string();

    // Get page metrics for viewport/scroll
    let metrics = cdp(state, "Page.getLayoutMetrics", json!({})).await.unwrap_or(Value::Null);
    let vw = metrics["visualViewport"]["clientWidth"].as_u64().unwrap_or(1280) as u32;
    let vh = metrics["visualViewport"]["clientHeight"].as_u64().unwrap_or(900) as u32;
    let pw = metrics["contentSize"]["width"].as_u64().unwrap_or(vw as u64) as u32;
    let ph = metrics["contentSize"]["height"].as_u64().unwrap_or(vh as u64) as u32;
    let sx = metrics["visualViewport"]["pageX"].as_f64().unwrap_or(0.0) as i64;
    let sy = metrics["visualViewport"]["pageY"].as_f64().unwrap_or(0.0) as i64;

    // Get interactive elements via DOM + accessibility
    let elements = get_interactive_elements(state, vw, vh).await.unwrap_or_default();

    // Get tabs
    let tabs_resp = cdp(state, "Target.getTargets", json!({})).await.unwrap_or(Value::Null);
    let tabs: Vec<Value> = tabs_resp["targetInfos"].as_array()
        .unwrap_or(&vec![])
        .iter()
        .filter(|t| t["type"].as_str() == Some("page"))
        .map(|t| {
            let tid = t["targetId"].as_str().unwrap_or("");
            json!({
                "tab_id": crate::tools::tab_id_from(tid),
                "url": t["url"],
                "title": t["title"],
            })
        })
        .collect();

    let current_tab_id = state.lock().await.current_tab_id.clone();

    let payload = StateBuilder {
        url: &url,
        title: &title,
        mode: &mode,
        since_hash: since_hash.as_deref(),
        focus_ref: focus_ref.as_deref(),
        max_min: DEFAULT_MIN_ELEMENTS,
        elements: &elements,
        viewport: Some((vw, vh)),
        page_size: Some((pw, ph)),
        scroll: Some((sx, sy)),
        current_tab_id,
        tabs,
    }.build();

    let mut contents = vec![Content::text(serde_json::to_string_pretty(&payload).unwrap_or_default())];

    if include_screenshot.unwrap_or(false) {
        if let Ok(img_b64) = take_screenshot_b64(state, false).await {
            contents.push(Content::image(img_b64, "image/png"));
        }
    }

    Ok(CallToolResult::success(contents))
}

async fn get_interactive_elements(state: &SharedState, vw: u32, vh: u32) -> Result<Vec<ElemSummary>> {
    // Use Runtime.evaluate to get interactive elements from the page
    let js = r#"
    (function() {
        const INTERACTIVE = ['a','button','input','select','textarea','details','summary'];
        const results = [];
        function isVisible(el) {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0'
                && r.width > 0 && r.height > 0;
        }
        const seen = new Set();
        document.querySelectorAll(INTERACTIVE.join(',') + ',[role],[tabindex],[onclick],[href]').forEach(el => {
            if (seen.has(el)) return; seen.add(el);
            if (!isVisible(el)) return;
            const r = el.getBoundingClientRect();
            results.push({
                backendNodeId: 0,
                tag: el.tagName.toLowerCase(),
                text: (el.innerText || el.textContent || '').trim().substring(0,200),
                role: el.getAttribute('role') || el.getAttribute('aria-role') || null,
                placeholder: el.getAttribute('placeholder') || null,
                href: el.getAttribute('href') || null,
                type: el.getAttribute('type') || null,
                value: el.value || null,
                disabled: el.disabled || el.hasAttribute('disabled'),
                x: r.x, y: r.y, width: r.width, height: r.height,
            });
        });
        return results;
    })()
    "#;
    let resp = cdp(state, "Runtime.evaluate", json!({
        "expression": js,
        "returnByValue": true,
    })).await?;
    let arr = resp["result"]["value"].as_array().cloned().unwrap_or_default();
    let elements: Vec<ElemSummary> = arr.iter().enumerate().map(|(i, v)| {
        let tag = v["tag"].as_str().unwrap_or("div").to_string();
        let text = v["text"].as_str().unwrap_or("").to_string();
        let disabled = v["disabled"].as_bool().unwrap_or(false);
        let input_type = v["type"].as_str().filter(|s| !s.is_empty()).map(str::to_string);
        let score = score_element(&tag, &text, input_type.as_deref().unwrap_or(""), disabled);
        ElemSummary {
            backend_node_id: (i as u64) + 1,
            tag,
            text,
            role: v["role"].as_str().filter(|s| !s.is_empty()).map(str::to_string),
            placeholder: v["placeholder"].as_str().filter(|s| !s.is_empty()).map(str::to_string),
            href: v["href"].as_str().filter(|s| !s.is_empty()).map(str::to_string),
            input_type,
            value: v["value"].as_str().filter(|s| !s.is_empty()).map(str::to_string),
            disabled,
            score,
            order: i,
            rect_y: v["y"].as_f64(),
            off_screen: {
                let x = v["x"].as_f64().unwrap_or(0.0);
                let y = v["y"].as_f64().unwrap_or(0.0);
                let w = v["width"].as_f64().unwrap_or(0.0);
                let h = v["height"].as_f64().unwrap_or(0.0);
                if y + h <= 0.0 { Some("above".into()) }
                else if y >= vh as f64 { Some("below".into()) }
                else if x + w <= 0.0 { Some("left".into()) }
                else if x >= vw as f64 { Some("right".into()) }
                else { None }
            },
        }
    }).collect();
    Ok(elements)
}

fn score_element(tag: &str, text: &str, input_type: &str, disabled: bool) -> f64 {
    let mut s = 0.0f64;
    match tag {
        "input" | "textarea" | "select" => s += 50.0,
        "button" => s += 42.0,
        "a" => s += 18.0,
        _ => {}
    }
    if !text.is_empty() { s += (text.len().min(40) as f64) / 3.0; }
    if matches!(input_type, "email" | "password" | "search" | "url") { s += 10.0; }
    if disabled { s -= 12.0; }
    if input_type == "hidden" { s -= 100.0; }
    s
}

async fn take_screenshot_b64(state: &SharedState, full_page: bool) -> Result<String> {
    let params = if full_page {
        json!({"format": "png", "captureBeyondViewport": true})
    } else {
        json!({"format": "png"})
    };
    let resp = cdp(state, "Page.captureScreenshot", params).await?;
    Ok(resp["data"].as_str().unwrap_or("").to_string())
}

pub async fn browser_get_html(
    state: &SharedState,
    selector: Option<String>,
) -> Result<CallToolResult> {
    let js = if let Some(sel) = selector {
        format!("document.querySelector({:?})?.outerHTML ?? ''", sel)
    } else {
        "document.documentElement.outerHTML".to_string()
    };
    let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
    let html = resp["result"]["value"].as_str().unwrap_or("").to_string();
    Ok(ok_text(html))
}

pub async fn browser_screenshot(
    state: &SharedState,
    full_page: Option<bool>,
) -> Result<CallToolResult> {
    let b64 = take_screenshot_b64(state, full_page.unwrap_or(false)).await?;
    let resp = cdp(state, "Runtime.evaluate", json!({
        "expression": "({w:window.innerWidth,h:window.innerHeight})",
        "returnByValue": true,
    })).await.unwrap_or(Value::Null);
    let w = resp["result"]["value"]["w"].as_u64().unwrap_or(1280);
    let h = resp["result"]["value"]["h"].as_u64().unwrap_or(900);
    let meta = format!("Screenshot taken ({w}x{h})");
    Ok(CallToolResult::success(vec![
        Content::text(meta),
        Content::image(b64, "image/png"),
    ]))
}

pub async fn browser_save_as_pdf(
    state: &SharedState,
    file_name: Option<String>,
    print_background: Option<bool>,
    landscape: Option<bool>,
    scale: Option<f64>,
    paper_format: Option<String>,
) -> Result<CallToolResult> {
    let title_resp = cdp(state, "Runtime.evaluate", json!({
        "expression": "document.title", "returnByValue": true
    })).await.unwrap_or(Value::Null);
    let title = title_resp["result"]["value"].as_str().unwrap_or("page").to_string();
    let name = file_name.unwrap_or_else(|| format!("{}.pdf", title.replace('/', "_")));

    let (paper_w, paper_h) = match paper_format.as_deref().unwrap_or("Letter") {
        "A4" => (8.27, 11.7),
        "A3" => (11.7, 16.54),
        "Legal" => (8.5, 14.0),
        "Tabloid" => (11.0, 17.0),
        _ => (8.5, 11.0), // Letter
    };

    let resp = cdp(state, "Page.printToPDF", json!({
        "printBackground": print_background.unwrap_or(true),
        "landscape": landscape.unwrap_or(false),
        "scale": scale.unwrap_or(1.0),
        "paperWidth": paper_w,
        "paperHeight": paper_h,
    })).await?;

    let data = resp["data"].as_str().unwrap_or("");
    let bytes = base64::engine::general_purpose::STANDARD.decode(data)?;
    let dir = dirs::download_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join("agentyc-mcp");
    std::fs::create_dir_all(&dir)?;
    let path = dir.join(&name);
    std::fs::write(&path, bytes)?;
    Ok(ok_text(format!("PDF saved to {}", path.display())))
}

pub async fn browser_set_viewport(
    state: &SharedState,
    width: u32,
    height: u32,
    device_scale_factor: Option<f64>,
) -> Result<CallToolResult> {
    cdp(state, "Emulation.setDeviceMetricsOverride", json!({
        "width": width,
        "height": height,
        "deviceScaleFactor": device_scale_factor.unwrap_or(1.0),
        "mobile": false,
    })).await?;
    Ok(ok_text(format!("Viewport set to {width}x{height}")))
}
