//! Autonomous-agent capability test — the read -> reason -> act loop an agent relies on.
//!
//! Exercises the read -> reason -> act loop an LLM agent relies on: structured
//! error content, orientation via titles, multi-step form flows, dynamic-content
//! waits, deterministic extraction for decision-making, SPA routing, and
//! `since_hash` polling. All deterministic (data: URLs), so Chrome-only.
//!
//! Run: `AGENTYC_HEADLESS=1 cargo test --test agent_autonomy -- --nocapture`

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
fn autonomy_stale_ref_is_readable_error_content() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<button>B</button>");
    m.wait(0.3);
    let old_ref = Mcp::ref_by_tag(&m.state("full"), "button").unwrap_or_else(|| "e99999".into());
    m.nav("data:text/html,<p>different page</p>");
    m.wait(0.2);
    let r = m.call("browser_click", json!({"ref": old_ref}));
    assert!(
        Mcp::is_err(&r),
        "stale ref should be an error result the agent can read"
    );
    assert!(!Mcp::text(&r).is_empty(), "error must carry a message");
}

#[test]
fn autonomy_invalid_url_is_recoverable_error() {
    let mut m = Mcp::start();
    require_browser!(m);
    let r = m.navigate("not-a-valid-url");
    assert!(Mcp::is_err(&r), "invalid url should be an error");
    assert!(!Mcp::text(&r).is_empty(), "error must carry a message");
}

#[test]
fn autonomy_navigate_returns_title_for_orientation() {
    let mut m = Mcp::start();
    require_browser!(m);
    let r = m.navigate("data:text/html,<title>Product Dashboard</title><h1>Hello</h1>");
    assert!(
        Mcp::text(&r).contains("Product Dashboard"),
        "navigate should return title"
    );
}

#[test]
fn autonomy_complete_login_form_flow() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<form><input id=u type=text placeholder=Username><input id=p type=password placeholder=Password><button type=button onclick=\"document.title='logged_in'\">Submit</button></form>");
    m.wait(0.4);
    let s = m.state("full");
    let user = Mcp::ref_by_placeholder(&s, "username")
        .or_else(|| Mcp::ref_by_type(&s, "text"))
        .expect("user ref");
    let pass = Mcp::ref_by_type(&s, "password").expect("pass ref");
    let btn = Mcp::ref_by_tag(&s, "button").expect("btn ref");
    m.call("browser_type", json!({"ref": user, "text": "admin"}));
    m.call("browser_type", json!({"ref": pass, "text": "secret123"}));
    m.call("browser_click", json!({"ref": btn}));
    m.wait(0.2);
    assert_eq!(
        m.eval("document.title"),
        "\"logged_in\"",
        "login flow did not complete"
    );
}

#[test]
fn autonomy_dynamic_content_load_and_wait() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<button onclick=\"setTimeout(function(){document.getElementById('d').innerHTML='<p>item1</p><p>item2</p><p>item3</p>'},300)\">Load</button><div id=d></div>");
    m.wait(0.3);
    let btn = Mcp::ref_by_tag(&m.state("full"), "button").expect("button ref");
    m.call("browser_click", json!({"ref": btn}));
    let w = m.call(
        "browser_wait_for_element",
        json!({"text": "item1", "appear": true, "timeout_seconds": 3}),
    );
    assert!(
        !Mcp::is_err(&w) && Mcp::text(&w).contains("appeared"),
        "wait_for_element"
    );
    let items = Mcp::json(&m.call("browser_find_elements", json!({"selector": "#d p"})));
    assert_eq!(
        items.as_array().map(|a| a.len()).unwrap_or(0),
        3,
        "expected 3 items"
    );
}

#[test]
fn autonomy_table_extraction_for_reasoning() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<table><tr><th>Product</th><th>Price</th><th>Stock</th></tr><tr><td>Widget A</td><td>$10</td><td>5</td></tr><tr><td>Widget B</td><td>$25</td><td>0</td></tr><tr><td>Widget C</td><td>$15</td><td>12</td></tr></table>");
    m.wait(0.2);
    let t = Mcp::text(&m.call("browser_extract_content", json!({"query": "table rows"})));
    // The agent can read all three products and reason over stock levels.
    assert!(
        t.contains("Widget A") && t.contains("Widget B") && t.contains("Widget C"),
        "missing products: {t}"
    );
    assert!(
        t.contains("12") && t.contains('5'),
        "missing stock values: {t}"
    );
}

#[test]
fn autonomy_search_page_and_scroll_to_target() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<div style=\"height:4000px\"></div><section><h2 id=tos>Terms of Service</h2><p>Section 1</p></section>");
    m.wait(0.2);
    let found = m.call(
        "browser_search_page",
        json!({"pattern": "Terms of Service"}),
    );
    assert!(
        Mcp::text(&found).contains("Terms of Service"),
        "search_page"
    );
    m.call(
        "browser_scroll_to_text",
        json!({"text": "Terms of Service"}),
    );
    m.wait(0.4);
    let y: f64 = m.eval("window.scrollY").parse().unwrap_or(0.0);
    assert!(y > 100.0, "did not scroll to target: y={y}");
}

#[test]
fn autonomy_spa_hash_routing() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<script>setTimeout(()=>{location.hash='#/users/42'},200)</script>");
    let r = m.call(
        "browser_wait_for_url",
        json!({"url_substring": "users/42", "timeout_seconds": 3}),
    );
    assert!(!Mcp::is_err(&r), "spa hash routing: {r:?}");
}

#[test]
fn autonomy_multi_tab_parallel_browsing() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<title>Tab A</title>");
    m.call(
        "browser_new_tab",
        json!({"url": "data:text/html,<title>Tab B</title>"}),
    );
    m.wait(0.3);
    let tabs = Mcp::json(&m.call("browser_list_tabs", json!({})));
    let arr = tabs.as_array().cloned().unwrap_or_default();
    assert!(arr.len() >= 2, "expected >=2 tabs, got {}", arr.len());
    let id = arr[0]["tab_id"].as_str().unwrap_or("");
    m.call("browser_switch_tab", json!({"tab_id": id}));
    m.wait(0.1);
    let title = m.eval("document.title");
    assert!(
        title.contains("Tab A") || title.contains("Tab B"),
        "tab switch: {title}"
    );
}

#[test]
fn autonomy_js_eval_for_reasoning() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav(
        "data:text/html,<ul><li class=p>Alpha</li><li class=p>Beta</li><li class=p>Gamma</li></ul>",
    );
    m.wait(0.2);
    assert_eq!(
        m.eval("document.querySelectorAll('.p').length"),
        "3",
        "count"
    );
    let texts = m.eval_json("Array.from(document.querySelectorAll('.p')).map(e=>e.textContent)");
    assert!(
        texts
            .as_array()
            .map(|a| a.iter().any(|v| v.as_str() == Some("Alpha")))
            .unwrap_or(false),
        "array eval: {texts}"
    );
}

#[test]
fn autonomy_since_hash_polling() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<button>Static</button>");
    m.wait(0.2);
    let h = m.state("min")["state_hash"]
        .as_str()
        .unwrap_or("")
        .to_string();
    assert!(!h.is_empty());
    let again = Mcp::json(&m.call("browser_get_state", json!({"mode": "min", "since_hash": h})));
    assert_eq!(
        again["changed"].as_bool(),
        Some(false),
        "since_hash should report no change"
    );
}

#[test]
fn autonomy_dom_stability_wait() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<div id=d>loading...</div><script>setTimeout(()=>{document.getElementById('d').textContent='done'},200)</script>");
    let r = m.call(
        "browser_wait_for_stable_dom",
        json!({"timeout_seconds": 3, "quiet_ms": 300}),
    );
    assert!(!Mcp::is_err(&r), "wait_for_stable_dom: {r:?}");
}
