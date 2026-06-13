//! End-to-end suite — broad soak coverage of the full MCP tool surface.
//!
//! Covers navigation, state/DOM, click/type/select/scroll loops, inspection,
//! extraction, wait tools, tabs, error handling, dense `since_hash` reads, and
//! bulk evaluate/find. Loop counts default low for CI; scale them up with
//! `AGENTYC_TEST_SCALE` (e.g. `AGENTYC_TEST_SCALE=25`) to reproduce the
//! original ~10k-operation run.
//!
//! Run: `AGENTYC_HEADLESS=1 cargo test --test e2e_suite -- --nocapture`
//! Network-dependent cases are marked `#[ignore]` (run with `-- --ignored`).

use std::collections::HashSet;

use agentyc_tests::{Mcp, iters};
use serde_json::json;

/// Skip the test body (printing a notice) when no browser is installed.
macro_rules! require_browser {
    ($m:expr) => {
        if !$m.browser_available() {
            eprintln!("skipping: no Chrome/Chromium available");
            return;
        }
    };
}

// ── 1. Navigation ───────────────────────────────────────────────────────────

#[test]
fn e2e_navigation_unique_hashes_and_titles() {
    let mut m = Mcp::start();
    require_browser!(m);

    let mut hashes: HashSet<String> = HashSet::new();
    let n = iters(30);
    for i in 0..n {
        m.nav(&format!(
            "data:text/html,<title>P{i}</title><p>Content {i}</p>"
        ));
        m.wait(0.05);
        let s = m.state("min");
        let h = s["state_hash"].as_str().unwrap_or("").to_string();
        assert!(!h.is_empty(), "missing state_hash at {i}");
        assert!(hashes.insert(h) || i == 0, "duplicate hash at {i}");
        let title = s["title"].as_str().unwrap_or("");
        assert!(
            title.contains(&format!("P{i}")),
            "wrong title at {i}: {title}"
        );
    }
}

#[test]
fn e2e_navigation_back_forward_refresh() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<title>A</title>");
    m.nav("data:text/html,<title>B</title>");
    assert!(
        !Mcp::is_err(&m.call("browser_go_back", json!({}))),
        "go_back"
    );
    m.wait(0.3);
    assert!(
        !Mcp::is_err(&m.call("browser_go_forward", json!({}))),
        "go_forward"
    );
    m.wait(0.3);
    assert!(
        !Mcp::is_err(&m.call("browser_refresh", json!({}))),
        "refresh"
    );
}

#[test]
fn e2e_since_hash_unchanged_then_changed() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<title>Static</title><button>B</button>");
    m.wait(0.2);
    let h = m.state("min")["state_hash"]
        .as_str()
        .unwrap_or("")
        .to_string();
    assert!(!h.is_empty());
    let unchanged = m.call("browser_get_state", json!({"mode": "min", "since_hash": h}));
    assert_eq!(Mcp::json(&unchanged)["changed"].as_bool(), Some(false));
    m.nav("data:text/html,<title>Changed</title>");
    m.wait(0.1);
    let changed = m.call("browser_get_state", json!({"mode": "min", "since_hash": h}));
    assert_eq!(Mcp::json(&changed)["changed"].as_bool(), Some(true));
}

#[test]
fn e2e_invalid_url_is_error() {
    let mut m = Mcp::start();
    require_browser!(m);
    let r = m.navigate("not-a-valid-url");
    assert!(Mcp::is_err(&r), "expected isError for invalid url: {r:?}");
}

// ── 2. State & DOM ────────────────────────────────────────────────────────────

#[test]
fn e2e_state_has_all_element_types() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<button>B1</button><input type=\"text\" placeholder=\"Name\"><select><option>A</option></select><a href=\"%23link\">Link</a><textarea>ta</textarea>");
    m.wait(0.4);
    let s = m.state("full");
    let els = Mcp::elements(&s);
    let has = |tag: &str| els.iter().any(|e| e["tag"].as_str() == Some(tag));
    assert!(has("button"), "no button");
    assert!(has("input"), "no input");
    assert!(has("select"), "no select");
    assert!(has("a"), "no link");
    assert!(has("textarea"), "no textarea");
    assert!(s.get("viewport").is_some(), "no viewport");
    assert!(s["state_hash"].is_string(), "no state_hash");
    assert!(s["tabs"].is_array(), "no tabs array");

    let min_count = Mcp::elements(&m.state("min")).len();
    assert!(min_count <= els.len(), "min should be <= full");
}

#[test]
fn e2e_get_html_and_screenshot_and_viewport() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<div id=\"target\"><span>Hello</span></div>");
    m.wait(0.2);
    let r = m.call("browser_get_html", json!({"selector": "#target"}));
    assert!(Mcp::text(&r).contains("Hello"), "get_html missing content");

    m.nav("data:text/html,<body style=\"background:blue\"><h1>Shot</h1></body>");
    m.wait(0.2);
    let sr = m.call("browser_screenshot", json!({}));
    assert!(Mcp::has_image(&sr), "screenshot has no image");

    assert!(!Mcp::is_err(&m.call(
        "browser_set_viewport",
        json!({"width": 800, "height": 600})
    )));
    assert_eq!(m.eval("window.innerWidth"), "800", "viewport not applied");
}

// ── 3. Click loop ─────────────────────────────────────────────────────────────

#[test]
fn e2e_click_loop_updates_title() {
    let mut m = Mcp::start();
    require_browser!(m);
    for i in 0..iters(20) {
        m.nav(&format!(
            "data:text/html,<button onclick=\"document.title='clicked{i}'\">Click {i}</button>"
        ));
        m.wait(0.15);
        let s = m.state("full");
        let btn = Mcp::ref_by_tag(&s, "button").expect("no button ref");
        assert!(
            !Mcp::is_err(&m.call("browser_click", json!({"ref": btn}))),
            "click {i}"
        );
        m.wait(0.1);
        assert!(
            m.eval("document.title").contains(&format!("clicked{i}")),
            "click effect {i}"
        );
    }
}

// ── 4. Type loop ──────────────────────────────────────────────────────────────

#[test]
fn e2e_type_loop_sets_value() {
    let mut m = Mcp::start();
    require_browser!(m);
    for i in 0..iters(20) {
        let val = format!("test input {i} value");
        m.nav(&format!("data:text/html,<input id=\"i{i}\" type=\"text\">"));
        m.wait(0.15);
        let s = m.state("full");
        let inp = Mcp::ref_by_tag(&s, "input").expect("no input ref");
        assert!(
            !Mcp::is_err(&m.call("browser_type", json!({"ref": inp, "text": val}))),
            "type {i}"
        );
        m.wait(0.1);
        let got = m.eval(&format!("document.getElementById('i{i}').value"));
        assert!(got.contains(&val), "type value {i}: {got}");
    }
}

#[test]
fn e2e_press_keys_fire_events() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<input id=k><span id=o></span><script>document.getElementById('k').addEventListener('keydown',e=>document.getElementById('o').textContent=e.key)</script>");
    m.wait(0.3);
    m.eval("document.getElementById('k').focus()");
    for key in ["Enter", "ArrowDown", "Escape"] {
        m.call("browser_press_key", json!({"key": key}));
        m.wait(0.05);
        assert!(
            m.eval("document.getElementById('o').textContent")
                .contains(key),
            "key {key} not fired"
        );
    }
    assert!(!Mcp::is_err(
        &m.call("browser_press_key", json!({"key": "Control+a"}))
    ));
}

// ── 5. Forms ──────────────────────────────────────────────────────────────────

#[test]
fn e2e_select_option_loop() {
    let mut m = Mcp::start();
    require_browser!(m);
    for i in 0..iters(15) {
        m.nav(&format!("data:text/html,<select id=\"s{i}\"><option>Alpha</option><option>Beta</option><option>Gamma</option></select>"));
        m.wait(0.15);
        let s = m.state("full");
        let sel = Mcp::ref_by_tag(&s, "select").expect("no select ref");
        let opts = Mcp::json(&m.call("browser_get_dropdown_options", json!({"ref": sel})));
        assert!(
            opts.as_array().map(|a| a.len() >= 3).unwrap_or(false),
            "options {i}: {opts}"
        );
        assert!(
            !Mcp::is_err(&m.call("browser_select_option", json!({"ref": sel, "text": "Beta"}))),
            "select {i}"
        );
        m.wait(0.05);
        assert!(
            m.eval(&format!("document.getElementById('s{i}').value"))
                .contains("Beta"),
            "value {i}"
        );
    }
}

#[test]
fn e2e_fill_form_batch() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<form><input id=n type=\"text\" placeholder=\"Name\"><input id=e type=\"email\" placeholder=\"Email\"><textarea id=msg placeholder=\"Message\"></textarea></form>");
    m.wait(0.3);
    let s = m.state("full");
    let n_ref = Mcp::ref_by_placeholder(&s, "name").or_else(|| Mcp::ref_by_tag(&s, "input"));
    let e_ref = Mcp::ref_by_placeholder(&s, "email");
    let (n_ref, e_ref) = (n_ref.expect("no name ref"), e_ref.expect("no email ref"));
    let r = m.call(
        "browser_fill_form",
        json!({"fields": [
            {"ref": n_ref, "text": "Alice Smith"},
            {"ref": e_ref, "text": "alice@example.com"},
        ]}),
    );
    assert!(!Mcp::is_err(&r), "fill_form: {r:?}");
    m.wait(0.1);
    assert!(
        m.eval("document.getElementById('n').value")
            .contains("Alice")
    );
    assert!(
        m.eval("document.getElementById('e').value")
            .contains("alice")
    );
}

// ── 6. Scroll ─────────────────────────────────────────────────────────────────

#[test]
fn e2e_scroll_down_then_up() {
    let mut m = Mcp::start();
    require_browser!(m);
    for i in 0..iters(15) {
        m.nav(&format!(
            "data:text/html,<div style=\"height:{}px\"></div><p>Bottom {i}</p>",
            3000 + i * 100
        ));
        m.wait(0.15);
        m.call("browser_scroll", json!({"direction": "down", "pages": 3}));
        m.wait(0.15);
        let y1: f64 = m.eval("window.scrollY").parse().unwrap_or(0.0);
        assert!(y1 > 0.0, "scroll down {i}: y={y1}");
        m.call("browser_scroll", json!({"direction": "up", "pages": 10}));
        m.wait(0.1);
        let y2: f64 = m.eval("window.scrollY").parse().unwrap_or(-1.0);
        assert!(y2 < y1, "scroll up {i}: y={y2}");
    }
}

#[test]
fn e2e_scroll_to_text() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<div style=\"height:4000px\"></div><p>UNIQUE_MARKER_TEXT</p>");
    m.wait(0.2);
    assert!(!Mcp::is_err(&m.call(
        "browser_scroll_to_text",
        json!({"text": "UNIQUE_MARKER_TEXT"})
    )));
    m.wait(0.2);
    let sy: f64 = m.eval("window.scrollY").parse().unwrap_or(0.0);
    assert!(sy > 100.0, "did not scroll to text: y={sy}");
}

// ── 7. Inspection ─────────────────────────────────────────────────────────────

#[test]
fn e2e_inspection_tools() {
    let mut m = Mcp::start();
    require_browser!(m);

    m.nav("data:text/html,<ul><li class=item>A</li><li class=item>B</li><li class=item>C</li><li class=item>D</li><li class=item>E</li></ul>");
    m.wait(0.2);
    let els = Mcp::json(&m.call("browser_find_elements", json!({"selector": ".item"})));
    assert_eq!(
        els.as_array().map(|a| a.len()).unwrap_or(0),
        5,
        "find_elements count"
    );

    m.nav("data:text/html,<p>The quick brown fox. SECRET_TOKEN_XYZ123</p>");
    m.wait(0.2);
    let hits = Mcp::json(&m.call(
        "browser_search_page",
        json!({"pattern": "SECRET_TOKEN_XYZ123"}),
    ));
    assert!(
        hits.as_array().map(|a| !a.is_empty()).unwrap_or(false),
        "search_page"
    );
    let rx = Mcp::json(&m.call(
        "browser_search_page",
        json!({"pattern": "\\d+", "regex": true}),
    ));
    assert!(
        rx.as_array().map(|a| !a.is_empty()).unwrap_or(false),
        "regex search"
    );

    m.nav("data:text/html,<div id=d></div><script>setTimeout(()=>{document.getElementById('d').textContent='appeared_marker'},250)</script>");
    let w = m.call(
        "browser_wait_for_element",
        json!({"text": "appeared_marker", "appear": true, "timeout_seconds": 5}),
    );
    assert!(
        !Mcp::is_err(&w) && Mcp::text(&w).contains("appeared"),
        "wait_for_element"
    );

    m.nav("data:text/html,<input id=f type=text>");
    m.wait(0.2);
    m.eval("document.getElementById('f').focus()");
    m.wait(0.1);
    let fe = Mcp::json(&m.call("browser_get_focused_element", json!({})));
    assert_eq!(fe["tag"].as_str(), Some("input"), "focused element");

    m.nav("data:text/html,<a id=l href=\"/test-path\" data-custom=\"myvalue\">Link</a>");
    m.wait(0.2);
    let link = Mcp::ref_by_tag(&m.state("full"), "a").expect("no link ref");
    assert!(
        Mcp::text(&m.call(
            "browser_get_attribute",
            json!({"ref": link, "name": "href"})
        ))
        .contains("/test-path")
    );
    assert!(
        Mcp::text(&m.call(
            "browser_get_attribute",
            json!({"ref": link, "name": "data-custom"})
        ))
        .contains("myvalue")
    );
}

#[test]
fn e2e_evaluate_value_types() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<p>eval</p>");
    m.wait(0.2);
    for (code, expected) in [
        ("2+2", "4"),
        ("\"hello\".length", "5"),
        ("[1,2,3].length", "3"),
        ("true", "true"),
    ] {
        let got = m.eval(code);
        assert_eq!(got, expected, "eval {code}");
    }
}

// ── 8. Extraction ─────────────────────────────────────────────────────────────

#[test]
fn e2e_extraction_routes() {
    let mut m = Mcp::start();
    require_browser!(m);

    m.nav("data:text/html,<table><tr><th>Name</th><th>Score</th></tr><tr><td>Alice</td><td>95</td></tr><tr><td>Bob</td><td>87</td></tr></table>");
    m.wait(0.2);
    let t = Mcp::text(&m.call("browser_extract_content", json!({"query": "table rows"})));
    assert!(
        t.contains("Alice") && t.contains("Bob"),
        "table extract: {t}"
    );

    m.nav("data:text/html,<a href=\"/home\">Home</a><a href=\"/about\">About</a>");
    m.wait(0.2);
    let l = Mcp::text(&m.call(
        "browser_extract_content",
        json!({"query": "all links", "extract_links": true}),
    ));
    assert!(
        l.to_lowercase().contains("home") && l.to_lowercase().contains("about"),
        "links extract: {l}"
    );

    m.nav("data:text/html,<form><input name=user type=text><input name=pass type=password></form>");
    m.wait(0.2);
    let f = Mcp::text(&m.call("browser_extract_content", json!({"query": "form fields"})));
    assert!(
        f.contains("user") || f.contains("pass"),
        "form extract: {f}"
    );

    m.nav("data:text/html,<ul><li>Item One</li><li>Item Two</li></ul>");
    m.wait(0.2);
    let li = Mcp::text(&m.call("browser_extract_content", json!({"query": "list items"})));
    assert!(li.contains("Item"), "list extract: {li}");

    let unknown = m.call(
        "browser_extract_content",
        json!({"query": "banana purple elephant"}),
    );
    assert!(Mcp::is_err(&unknown), "unknown query should error");
}

// ── 9. Wait tools (deterministic) ─────────────────────────────────────────────

#[test]
fn e2e_wait_for_url_loop() {
    let mut m = Mcp::start();
    require_browser!(m);
    for i in 0..iters(20) {
        let html = String::from("data:text/html,<script>setTimeout(()=>{location.hash='#route")
            + &i.to_string()
            + "'},120)</script>";
        m.nav(&html);
        let r = m.call(
            "browser_wait_for_url",
            json!({"url_substring": format!("route{i}"), "timeout_seconds": 3}),
        );
        assert!(!Mcp::is_err(&r), "wait_for_url {i}: {r:?}");
    }
}

#[test]
fn e2e_wait_for_stable_dom() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<div id=d>loading</div><script>setTimeout(()=>{document.getElementById('d').textContent='done'},200)</script>");
    let r = m.call(
        "browser_wait_for_stable_dom",
        json!({"timeout_seconds": 3, "quiet_ms": 250}),
    );
    assert!(!Mcp::is_err(&r), "wait_for_stable_dom: {r:?}");
}

// ── 10. Frames ────────────────────────────────────────────────────────────────

#[test]
fn e2e_frames() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<p>main</p><iframe src=\"data:text/html,<p>inner</p>\" style=\"width:200px;height:100px\"></iframe>");
    m.wait(0.4);
    let frames = Mcp::json(&m.call("browser_list_frames", json!({})));
    assert!(
        frames.as_array().map(|a| !a.is_empty()).unwrap_or(false),
        "no frames"
    );
    let fid = frames[0]["frame_id"].as_str().unwrap_or("").to_string();
    let html = m.call("browser_get_frame_html", json!({"frame_id": fid}));
    assert!(!Mcp::is_err(&html), "get_frame_html: {html:?}");
}

// ── 11. Tabs ──────────────────────────────────────────────────────────────────

#[test]
fn e2e_tabs_lifecycle() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<title>Main</title>");
    for i in 0..5 {
        let r = m.call(
            "browser_new_tab",
            json!({"url": format!("data:text/html,<title>Tab{i}</title>")}),
        );
        assert!(!Mcp::is_err(&r), "new_tab {i}");
        m.wait(0.1);
    }
    let tabs = Mcp::json(&m.call("browser_list_tabs", json!({})));
    let arr = tabs.as_array().cloned().unwrap_or_default();
    assert!(arr.len() >= 6, "expected >=6 tabs, got {}", arr.len());

    // Switch to each of the first few tabs.
    for t in arr.iter().take(3) {
        if let Some(id) = t["tab_id"].as_str() {
            assert!(
                !Mcp::is_err(&m.call("browser_switch_tab", json!({"tab_id": id}))),
                "switch_tab"
            );
        }
    }
    // Close all but the first.
    for t in arr.iter().skip(1) {
        if let Some(id) = t["tab_id"].as_str() {
            m.call("browser_close_tab", json!({"tab_id": id}));
            m.wait(0.05);
        }
    }
    // close_all then re-navigate proves recovery.
    m.call("browser_close_all", json!({}));
    m.wait(0.4);
    m.nav("data:text/html,<title>Restarted</title>");
    m.wait(0.2);
    assert!(
        m.eval("document.title").contains("Restarted"),
        "restart after close_all"
    );
}

// ── 12. Emulation ─────────────────────────────────────────────────────────────

#[test]
fn e2e_emulation() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<p>emulation</p>");
    m.wait(0.2);
    assert!(!Mcp::is_err(&m.call(
        "browser_set_user_agent",
        json!({"user_agent": "TestBot/1.0"})
    )));
    assert!(!Mcp::is_err(&m.call(
        "browser_set_timezone",
        json!({"timezone_id": "America/New_York"})
    )));
    let off: i64 = m
        .eval("new Date().getTimezoneOffset()")
        .parse()
        .unwrap_or(0);
    assert!(off == 240 || off == 300, "timezone offset {off}");
    assert!(!Mcp::is_err(
        &m.call("browser_set_locale", json!({"locale": "fr-FR"}))
    ));
    assert!(!Mcp::is_err(
        &m.call("browser_emulate_media", json!({"color_scheme": "dark"}))
    ));
    assert!(!Mcp::is_err(&m.call(
        "browser_grant_permissions",
        json!({"permissions": ["geolocation"]})
    )));
    assert!(!Mcp::is_err(&m.call(
        "browser_set_geolocation",
        json!({"latitude": 37.77, "longitude": -122.41, "accuracy": 50})
    )));
    assert!(!Mcp::is_err(&m.call(
        "browser_set_extra_headers",
        json!({"headers": {"X-Test": "value"}})
    )));
    assert!(!Mcp::is_err(
        &m.call("browser_set_extra_headers", json!({"headers": {}}))
    ));
    m.call("browser_set_timezone", json!({"timezone_id": ""}));
    m.call("browser_set_locale", json!({"locale": ""}));
}

// ── 13. Error handling & recovery ─────────────────────────────────────────────

#[test]
fn e2e_invalid_refs_are_structured_errors() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<p>errors</p>");
    m.wait(0.2);
    for i in 0..iters(20) {
        let r = m.call("browser_click", json!({"ref": format!("e{}", 999000 + i)}));
        assert!(Mcp::is_err(&r), "invalid ref {i} should error");
        let msg = Mcp::text(&r);
        assert!(
            ["[stale_ref]", "[element_not_interactable]", "[no_browser]"]
                .iter()
                .any(|c| msg.contains(c)),
            "structured code missing {i}: {msg}"
        );
    }
    // wait_for_element timeout is an error too.
    let to = m.call(
        "browser_wait_for_element",
        json!({"text": "never_appears_xyzzy", "appear": true, "timeout_seconds": 0.5}),
    );
    assert!(Mcp::is_err(&to), "timeout should error");
    // Recovery: a normal navigation still works.
    m.nav("data:text/html,<title>Recovered</title>");
    assert!(
        m.eval("document.title").contains("Recovered"),
        "recovery after errors"
    );
}

// ── 14. Dense state reads ──────────────────────────────────────────────────────

#[test]
fn e2e_dense_since_hash_reads() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<button>A</button><input type=text><select><option>X</option></select>");
    m.wait(0.3);
    let h = m.state("min")["state_hash"]
        .as_str()
        .unwrap_or("")
        .to_string();
    assert!(!h.is_empty());
    for i in 0..iters(100) {
        let s = Mcp::json(&m.call("browser_get_state", json!({"mode": "min", "since_hash": h})));
        assert_eq!(
            s["changed"].as_bool(),
            Some(false),
            "dense read {i} should be unchanged"
        );
    }
}

// ── 15. Bulk evaluate / find ───────────────────────────────────────────────────

#[test]
fn e2e_bulk_evaluate_counter_and_expressions() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<div id=c>0</div>");
    m.wait(0.2);
    for i in 0..iters(50) {
        let code = format!("document.getElementById('c').textContent = '{i}'; {i}");
        let v = m.eval_json(&code);
        assert_eq!(v.as_i64(), Some(i as i64), "eval counter {i}");
    }
    m.nav("data:text/html,<p>eval</p>");
    m.wait(0.1);
    for i in 0..iters(30) {
        let got = m.eval(&format!("{i}*{i}"));
        assert_eq!(got, (i * i).to_string(), "bulk eval {i}");
    }
}

#[test]
fn e2e_bulk_find_elements() {
    let mut m = Mcp::start();
    require_browser!(m);
    let mut html = String::from("data:text/html,<ul>");
    for i in 0..50 {
        html += &format!("<li class=row data-id={i}>Row {i}</li>");
    }
    html += "</ul>";
    m.nav(&html);
    m.wait(0.3);
    for i in 0..iters(30) {
        let els = Mcp::json(&m.call(
            "browser_find_elements",
            json!({"selector": ".row", "max_results": 50}),
        ));
        assert_eq!(
            els.as_array().map(|a| a.len()).unwrap_or(0),
            50,
            "find .row iter {i}"
        );
    }
}

#[test]
fn e2e_interaction_stress_type_cycles() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<input id=stress type=text>");
    m.wait(0.3);
    let inp = Mcp::ref_by_tag(&m.state("full"), "input").expect("no input");
    for i in 0..iters(30) {
        let val = format!("stress_{i}_{}", "x".repeat(i % 12));
        m.call("browser_type", json!({"ref": inp, "text": val.clone()}));
        let got = m.eval("document.getElementById('stress').value");
        assert!(got.contains(&val), "stress type {i}: {got}");
    }
}

// ── 16. PDF & screenshot ───────────────────────────────────────────────────────

#[test]
fn e2e_pdf_and_screenshot() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("data:text/html,<body style=\"background:teal\"><h1>PDF</h1></body>");
    m.wait(0.3);
    for i in 0..iters(4) {
        let sr = m.call("browser_screenshot", json!({"full_page": i % 2 == 0}));
        assert!(Mcp::has_image(&sr), "screenshot {i} has no image");
    }
    let r = m.call(
        "browser_save_as_pdf",
        json!({"file_name": "agentyc_e2e_test.pdf"}),
    );
    assert!(!Mcp::is_err(&r), "save_as_pdf: {r:?}");
}

// ── 17. Save / load state ───────────────────────────────────────────────────────

#[test]
fn e2e_save_and_load_state() {
    let mut m = Mcp::start();
    require_browser!(m);
    let tmp = std::env::temp_dir().join("agentyc_e2e_state.json");
    m.nav("data:text/html,<title>state</title>");
    m.wait(0.2);
    let r = m.call("browser_save_state", json!({"path": tmp.to_str().unwrap()}));
    assert!(!Mcp::is_err(&r), "save_state: {r:?}");
    assert!(tmp.exists(), "state file not created");
    let r2 = m.call("browser_load_state", json!({"path": tmp.to_str().unwrap()}));
    assert!(!Mcp::is_err(&r2), "load_state: {r2:?}");
    let _ = std::fs::remove_file(&tmp);
}

// ── 18. Domain blocking (separate process with allowlist) ──────────────────────

#[test]
fn e2e_domain_blocking() {
    let mut m = Mcp::start_with(&[
        ("AGENTYC_HEADLESS", "1"),
        ("AGENTYC_ALLOWED_DOMAINS", "example.com"),
    ]);
    require_browser!(m);
    let r = m.navigate("https://blocked.io");
    let msg = Mcp::text(&r);
    assert!(
        Mcp::is_err(&r) || msg.contains("blocked"),
        "domain not blocked: {r:?}"
    );
}

// ── Network-dependent cases (opt-in) ───────────────────────────────────────────

#[test]
#[ignore = "requires network access"]
fn e2e_localstorage_roundtrip() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("https://example.com");
    m.wait(0.6);
    let origin = m.eval("location.origin").trim_matches('"').to_string();
    for i in 0..iters(10) {
        m.call("browser_set_storage", json!({"origin": origin, "storage_type": "localStorage", "key": format!("k{i}"), "value": format!("v{i}")}));
        let got = Mcp::text(&m.call(
            "browser_get_storage",
            json!({"storage_type": "localStorage", "key": format!("k{i}")}),
        ));
        assert!(
            got.contains(&format!("v{i}")),
            "storage roundtrip {i}: {got}"
        );
    }
}

#[test]
#[ignore = "requires network access"]
fn e2e_real_world_example_and_httpbin() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("https://example.com");
    m.wait(0.5);
    assert!(
        m.state("min")["title"]
            .as_str()
            .unwrap_or("")
            .contains("Example Domain")
    );

    m.nav("https://httpbin.org/json");
    m.wait(0.5);
    let body = m.eval("document.body.innerText");
    assert!(body.contains("slideshow"), "httpbin json: {body}");
}
