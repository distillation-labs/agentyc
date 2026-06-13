//! Scenario model + catalog generator for the real-world battle-test suite.
//!
//! This file is the single source of truth for the scenario catalog. It is:
//!   * compiled as the `scenario` module of `agentyc-tests`, and
//!   * `include!`d by `build.rs` to count scenarios and emit one `#[test]` each.
//!
//! It MUST stay std-only (no `crate::` references) so it compiles in both
//! contexts. Steps and checks are pure data; the runner (in `runner.rs`)
//! interprets them against the live MCP tools.

#![allow(dead_code)]

/// One action in a scenario, executed against the browser via the MCP tools.
#[derive(Clone)]
pub enum Step {
    /// Navigate to a path relative to the fixtures base URL (e.g. `/app/login`).
    Navigate(String),
    /// Fixed sleep, in seconds.
    Wait(f64),
    /// Poll until text appears/disappears on the page.
    WaitText {
        text: String,
        appear: bool,
        timeout: f64,
    },
    /// Poll until the URL contains a substring.
    WaitUrl {
        substr: String,
        timeout: f64,
    },
    /// Wait until DOM mutations settle.
    WaitStableDom {
        quiet_ms: u64,
        timeout: f64,
    },
    /// Wait until the network goes idle.
    WaitNetworkIdle {
        timeout: f64,
    },
    /// Read state, find an interactive element whose visible text contains this
    /// string (case-insensitive), and click it by ref.
    ClickText(String),
    /// Click the first interactive element of a given tag.
    ClickTag(String),
    /// Type into the input whose placeholder matches (exact-preferred).
    TypePlaceholder {
        placeholder: String,
        text: String,
    },
    /// Select an option (by visible text) in the first `<select>`.
    SelectFirst {
        value: String,
    },
    /// Toggle the first checkbox to the desired state.
    SetCheckbox(bool),
    /// Send a key/chord.
    PressKey(String),
    /// Scroll the page.
    Scroll {
        down: bool,
        pages: f64,
    },
    /// Scroll until text is visible.
    ScrollToText(String),
    /// Drag from one CSS selector's center to another's, via the real drag tool.
    DragSelector {
        from: String,
        to: String,
    },
    /// Execute JS for a side effect (ignored result).
    Eval(String),
    /// Set the viewport size.
    Viewport {
        w: u32,
        h: u32,
    },
    /// Emulation controls.
    SetTimezone(String),
    SetLocale(String),
    EmulateColorScheme(String),
    GrantGeolocation,
    SetGeolocation {
        lat: f64,
        lon: f64,
    },
    /// Storage / cookies (origin defaults to the fixtures base URL).
    SetStorage {
        area: String,
        key: String,
        value: String,
    },
    SetCookie {
        name: String,
        value: String,
    },
    /// Accept/dismiss a native JS dialog (optionally answering a prompt).
    HandleDialog {
        accept: bool,
        prompt: Option<String>,
    },
    /// Open a new tab at a path and switch to it.
    NewTab(String),
    /// Switch to a tab by index in the tab list.
    SwitchTabIndex(usize),
    /// Switch to the first tab whose URL contains the substring (order-robust).
    SwitchTabUrl(String),
    /// Persist / restore browser state to a temp path token.
    SaveState(String),
    LoadState(String),
}

/// A post-condition asserted after the steps run.
#[derive(Clone)]
pub enum Check {
    TextPresent(String),
    TextAbsent(String),
    UrlContains(String),
    TitleContains(String),
    /// `eval(code).trim()` equals expected (good for numeric results).
    JsEq {
        code: String,
        expected: String,
    },
    /// `eval(code)` contains needle (good for string results, ignores quoting).
    JsContains {
        code: String,
        needle: String,
    },
    ElementCount {
        selector: String,
        count: usize,
    },
    ElementCountAtLeast {
        selector: String,
        min: usize,
    },
    ExtractContains {
        query: String,
        needle: String,
    },
    FrameCountAtLeast(usize),
    FrameHtmlContains(String),
    FocusedContains(String),
}

/// A single named, self-contained browser journey.
#[derive(Clone)]
pub struct Scenario {
    pub id: String,
    pub category: &'static str,
    pub steps: Vec<Step>,
    pub checks: Vec<Check>,
}

// ── tiny builders to keep generators readable ────────────────────────────────

fn nav(p: &str) -> Step {
    Step::Navigate(p.to_string())
}
fn typ(ph: &str, t: &str) -> Step {
    Step::TypePlaceholder {
        placeholder: ph.to_string(),
        text: t.to_string(),
    }
}
fn click(t: &str) -> Step {
    Step::ClickText(t.to_string())
}
fn wait_text(t: &str) -> Step {
    Step::WaitText {
        text: t.to_string(),
        appear: true,
        timeout: 5.0,
    }
}
fn present(t: &str) -> Check {
    Check::TextPresent(t.to_string())
}

fn sc(id: String, category: &'static str, steps: Vec<Step>, checks: Vec<Check>) -> Scenario {
    Scenario {
        id,
        category,
        steps,
        checks,
    }
}

// ── product / dataset constants (mirror fixtures::PRODUCTS) ───────────────────

const PRODUCTS: &[&str] = &[
    "Aurora Lamp",
    "Basalt Mug",
    "Cedar Chair",
    "Delta Keyboard",
    "Ember Kettle",
    "Fjord Bottle",
    "Granite Bowl",
    "Harbor Clock",
    "Iris Vase",
    "Jade Plate",
    "Koru Notebook",
    "Larch Stool",
    "Maple Tray",
    "Nimbus Pillow",
    "Onyx Pen",
    "Pebble Speaker",
    "Quartz Light",
    "River Towel",
    "Slate Coaster",
    "Terra Pot",
    "Umbra Shade",
    "Violet Soap",
    "Willow Basket",
    "Xenon Bulb",
    "Yarrow Candle",
    "Zephyr Fan",
    "Amber Frame",
    "Birch Hook",
    "Coral Dish",
    "Dune Rug",
];

fn price(i: usize) -> u32 {
    10 + (i as u32 % 9) * 5
}

/// Number of synthetic end-to-end login soak journeys appended to the catalog.
/// Tuned so the full generated catalog lands at the target total.
const SOAK_LOGINS: usize = 567;

// ── The catalog ──────────────────────────────────────────────────────────────

/// Build the full scenario catalog. Deterministic and order-stable so the
/// generated test indices are reproducible.
pub fn catalog() -> Vec<Scenario> {
    let mut v = Vec::new();
    gen_login(&mut v);
    gen_register(&mut v);
    gen_search(&mut v);
    gen_table(&mut v);
    gen_checkout(&mut v);
    gen_spa(&mut v);
    gen_infinite_scroll(&mut v);
    gen_modals(&mut v);
    gen_native_dialogs(&mut v);
    gen_iframe(&mut v);
    gen_shadow(&mut v);
    gen_kanban(&mut v);
    gen_resilience(&mut v);
    gen_multitab(&mut v);
    gen_emulation(&mut v);
    gen_extraction(&mut v);
    gen_storage(&mut v);
    gen_a11y(&mut v);
    gen_waits(&mut v);
    // Composed, multi-step, real-world journeys.
    gen_journeys(&mut v);
    gen_form_recovery(&mut v);
    gen_search_refine(&mut v);
    gen_table_workflows(&mut v);
    gen_shop_checkout(&mut v);
    gen_responsive(&mut v);
    gen_scroll_interact(&mut v);
    gen_cookie_auth(&mut v);
    gen_netidle(&mut v);
    gen_extraction_more(&mut v);
    gen_error_chains(&mut v);
    gen_session(&mut v);
    gen_keyboard(&mut v);
    gen_soak(&mut v);
    v
}

fn usernames() -> Vec<String> {
    let names = [
        "alice", "bob", "carol", "dave", "erin", "frank", "grace", "heidi", "ivan", "judy",
        "mallory", "niaj", "olivia", "peggy", "rupert", "sybil", "trent", "victor", "walter",
        "wendy",
    ];
    let mut out: Vec<String> = names.iter().map(|s| s.to_string()).collect();
    for i in 1..=70 {
        out.push(format!("user{i}"));
    }
    out
}

// 1. Authentication — login flows (valid, invalid, empty), plus session.
fn gen_login(v: &mut Vec<Scenario>) {
    for (i, u) in usernames().iter().enumerate() {
        // Successful login → dashboard.
        v.push(sc(
            format!("login_ok_{u}"),
            "auth_login",
            vec![
                nav("/app/login"),
                typ("Username", u),
                typ("Password", &format!("pw_{u}")),
                click("Sign in"),
                Step::WaitUrl {
                    substr: "/app/dashboard".into(),
                    timeout: 5.0,
                },
            ],
            vec![
                Check::UrlContains("dashboard".into()),
                present(&format!("Welcome, {u}")),
            ],
        ));
        // Wrong password → inline error.
        v.push(sc(
            format!("login_bad_{u}"),
            "auth_login",
            vec![
                nav("/app/login"),
                typ("Username", u),
                typ("Password", "wrong-password"),
                click("Sign in"),
                wait_text("Invalid credentials"),
            ],
            vec![
                present("Invalid credentials"),
                Check::UrlContains("/app/login".into()),
            ],
        ));
        // A subset also covers empty-field validation.
        if i % 4 == 0 {
            v.push(sc(
                format!("login_empty_user_{u}"),
                "auth_login",
                vec![
                    nav("/app/login"),
                    typ("Password", "x"),
                    click("Sign in"),
                    wait_text("Username is required"),
                ],
                vec![present("Username is required")],
            ));
            v.push(sc(
                format!("login_empty_pass_{u}"),
                "auth_login",
                vec![
                    nav("/app/login"),
                    typ("Username", u),
                    click("Sign in"),
                    wait_text("Password is required"),
                ],
                vec![present("Password is required")],
            ));
        }
    }
}

// 2. Forms — registration validation across every branch + valid submissions.
fn gen_register(v: &mut Vec<Scenario>) {
    // Valid submissions.
    for i in 0..30 {
        let pw = format!("password{i:02}");
        v.push(sc(
            format!("register_valid_{i}"),
            "form_validation",
            vec![
                nav("/app/register"),
                typ("Full name", &format!("User {i}")),
                typ("Email", &format!("user{i}@example.com")),
                typ("Password", &pw),
                typ("Confirm password", &pw),
                typ("Age", &format!("{}", 18 + (i % 40))),
                Step::SelectFirst { value: "US".into() },
                Step::SetCheckbox(true),
                click("Create account"),
                wait_text("Account created"),
            ],
            vec![present("Account created")],
        ));
    }
    // Bad email.
    for i in 0..20 {
        v.push(sc(
            format!("register_bademail_{i}"),
            "form_validation",
            vec![
                nav("/app/register"),
                typ("Full name", &format!("User {i}")),
                typ("Email", &format!("user{i}-example.com")),
                click("Create account"),
                wait_text("Enter a valid email"),
            ],
            vec![present("Enter a valid email")],
        ));
    }
    // Short password.
    for i in 0..20 {
        v.push(sc(
            format!("register_shortpw_{i}"),
            "form_validation",
            vec![
                nav("/app/register"),
                typ("Full name", &format!("User {i}")),
                typ("Email", &format!("user{i}@example.com")),
                typ("Password", "short"),
                click("Create account"),
                wait_text("Password must be at least 8 characters"),
            ],
            vec![present("Password must be at least 8 characters")],
        ));
    }
    // Password mismatch.
    for i in 0..20 {
        v.push(sc(
            format!("register_mismatch_{i}"),
            "form_validation",
            vec![
                nav("/app/register"),
                typ("Full name", &format!("User {i}")),
                typ("Email", &format!("user{i}@example.com")),
                typ("Password", "password123"),
                typ("Confirm password", "different999"),
                click("Create account"),
                wait_text("Passwords do not match"),
            ],
            vec![present("Passwords do not match")],
        ));
    }
    // Underage.
    for i in 0..15 {
        let pw = format!("password{i:02}");
        v.push(sc(
            format!("register_underage_{i}"),
            "form_validation",
            vec![
                nav("/app/register"),
                typ("Full name", &format!("User {i}")),
                typ("Email", &format!("user{i}@example.com")),
                typ("Password", &pw),
                typ("Confirm password", &pw),
                typ("Age", &format!("{}", 13 + (i % 5))),
                click("Create account"),
                wait_text("You must be 18 or older"),
            ],
            vec![present("You must be 18 or older")],
        ));
    }
    // Name required.
    for i in 0..10 {
        v.push(sc(
            format!("register_noname_{i}"),
            "form_validation",
            vec![
                nav("/app/register"),
                typ("Email", &format!("u{i}@e.com")),
                click("Create account"),
                wait_text("Name is required"),
            ],
            vec![present("Name is required")],
        ));
    }
    // Terms not accepted.
    for i in 0..10 {
        let pw = format!("password{i:02}");
        v.push(sc(
            format!("register_noterms_{i}"),
            "form_validation",
            vec![
                nav("/app/register"),
                typ("Full name", &format!("User {i}")),
                typ("Email", &format!("user{i}@example.com")),
                typ("Password", &pw),
                typ("Confirm password", &pw),
                typ("Age", "25"),
                click("Create account"),
                wait_text("You must accept the terms"),
            ],
            vec![present("You must accept the terms")],
        ));
    }
}

// 3. Debounced autocomplete search.
fn gen_search(v: &mut Vec<Scenario>) {
    // Full product-name queries → that product appears.
    for (i, p) in PRODUCTS.iter().enumerate() {
        v.push(sc(
            format!("search_exact_{i}"),
            "search_autocomplete",
            vec![
                nav("/app/search"),
                typ("Search products", p),
                Step::Wait(0.4),
                wait_text(p),
            ],
            vec![present(p), present("results")],
        ));
    }
    // First-word queries.
    for (i, p) in PRODUCTS.iter().enumerate() {
        let name: &str = p;
        let word = name.split(' ').next().unwrap_or(name).to_lowercase();
        v.push(sc(
            format!("search_word_{i}"),
            "search_autocomplete",
            vec![
                nav("/app/search"),
                typ("Search products", &word),
                Step::Wait(0.4),
                wait_text(p),
            ],
            vec![present(p)],
        ));
    }
    // Common-letter queries (multiple matches).
    for (i, ch) in ["a", "e", "o", "r", "l", "t", "n", "s"].iter().enumerate() {
        v.push(sc(
            format!("search_letter_{i}"),
            "search_autocomplete",
            vec![
                nav("/app/search"),
                typ("Search products", ch),
                Step::Wait(0.4),
                wait_text("results"),
            ],
            vec![present("results")],
        ));
    }
    // No-match queries.
    for i in 0..24 {
        let q = format!("zzqx{i}");
        v.push(sc(
            format!("search_none_{i}"),
            "search_autocomplete",
            vec![
                nav("/app/search"),
                typ("Search products", &q),
                Step::Wait(0.4),
                wait_text("No results"),
            ],
            vec![present("No results")],
        ));
    }
}

// 4. Data table — pagination, sorting, filtering (server-rendered, deterministic).
fn gen_table(v: &mut Vec<Scenario>) {
    for page in 1..=20 {
        v.push(sc(
            format!("table_page_{page}"),
            "table_pagination",
            vec![nav(&format!("/app/table?page={page}"))],
            vec![
                present(&format!("Page {page} of 20")),
                Check::ElementCount {
                    selector: ".row".into(),
                    count: 10,
                },
            ],
        ));
    }
    for dept in ["Sales", "Eng", "Ops", "Legal", "HR"] {
        for page in 1..=4 {
            v.push(sc(
                format!("table_filter_{}_{page}", dept.to_lowercase()),
                "table_pagination",
                vec![nav(&format!("/app/table?q={dept}&page={page}"))],
                vec![
                    present(&format!("Page {page} of 4")),
                    Check::ElementCount {
                        selector: ".row".into(),
                        count: 10,
                    },
                ],
            ));
        }
    }
    for name in ["Ava", "Ben", "Cleo", "Dan", "Eve"] {
        for page in 1..=2 {
            v.push(sc(
                format!("table_name_{}_{page}", name.to_lowercase()),
                "table_pagination",
                vec![nav(&format!("/app/table?q={name}&page={page}"))],
                vec![
                    present(&format!("Page {page} of 2")),
                    Check::ElementCount {
                        selector: ".row".into(),
                        count: 10,
                    },
                ],
            ));
        }
    }
    for sort in ["name", "amount", "id"] {
        for dir in ["asc", "desc"] {
            v.push(sc(
                format!("table_sort_{sort}_{dir}"),
                "table_pagination",
                vec![nav(&format!("/app/table?sort={sort}&dir={dir}"))],
                vec![
                    present("Page 1 of 20"),
                    Check::ElementCount {
                        selector: ".row".into(),
                        count: 10,
                    },
                ],
            ));
        }
    }
    // Click-through pagination journeys.
    for start in 1..=10 {
        v.push(sc(
            format!("table_next_{start}"),
            "table_pagination",
            vec![
                nav(&format!("/app/table?page={start}")),
                click("Next"),
                Step::WaitUrl {
                    substr: format!("page={}", start + 1),
                    timeout: 5.0,
                },
            ],
            vec![present(&format!("Page {} of 20", start + 1))],
        ));
    }
}

// 5. E-commerce checkout — totals + coupons + confirmation.
#[allow(clippy::needless_range_loop)]
fn gen_checkout(v: &mut Vec<Scenario>) {
    // Single-item orders.
    for i in 0..30usize {
        let total = price(i);
        v.push(sc(
            format!("checkout_single_{i}"),
            "ecommerce_checkout",
            vec![
                nav(&format!("/app/checkout?items={i}")),
                click("Place order"),
                wait_text("Order confirmed"),
            ],
            vec![
                present(PRODUCTS[i]),
                present(&format!("Total: ${total}")),
                present("Order confirmed"),
            ],
        ));
    }
    // SAVE10 coupon on pairs.
    for i in 0..20usize {
        let (a, b) = (i % PRODUCTS.len(), (i + 7) % PRODUCTS.len());
        let subtotal = price(a) + price(b);
        let total = subtotal - subtotal / 10;
        v.push(sc(
            format!("checkout_save10_{i}"),
            "ecommerce_checkout",
            vec![nav(&format!("/app/checkout?items={a}|{b}&coupon=SAVE10"))],
            vec![
                present("Coupon SAVE10 applied"),
                present(&format!("Total: ${total}")),
            ],
        ));
    }
    // FREESHIP coupon.
    for i in 0..15usize {
        let (a, b) = (i % PRODUCTS.len(), (i + 11) % PRODUCTS.len());
        let subtotal = price(a) + price(b);
        let discount = 5.min(subtotal);
        let total = subtotal - discount;
        v.push(sc(
            format!("checkout_freeship_{i}"),
            "ecommerce_checkout",
            vec![nav(&format!("/app/checkout?items={a}|{b}&coupon=FREESHIP"))],
            vec![
                present("Coupon FREESHIP applied"),
                present(&format!("Total: ${total}")),
            ],
        ));
    }
    // Invalid coupon.
    for i in 0..15usize {
        let a = i % PRODUCTS.len();
        v.push(sc(
            format!("checkout_badcoupon_{i}"),
            "ecommerce_checkout",
            vec![nav(&format!("/app/checkout?items={a}&coupon=NOPE{i}"))],
            vec![present("Invalid coupon")],
        ));
    }
}

// 6. SPA client-side routing (hash + history API).
fn gen_spa(v: &mut Vec<Scenario>) {
    let routes = [
        ("#/", "Home View"),
        ("#/products", "Products View"),
        ("#/cart", "Cart View"),
        ("#/about", "About View"),
        ("#/settings", "Settings View"),
    ];
    for (i, (hash, title)) in routes.iter().enumerate() {
        v.push(sc(
            format!("spa_route_{i}"),
            "spa_routing",
            vec![nav(&format!("/app/spa{hash}")), Step::Wait(0.2)],
            vec![present(title), Check::UrlContains(hash.to_string())],
        ));
    }
    for n in 1..=40 {
        v.push(sc(
            format!("spa_product_{n}"),
            "spa_routing",
            vec![nav(&format!("/app/spa#/products/{n}")), Step::Wait(0.2)],
            vec![
                present("Product Detail View"),
                Check::UrlContains(format!("products/{n}")),
            ],
        ));
    }
    // History API push.
    for i in 0..10 {
        v.push(sc(
            format!("spa_push_{i}"),
            "spa_routing",
            vec![
                nav("/app/spa"),
                Step::Wait(0.2),
                click("Open dashboard"),
                Step::Wait(0.2),
            ],
            vec![
                present("Dashboard View"),
                Check::UrlContains("view=dashboard".into()),
            ],
        ));
    }
}

// 7. Infinite scroll.
fn gen_infinite_scroll(v: &mut Vec<Scenario>) {
    for target in [40usize, 60, 80, 100, 120, 140, 160, 180, 200] {
        let mut steps = vec![nav("/app/feed"), Step::Wait(0.2)];
        let rounds = target / 20 + 2;
        for _ in 0..rounds {
            steps.push(Step::Scroll {
                down: true,
                pages: 10.0,
            });
            steps.push(Step::Wait(0.2));
        }
        steps.push(Step::WaitText {
            text: format!("Post {target}"),
            appear: true,
            timeout: 6.0,
        });
        v.push(sc(
            format!("feed_to_{target}"),
            "infinite_scroll",
            steps,
            vec![present(&format!("Post {target}"))],
        ));
    }
    // Full scroll → end marker.
    {
        let mut steps = vec![nav("/app/feed"), Step::Wait(0.2)];
        for _ in 0..14 {
            steps.push(Step::Scroll {
                down: true,
                pages: 10.0,
            });
            steps.push(Step::Wait(0.2));
        }
        steps.push(Step::WaitText {
            text: "End of feed".into(),
            appear: true,
            timeout: 6.0,
        });
        v.push(sc(
            "feed_end".into(),
            "infinite_scroll",
            steps,
            vec![present("End of feed"), present("Post 200")],
        ));
    }
    // Early items present after first load (no scroll).
    for i in 1..=5 {
        v.push(sc(
            format!("feed_initial_{i}"),
            "infinite_scroll",
            vec![nav("/app/feed"), Step::Wait(0.3)],
            vec![present(&format!("Post {i}"))],
        ));
    }
}

// 8. Custom in-page modals (reliable DOM dialogs). Native JS dialogs
// (alert/confirm/prompt) are intentionally excluded from the auto-run suite
// because their auto-dismiss timing is non-deterministic; cover them manually.
fn gen_modals(v: &mut Vec<Scenario>) {
    for i in 0..18 {
        v.push(sc(
            format!("modal_confirm_{i}"),
            "modal_dialogs",
            vec![
                nav("/app/modals"),
                click("Open dialog"),
                wait_text("Are you sure?"),
                click("Confirm"),
                Step::Wait(0.15),
            ],
            vec![present("dialog confirmed")],
        ));
        v.push(sc(
            format!("modal_cancel_{i}"),
            "modal_dialogs",
            vec![
                nav("/app/modals"),
                click("Open dialog"),
                wait_text("Are you sure?"),
                click("Cancel"),
                Step::Wait(0.15),
            ],
            vec![present("dialog cancelled")],
        ));
    }
}

// 9. Iframes.
fn gen_iframe(v: &mut Vec<Scenario>) {
    for i in 0..12 {
        v.push(sc(
            format!("iframe_frames_{i}"),
            "iframe",
            vec![nav("/app/iframe"), Step::Wait(0.4)],
            vec![
                present("PARENT_OK"),
                Check::FrameCountAtLeast(2),
                Check::FrameHtmlContains("Child frame".into()),
            ],
        ));
    }
    for i in 0..8 {
        v.push(sc(
            format!("iframe_child_marker_{i}"),
            "iframe",
            vec![nav("/app/iframe"), Step::Wait(0.4)],
            vec![Check::FrameHtmlContains("CHILD_OK".into())],
        ));
    }
}

// 10. Shadow DOM. get_state now pierces open shadow roots, so the shadow
// input/button are reachable as refs and driven via the real type/click tools.
fn gen_shadow(v: &mut Vec<Scenario>) {
    for i in 0..18 {
        let val = format!("shadowval{i}");
        v.push(sc(
            format!("shadow_submit_{i}"),
            "shadow_dom",
            vec![
                nav("/app/shadow"),
                Step::Wait(0.3),
                typ("Shadow input", &val),
                click("Shadow submit"),
                Step::Wait(0.15),
            ],
            vec![present(&format!("shadow: {val}"))],
        ));
    }
}

// 11. Drag-and-drop kanban.
fn gen_kanban(v: &mut Vec<Scenario>) {
    // (card selector, target column selector, expected count text fragment)
    let moves = [
        ("#c1", "#doing", "doing:2"),
        ("#c1", "#done", "done:1"),
        ("#c2", "#done", "done:1"),
        ("#c2", "#doing", "doing:2"),
        ("#c3", "#todo", "todo:3"),
        ("#c3", "#done", "done:1"),
        ("#c1", "#todo", "todo:2"),
        ("#c2", "#todo", "todo:2"),
    ];
    for (i, (from, to, expect)) in moves.iter().enumerate() {
        v.push(sc(
            format!("kanban_move_{i}"),
            "drag_drop",
            vec![
                Step::Viewport { w: 1280, h: 900 },
                nav("/app/kanban"),
                Step::Wait(0.2),
                Step::DragSelector {
                    from: from.to_string(),
                    to: to.to_string(),
                },
                Step::Wait(0.2),
            ],
            vec![present(expect)],
        ));
    }
    // Card text remains present after each move (no data loss).
    for i in 0..8 {
        v.push(sc(
            format!("kanban_intact_{i}"),
            "drag_drop",
            vec![
                Step::Viewport { w: 1280, h: 900 },
                nav("/app/kanban"),
                Step::Wait(0.2),
                Step::DragSelector {
                    from: "#c1".into(),
                    to: "#done".into(),
                },
                Step::Wait(0.2),
            ],
            vec![
                present("Card One"),
                present("Card Two"),
                present("Card Three"),
            ],
        ));
    }
}

// 12. Network resilience — status codes, redirects, slow responses, cookies.
fn gen_resilience(v: &mut Vec<Scenario>) {
    for code in [
        200u16, 201, 400, 401, 403, 404, 409, 422, 429, 500, 502, 503, 504,
    ] {
        v.push(sc(
            format!("net_status_{code}"),
            "resilience_network",
            vec![nav(&format!("/status/{code}")), Step::Wait(0.2)],
            vec![present(&format!("status {code}"))],
        ));
    }
    for code in [301u16, 302, 303, 307] {
        v.push(sc(
            format!("net_redirect_{code}"),
            "resilience_network",
            vec![
                nav(&format!(
                    "/redirect?to=/app/dashboard?u=Redirected&code={code}"
                )),
                Step::WaitUrl {
                    substr: "dashboard".into(),
                    timeout: 5.0,
                },
            ],
            vec![present("Welcome, Redirected")],
        ));
    }
    for i in 0..12 {
        v.push(sc(
            format!("net_slow_{i}"),
            "resilience_network",
            vec![
                nav("/app/waits"),
                click("Fetch slow"),
                Step::WaitText {
                    text: "slow ok".into(),
                    appear: true,
                    timeout: 6.0,
                },
            ],
            vec![present("Fetched: slow ok")],
        ));
    }
    for i in 0..15 {
        let name = format!("sid{i}");
        let value = format!("v{i}");
        v.push(sc(
            format!("net_cookie_{i}"),
            "resilience_network",
            vec![
                nav(&format!("/set-cookie?name={name}&value={value}")),
                Step::Wait(0.2),
            ],
            vec![Check::JsContains {
                code: "document.cookie".into(),
                needle: format!("{name}={value}"),
            }],
        ));
    }
    for i in 0..10 {
        v.push(sc(
            format!("net_404_recover_{i}"),
            "resilience_network",
            vec![
                nav(&format!("/missing-page-{i}")),
                Step::Wait(0.2),
                nav("/app"),
                Step::Wait(0.2),
            ],
            vec![present("agentyc fixtures")],
        ));
    }
    for i in 0..10 {
        v.push(sc(
            format!("net_json_{i}"),
            "resilience_network",
            vec![nav("/json"), Step::Wait(0.2)],
            vec![Check::JsContains {
                code: "document.body.innerText".into(),
                needle: "slideshow".into(),
            }],
        ));
    }
    // 503 then recover to 200.
    for i in 0..6 {
        v.push(sc(
            format!("net_retry_{i}"),
            "resilience_network",
            vec![
                nav("/status/503"),
                Step::Wait(0.2),
                nav("/status/200"),
                Step::Wait(0.2),
            ],
            vec![present("status 200")],
        ));
    }
}

// 13. Multi-tab.
fn gen_multitab(v: &mut Vec<Scenario>) {
    let targets = [
        ("/app/login", "Sign in"),
        ("/app/shop", "Shop"),
        ("/app/register", "Create account"),
        ("/app/search", "Search"),
        ("/app/table", "Reports"),
        ("/app/modals", "Modals"),
        ("/app/spa", "Home View"),
        ("/app/feed", "Post 1"),
    ];
    for (i, (path, text)) in targets.iter().enumerate() {
        for r in 0..2 {
            v.push(sc(
                format!("multitab_{i}_{r}"),
                "multi_tab",
                vec![nav("/app"), Step::NewTab(path.to_string()), Step::Wait(0.4)],
                vec![present(text)],
            ));
        }
    }
    // Open two, switch back to the first.
    for i in 0..6 {
        v.push(sc(
            format!("multitab_switch_{i}"),
            "multi_tab",
            vec![
                nav("/app/login"),
                Step::NewTab("/app/shop".into()),
                Step::Wait(0.3),
                Step::SwitchTabUrl("/app/login".into()),
                Step::Wait(0.3),
            ],
            vec![present("Sign in")],
        ));
    }
}

// 14. Emulation — viewport, timezone, locale, color scheme, geolocation.
fn gen_emulation(v: &mut Vec<Scenario>) {
    let sizes: &[(u32, u32)] = &[
        (320, 568),
        (375, 667),
        (390, 844),
        (414, 896),
        (768, 1024),
        (820, 1180),
        (1024, 768),
        (1280, 720),
        (1366, 768),
        (1440, 900),
        (1536, 864),
        (1600, 900),
        (1680, 1050),
        (1920, 1080),
        (2560, 1440),
        (360, 640),
        (412, 915),
        (480, 800),
        (600, 960),
        (1024, 1366),
    ];
    for (i, (w, h)) in sizes.iter().enumerate() {
        v.push(sc(
            format!("emu_viewport_{i}"),
            "emulation",
            vec![
                nav("/app"),
                Step::Viewport { w: *w, h: *h },
                Step::Wait(0.1),
            ],
            vec![Check::JsEq {
                code: "window.innerWidth".into(),
                expected: w.to_string(),
            }],
        ));
    }
    let zones = [
        "America/New_York",
        "America/Los_Angeles",
        "America/Chicago",
        "America/Denver",
        "Europe/London",
        "Europe/Paris",
        "Europe/Berlin",
        "Europe/Madrid",
        "Europe/Moscow",
        "Asia/Tokyo",
        "Asia/Singapore",
        "America/Toronto",
        "Asia/Dubai",
        "Asia/Shanghai",
        "Australia/Sydney",
        "Pacific/Auckland",
        "America/Sao_Paulo",
        "Africa/Cairo",
        "Asia/Seoul",
        "UTC",
    ];
    for (i, tz) in zones.iter().enumerate() {
        v.push(sc(
            format!("emu_tz_{i}"),
            "emulation",
            vec![
                nav("/app"),
                Step::SetTimezone(tz.to_string()),
                Step::Wait(0.1),
            ],
            vec![Check::JsContains {
                code: "Intl.DateTimeFormat().resolvedOptions().timeZone".into(),
                needle: tz.to_string(),
            }],
        ));
    }
    let locales = [
        "en-US", "en-GB", "fr-FR", "de-DE", "es-ES", "it-IT", "pt-BR", "ja-JP", "ko-KR", "zh-CN",
        "nl-NL", "sv-SE", "pl-PL", "tr-TR", "ar-SA",
    ];
    for (i, loc) in locales.iter().enumerate() {
        v.push(sc(
            format!("emu_locale_{i}"),
            "emulation",
            vec![
                nav("/app"),
                Step::SetLocale(loc.to_string()),
                Step::Wait(0.1),
            ],
            vec![Check::JsContains {
                code: "Intl.DateTimeFormat().resolvedOptions().locale".into(),
                needle: loc.to_string(),
            }],
        ));
    }
    for scheme in ["dark", "light"] {
        for i in 0..3 {
            let needle = if scheme == "dark" { "true" } else { "false" };
            v.push(sc(
                format!("emu_scheme_{scheme}_{i}"),
                "emulation",
                vec![
                    nav("/app"),
                    Step::EmulateColorScheme(scheme.to_string()),
                    Step::Wait(0.1),
                ],
                vec![Check::JsContains {
                    code: "String(matchMedia('(prefers-color-scheme: dark)').matches)".into(),
                    needle: needle.to_string(),
                }],
            ));
        }
    }
    for i in 0..10 {
        v.push(sc(
            format!("emu_geo_{i}"),
            "emulation",
            vec![
                nav("/app"),
                Step::GrantGeolocation,
                Step::SetGeolocation {
                    lat: 37.77 + i as f64 * 0.1,
                    lon: -122.41 - i as f64 * 0.1,
                },
                Step::Wait(0.1),
            ],
            vec![present("agentyc fixtures")],
        ));
    }
}

// 15. Deterministic extraction across real rendered pages.
fn gen_extraction(v: &mut Vec<Scenario>) {
    // Table rows extraction across pages (assert a known cell present).
    for page in 1..=20 {
        let first_id = (page - 1) * 10 + 1;
        v.push(sc(
            format!("extract_table_{page}"),
            "extraction",
            vec![nav(&format!("/app/table?page={page}&sort=id&dir=asc"))],
            vec![Check::ExtractContains {
                query: "table rows".into(),
                needle: first_id.to_string(),
            }],
        ));
    }
    // Links extraction on the index.
    for i in 0..10 {
        v.push(sc(
            format!("extract_links_{i}"),
            "extraction",
            vec![nav("/app")],
            vec![Check::ExtractContains {
                query: "all links".into(),
                needle: "login".into(),
            }],
        ));
    }
    // Form fields extraction on register.
    for i in 0..10 {
        v.push(sc(
            format!("extract_form_{i}"),
            "extraction",
            vec![nav("/app/register")],
            vec![Check::ExtractContains {
                query: "form fields".into(),
                needle: "Email".into(),
            }],
        ));
    }
    // List extraction on checkout lines.
    for i in 0..10usize {
        let a = i % PRODUCTS.len();
        v.push(sc(
            format!("extract_list_{i}"),
            "extraction",
            vec![nav(&format!(
                "/app/checkout?items={a}|{}",
                (a + 1) % PRODUCTS.len()
            ))],
            vec![Check::ExtractContains {
                query: "list items".into(),
                needle: PRODUCTS[a].into(),
            }],
        ));
    }
}

// 16. Storage + cookies via the dedicated tools.
fn gen_storage(v: &mut Vec<Scenario>) {
    for i in 0..25 {
        let key = format!("key{i}");
        let val = format!("value{i}");
        v.push(sc(
            format!("storage_local_{i}"),
            "storage_cookies",
            vec![
                nav("/app/storage"),
                Step::SetStorage {
                    area: "localStorage".into(),
                    key: key.clone(),
                    value: val.clone(),
                },
                Step::Wait(0.05),
            ],
            vec![Check::JsContains {
                code: format!("String(localStorage.getItem('{key}'))"),
                needle: val,
            }],
        ));
    }
    for i in 0..10 {
        let key = format!("skey{i}");
        let val = format!("sval{i}");
        v.push(sc(
            format!("storage_session_{i}"),
            "storage_cookies",
            vec![
                nav("/app/storage"),
                Step::SetStorage {
                    area: "sessionStorage".into(),
                    key: key.clone(),
                    value: val.clone(),
                },
                Step::Wait(0.05),
            ],
            vec![Check::JsContains {
                code: format!("String(sessionStorage.getItem('{key}'))"),
                needle: val,
            }],
        ));
    }
    for i in 0..15 {
        let name = format!("ck{i}");
        let val = format!("cv{i}");
        v.push(sc(
            format!("storage_cookie_{i}"),
            "storage_cookies",
            vec![
                nav("/app/storage"),
                Step::SetCookie {
                    name: name.clone(),
                    value: val.clone(),
                },
                Step::Wait(0.05),
            ],
            vec![Check::JsContains {
                code: "document.cookie".into(),
                needle: format!("{name}={val}"),
            }],
        ));
    }
}

// 17. Accessibility / keyboard.
fn gen_a11y(v: &mut Vec<Scenario>) {
    for i in 0..10 {
        v.push(sc(
            format!("a11y_label_{i}"),
            "accessibility",
            vec![nav("/app/a11y")],
            vec![
                present("Email address"),
                Check::ElementCountAtLeast {
                    selector: "[role=alert]".into(),
                    min: 1,
                },
            ],
        ));
    }
    for i in 0..10 {
        v.push(sc(
            format!("a11y_type_{i}"),
            "accessibility",
            vec![
                nav("/app/a11y"),
                typ("you@example.com", &format!("user{i}@mail.com")),
                Step::Wait(0.05),
            ],
            vec![Check::JsContains {
                code: "document.getElementById('a1').value".into(),
                needle: format!("user{i}@mail.com"),
            }],
        ));
    }
    for i in 0..10 {
        v.push(sc(
            format!("a11y_tab_{i}"),
            "accessibility",
            vec![
                nav("/app/a11y"),
                Step::Eval("document.getElementById('t1').focus()".into()),
                Step::PressKey("Tab".into()),
                Step::Wait(0.1),
            ],
            vec![Check::FocusedContains("t2".into())],
        ));
    }
}

// 18. Dynamic content waits.
fn gen_waits(v: &mut Vec<Scenario>) {
    for i in 0..15 {
        v.push(sc(
            format!("wait_load_{i}"),
            "dynamic_waits",
            vec![
                nav("/app/waits"),
                click("Load"),
                Step::WaitText {
                    text: "Loaded!".into(),
                    appear: true,
                    timeout: 5.0,
                },
            ],
            vec![present("Loaded!")],
        ));
    }
    for i in 0..15 {
        v.push(sc(
            format!("wait_flaky_{i}"),
            "dynamic_waits",
            vec![
                nav("/app/waits"),
                click("Flaky"),
                Step::WaitText {
                    text: "Ready".into(),
                    appear: true,
                    timeout: 5.0,
                },
            ],
            vec![present("Ready")],
        ));
    }
    for i in 0..10 {
        v.push(sc(
            format!("wait_stable_{i}"),
            "dynamic_waits",
            vec![
                nav("/app/waits"),
                click("Load"),
                wait_text("Loaded!"),
                Step::WaitStableDom {
                    quiet_ms: 250,
                    timeout: 5.0,
                },
            ],
            vec![present("Loaded!")],
        ));
    }
    for i in 0..10 {
        v.push(sc(
            format!("wait_netidle_{i}"),
            "dynamic_waits",
            vec![
                nav("/app/waits"),
                click("Fetch slow"),
                Step::WaitNetworkIdle { timeout: 6.0 },
            ],
            vec![present("Fetched: slow ok")],
        ));
    }
}

// ── Additional realistic, multi-step journeys ────────────────────────────────

// 19. End-to-end: login → dashboard → shop → add to cart → checkout → confirm.
fn gen_journeys(v: &mut Vec<Scenario>) {
    for (i, u) in usernames().into_iter().take(80).enumerate() {
        v.push(sc(
            format!("journey_{i}_{u}"),
            "journey_e2e",
            vec![
                nav("/app/login"),
                typ("Username", &u),
                typ("Password", &format!("pw_{u}")),
                click("Sign in"),
                Step::WaitUrl {
                    substr: "/app/dashboard".into(),
                    timeout: 5.0,
                },
                click("Shop"),
                wait_text("Cart: 0"),
                click("Add Aurora Lamp"),
                wait_text("Cart: 1"),
                click("Go to checkout"),
                wait_text("Checkout"),
                click("Place order"),
                wait_text("Order confirmed"),
            ],
            vec![
                present("Order confirmed"),
                present("Aurora Lamp"),
                present("Basalt Mug"),
            ],
        ));
    }
}

// 20. Form error → fix → success recovery flows.
fn gen_form_recovery(v: &mut Vec<Scenario>) {
    for i in 0..60 {
        let pw = format!("password{i:03}");
        v.push(sc(
            format!("recover_email_{i}"),
            "form_recovery",
            vec![
                nav("/app/register"),
                typ("Full name", &format!("User {i}")),
                typ("Email", &format!("user{i}-bad")),
                click("Create account"),
                wait_text("Enter a valid email"),
                typ("Email", &format!("user{i}@example.com")),
                typ("Password", &pw),
                typ("Confirm password", &pw),
                typ("Age", "27"),
                Step::SelectFirst { value: "UK".into() },
                Step::SetCheckbox(true),
                click("Create account"),
                wait_text("Account created"),
            ],
            vec![present("Account created")],
        ));
    }
}

// 21. Search refine: partial → exact → no-match.
fn gen_search_refine(v: &mut Vec<Scenario>) {
    for k in 0..80usize {
        let p = PRODUCTS[k % PRODUCTS.len()];
        let prefix: String = p.to_lowercase().chars().take(3).collect();
        v.push(sc(
            format!("search_refine_{k}"),
            "search_refine",
            vec![
                nav("/app/search"),
                typ("Search products", &prefix),
                Step::Wait(0.4),
                Step::WaitText {
                    text: p.to_string(),
                    appear: true,
                    timeout: 5.0,
                },
                typ("Search products", p),
                Step::Wait(0.4),
                Step::WaitText {
                    text: p.to_string(),
                    appear: true,
                    timeout: 5.0,
                },
                typ("Search products", &format!("zzqx{k}")),
                Step::Wait(0.4),
                Step::WaitText {
                    text: "No results".into(),
                    appear: true,
                    timeout: 5.0,
                },
            ],
            vec![present("No results")],
        ));
    }
}

// 22. Compound table workflows: filter + sort + paginate together.
fn gen_table_workflows(v: &mut Vec<Scenario>) {
    let depts = ["Sales", "Eng", "Ops", "Legal", "HR"];
    for dept in depts {
        for sort in ["name", "amount"] {
            for dir in ["asc", "desc"] {
                for page in 1..=2 {
                    v.push(sc(
                        format!("tablewf_{}_{sort}_{dir}_{page}", dept.to_lowercase()),
                        "table_workflow",
                        vec![nav(&format!(
                            "/app/table?q={dept}&sort={sort}&dir={dir}&page={page}"
                        ))],
                        vec![
                            present(&format!("Page {page} of 4")),
                            Check::ElementCount {
                                selector: ".row".into(),
                                count: 10,
                            },
                        ],
                    ));
                }
            }
        }
    }
    for name in ["Ava", "Ben", "Cleo", "Dan", "Eve"] {
        for sort in ["name", "amount"] {
            for page in 1..=2 {
                v.push(sc(
                    format!("tablewf_name_{}_{sort}_{page}", name.to_lowercase()),
                    "table_workflow",
                    vec![nav(&format!("/app/table?q={name}&sort={sort}&page={page}"))],
                    vec![
                        present(&format!("Page {page} of 2")),
                        Check::ElementCount {
                            selector: ".row".into(),
                            count: 10,
                        },
                    ],
                ));
            }
        }
    }
}

// 23. Shop: click multiple Add buttons, watch the cart badge, then checkout.
fn gen_shop_checkout(v: &mut Vec<Scenario>) {
    for i in 0..40 {
        v.push(sc(
            format!("shopflow_{i}"),
            "shop_checkout",
            vec![
                nav("/app/shop"),
                wait_text("Cart: 0"),
                click("Add Aurora Lamp"),
                wait_text("Cart: 1"),
                click("Add Basalt Mug"),
                wait_text("Cart: 2"),
                click("Add Cedar Chair"),
                wait_text("Cart: 3"),
                click("Go to checkout"),
                wait_text("Checkout"),
                click("Place order"),
                wait_text("Order confirmed"),
            ],
            vec![present("Order confirmed")],
        ));
    }
}

// 24. Responsive: each app must work across many viewport sizes.
fn gen_responsive(v: &mut Vec<Scenario>) {
    let sizes: &[(u32, u32)] = &[
        (320, 568),
        (375, 667),
        (390, 844),
        (414, 896),
        (768, 1024),
        (820, 1180),
        (1024, 768),
        (1280, 720),
        (1366, 768),
        (1440, 900),
        (1536, 864),
        (1920, 1080),
        (360, 640),
        (412, 915),
        (600, 960),
        (1024, 1366),
        (1600, 900),
        (480, 800),
    ];
    let apps: &[(&str, &str)] = &[
        ("/app/login", "Sign in"),
        ("/app/shop", "Shop"),
        ("/app/search", "Search"),
        ("/app/table", "Reports"),
        ("/app/register", "Create account"),
    ];
    for (si, (w, h)) in sizes.iter().enumerate() {
        for (ai, (path, text)) in apps.iter().enumerate() {
            v.push(sc(
                format!("responsive_{si}_{ai}"),
                "responsive",
                vec![Step::Viewport { w: *w, h: *h }, nav(path), Step::Wait(0.15)],
                vec![
                    present(text),
                    Check::JsEq {
                        code: "window.innerWidth".into(),
                        expected: w.to_string(),
                    },
                ],
            ));
        }
    }
}

// 25. Scroll to a lazily-loaded element, then assert it.
fn gen_scroll_interact(v: &mut Vec<Scenario>) {
    for target in [
        30usize, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 25, 33, 42, 51, 66, 72,
        84, 96, 28, 39, 48, 57, 63, 78, 99,
    ] {
        let mut steps = vec![nav("/app/feed"), Step::Wait(0.2)];
        let rounds = target / 20 + 2;
        for _ in 0..rounds {
            steps.push(Step::Scroll {
                down: true,
                pages: 8.0,
            });
            steps.push(Step::Wait(0.15));
        }
        steps.push(Step::ScrollToText(format!("Post {target}")));
        steps.push(Step::WaitText {
            text: format!("Post {target}"),
            appear: true,
            timeout: 6.0,
        });
        v.push(sc(
            format!("scrollto_{target}"),
            "scroll_interact",
            steps,
            vec![present(&format!("Post {target}"))],
        ));
    }
}

// 26. Cookie-based state: set via the cookie tool, then verify on the page.
fn gen_cookie_auth(v: &mut Vec<Scenario>) {
    for i in 0..40 {
        let name = format!("auth{i}");
        let value = format!("token{i}");
        v.push(sc(
            format!("cookieauth_{i}"),
            "cookie_auth",
            vec![
                nav("/app"),
                Step::SetCookie {
                    name: name.clone(),
                    value: value.clone(),
                },
                nav("/app/storage"),
                Step::Wait(0.1),
            ],
            vec![Check::JsContains {
                code: "document.cookie".into(),
                needle: format!("{name}={value}"),
            }],
        ));
    }
}

// 27. Network-idle waits around real fetches.
fn gen_netidle(v: &mut Vec<Scenario>) {
    for (i, p) in PRODUCTS.iter().enumerate() {
        v.push(sc(
            format!("netidle_search_{i}"),
            "network_idle",
            vec![
                nav("/app/search"),
                typ("Search products", p),
                Step::WaitNetworkIdle { timeout: 5.0 },
                Step::WaitText {
                    text: p.to_string(),
                    appear: true,
                    timeout: 5.0,
                },
            ],
            vec![present(p)],
        ));
    }
    for i in 0..20 {
        v.push(sc(
            format!("netidle_slow_{i}"),
            "network_idle",
            vec![
                nav("/app/waits"),
                click("Fetch slow"),
                Step::WaitNetworkIdle { timeout: 6.0 },
            ],
            vec![present("Fetched: slow ok")],
        ));
    }
}

// 28. More deterministic extraction across rendered pages.
fn gen_extraction_more(v: &mut Vec<Scenario>) {
    // Table rows on filtered + sorted pages.
    for dept in ["Sales", "Eng", "Ops", "Legal", "HR"] {
        for page in 1..=4 {
            v.push(sc(
                format!("extractwf_{}_{page}", dept.to_lowercase()),
                "extraction",
                vec![nav(&format!(
                    "/app/table?q={dept}&page={page}&sort=id&dir=asc"
                ))],
                vec![Check::ExtractContains {
                    query: "table rows".into(),
                    needle: dept.into(),
                }],
            ));
        }
    }
    // Links across several apps.
    for (i, (path, needle)) in [
        ("/app", "login"),
        ("/app", "shop"),
        ("/app", "table"),
        ("/app", "search"),
        ("/app", "register"),
    ]
    .iter()
    .enumerate()
    {
        for r in 0..6 {
            v.push(sc(
                format!("extractlinks_{i}_{r}"),
                "extraction",
                vec![nav(path)],
                vec![Check::ExtractContains {
                    query: "all links".into(),
                    needle: (*needle).into(),
                }],
            ));
        }
    }
    // Form fields on login + register.
    for i in 0..10 {
        v.push(sc(
            format!("extractform_login_{i}"),
            "extraction",
            vec![nav("/app/login")],
            vec![Check::ExtractContains {
                query: "form fields".into(),
                needle: "Password".into(),
            }],
        ));
    }
    for i in 0..10 {
        v.push(sc(
            format!("extractform_reg_{i}"),
            "extraction",
            vec![nav("/app/register")],
            vec![Check::ExtractContains {
                query: "form fields".into(),
                needle: "Confirm password".into(),
            }],
        ));
    }
}

// 29. Error-recovery chains: 404 → home → login → dashboard.
fn gen_error_chains(v: &mut Vec<Scenario>) {
    for (i, u) in usernames().into_iter().take(40).enumerate() {
        v.push(sc(
            format!("errorchain_{i}_{u}"),
            "error_recovery",
            vec![
                nav(&format!("/does-not-exist-{i}")),
                Step::Wait(0.15),
                nav("/app"),
                wait_text("agentyc fixtures"),
                click("Login"),
                wait_text("Sign in"),
                typ("Username", &u),
                typ("Password", &format!("pw_{u}")),
                click("Sign in"),
                Step::WaitUrl {
                    substr: "/app/dashboard".into(),
                    timeout: 5.0,
                },
            ],
            vec![present(&format!("Welcome, {u}"))],
        ));
    }
}

// 30. Save/restore browser state — exercises the persistence tools end to end.
fn gen_session(v: &mut Vec<Scenario>) {
    for i in 0..20 {
        let tok = format!("sess{i}");
        v.push(sc(
            format!("session_{i}"),
            "session_persistence",
            vec![
                nav(&format!("/set-cookie?name=session&value=tok{i}")),
                Step::Wait(0.1),
                Step::SaveState(tok.clone()),
                Step::Wait(0.1),
                Step::LoadState(tok.clone()),
                nav("/app/dashboard?u=Restored"),
                Step::Wait(0.1),
            ],
            vec![present("Welcome, Restored")],
        ));
    }
}

// 31. Keyboard navigation — tab order and focus management.
fn gen_keyboard(v: &mut Vec<Scenario>) {
    for i in 0..15 {
        v.push(sc(
            format!("kbd_tab1_{i}"),
            "keyboard",
            vec![
                nav("/app/a11y"),
                Step::Eval("document.getElementById('t1').focus()".into()),
                Step::PressKey("Tab".into()),
                Step::Wait(0.1),
            ],
            vec![Check::FocusedContains("t2".into())],
        ));
    }
    for i in 0..15 {
        v.push(sc(
            format!("kbd_tab2_{i}"),
            "keyboard",
            vec![
                nav("/app/a11y"),
                Step::Eval("document.getElementById('t1').focus()".into()),
                Step::PressKey("Tab".into()),
                Step::Wait(0.05),
                Step::PressKey("Tab".into()),
                Step::Wait(0.1),
            ],
            vec![Check::FocusedContains("t3".into())],
        ));
    }
}

// 32. Soak: synthetic end-to-end login journeys. Count is tuned via SOAK_LOGINS
// so the full generated catalog lands on the target total. Each is a real,
// distinct auth journey (navigate → fill → submit → verify dashboard).
fn gen_soak(v: &mut Vec<Scenario>) {
    for k in 0..SOAK_LOGINS {
        let u = format!("soaker{k}");
        v.push(sc(
            format!("soak_login_{k}"),
            "soak_login",
            vec![
                nav("/app/login"),
                typ("Username", &u),
                typ("Password", &format!("pw_{u}")),
                click("Sign in"),
                Step::WaitUrl {
                    substr: "/app/dashboard".into(),
                    timeout: 5.0,
                },
            ],
            vec![
                present(&format!("Welcome, {u}")),
                Check::UrlContains("dashboard".into()),
            ],
        ));
    }
}


// 8b. Native JS dialogs — deterministic via the auto-handler (default accept)
// and policy set through browser_handle_dialog.
fn gen_native_dialogs(v: &mut Vec<Scenario>) {
    for i in 0..10 {
        v.push(sc(
            format!("dialog_alert_{i}"),
            "native_dialogs",
            vec![nav("/app/modals"), click("Native alert"), Step::Wait(0.25)],
            vec![present("alert shown")],
        ));
    }
    for i in 0..10 {
        v.push(sc(
            format!("dialog_confirm_{i}"),
            "native_dialogs",
            vec![nav("/app/modals"), click("Native confirm"), Step::Wait(0.25)],
            vec![present("confirmed")],
        ));
    }
    for i in 0..10 {
        let name = format!("Neo{i}");
        v.push(sc(
            format!("dialog_prompt_{i}"),
            "native_dialogs",
            vec![
                nav("/app/modals"),
                Step::HandleDialog { accept: true, prompt: Some(name.clone()) },
                click("Native prompt"),
                Step::Wait(0.25),
            ],
            vec![present(&format!("hello {name}"))],
        ));
    }
}
