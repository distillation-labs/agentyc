//! Battle test — real-world browser automation against live sites.
//! Real-world browser automation against live sites. Every test hits the public internet, so they
//! are `#[ignore]` by default.
//!
//! Run: `AGENTYC_HEADLESS=1 cargo test --test battle_test -- --ignored --nocapture`

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
#[ignore = "live site"]
fn battle_example_com() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("https://example.com");
    m.wait(0.4);
    let s = m.state("full");
    assert!(
        s["title"].as_str().unwrap_or("").contains("Example Domain"),
        "title: {}",
        s["title"]
    );

    let links = Mcp::json(&m.call("browser_extract_content", json!({"query": "all links"})));
    let count = links["data"].as_array().map(|a| a.len()).unwrap_or(0);
    assert!(count >= 1, "expected >=1 link, got {count}");

    let h = s["state_hash"].as_str().unwrap_or("").to_string();
    let cached = Mcp::json(&m.call("browser_get_state", json!({"mode": "min", "since_hash": h})));
    assert_eq!(
        cached["changed"].as_bool(),
        Some(false),
        "since_hash should cache"
    );
}

#[test]
#[ignore = "live site"]
fn battle_wikipedia_reading() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("https://en.wikipedia.org/wiki/Web_scraping");
    m.wait(0.6);
    assert!(
        m.state("min")["title"]
            .as_str()
            .unwrap_or("")
            .contains("Wikipedia")
    );

    let headings = m.eval_json("Array.from(document.querySelectorAll('h2')).map(h=>h.textContent.trim()).filter(Boolean).slice(0,5)");
    assert!(
        headings.as_array().map(|a| a.len() >= 2).unwrap_or(false),
        "headings: {headings}"
    );

    let hits = Mcp::json(&m.call(
        "browser_search_page",
        json!({"pattern": "scraping", "max_results": 5}),
    ));
    assert!(
        hits.as_array().map(|a| !a.is_empty()).unwrap_or(false),
        "search hits"
    );
}

#[test]
#[ignore = "live site"]
fn battle_hacker_news_listing_and_pagination() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("https://news.ycombinator.com");
    m.wait(0.6);
    assert!(!m.state("min")["title"].as_str().unwrap_or("").is_empty());

    let links = Mcp::json(&m.call("browser_extract_content", json!({"query": "all links"})));
    let external = links["data"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter(|l| {
                    let href = l["href"].as_str().unwrap_or("");
                    href.starts_with("http") && !href.contains("ycombinator")
                })
                .count()
        })
        .unwrap_or(0);
    assert!(
        external >= 10,
        "expected >=10 external story links, got {external}"
    );

    m.nav("https://news.ycombinator.com/?p=2");
    m.wait(0.5);
    assert!(m.eval("location.href").contains("p=2"), "pagination");
}

#[test]
#[ignore = "live site"]
fn battle_duckduckgo_react_input() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("https://duckduckgo.com");
    m.wait(0.6);
    // React-controlled inputs are set most reliably via the native value setter.
    let set = m.eval_json(
        "(function(){var el=document.querySelector(\"input[type=text],input[name=q]\");if(!el)return false;el.focus();var d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');d.set.call(el,'rust browser automation');el.dispatchEvent(new Event('input',{bubbles:true}));return el.value;})()",
    );
    assert!(
        set.as_str().unwrap_or("").to_lowercase().contains("rust"),
        "value set: {set}"
    );
    m.call("browser_press_key", json!({"key": "Enter"}));
    let r = m.call(
        "browser_wait_for_url",
        json!({"url_substring": "q=rust", "timeout_seconds": 8}),
    );
    assert!(!Mcp::is_err(&r), "search did not navigate: {r:?}");
}

#[test]
#[ignore = "live site"]
fn battle_httpbin_form_submit() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("https://httpbin.org/forms/post");
    m.wait(0.6);
    let s = m.state("full");
    let inputs: Vec<String> = Mcp::elements(&s)
        .iter()
        .filter(|e| {
            e["tag"].as_str() == Some("input")
                && !matches!(
                    e["type"].as_str(),
                    Some("submit")
                        | Some("hidden")
                        | Some("checkbox")
                        | Some("radio")
                        | Some("button")
                )
        })
        .filter_map(|e| e["ref"].as_str().map(str::to_string))
        .take(2)
        .collect();
    assert!(!inputs.is_empty(), "no text inputs found");
    for r in &inputs {
        m.call(
            "browser_type",
            json!({"ref": r, "text": "agent_test_value"}),
        );
    }
    let submit = Mcp::elements(&s).into_iter().find(|e| {
        e["type"].as_str() == Some("submit")
            || (e["tag"].as_str() == Some("button")
                && e.to_string().to_lowercase().contains("submit"))
    });
    if let Some(btn) = submit.and_then(|e| e["ref"].as_str().map(str::to_string)) {
        m.call("browser_click", json!({"ref": btn}));
        m.wait(1.0);
        let body = m.eval("document.body.innerText");
        assert!(
            body.contains("agent_test_value"),
            "form not echoed: {}",
            &body[..body.len().min(120)]
        );
    }
}

#[test]
#[ignore = "live site"]
fn battle_httpbin_json_api() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("https://httpbin.org/json");
    m.wait(0.4);
    let body = m.eval("document.body.innerText");
    assert!(body.contains("slideshow"), "json api: {body}");
}

#[test]
#[ignore = "live site"]
fn battle_multi_tab_real_pages() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("https://example.com");
    m.wait(0.4);
    m.call("browser_new_tab", json!({"url": "https://httpbin.org/get"}));
    m.wait(0.6);
    let tabs = Mcp::json(&m.call("browser_list_tabs", json!({})));
    let arr = tabs.as_array().cloned().unwrap_or_default();
    assert!(arr.len() >= 2, "expected >=2 tabs, got {}", arr.len());
    if let Some(ex) = arr
        .iter()
        .find(|t| t["url"].as_str().unwrap_or("").contains("example.com"))
    {
        let id = ex["tab_id"].as_str().unwrap_or("");
        m.call("browser_switch_tab", json!({"tab_id": id}));
        m.wait(0.3);
        assert!(
            m.eval("document.title").contains("Example"),
            "switched tab content"
        );
    }
}

#[test]
#[ignore = "live site"]
fn battle_screenshot_and_session_roundtrip() {
    let mut m = Mcp::start();
    require_browser!(m);
    m.nav("https://example.com");
    m.wait(0.4);
    assert!(
        Mcp::has_image(&m.call("browser_screenshot", json!({}))),
        "screenshot"
    );

    m.nav("https://httpbin.org/cookies/set/test_cookie/hello");
    m.wait(0.5);
    let tmp = std::env::temp_dir().join("agentyc_battle_state.json");
    let r = m.call("browser_save_state", json!({"path": tmp.to_str().unwrap()}));
    assert!(!Mcp::is_err(&r) && tmp.exists(), "save_state");
    let _ = std::fs::remove_file(&tmp);
}

#[test]
#[ignore = "live site"]
fn battle_error_recovery() {
    let mut m = Mcp::start();
    require_browser!(m);
    // A dead domain loads a chrome-error page rather than an MCP error.
    m.navigate("https://this-domain-does-not-exist-xyz-abc-999.com");
    m.wait(0.5);
    // Recovery: a valid navigation still works.
    let r = m.navigate("https://example.com");
    assert!(!Mcp::is_err(&r), "recovery navigation failed: {r:?}");
}
