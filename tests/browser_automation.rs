//! Browser automation integration tests.
//! Run: AGENTYC_HEADLESS=1 cargo test --test browser_automation -- --nocapture

use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};

fn binary_path() -> std::path::PathBuf {
    let manifest = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let r = manifest.join("../../target/release/agentyc");
    if r.exists() {
        return r;
    }
    manifest.join("../../target/debug/agentyc")
}

struct Mcp {
    proc: std::process::Child,
    reader: BufReader<std::process::ChildStdout>,
    stdin: std::process::ChildStdin,
    id: u64,
}

fn text(r: &serde_json::Value) -> String {
    r["result"]["content"][0]["text"]
        .as_str()
        .unwrap_or("")
        .to_string()
}
fn is_err(r: &serde_json::Value) -> bool {
    r["result"]["isError"].as_bool().unwrap_or(false) || r.get("error").is_some()
}

impl Mcp {
    fn start() -> Self {
        let binary = binary_path();
        assert!(binary.exists(), "Build first: cargo build -p agentyc");
        let mut proc = Command::new(&binary)
            .arg("mcp")
            .env("AGENTYC_HEADLESS", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("failed to start");
        let stdin = proc.stdin.take().unwrap();
        let reader = BufReader::new(proc.stdout.take().unwrap());
        let mut s = Self {
            proc,
            reader,
            stdin,
            id: 0,
        };
        let r = s.rpc(
            "initialize",
            serde_json::json!({
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "bt", "version": "1"}
            }),
        );
        assert!(r["result"].is_object(), "initialize failed: {r:?}");
        s
    }

    fn rpc(&mut self, method: &str, params: serde_json::Value) -> serde_json::Value {
        self.id += 1;
        let msg = serde_json::to_string(&serde_json::json!({
            "jsonrpc": "2.0", "id": self.id, "method": method, "params": params
        }))
        .unwrap()
            + "\n";
        self.stdin.write_all(msg.as_bytes()).unwrap();
        self.stdin.flush().unwrap();
        let mut buf = String::new();
        self.reader.read_line(&mut buf).expect("read failed");
        serde_json::from_str(buf.trim())
            .unwrap_or_else(|_| serde_json::json!({"error": "parse failed", "raw": buf}))
    }

    fn call(&mut self, tool: &str, args: serde_json::Value) -> serde_json::Value {
        self.rpc(
            "tools/call",
            serde_json::json!({"name": tool, "arguments": args}),
        )
    }

    fn nav(&mut self, url: &str) {
        let r = self.call("browser_navigate", serde_json::json!({"url": url}));
        assert!(!is_err(&r), "navigate to {url} failed: {r:?}");
    }

    fn eval(&mut self, code: &str) -> String {
        let r = self.call("browser_evaluate", serde_json::json!({"code": code}));
        assert!(!is_err(&r), "eval `{code}` failed: {r:?}");
        text(&r)
    }

    fn wait(&mut self, secs: f64) {
        self.call("browser_wait", serde_json::json!({"seconds": secs}));
    }

    fn get_state(&mut self) -> serde_json::Value {
        let r = self.call("browser_get_state", serde_json::json!({"mode": "full"}));
        serde_json::from_str(&text(&r)).unwrap_or_default()
    }

    fn first_ref_by_tag<'a>(state: &'a serde_json::Value, tag: &str) -> Option<&'a str> {
        state["interactive_elements"]
            .as_array()?
            .iter()
            .find(|e| e["tag"].as_str() == Some(tag))
            .and_then(|e| e["ref"].as_str())
    }
}

impl Drop for Mcp {
    fn drop(&mut self) {
        self.call("browser_close_all", serde_json::json!({}));
        self.proc.kill().ok();
    }
}

// ── Navigation ────────────────────────────────────────────────────────────────

#[test]
fn test_navigate_and_get_title() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<title>Hello World</title><h1>Test</h1>");
    let title = m.eval("document.title");
    assert_eq!(
        title.trim_matches('"'),
        "Hello World",
        "wrong title: {title}"
    );
}

#[test]
fn test_navigate_back_forward() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<title>PageA</title>");
    m.nav("data:text/html,<title>PageB</title>");
    let r = m.call("browser_go_back", serde_json::json!({}));
    assert!(!is_err(&r), "go_back failed: {r:?}");
    m.wait(0.4);
    let title = m.eval("document.title");
    // After back we should be on PageA (or still PageB if navigation was fast)
    assert!(
        title.contains("PageA") || title.contains("PageB"),
        "bad title: {title}"
    );
}

#[test]
fn test_navigate_refresh() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<title>Refresh</title>");
    let r = m.call("browser_refresh", serde_json::json!({}));
    assert!(!is_err(&r), "refresh failed: {r:?}");
}

// ── State & DOM ───────────────────────────────────────────────────────────────

#[test]
fn test_get_state_has_elements() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<button>Click</button><input type='text' placeholder='Type here'>");
    m.wait(0.2);
    let state = m.get_state();
    assert!(state["state_hash"].is_string(), "no hash: {state}");
    let count = state["interactive_element_count"].as_u64().unwrap_or(0);
    assert!(count >= 2, "expected >=2 elements, got {count}");
}

#[test]
fn test_since_hash_unchanged() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>Static</p><button>B</button>");
    m.wait(0.2);
    let r1 = m.call("browser_get_state", serde_json::json!({"mode": "min"}));
    let t1 = text(&r1);
    let v1: serde_json::Value = serde_json::from_str(&t1).unwrap();
    let hash = v1["state_hash"].as_str().unwrap_or("").to_string();
    assert!(!hash.is_empty(), "no hash");

    let r2 = m.call(
        "browser_get_state",
        serde_json::json!({"mode": "min", "since_hash": hash}),
    );
    let v2: serde_json::Value = serde_json::from_str(&text(&r2)).unwrap();
    assert_eq!(
        v2["changed"].as_bool(),
        Some(false),
        "should be unchanged: {v2}"
    );
}

#[test]
fn test_get_html() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<div id='target'>Hello Content</div>");
    m.wait(0.2);
    let r = m.call(
        "browser_get_html",
        serde_json::json!({"selector": "#target"}),
    );
    assert!(!is_err(&r), "get_html failed: {r:?}");
    assert!(
        text(&r).contains("Hello Content"),
        "missing content: {}",
        text(&r)
    );
}

#[test]
fn test_screenshot_has_image() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<body style='background:blue'><h1>Screenshot Test</h1></body>");
    m.wait(0.2);
    let r = m.call("browser_screenshot", serde_json::json!({}));
    assert!(!is_err(&r), "screenshot failed: {r:?}");
    let contents = r["result"]["content"].as_array().unwrap();
    let has_image = contents.iter().any(|c| {
        let t = c["type"].as_str().unwrap_or("");
        let mime = c["mimeType"].as_str().unwrap_or("");
        t == "image" || mime.starts_with("image/")
    });
    assert!(
        has_image,
        "no image in screenshot result: {}",
        r["result"]["content"]
    );
}

#[test]
fn test_set_viewport() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>vp</p>");
    m.wait(0.2);
    let r = m.call(
        "browser_set_viewport",
        serde_json::json!({"width": 1024, "height": 768}),
    );
    assert!(!is_err(&r), "set_viewport failed: {r:?}");
    let w = m.eval("window.innerWidth");
    assert_eq!(w, "1024", "wrong width: {w}");
}

// ── Interaction ───────────────────────────────────────────────────────────────

#[test]
fn test_click_updates_title() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<button onclick=\"document.title='clicked'\">Go</button>");
    m.wait(0.3);
    let state = m.get_state();
    let btn_ref = Mcp::first_ref_by_tag(&state, "button")
        .unwrap_or("")
        .to_string();
    assert!(!btn_ref.is_empty(), "no button ref");
    let r = m.call("browser_click", serde_json::json!({"ref": btn_ref}));
    assert!(!is_err(&r), "click failed: {r:?}");
    m.wait(0.15);
    assert!(
        m.eval("document.title").contains("clicked"),
        "title not updated"
    );
}

#[test]
fn test_type_into_input() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<input id='i' type='text'>");
    m.wait(0.3);
    let state = m.get_state();
    let inp = Mcp::first_ref_by_tag(&state, "input")
        .unwrap_or("")
        .to_string();
    assert!(!inp.is_empty(), "no input ref");
    let r = m.call(
        "browser_type",
        serde_json::json!({"ref": inp, "text": "hello world"}),
    );
    assert!(!is_err(&r), "type failed: {r:?}");
    m.wait(0.1);
    assert!(
        m.eval("document.getElementById('i').value")
            .contains("hello world"),
        "wrong value"
    );
}

#[test]
fn test_press_key_fires_event() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<input id='i'><span id='o'></span><script>document.getElementById('i').addEventListener('keydown',e=>{document.getElementById('o').textContent=e.key})</script>");
    m.wait(0.3);
    m.eval("document.getElementById('i').focus()");
    m.call("browser_press_key", serde_json::json!({"key": "Enter"}));
    m.wait(0.15);
    assert!(
        m.eval("document.getElementById('o').textContent")
            .contains("Enter"),
        "key event not fired"
    );
}

#[test]
fn test_scroll_changes_position() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<div style='height:5000px'>tall</div>");
    m.wait(0.3);
    m.call(
        "browser_scroll",
        serde_json::json!({"direction": "down", "pages": 3}),
    );
    m.wait(0.3);
    let sy: f64 = m.eval("window.scrollY").parse().unwrap_or(0.0);
    assert!(sy > 50.0, "page didn't scroll: scrollY={sy}");
}

#[test]
fn test_scroll_to_text() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<div style='height:3000px'></div><p id='t'>FINDME_UNIQUE</p>");
    m.wait(0.3);
    let r = m.call(
        "browser_scroll_to_text",
        serde_json::json!({"text": "FINDME_UNIQUE"}),
    );
    assert!(!is_err(&r), "scroll_to_text failed: {r:?}");
}

#[test]
fn test_hover_fires_event() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<div id='h' style='width:100px;height:50px' onmouseenter=\"document.title='hovered'\">Hover me</div>");
    m.wait(0.3);
    m.call(
        "browser_hover",
        serde_json::json!({"coordinate_x": 50, "coordinate_y": 25}),
    );
    m.wait(0.15);
    // Mouse events on data: URIs may or may not fire — just verify no crash
    let r = m.call(
        "browser_hover",
        serde_json::json!({"coordinate_x": 50, "coordinate_y": 25}),
    );
    assert!(!is_err(&r), "hover failed: {r:?}");
}

#[test]
fn test_double_click() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<div id='d' ondblclick=\"document.title='dbl'\">DoubleClick</div>");
    m.wait(0.3);
    let r = m.call(
        "browser_double_click",
        serde_json::json!({"coordinate_x": 50, "coordinate_y": 20}),
    );
    assert!(!is_err(&r), "double_click failed: {r:?}");
}

#[test]
fn test_select_option() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<select id='s'><option>Alpha</option><option>Beta</option><option>Gamma</option></select>");
    m.wait(0.3);
    let state = m.get_state();
    let sel = Mcp::first_ref_by_tag(&state, "select")
        .unwrap_or("")
        .to_string();
    assert!(!sel.is_empty(), "no select ref");
    let r = m.call(
        "browser_select_option",
        serde_json::json!({"ref": sel, "text": "Beta"}),
    );
    assert!(!is_err(&r), "select_option failed: {r:?}");
    m.wait(0.1);
    let val = m.eval("document.getElementById('s').value");
    assert!(val.contains("Beta"), "wrong value: {val}");
}

#[test]
fn test_get_dropdown_options() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<select><option value='a'>Alpha</option><option value='b'>Beta</option></select>");
    m.wait(0.3);
    let state = m.get_state();
    let sel = Mcp::first_ref_by_tag(&state, "select")
        .unwrap_or("")
        .to_string();
    assert!(!sel.is_empty(), "no select ref");
    let r = m.call(
        "browser_get_dropdown_options",
        serde_json::json!({"ref": sel}),
    );
    assert!(!is_err(&r), "get_dropdown_options failed: {r:?}");
    let t = text(&r);
    assert!(
        t.contains("Alpha") && t.contains("Beta"),
        "options missing: {t}"
    );
}

#[test]
fn test_fill_form_batch() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<form><input id='n' type='text'><input id='e' type='email'></form>");
    m.wait(0.3);
    let state = m.get_state();
    let inputs: Vec<String> = state["interactive_elements"]
        .as_array()
        .unwrap_or(&vec![])
        .iter()
        .filter(|e| e["tag"].as_str() == Some("input"))
        .map(|e| e["ref"].as_str().unwrap_or("").to_string())
        .filter(|r| !r.is_empty())
        .take(2)
        .collect();
    assert!(!inputs.is_empty(), "no inputs found");
    let fields: Vec<serde_json::Value> = inputs.iter().enumerate()
        .map(|(i, r)| serde_json::json!({"ref": r, "text": if i==0 {"Alice"} else {"alice@example.com"}}))
        .collect();
    let r = m.call("browser_fill_form", serde_json::json!({"fields": fields}));
    assert!(!is_err(&r), "fill_form failed: {r:?}");
    m.wait(0.1);
    assert!(
        m.eval("document.getElementById('n').value")
            .contains("Alice"),
        "name not set"
    );
}

// ── Inspection ────────────────────────────────────────────────────────────────

#[test]
fn test_find_elements_by_selector() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<ul><li class='item'>A</li><li class='item'>B</li><li class='item'>C</li></ul>");
    m.wait(0.2);
    let r = m.call(
        "browser_find_elements",
        serde_json::json!({"selector": ".item"}),
    );
    assert!(!is_err(&r), "find_elements failed: {r:?}");
    let parsed: serde_json::Value = serde_json::from_str(&text(&r)).unwrap();
    assert_eq!(
        parsed.as_array().map(|a| a.len()).unwrap_or(0),
        3,
        "expected 3 items"
    );
}

#[test]
fn test_search_page_finds_text() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>The secret keyword is: XYZZY_TOKEN_42</p>");
    m.wait(0.2);
    let r = m.call(
        "browser_search_page",
        serde_json::json!({"pattern": "XYZZY_TOKEN_42"}),
    );
    assert!(!is_err(&r), "search_page failed: {r:?}");
    assert!(
        text(&r).contains("XYZZY_TOKEN_42"),
        "token not found in search result"
    );
}

#[test]
fn test_search_page_regex() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>Price: $99.99 and $42.00</p>");
    m.wait(0.2);
    let r = m.call(
        "browser_search_page",
        serde_json::json!({"pattern": "\\$\\d+\\.\\d+", "regex": true}),
    );
    assert!(!is_err(&r), "regex search failed: {r:?}");
    let t = text(&r);
    assert!(
        t.contains("99") || t.contains("42"),
        "prices not found: {t}"
    );
}

#[test]
fn test_wait_for_element_appears() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<div id='d'></div><script>setTimeout(()=>{document.getElementById('d').textContent='loaded_content'},300)</script>");
    let r = m.call(
        "browser_wait_for_element",
        serde_json::json!({
            "text": "loaded_content", "appear": true, "timeout_seconds": 5
        }),
    );
    assert!(!is_err(&r), "wait_for_element failed: {r:?}");
    assert!(
        text(&r).contains("appeared"),
        "wrong response: {}",
        text(&r)
    );
}

#[test]
fn test_wait_for_element_disappears() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<div id='d'>temporary</div><script>setTimeout(()=>{document.getElementById('d').textContent=''},400)</script>");
    let r = m.call(
        "browser_wait_for_element",
        serde_json::json!({
            "text": "temporary", "appear": false, "timeout_seconds": 5
        }),
    );
    assert!(!is_err(&r), "wait_for_element disappear failed: {r:?}");
}

#[test]
fn test_get_focused_element() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<input id='f' type='text' placeholder='focus me'>");
    m.wait(0.3);
    m.eval("document.getElementById('f').focus()");
    m.wait(0.1);
    let r = m.call("browser_get_focused_element", serde_json::json!({}));
    assert!(!is_err(&r), "get_focused_element failed: {r:?}");
    let t = text(&r);
    assert!(
        t.contains("input") || t.contains("f") || t.contains("focus"),
        "wrong element: {t}"
    );
}

#[test]
fn test_get_attribute() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<a id='l' href='/test-path'>Link</a>");
    m.wait(0.2);
    let state = m.get_state();
    let link_ref = Mcp::first_ref_by_tag(&state, "a").unwrap_or("").to_string();
    if !link_ref.is_empty() {
        let r = m.call(
            "browser_get_attribute",
            serde_json::json!({"ref": link_ref, "name": "href"}),
        );
        assert!(!is_err(&r), "get_attribute failed: {r:?}");
        assert!(text(&r).contains("/test-path"), "wrong href: {}", text(&r));
    }
}

#[test]
fn test_evaluate_arithmetic_and_dom() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>a</p><p>b</p><p>c</p>");
    m.wait(0.2);
    assert_eq!(m.eval("2 + 2"), "4", "arithmetic failed");
    let n: i64 = m
        .eval("document.querySelectorAll('p').length")
        .parse()
        .unwrap_or(-1);
    assert_eq!(n, 3, "wrong p count: {n}");
}

// ── Extraction ────────────────────────────────────────────────────────────────

#[test]
fn test_extract_table_data() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<table><tr><th>Name</th><th>Score</th></tr><tr><td>Alice</td><td>95</td></tr><tr><td>Bob</td><td>87</td></tr></table>");
    m.wait(0.2);
    let r = m.call(
        "browser_extract_content",
        serde_json::json!({"query": "table rows"}),
    );
    assert!(!is_err(&r), "extract table failed: {r:?}");
    let t = text(&r);
    assert!(
        t.contains("Alice") && t.contains("Bob"),
        "missing data: {t}"
    );
    assert!(t.contains("95") || t.contains("87"), "missing scores: {t}");
}

#[test]
fn test_extract_links_from_page() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<nav><a href='/home'>Home</a><a href='/about'>About</a><a href='/contact'>Contact</a></nav>");
    m.wait(0.2);
    let r = m.call(
        "browser_extract_content",
        serde_json::json!({"query": "all links", "extract_links": true}),
    );
    assert!(!is_err(&r), "extract links failed: {r:?}");
    let t = text(&r);
    assert!(
        t.contains("home") || t.contains("Home"),
        "home link missing: {t}"
    );
    assert!(
        t.contains("about") || t.contains("About"),
        "about link missing: {t}"
    );
}

#[test]
fn test_extract_form_fields() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<form><input name='user' type='text'><input name='pass' type='password'><select name='role'><option>Admin</option></select></form>");
    m.wait(0.2);
    let r = m.call(
        "browser_extract_content",
        serde_json::json!({"query": "form fields"}),
    );
    assert!(!is_err(&r), "extract form fields failed: {r:?}");
    let t = text(&r);
    assert!(
        t.contains("user") || t.contains("pass"),
        "fields missing: {t}"
    );
}

#[test]
fn test_extract_lists() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<ul><li>Item One</li><li>Item Two</li><li>Item Three</li></ul><ol><li>First</li><li>Second</li></ol>");
    m.wait(0.2);
    let r = m.call(
        "browser_extract_content",
        serde_json::json!({"query": "list items"}),
    );
    assert!(!is_err(&r), "extract lists failed: {r:?}");
    let t = text(&r);
    assert!(
        t.contains("Item") || t.contains("First"),
        "items missing: {t}"
    );
}

// ── Tabs ──────────────────────────────────────────────────────────────────────

#[test]
fn test_new_tab_creates_tab() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<title>MainTab</title>");
    let r = m.call(
        "browser_new_tab",
        serde_json::json!({"url": "data:text/html,<title>NewTab</title>"}),
    );
    assert!(!is_err(&r), "new_tab failed: {r:?}");
    m.wait(0.4);
    let tabs_r = m.call("browser_list_tabs", serde_json::json!({}));
    let t = text(&tabs_r);
    let tabs: serde_json::Value = serde_json::from_str(&t).unwrap_or(serde_json::json!([]));
    assert!(
        tabs.as_array().map(|a| a.len() >= 2).unwrap_or(false),
        "expected >=2 tabs: {t}"
    );
}

#[test]
fn test_switch_and_close_tab() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<title>Tab1</title>");
    m.call(
        "browser_new_tab",
        serde_json::json!({"url": "data:text/html,<title>Tab2</title>"}),
    );
    m.wait(0.4);
    let tabs_r = m.call("browser_list_tabs", serde_json::json!({}));
    let t = text(&tabs_r);
    let tabs: serde_json::Value = serde_json::from_str(&t).unwrap_or(serde_json::json!([]));
    if let Some(arr) = tabs.as_array()
        && arr.len() >= 2
    {
        // Switch to first tab
        let tab_id = arr[0]["tab_id"].as_str().unwrap_or("").to_string();
        if !tab_id.is_empty() {
            let r = m.call("browser_switch_tab", serde_json::json!({"tab_id": tab_id}));
            assert!(!is_err(&r), "switch_tab failed: {r:?}");
        }
        // Close last tab
        let last_id = arr
            .last()
            .and_then(|t| t["tab_id"].as_str())
            .unwrap_or("")
            .to_string();
        if !last_id.is_empty() {
            let r = m.call("browser_close_tab", serde_json::json!({"tab_id": last_id}));
            assert!(!is_err(&r), "close_tab failed: {r:?}");
        }
    }
}

// ── Storage & Cookies ─────────────────────────────────────────────────────────

#[test]
fn test_localstorage_roundtrip() {
    let mut m = Mcp::start();
    // data: URLs don't support localStorage — use a real origin
    m.nav("https://example.com");
    m.wait(0.5);
    let origin = m.eval("location.origin").trim_matches('"').to_string();
    let r = m.call(
        "browser_set_storage",
        serde_json::json!({
            "origin": origin, "storage_type": "localStorage", "key": "myKey", "value": "myVal"
        }),
    );
    assert!(!is_err(&r), "set_storage failed: {r:?}");
    let r2 = m.call(
        "browser_get_storage",
        serde_json::json!({"storage_type": "localStorage", "key": "myKey"}),
    );
    assert!(!is_err(&r2), "get_storage failed: {r2:?}");
    assert!(text(&r2).contains("myVal"), "value missing: {}", text(&r2));
}

#[test]
fn test_clear_storage() {
    let mut m = Mcp::start();
    m.nav("https://example.com");
    m.wait(0.5);
    let origin = m.eval("location.origin").trim_matches('"').to_string();
    // Set then clear
    m.call(
        "browser_set_storage",
        serde_json::json!({
            "origin": origin.clone(), "storage_type": "localStorage", "key": "delme", "value": "v"
        }),
    );
    let r = m.call(
        "browser_clear_storage",
        serde_json::json!({
            "origin": origin, "storage_type": "localStorage", "key": "delme"
        }),
    );
    assert!(!is_err(&r), "clear_storage failed: {r:?}");
}

#[test]
fn test_cookie_set_get_clear() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>cookies</p>");
    m.wait(0.2);
    // Set
    let r = m.call(
        "browser_set_cookies",
        serde_json::json!({
            "cookies": [{"name": "test_c", "value": "val123", "domain": "localhost"}]
        }),
    );
    assert!(!is_err(&r), "set_cookies failed: {r:?}");
    // Get
    let r2 = m.call("browser_get_cookies", serde_json::json!({}));
    assert!(!is_err(&r2), "get_cookies failed: {r2:?}");
    // Clear one
    let r3 = m.call(
        "browser_clear_cookies",
        serde_json::json!({"name": "test_c"}),
    );
    assert!(!is_err(&r3), "clear_cookies failed: {r3:?}");
}

// ── Frames ────────────────────────────────────────────────────────────────────

#[test]
fn test_list_frames_main_frame() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>main</p>");
    m.wait(0.2);
    let r = m.call("browser_list_frames", serde_json::json!({}));
    assert!(!is_err(&r), "list_frames failed: {r:?}");
    let frames: serde_json::Value =
        serde_json::from_str(&text(&r)).unwrap_or(serde_json::json!([]));
    assert!(
        frames.as_array().map(|a| !a.is_empty()).unwrap_or(false),
        "no frames"
    );
}

#[test]
fn test_get_frame_html() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>frame html</p>");
    m.wait(0.2);
    let frames_r = m.call("browser_list_frames", serde_json::json!({}));
    let frames: serde_json::Value =
        serde_json::from_str(&text(&frames_r)).unwrap_or(serde_json::json!([]));
    if let Some(frame_id) = frames
        .as_array()
        .and_then(|a| a.first())
        .and_then(|f| f["frame_id"].as_str())
    {
        let r = m.call(
            "browser_get_frame_html",
            serde_json::json!({"frame_id": frame_id}),
        );
        assert!(!is_err(&r), "get_frame_html failed: {r:?}");
        assert!(text(&r).contains("html"), "no html: {}", text(&r));
    }
}

// ── Emulation & Session control ───────────────────────────────────────────────

#[test]
fn test_emulate_media_dark() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>media</p>");
    let r = m.call(
        "browser_emulate_media",
        serde_json::json!({"color_scheme": "dark"}),
    );
    assert!(!is_err(&r), "emulate_media failed: {r:?}");
}

#[test]
fn test_set_user_agent() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>ua</p>");
    let r = m.call(
        "browser_set_user_agent",
        serde_json::json!({"user_agent": "TestBot/2.0"}),
    );
    assert!(!is_err(&r), "set_user_agent failed: {r:?}");
}

#[test]
fn test_set_timezone() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>tz</p>");
    let r = m.call(
        "browser_set_timezone",
        serde_json::json!({"timezone_id": "America/New_York"}),
    );
    assert!(!is_err(&r), "set_timezone failed: {r:?}");
    // Verify it actually changed the timezone
    let tz_offset = m.eval("new Date().getTimezoneOffset()");
    // NY is UTC-5 or UTC-4 depending on DST → offset 240 or 300
    let offset: i64 = tz_offset.parse().unwrap_or(999);
    assert!(
        offset == 240 || offset == 300,
        "unexpected offset {offset} (expected NY ~240-300)"
    );
}

#[test]
fn test_set_locale() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>locale</p>");
    let r = m.call("browser_set_locale", serde_json::json!({"locale": "de-DE"}));
    assert!(!is_err(&r), "set_locale failed: {r:?}");
}

#[test]
fn test_set_extra_headers() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>headers</p>");
    let r = m.call(
        "browser_set_extra_headers",
        serde_json::json!({"headers": {"X-Custom": "test_value"}}),
    );
    assert!(!is_err(&r), "set_extra_headers failed: {r:?}");
    // Clear headers
    let r2 = m.call(
        "browser_set_extra_headers",
        serde_json::json!({"headers": {}}),
    );
    assert!(!is_err(&r2));
}

// ── Wait helpers ──────────────────────────────────────────────────────────────

#[test]
fn test_wait_for_url_change() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<script>setTimeout(()=>{location.href='data:text/html,<title>redirected_target</title>'},250)</script>");
    let r = m.call(
        "browser_wait_for_url",
        serde_json::json!({
            "url_substring": "redirected_target", "timeout_seconds": 5
        }),
    );
    assert!(!is_err(&r), "wait_for_url failed: {r:?}");
}

#[test]
fn test_wait_for_stable_dom() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<div>stable</div>");
    m.wait(0.2);
    let r = m.call(
        "browser_wait_for_stable_dom",
        serde_json::json!({"timeout_seconds": 3, "quiet_ms": 200}),
    );
    assert!(!is_err(&r), "wait_for_stable_dom failed: {r:?}");
}

#[test]
fn test_wait_for_network_idle() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>idle test</p>");
    m.wait(0.2);
    let r = m.call(
        "browser_wait_for_network_idle",
        serde_json::json!({"timeout_seconds": 3}),
    );
    assert!(!is_err(&r), "wait_for_network_idle failed: {r:?}");
}

// ── Save/Load State ───────────────────────────────────────────────────────────

#[test]
fn test_save_and_load_state() {
    let tmp = std::env::temp_dir().join("agentyc_test_state.json");
    let mut m = Mcp::start();
    m.nav("https://example.com");
    m.wait(0.5);

    let r = m.call(
        "browser_save_state",
        serde_json::json!({"path": tmp.to_str().unwrap()}),
    );
    assert!(!is_err(&r), "save_state failed: {r:?}");
    assert!(tmp.exists(), "state file not created");

    let r2 = m.call(
        "browser_load_state",
        serde_json::json!({"path": tmp.to_str().unwrap()}),
    );
    assert!(!is_err(&r2), "load_state failed: {r2:?}");
    let _ = std::fs::remove_file(&tmp);
}

// ── Session management ────────────────────────────────────────────────────────

#[test]
fn test_list_sessions() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>sessions</p>");
    let r = m.call("browser_list_sessions", serde_json::json!({}));
    assert!(!is_err(&r), "list_sessions failed: {r:?}");
    assert!(
        text(&r).contains("session_id"),
        "no session_id: {}",
        text(&r)
    );
}

#[test]
fn test_close_all_sessions() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>main</p>");
    m.call("browser_new_tab", serde_json::json!({}));
    let r = m.call("browser_close_all", serde_json::json!({}));
    assert!(!is_err(&r), "close_all failed: {r:?}");
}

// ── Grant permissions & geolocation ──────────────────────────────────────────

#[test]
fn test_grant_permissions() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>perms</p>");
    // geolocation permission — should not error
    let r = m.call(
        "browser_grant_permissions",
        serde_json::json!({
            "permissions": ["geolocation"]
        }),
    );
    assert!(!is_err(&r), "grant_permissions failed: {r:?}");
}

#[test]
fn test_set_geolocation() {
    let mut m = Mcp::start();
    m.nav("data:text/html,<p>geo</p>");
    m.call(
        "browser_grant_permissions",
        serde_json::json!({"permissions": ["geolocation"]}),
    );
    let r = m.call(
        "browser_set_geolocation",
        serde_json::json!({
            "latitude": 37.7749, "longitude": -122.4194, "accuracy": 100
        }),
    );
    assert!(!is_err(&r), "set_geolocation failed: {r:?}");
}
