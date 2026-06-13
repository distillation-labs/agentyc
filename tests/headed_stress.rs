//! Headed-mode stress test — visible-browser interactions and concurrent network waits.
//!
//! These run a *visible* (non-headless) Chrome, so they require a display and
//! are `#[ignore]` by default. Several also use real network.
//!
//! Run: `cargo test --test headed_stress -- --ignored --nocapture`

use std::thread::sleep;
use std::time::Duration;

use agentyc_tests::Mcp;
use serde_json::json;

macro_rules! require_browser {
    ($m:expr) => {
        if !$m.browser_available() {
            eprintln!("skipping: no Chrome/Chromium available");
            return;
        }
    };
}

#[test]
#[ignore = "headed: needs a display"]
fn headed_screenshot_and_scroll_and_viewport() {
    let mut m = Mcp::start_headed();
    require_browser!(m);

    m.nav("data:text/html,<body style=\"background:red\"><h1 style=\"color:white\">HEADED</h1></body>");
    m.wait(0.3);
    assert!(
        Mcp::has_image(&m.call("browser_screenshot", json!({}))),
        "screenshot"
    );

    m.nav("data:text/html,<div style=\"height:5000px;background:linear-gradient(red,blue)\"></div><p>bottom</p>");
    m.wait(0.3);
    m.call("browser_scroll", json!({"direction": "down", "pages": 5}));
    m.wait(0.3);
    let y: f64 = m.eval("window.scrollY").parse().unwrap_or(0.0);
    assert!(y > 0.0, "scrollY={y}");

    m.nav("data:text/html,<p>vp</p>");
    m.wait(0.2);
    m.call(
        "browser_set_viewport",
        json!({"width": 1440, "height": 900}),
    );
    m.wait(0.1);
    assert_eq!(m.eval("window.innerWidth"), "1440", "viewport");
}

#[test]
#[ignore = "headed: needs a display"]
fn headed_rapid_navigation() {
    let mut m = Mcp::start_headed();
    require_browser!(m);
    for i in 0..5 {
        m.nav(&format!(
            "data:text/html,<title>P{i}</title><p>Page {i}</p>"
        ));
        m.wait(0.1);
    }
    assert!(m.eval("document.title").contains("P4"), "final title");
}

#[test]
#[ignore = "headed: needs a display"]
fn headed_keyboard_focus_and_tab() {
    let mut m = Mcp::start_headed();
    require_browser!(m);
    m.nav("data:text/html,<input id=a type=text><input id=b type=text><input id=c type=text>");
    m.wait(0.3);
    let inputs = Mcp::elements(&m.state("full"));
    let first = inputs
        .iter()
        .find(|e| e["tag"].as_str() == Some("input"))
        .and_then(|e| e["ref"].as_str())
        .expect("no input");
    m.call("browser_click", json!({"ref": first}));
    m.wait(0.15);
    assert_eq!(
        Mcp::json(&m.call("browser_get_focused_element", json!({})))["tag"].as_str(),
        Some("input")
    );
    m.call("browser_press_key", json!({"key": "Tab"}));
    m.wait(0.15);
    assert_eq!(
        Mcp::json(&m.call("browser_get_focused_element", json!({})))["tag"].as_str(),
        Some("input")
    );
}

#[test]
#[ignore = "headed: needs a display"]
fn headed_right_click_context_menu() {
    let mut m = Mcp::start_headed();
    require_browser!(m);
    m.nav("data:text/html,<div id=d style=\"width:200px;height:100px;background:blue\" oncontextmenu=\"document.title='rclicked';return false\">Right-click me</div>");
    m.wait(0.3);
    let r = m.call(
        "browser_right_click",
        json!({"coordinate_x": 100, "coordinate_y": 50}),
    );
    assert!(!Mcp::is_err(&r), "right_click: {r:?}");
    m.wait(0.15);
    assert!(
        m.eval("document.title").contains("rclicked"),
        "contextmenu did not fire"
    );
    m.call("browser_press_key", json!({"key": "Escape"}));
}

#[test]
#[ignore = "headed: needs a display"]
fn headed_double_click() {
    let mut m = Mcp::start_headed();
    require_browser!(m);
    m.nav("data:text/html,<p id=p ondblclick=\"document.title='dblclicked'\" style=\"padding:20px;font-size:20px\">Double click me</p>");
    m.wait(0.3);
    m.call(
        "browser_double_click",
        json!({"coordinate_x": 100, "coordinate_y": 20}),
    );
    m.wait(0.2);
    assert!(
        m.eval("document.title").contains("dblclicked"),
        "dblclick did not fire"
    );
}

#[test]
#[ignore = "headed: needs a display"]
fn headed_drag_and_drop() {
    let mut m = Mcp::start_headed();
    require_browser!(m);
    m.nav("data:text/html,<div id=src style=\"position:absolute;left:50px;top:80px;width:80px;height:60px;background:red\">DRAG</div><div id=tgt style=\"position:absolute;left:250px;top:80px;width:120px;height:80px;background:green\" onmouseup=\"document.title='dropped'\">DROP</div>");
    m.wait(0.4);
    let r = m.call(
        "browser_drag_to",
        json!({"source_x": 90, "source_y": 110, "target_x": 310, "target_y": 120, "steps": 20}),
    );
    assert!(!Mcp::is_err(&r), "drag_to: {r:?}");
}

#[test]
#[ignore = "headed: needs a display"]
fn headed_fill_form_batch() {
    let mut m = Mcp::start_headed();
    require_browser!(m);
    m.nav("data:text/html,<form><input id=n type=text placeholder=Name><input id=e type=email placeholder=Email></form>");
    m.wait(0.4);
    let s = m.state("full");
    let n = Mcp::ref_by_placeholder(&s, "name").expect("name ref");
    let e = Mcp::ref_by_placeholder(&s, "email").expect("email ref");
    m.call(
        "browser_fill_form",
        json!({"fields": [
            {"ref": n, "text": "Alice"},
            {"ref": e, "text": "alice@test.com"},
        ]}),
    );
    m.wait(0.2);
    assert!(
        m.eval("document.getElementById('n').value")
            .contains("Alice")
    );
    assert!(
        m.eval("document.getElementById('e').value")
            .contains("alice")
    );
}

#[test]
#[ignore = "headed: needs a display"]
fn headed_structured_error_codes() {
    let mut m = Mcp::start_headed();
    require_browser!(m);
    m.nav("data:text/html,<p>e</p>");
    m.wait(0.2);
    let r = m.call("browser_click", json!({"ref": "e999999999"}));
    let msg = Mcp::text(&r);
    assert!(
        [
            "[stale_ref]",
            "[element_not_interactable]",
            "[no_browser]",
            "[timeout]"
        ]
        .iter()
        .any(|c| msg.contains(c)),
        "no structured code: {msg}"
    );
    assert!(msg.contains("Hint:"), "no hint: {msg}");
}

#[test]
#[ignore = "headed: needs a display"]
fn headed_crash_recovery() {
    let mut m = Mcp::start_headed();
    require_browser!(m);
    m.nav("data:text/html,<title>before</title>");
    m.wait(0.2);
    m.call("browser_close_all", json!({}));
    m.wait(0.5);
    m.nav("data:text/html,<title>Recovered</title>");
    m.wait(0.4);
    assert!(
        m.eval("document.title").contains("Recovered"),
        "crash recovery"
    );
}

#[test]
#[ignore = "headed + network"]
fn headed_wait_for_request_real_network() {
    let mut m = Mcp::start_headed();
    require_browser!(m);
    m.nav("data:text/html,<title>start</title>");
    m.wait(0.2);
    // Arm the wait, then trigger navigation concurrently (two in-flight requests).
    let wait_id = m.send_async(
        "tools/call",
        json!({"name": "browser_wait_for_request", "arguments": {"url_substring": "httpbin.org", "timeout_seconds": 8}}),
    );
    sleep(Duration::from_millis(300));
    m.send_async(
        "tools/call",
        json!({"name": "browser_navigate", "arguments": {"url": "https://httpbin.org/get"}}),
    );
    let r = m.read_response(wait_id);
    assert!(
        !Mcp::is_err(&r) && Mcp::text(&r).contains("httpbin"),
        "wait_for_request: {r:?}"
    );
}

#[test]
#[ignore = "headed + network"]
fn headed_wait_for_response_real_network() {
    let mut m = Mcp::start_headed();
    require_browser!(m);
    m.nav("data:text/html,<title>start</title>");
    m.wait(0.2);
    let wait_id = m.send_async(
        "tools/call",
        json!({"name": "browser_wait_for_response", "arguments": {"url_substring": "httpbin.org", "status": 200, "timeout_seconds": 8}}),
    );
    sleep(Duration::from_millis(300));
    m.send_async(
        "tools/call",
        json!({"name": "browser_navigate", "arguments": {"url": "https://httpbin.org/status/200"}}),
    );
    let r = m.read_response(wait_id);
    assert!(!Mcp::is_err(&r), "wait_for_response: {r:?}");
}

#[test]
#[ignore = "headed + network"]
fn headed_multi_tab_real_pages() {
    let mut m = Mcp::start_headed();
    require_browser!(m);
    m.nav("https://example.com");
    m.wait(0.4);
    m.call(
        "browser_new_tab",
        json!({"url": "https://httpbin.org/json"}),
    );
    m.wait(0.5);
    let tabs = Mcp::json(&m.call("browser_list_tabs", json!({})));
    let arr = tabs.as_array().cloned().unwrap_or_default();
    assert!(arr.len() >= 2, "expected >=2 tabs, got {}", arr.len());
    if let Some(ex) = arr
        .iter()
        .find(|t| t["url"].as_str().unwrap_or("").contains("example"))
    {
        m.call(
            "browser_switch_tab",
            json!({"tab_id": ex["tab_id"].as_str().unwrap_or("")}),
        );
        m.wait(0.3);
        assert!(
            m.eval("document.title").contains("Example"),
            "tab switch content"
        );
    }
}
