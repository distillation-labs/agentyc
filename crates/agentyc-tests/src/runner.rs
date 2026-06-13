//! Scenario runner — interprets `scenario::Step`/`Check` against the live MCP
//! tools, driving the real read → ref → act → verify loop.

use serde_json::{Value, json};

use crate::Mcp;
use crate::scenario::{Check, Scenario, Step};

/// Execute a scenario against the fixtures at `base`. Returns Err(message) on the
/// first failed step or check.
pub fn run(m: &mut Mcp, base: &str, s: &Scenario) -> Result<(), String> {
    for (i, step) in s.steps.iter().enumerate() {
        run_step(m, base, step).map_err(|e| format!("[{}] step {i}: {e}", s.id))?;
    }
    for (i, check) in s.checks.iter().enumerate() {
        run_check(m, base, check).map_err(|e| format!("[{}] check {i}: {e}", s.id))?;
    }
    Ok(())
}

fn url(base: &str, path: &str) -> String {
    if path.starts_with("http") {
        path.to_string()
    } else {
        format!("{base}{path}")
    }
}

fn body_text(m: &mut Mcp) -> String {
    m.eval("document.body.innerText")
}

fn elements(m: &mut Mcp) -> Vec<Value> {
    Mcp::elements(&m.state("full"))
}

/// Find the ref of the first interactive element whose visible text/value
/// matches `needle` (case-insensitive). Exact matches win over substring
/// matches so e.g. "Confirm" does not resolve to "Native confirm".
fn ref_by_text(m: &mut Mcp, needle: &str) -> Option<String> {
    let nl = needle.to_lowercase();
    let pick = |els: &[Value], exact: bool| -> Option<String> {
        els.iter()
            .find(|e| {
                let t = e["text"].as_str().unwrap_or("").trim().to_lowercase();
                let val = e["value"].as_str().unwrap_or("").trim().to_lowercase();
                if exact {
                    t == nl || val == nl
                } else {
                    t.contains(&nl) || val.contains(&nl)
                }
            })
            .and_then(|e| e["ref"].as_str())
            .map(str::to_string)
    };
    let els = elements(m);
    if let Some(r) = pick(&els, true).or_else(|| pick(&els, false)) {
        return Some(r);
    }
    // One retry after a brief settle for late-rendered controls.
    m.wait(0.3);
    let els = elements(m);
    pick(&els, true).or_else(|| pick(&els, false))
}

/// Extract the tab list from `browser_list_tabs`, handling both the bare-array
/// and `{"tabs": [...]}` response shapes across builds.
fn tabs_array(v: &Value) -> Vec<Value> {
    if let Some(a) = v.as_array() {
        a.clone()
    } else if let Some(a) = v["tabs"].as_array() {
        a.clone()
    } else {
        Vec::new()
    }
}

/// Find the ref of an input by placeholder (exact match preferred).
fn ref_by_placeholder(m: &mut Mcp, ph: &str) -> Option<String> {
    let els = elements(m);
    let exact = els.iter().find(|e| {
        e["placeholder"]
            .as_str()
            .map(|p| p.eq_ignore_ascii_case(ph))
            .unwrap_or(false)
    });
    if let Some(e) = exact {
        return e["ref"].as_str().map(str::to_string);
    }
    let pl = ph.to_lowercase();
    els.iter()
        .find(|e| {
            e["placeholder"]
                .as_str()
                .map(|p| p.to_lowercase().contains(&pl))
                .unwrap_or(false)
        })
        .and_then(|e| e["ref"].as_str())
        .map(str::to_string)
}

fn run_step(m: &mut Mcp, base: &str, step: &Step) -> Result<(), String> {
    match step {
        Step::Navigate(p) => {
            // Navigation may legitimately load 4xx/5xx pages; don't treat as fatal.
            let _ = m.navigate(&url(base, p));
            Ok(())
        }
        Step::Wait(s) => {
            m.wait(*s);
            Ok(())
        }
        Step::WaitText {
            text,
            appear,
            timeout,
        } => {
            let r = m.call(
                "browser_wait_for_element",
                json!({"text": text, "appear": appear, "timeout_seconds": timeout}),
            );
            if Mcp::is_err(&r) {
                Err(format!(
                    "wait_for_element {text:?} failed: {}",
                    Mcp::text(&r)
                ))
            } else {
                Ok(())
            }
        }
        Step::WaitUrl { substr, timeout } => {
            let r = m.call(
                "browser_wait_for_url",
                json!({"url_substring": substr, "timeout_seconds": timeout}),
            );
            if Mcp::is_err(&r) {
                Err(format!("wait_for_url {substr:?} failed: {}", Mcp::text(&r)))
            } else {
                Ok(())
            }
        }
        Step::WaitStableDom { quiet_ms, timeout } => {
            m.call(
                "browser_wait_for_stable_dom",
                json!({"quiet_ms": quiet_ms, "timeout_seconds": timeout}),
            );
            Ok(())
        }
        Step::WaitNetworkIdle { timeout } => {
            m.call(
                "browser_wait_for_network_idle",
                json!({"timeout_seconds": timeout}),
            );
            Ok(())
        }
        Step::ClickText(t) => {
            let r = ref_by_text(m, t).ok_or_else(|| format!("no element with text {t:?}"))?;
            let res = m.call("browser_click", json!({"ref": r}));
            if Mcp::is_err(&res) {
                Err(format!("click {t:?} failed: {}", Mcp::text(&res)))
            } else {
                Ok(())
            }
        }
        Step::ClickTag(tag) => {
            let r = Mcp::ref_by_tag(&m.state("full"), tag).ok_or_else(|| format!("no <{tag}>"))?;
            m.call("browser_click", json!({"ref": r}));
            Ok(())
        }
        Step::TypePlaceholder { placeholder, text } => {
            let r = ref_by_placeholder(m, placeholder)
                .ok_or_else(|| format!("no input with placeholder {placeholder:?}"))?;
            let res = m.call("browser_type", json!({"ref": r, "text": text}));
            if Mcp::is_err(&res) {
                Err(format!(
                    "type into {placeholder:?} failed: {}",
                    Mcp::text(&res)
                ))
            } else {
                Ok(())
            }
        }
        Step::SelectFirst { value } => {
            let r = Mcp::ref_by_tag(&m.state("full"), "select").ok_or("no <select>")?;
            m.call("browser_select_option", json!({"ref": r, "text": value}));
            Ok(())
        }
        Step::SetCheckbox(desired) => {
            let st = m.state("full");
            let r = Mcp::ref_by_type(&st, "checkbox").ok_or("no checkbox")?;
            if *desired {
                m.call("browser_click", json!({"ref": r}));
            }
            Ok(())
        }
        Step::PressKey(k) => {
            m.call("browser_press_key", json!({"key": k}));
            Ok(())
        }
        Step::Scroll { down, pages } => {
            m.call(
                "browser_scroll",
                json!({"direction": if *down {"down"} else {"up"}, "pages": pages}),
            );
            Ok(())
        }
        Step::ScrollToText(t) => {
            m.call("browser_scroll_to_text", json!({"text": t}));
            Ok(())
        }
        Step::DragSelector { from, to } => {
            let code = format!(
                "(function(){{var a=document.querySelector('{from}');var b=document.querySelector('{to}');if(!a||!b)return '';var x=a.getBoundingClientRect();var y=b.getBoundingClientRect();return [x.left+x.width/2,x.top+x.height/2,y.left+y.width/2,y.top+y.height/2].join(',');}})()"
            );
            let raw = m.eval(&code);
            let nums: Vec<f64> = raw
                .trim()
                .trim_matches('"')
                .split(',')
                .filter_map(|s| s.parse().ok())
                .collect();
            if nums.len() == 4 {
                m.call(
                    "browser_drag_to",
                    json!({"source_x": nums[0], "source_y": nums[1], "target_x": nums[2], "target_y": nums[3], "steps": 20}),
                );
                Ok(())
            } else {
                Err(format!("could not resolve drag rects for {from} -> {to}"))
            }
        }
        Step::Eval(code) => {
            m.eval(code);
            Ok(())
        }
        Step::Viewport { w, h } => {
            m.call("browser_set_viewport", json!({"width": w, "height": h}));
            Ok(())
        }
        Step::SetTimezone(tz) => {
            m.call("browser_set_timezone", json!({"timezone_id": tz}));
            Ok(())
        }
        Step::SetLocale(loc) => {
            m.call("browser_set_locale", json!({"locale": loc}));
            Ok(())
        }
        Step::EmulateColorScheme(scheme) => {
            m.call("browser_emulate_media", json!({"color_scheme": scheme}));
            Ok(())
        }
        Step::GrantGeolocation => {
            m.call(
                "browser_grant_permissions",
                json!({"permissions": ["geolocation"]}),
            );
            Ok(())
        }
        Step::SetGeolocation { lat, lon } => {
            m.call(
                "browser_set_geolocation",
                json!({"latitude": lat, "longitude": lon, "accuracy": 50}),
            );
            Ok(())
        }
        Step::SetStorage { area, key, value } => {
            let r = m.call(
                "browser_set_storage",
                json!({"origin": base, "storage_type": area, "key": key, "value": value}),
            );
            if Mcp::is_err(&r) {
                Err(format!("set_storage failed: {}", Mcp::text(&r)))
            } else {
                Ok(())
            }
        }
        Step::SetCookie { name, value } => {
            m.call(
                "browser_set_cookies",
                json!({"cookies": [{"name": name, "value": value, "domain": "127.0.0.1", "path": "/"}]}),
            );
            Ok(())
        }
        Step::HandleDialog { accept, prompt } => {
            let mut args = json!({"accept": accept});
            if let Some(p) = prompt {
                args["prompt_text"] = json!(p);
            }
            m.call("browser_handle_dialog", args);
            Ok(())
        }
        Step::NewTab(p) => {
            m.call("browser_new_tab", json!({"url": url(base, p)}));
            Ok(())
        }
        Step::SwitchTabIndex(i) => {
            let tabs = tabs_array(&Mcp::json(&m.call("browser_list_tabs", json!({}))));
            let id = tabs
                .get(*i)
                .and_then(|t| t["tab_id"].as_str())
                .map(str::to_string);
            match id {
                Some(id) => {
                    m.call("browser_switch_tab", json!({"tab_id": id}));
                    Ok(())
                }
                None => Err(format!("no tab at index {i}")),
            }
        }
        Step::SwitchTabUrl(substr) => {
            let tabs = tabs_array(&Mcp::json(&m.call("browser_list_tabs", json!({}))));
            let id = tabs
                .iter()
                .find(|t| t["url"].as_str().unwrap_or("").contains(substr.as_str()))
                .and_then(|t| t["tab_id"].as_str())
                .map(str::to_string);
            match id {
                Some(id) => {
                    m.call("browser_switch_tab", json!({"tab_id": id}));
                    Ok(())
                }
                None => Err(format!("no tab with url containing {substr:?}")),
            }
        }
        Step::SaveState(tok) => {
            let path = std::env::temp_dir().join(format!("agentyc_rw_{tok}.json"));
            m.call(
                "browser_save_state",
                json!({"path": path.to_str().unwrap()}),
            );
            Ok(())
        }
        Step::LoadState(tok) => {
            let path = std::env::temp_dir().join(format!("agentyc_rw_{tok}.json"));
            m.call(
                "browser_load_state",
                json!({"path": path.to_str().unwrap()}),
            );
            Ok(())
        }
    }
}

fn run_check(m: &mut Mcp, _base: &str, check: &Check) -> Result<(), String> {
    match check {
        Check::TextPresent(s) => {
            let body = body_text(m);
            if body.contains(s.as_str()) {
                Ok(())
            } else {
                Err(format!("expected text {s:?} not found"))
            }
        }
        Check::TextAbsent(s) => {
            let body = body_text(m);
            if body.contains(s.as_str()) {
                Err(format!("unexpected text {s:?} present"))
            } else {
                Ok(())
            }
        }
        Check::UrlContains(s) => {
            let href = m.eval("location.href");
            if href.contains(s.as_str()) {
                Ok(())
            } else {
                Err(format!("url {href} does not contain {s:?}"))
            }
        }
        Check::TitleContains(s) => {
            let t = m.eval("document.title");
            if t.contains(s.as_str()) {
                Ok(())
            } else {
                Err(format!("title {t} does not contain {s:?}"))
            }
        }
        Check::JsEq { code, expected } => {
            let got = m.eval(code);
            let got = got.trim().trim_matches('"');
            if got == expected {
                Ok(())
            } else {
                Err(format!("js {code:?} = {got:?}, expected {expected:?}"))
            }
        }
        Check::JsContains { code, needle } => {
            let got = m.eval(code);
            if got.contains(needle.as_str()) {
                Ok(())
            } else {
                Err(format!("js {code:?} = {got:?} does not contain {needle:?}"))
            }
        }
        Check::ElementCount { selector, count } => {
            let r = Mcp::json(&m.call(
                "browser_find_elements",
                json!({"selector": selector, "max_results": 500}),
            ));
            let n = r.as_array().map(|a| a.len()).unwrap_or(0);
            if n == *count {
                Ok(())
            } else {
                Err(format!(
                    "selector {selector:?} matched {n}, expected {count}"
                ))
            }
        }
        Check::ElementCountAtLeast { selector, min } => {
            let r = Mcp::json(&m.call(
                "browser_find_elements",
                json!({"selector": selector, "max_results": 500}),
            ));
            let n = r.as_array().map(|a| a.len()).unwrap_or(0);
            if n >= *min {
                Ok(())
            } else {
                Err(format!(
                    "selector {selector:?} matched {n}, expected >= {min}"
                ))
            }
        }
        Check::ExtractContains { query, needle } => {
            let t = Mcp::text(&m.call("browser_extract_content", json!({"query": query})));
            if t.contains(needle.as_str()) {
                Ok(())
            } else {
                Err(format!("extract {query:?} did not contain {needle:?}"))
            }
        }
        Check::FrameCountAtLeast(n) => {
            let frames = Mcp::json(&m.call("browser_list_frames", json!({})));
            let count = frames.as_array().map(|a| a.len()).unwrap_or(0);
            if count >= *n {
                Ok(())
            } else {
                Err(format!("frame count {count} < {n}"))
            }
        }
        Check::FrameHtmlContains(s) => {
            let frames = Mcp::json(&m.call("browser_list_frames", json!({})));
            if let Some(arr) = frames.as_array() {
                for f in arr {
                    if let Some(fid) = f["frame_id"].as_str() {
                        let html =
                            Mcp::text(&m.call("browser_get_frame_html", json!({"frame_id": fid})));
                        if html.contains(s.as_str()) {
                            return Ok(());
                        }
                    }
                }
            }
            Err(format!("no frame html contained {s:?}"))
        }
        Check::FocusedContains(s) => {
            let f = Mcp::text(&m.call("browser_get_focused_element", json!({})));
            if f.to_lowercase().contains(&s.to_lowercase()) {
                Ok(())
            } else {
                Err(format!("focused element {f:?} does not contain {s:?}"))
            }
        }
    }
}
