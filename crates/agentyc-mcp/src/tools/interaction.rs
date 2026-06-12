//! Interaction tools: click, right_click, double_click, hover, drag_to, type,
#![allow(clippy::too_many_arguments, clippy::collapsible_if, clippy::collapsible_match)]
//! fill_form, press_key, scroll, scroll_to_text, select_option,
//! get_dropdown_options, upload_file, handle_dialog.

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

/// Resolve an element to a (x, y) viewport coordinate via DOM.getBoxModel.
async fn element_center(state: &SharedState, backend_node_id: u64) -> Result<(f64, f64)> {
    let resp = cdp(state, "DOM.getBoxModel", json!({"backendNodeId": backend_node_id})).await?;
    let content = resp["model"]["content"].as_array()
        .ok_or_else(|| anyhow!("No box model for element"))?;
    // content is [x1,y1,x2,y2,x3,y3,x4,y4]
    let xs: Vec<f64> = content.iter().step_by(2).filter_map(|v| v.as_f64()).collect();
    let ys: Vec<f64> = content.iter().skip(1).step_by(2).filter_map(|v| v.as_f64()).collect();
    let cx = xs.iter().sum::<f64>() / xs.len().max(1) as f64;
    let cy = ys.iter().sum::<f64>() / ys.len().max(1) as f64;
    Ok((cx, cy))
}

/// Scroll element into view, then return its center.
async fn scroll_and_center(state: &SharedState, backend_node_id: u64) -> Result<(f64, f64)> {
    cdp(state, "DOM.scrollIntoViewIfNeeded", json!({"backendNodeId": backend_node_id})).await.ok();
    element_center(state, backend_node_id).await
}

async fn mouse_event(state: &SharedState, event_type: &str, x: f64, y: f64, button: &str, click_count: u32) -> Result<()> {
    cdp(state, "Input.dispatchMouseEvent", json!({
        "type": event_type,
        "x": x, "y": y,
        "button": button,
        "clickCount": click_count,
    })).await?;
    Ok(())
}

async fn resolve_target(
    state: &SharedState,
    r#ref: Option<&str>,
    index: Option<u64>,
    coordinate_x: Option<f64>,
    coordinate_y: Option<f64>,
    label: Option<&str>,
) -> Result<(f64, f64)> {
    if let Some(x) = coordinate_x {
        if let Some(y) = coordinate_y {
            return Ok((x, y));
        }
    }
    let id = if let Some(r) = r#ref {
        parse_ref(r)?
    } else if let Some(i) = index {
        i
    } else if let Some(lbl) = label {
        // Try to find by text label via JS
        let js = format!(
            r#"(function(){{
                const all = document.querySelectorAll('a,button,input,select,textarea,[role]');
                for(const el of all) {{
                    const t = (el.innerText||el.textContent||el.value||el.getAttribute('aria-label')||'').trim();
                    if(t.toLowerCase().includes({:?}.toLowerCase())) return el.getAttribute('data-backend-node-id')||'0';
                }}
                return '0';
            }})()"#,
            lbl
        );
        let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
        let id_str = resp["result"]["value"].as_str().unwrap_or("0");
        id_str.parse::<u64>().unwrap_or(0)
    } else {
        return Err(anyhow!("Must provide ref, index, label, or coordinates"));
    };
    if id == 0 {
        return Err(anyhow!("Could not resolve element target"));
    }
    scroll_and_center(state, id).await
}

pub async fn browser_click(
    state: &SharedState,
    r#ref: Option<String>,
    index: Option<u64>,
    label: Option<String>,
    coordinate_x: Option<f64>,
    coordinate_y: Option<f64>,
    wait_for_url_substring: Option<String>,
    wait_for_url_regex: Option<String>,
    url_timeout_seconds: Option<f64>,
) -> Result<CallToolResult> {
    let (x, y) = resolve_target(state, r#ref.as_deref(), index, coordinate_x, coordinate_y, label.as_deref()).await?;
    mouse_event(state, "mouseMoved", x, y, "none", 0).await?;
    mouse_event(state, "mousePressed", x, y, "left", 1).await?;
    mouse_event(state, "mouseReleased", x, y, "left", 1).await?;

    if let Some(sub) = wait_for_url_substring {
        crate::tools::navigation::browser_wait_for_url(state, Some(sub), wait_for_url_regex, url_timeout_seconds).await?;
    }
    Ok(ok_text(format!("Clicked at ({x:.0},{y:.0})")))
}

pub async fn browser_right_click(
    state: &SharedState,
    r#ref: Option<String>,
    index: Option<u64>,
    coordinate_x: Option<f64>,
    coordinate_y: Option<f64>,
) -> Result<CallToolResult> {
    let (x, y) = resolve_target(state, r#ref.as_deref(), index, coordinate_x, coordinate_y, None).await?;
    mouse_event(state, "mouseMoved", x, y, "none", 0).await?;
    mouse_event(state, "mousePressed", x, y, "right", 1).await?;
    mouse_event(state, "mouseReleased", x, y, "right", 1).await?;
    Ok(ok_text(format!("Right-clicked at ({x:.0},{y:.0})")))
}

pub async fn browser_double_click(
    state: &SharedState,
    r#ref: Option<String>,
    index: Option<u64>,
    coordinate_x: Option<f64>,
    coordinate_y: Option<f64>,
) -> Result<CallToolResult> {
    let (x, y) = resolve_target(state, r#ref.as_deref(), index, coordinate_x, coordinate_y, None).await?;
    mouse_event(state, "mouseMoved", x, y, "none", 0).await?;
    mouse_event(state, "mousePressed", x, y, "left", 2).await?;
    mouse_event(state, "mouseReleased", x, y, "left", 2).await?;
    Ok(ok_text(format!("Double-clicked at ({x:.0},{y:.0})")))
}

pub async fn browser_hover(
    state: &SharedState,
    r#ref: Option<String>,
    index: Option<u64>,
    coordinate_x: Option<f64>,
    coordinate_y: Option<f64>,
) -> Result<CallToolResult> {
    let (x, y) = resolve_target(state, r#ref.as_deref(), index, coordinate_x, coordinate_y, None).await?;
    mouse_event(state, "mouseMoved", x, y, "none", 0).await?;
    Ok(ok_text(format!("Hovering at ({x:.0},{y:.0})")))
}

pub async fn browser_drag_to(
    state: &SharedState,
    source_ref: Option<String>,
    target_ref: Option<String>,
    source_x: Option<f64>,
    source_y: Option<f64>,
    target_x: Option<f64>,
    target_y: Option<f64>,
    steps: Option<u32>,
) -> Result<CallToolResult> {
    let (sx, sy) = if let (Some(x), Some(y)) = (source_x, source_y) {
        (x, y)
    } else {
        resolve_target(state, source_ref.as_deref(), None, None, None, None).await?
    };
    let (tx, ty) = if let (Some(x), Some(y)) = (target_x, target_y) {
        (x, y)
    } else {
        resolve_target(state, target_ref.as_deref(), None, None, None, None).await?
    };
    let n = steps.unwrap_or(10).max(1) as f64;
    mouse_event(state, "mousePressed", sx, sy, "left", 1).await?;
    for i in 1..=(n as u32) {
        let t = i as f64 / n;
        let mx = sx + (tx - sx) * t;
        let my = sy + (ty - sy) * t;
        mouse_event(state, "mouseMoved", mx, my, "left", 0).await?;
    }
    mouse_event(state, "mouseReleased", tx, ty, "left", 1).await?;
    Ok(ok_text(format!("Dragged from ({sx:.0},{sy:.0}) to ({tx:.0},{ty:.0})")))
}

pub async fn browser_type(
    state: &SharedState,
    r#ref: Option<String>,
    index: Option<u64>,
    label: Option<String>,
    text: String,
) -> Result<CallToolResult> {
    let (x, y) = resolve_target(state, r#ref.as_deref(), index, None, None, label.as_deref()).await?;

    // Click to focus first
    mouse_event(state, "mousePressed", x, y, "left", 1).await?;
    mouse_event(state, "mouseReleased", x, y, "left", 1).await?;

    let bid = if let Some(r) = &r#ref {
        parse_ref(r).ok()
    } else {
        index
    };

    let text_json = serde_json::to_string(&text).unwrap_or_else(|_| "\"\"".to_string());

    // Primary path: callFunctionOn via resolved objectId
    if let Some(id) = bid.filter(|&id| id > 0) {
        let resolve = cdp(state, "DOM.resolveNode", json!({"backendNodeId": id})).await;
        if let Ok(rr) = resolve {
            if let Some(obj_id) = rr["object"]["objectId"].as_str() {
                let func = format!(
                    r#"function(){{
                        var v={text_json};
                        var el=this;
                        el.focus();
                        var d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value')
                            ||Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value');
                        if(d&&d.set){{d.set.call(el,v);}}else{{el.value=v;}}
                        el.dispatchEvent(new Event('input',{{bubbles:true}}));
                        el.dispatchEvent(new Event('change',{{bubbles:true}}));
                        return el.value;
                    }}"#
                );
                let g = state.lock().await;
                let sid = g.session_id.clone();
                let result = g.cdp()?.send::<serde_json::Value>("Runtime.callFunctionOn", json!({
                    "objectId": obj_id,
                    "functionDeclaration": func,
                    "returnByValue": true,
                }), sid.as_deref()).await;
                drop(g);
                if let Ok(r) = result {
                    let returned = r["result"]["value"].as_str().unwrap_or("");
                    if !returned.is_empty() {
                        return Ok(ok_text(format!("Typed {} chars", text.len())));
                    }
                }
            }
        }
    }

    // Fallback 1: Runtime.evaluate on focused element (works for React inputs, same context as browser_evaluate)
    let js_set = format!(
        r#"(function(){{
            var el = document.activeElement;
            if(!el || el===document.body) return false;
            var v={text_json};
            var d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value')
                ||Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value');
            if(d&&d.set){{d.set.call(el,v);}}else{{el.value=v;}}
            el.dispatchEvent(new Event('input',{{bubbles:true}}));
            el.dispatchEvent(new Event('change',{{bubbles:true}}));
            return el.value===v;
        }})()"#
    );
    let resp = cdp(state, "Runtime.evaluate", json!({"expression": js_set, "returnByValue": true})).await;
    if let Ok(r) = resp {
        if r["result"]["value"].as_bool() == Some(true) {
            return Ok(ok_text(format!("Typed {} chars", text.len())));
        }
    }

    // Fallback 2: insertText (native inputs without React)
    dispatch_key(state, "a", true, false, false).await?;
    cdp(state, "Input.dispatchKeyEvent", json!({"type":"keyDown","key":"Delete"})).await.ok();
    cdp(state, "Input.insertText", json!({"text": text})).await?;
    Ok(ok_text(format!("Typed {} chars", text.len())))
}

async fn dispatch_key(state: &SharedState, key: &str, ctrl: bool, shift: bool, alt: bool) -> Result<()> {
    let mods = (if ctrl { 2 } else { 0 }) | (if shift { 8 } else { 0 }) | (if alt { 1 } else { 0 });
    cdp(state, "Input.dispatchKeyEvent", json!({
        "type": "keyDown", "key": key, "modifiers": mods
    })).await?;
    cdp(state, "Input.dispatchKeyEvent", json!({
        "type": "keyUp", "key": key, "modifiers": mods
    })).await?;
    Ok(())
}

pub async fn browser_fill_form(
    state: &SharedState,
    fields: Vec<Value>,
) -> Result<CallToolResult> {
    let mut done = 0u32;
    for field in &fields {
        let r = field["ref"].as_str().map(str::to_string);
        let idx = field["index"].as_u64();
        let lbl = field["label"].as_str().map(str::to_string);

        if let Some(text) = field["text"].as_str() {
            browser_type(state, r.clone(), idx, lbl.clone(), text.to_string()).await?;
            done += 1;
        } else if let Some(opt) = field["option_text"].as_str() {
            let id = if let Some(rf) = &r { parse_ref(rf)? } else { idx.unwrap_or(0) };
            if id > 0 {
                let js = format!(
                    r#"(function(){{
                        const el = document.querySelector('[data-backend-node-id="{id}"]') || document.querySelectorAll('select')[{id}-1];
                        if(!el) return false;
                        for(const o of el.options) {{
                            if(o.text === {:?}) {{ el.value = o.value; el.dispatchEvent(new Event('change',{{bubbles:true}})); return true; }}
                        }}
                        return false;
                    }})()"#,
                    opt
                );
                cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
            }
            done += 1;
        } else if let Some(checked) = field["checked"].as_bool() {
            let id = if let Some(rf) = &r { parse_ref(rf)? } else { idx.unwrap_or(0) };
            if id > 0 {
                let js = format!(
                    r#"(function(){{
                        const el = document.querySelector('[data-backend-node-id="{id}"]');
                        if(el && el.checked !== {checked}) {{ el.click(); }}
                    }})()"#
                );
                cdp(state, "Runtime.evaluate", json!({"expression": js})).await?;
            }
            done += 1;
        }
    }
    Ok(ok_text(format!("Filled {done} fields")))
}

pub async fn browser_press_key(state: &SharedState, key: String) -> Result<CallToolResult> {
    // Parse chord like "Control+a", "Meta+r"
    let parts: Vec<&str> = key.split('+').collect();
    let (ctrl, shift, alt, actual_key) = if parts.len() > 1 {
        let mods: Vec<&str> = parts[..parts.len()-1].to_vec();
        let k = parts.last().unwrap_or(&"");
        (
            mods.iter().any(|m| *m == "Control" || *m == "Ctrl"),
            mods.contains(&"Shift"),
            mods.contains(&"Alt"),
            *k,
        )
    } else {
        (false, false, false, key.as_str())
    };
    dispatch_key(state, actual_key, ctrl, shift, alt).await?;
    Ok(ok_text(format!("Pressed key: {key}")))
}

pub async fn browser_scroll(
    state: &SharedState,
    direction: Option<String>,
    pages: Option<f64>,
    r#ref: Option<String>,
    index: Option<u64>,
) -> Result<CallToolResult> {
    let dir = direction.as_deref().unwrap_or("down");
    let pg = pages.unwrap_or(1.0);
    let delta_y = if dir == "down" { pg * 900.0 } else { -(pg * 900.0) };

    if r#ref.is_some() || index.is_some() {
        let id = if let Some(r) = &r#ref { parse_ref(r)? } else { index.unwrap_or(0) };
        cdp(state, "Runtime.evaluate", json!({
            "expression": format!("document.querySelector('[data-backend-node-id=\"{id}\"]')?.scrollBy(0, {delta_y})"),
        })).await?;
    } else {
        cdp(state, "Input.dispatchMouseEvent", json!({
            "type": "mouseWheel", "x": 640, "y": 360,
            "deltaX": 0, "deltaY": delta_y,
        })).await?;
    }
    Ok(ok_text(format!("Scrolled {dir} {pg} page(s)")))
}

pub async fn browser_scroll_to_text(state: &SharedState, text: String) -> Result<CallToolResult> {
    let js = format!(
        r#"(function(){{
            const iter = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let node;
            while(node = iter.nextNode()) {{
                if(node.textContent.toLowerCase().includes({:?}.toLowerCase())) {{
                    node.parentElement?.scrollIntoView({{behavior:'smooth',block:'center'}});
                    return true;
                }}
            }}
            return false;
        }})()"#,
        text
    );
    let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
    if resp["result"]["value"].as_bool().unwrap_or(false) {
        Ok(ok_text(format!("Scrolled to text: {text}")))
    } else {
        Err(anyhow!("Text not found: {text}"))
    }
}

pub async fn browser_select_option(
    state: &SharedState,
    r#ref: Option<String>,
    index: Option<u64>,
    label: Option<String>,
    text: String,
) -> Result<CallToolResult> {
    let id = if let Some(r) = &r#ref {
        parse_ref(r)?
    } else if let Some(i) = index {
        i
    } else if let Some(lbl) = &label {
        let js = format!(
            r#"(function(){{
                for(const el of document.querySelectorAll('select')) {{
                    const lbl = document.querySelector('label[for="'+el.id+'"]');
                    if(lbl && lbl.textContent.toLowerCase().includes({:?}.toLowerCase())) return el.getAttribute('data-backend-node-id')||'0';
                }}
                return '0';
            }})()"#, lbl
        );
        let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
        resp["result"]["value"].as_str().unwrap_or("0").parse::<u64>().unwrap_or(0)
    } else {
        0
    };

    // Use CDP DOM.resolveNode to get a remote object, then call select via JS on the node
    let js_select = if id > 0 {
        // Resolve via backendNodeId then select by option text
        let resolve = cdp(state, "DOM.resolveNode", json!({"backendNodeId": id})).await?;
        let obj_id = resolve["object"]["objectId"].as_str().unwrap_or("").to_string();
        if !obj_id.is_empty() {
            // Call function on the resolved object
            let call_resp = {
                let g = state.lock().await;
                let sid = g.session_id.clone();
                g.cdp()?.send::<Value>("Runtime.callFunctionOn", json!({
                    "objectId": obj_id,
                    "functionDeclaration": format!(r#"function(){{
                        for(const o of this.options) {{
                            if(o.text === {:?}) {{
                                this.value = o.value;
                                this.dispatchEvent(new Event('change', {{bubbles:true}}));
                                return true;
                            }}
                        }}
                        return false;
                    }}"#, text),
                    "returnByValue": true,
                }), sid.as_deref()).await?
            };
            if call_resp["result"]["value"].as_bool().unwrap_or(false) {
                return Ok(ok_text(format!("Selected option: {text}")));
            }
        }
        // Fallback to JS by iterating all selects
        format!(r#"(function(){{
            for(const el of document.querySelectorAll('select')) {{
                for(const o of el.options) {{
                    if(o.text === {:?}) {{ el.value = o.value; el.dispatchEvent(new Event('change',{{bubbles:true}})); return true; }}
                }}
            }}
            return false;
        }})()"#, text)
    } else {
        format!(r#"(function(){{
            for(const el of document.querySelectorAll('select')) {{
                for(const o of el.options) {{
                    if(o.text === {:?}) {{ el.value = o.value; el.dispatchEvent(new Event('change',{{bubbles:true}})); return true; }}
                }}
            }}
            return false;
        }})()"#, text)
    };

    let resp = cdp(state, "Runtime.evaluate", json!({"expression": js_select, "returnByValue": true})).await?;
    if resp["result"]["value"].as_bool().unwrap_or(false) {
        Ok(ok_text(format!("Selected option: {text}")))
    } else {
        Err(anyhow!("Option {:?} not found", text))
    }
}

pub async fn browser_get_dropdown_options(
    state: &SharedState,
    r#ref: Option<String>,
    index: Option<u64>,
    label: Option<String>,
) -> Result<CallToolResult> {
    let id = if let Some(r) = &r#ref {
        parse_ref(r)?
    } else if let Some(i) = index {
        i
    } else if let Some(lbl) = &label {
        let js = format!(
            r#"(function(){{
                const selects = document.querySelectorAll('select');
                for(let i=0;i<selects.length;i++) {{
                    const lbl = document.querySelector('label[for="'+selects[i].id+'"]');
                    if(lbl && lbl.textContent.toLowerCase().includes({:?}.toLowerCase())) return String(i+1);
                }}
                return '1';
            }})()"#, lbl
        );
        let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
        resp["result"]["value"].as_str().unwrap_or("1").parse::<u64>().unwrap_or(1)
    } else {
        0
    };

    // Use CDP DOM.resolveNode + callFunctionOn for reliable option access
    if id > 0 {
        let resolve = cdp(state, "DOM.resolveNode", json!({"backendNodeId": id})).await;
        if let Ok(r) = resolve {
            if let Some(obj_id) = r["object"]["objectId"].as_str() {
                let g = state.lock().await;
                let sid = g.session_id.clone();
                let call_resp = g.cdp()?.send::<Value>("Runtime.callFunctionOn", json!({
                    "objectId": obj_id,
                    "functionDeclaration": "function(){return Array.from(this.options).map(o=>({value:o.value,text:o.text,selected:o.selected}))}",
                    "returnByValue": true,
                }), sid.as_deref()).await;
                drop(g);
                if let Ok(cr) = call_resp {
                    return Ok(ok_json(&cr["result"]["value"]));
                }
            }
        }
    }

    // Fallback: get options from first select on page
    let js = r#"(function(){
        const el = document.querySelector('select');
        if(!el) return [];
        return Array.from(el.options).map(o=>({value:o.value,text:o.text,selected:o.selected}));
    })()"#;
    let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
    Ok(ok_json(&resp["result"]["value"]))
}

pub async fn browser_upload_file(
    state: &SharedState,
    r#ref: Option<String>,
    index: Option<u64>,
    label: Option<String>,
    path: String,
) -> Result<CallToolResult> {
    let id = if let Some(r) = &r#ref {
        parse_ref(r)?
    } else if let Some(i) = index {
        i
    } else if let Some(lbl) = &label {
        let js = format!(
            r#"(function(){{
                for(const el of document.querySelectorAll('input[type=file]')) {{
                    const lbl = document.querySelector('label[for="'+el.id+'"]');
                    const aria = el.getAttribute('aria-label')||'';
                    if((lbl && lbl.textContent.toLowerCase().includes({:?}.toLowerCase())) ||
                       aria.toLowerCase().includes({:?}.toLowerCase())) {{
                        return el.getAttribute('data-backend-node-id')||'0';
                    }}
                }}
                return '0';
            }})()"#, lbl, lbl
        );
        let resp = cdp(state, "Runtime.evaluate", json!({"expression": js, "returnByValue": true})).await?;
        resp["result"]["value"].as_str().unwrap_or("0").parse::<u64>().unwrap_or(0)
    } else {
        0
    };
    if id == 0 {
        return Err(anyhow!("Must provide ref, index, or label for upload_file"));
    }
    // Use DOM.setFileInputFiles
    cdp(state, "DOM.setFileInputFiles", json!({
        "files": [path],
        "backendNodeId": id,
    })).await?;
    Ok(ok_text(format!("Uploaded file: {path}")))
}

pub async fn browser_handle_dialog(
    state: &SharedState,
    accept: Option<bool>,
    prompt_text: Option<String>,
) -> Result<CallToolResult> {
    cdp(state, "Page.handleJavaScriptDialog", json!({
        "accept": accept.unwrap_or(true),
        "promptText": prompt_text.unwrap_or_default(),
    })).await?;
    Ok(ok_text("Dialog handled"))
}
