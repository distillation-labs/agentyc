//! Local fixtures HTTP server for the real-world battle-test suite.
//!
//! A tiny std-only HTTP/1.1 server that hosts realistic, stateful web apps plus
//! dynamic API endpoints. It exists so the suite can exercise the real MCP tools
//! against real browser behaviors (cookies, storage, fetch/XHR, navigation,
//! redirects, status codes, debounced inputs, infinite scroll, modals, iframes,
//! shadow DOM, drag-and-drop) with zero network dependency.
//!
//! Apps are designed with predictable, stable visible text and input
//! placeholders so the scenario engine can target elements through Agentyc's
//! own `browser_get_state` → ref → act loop.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::OnceLock;
use std::thread;
use std::time::Duration;

/// A running fixtures server. Drop is not implemented — it lives for the test
/// binary's lifetime via a global `OnceLock`.
pub struct Fixtures {
    pub base: String,
}

static SERVER: OnceLock<Fixtures> = OnceLock::new();

/// Start (once) and return the shared fixtures server base URL, e.g.
/// `http://127.0.0.1:54321`.
pub fn base_url() -> &'static str {
    &SERVER.get_or_init(start).base
}

fn start() -> Fixtures {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind fixtures server");
    let port = listener.local_addr().unwrap().port();
    thread::spawn(move || {
        for s in listener.incoming().flatten() {
            thread::spawn(move || {
                let _ = handle_conn(s);
            });
        }
    });
    Fixtures {
        base: format!("http://127.0.0.1:{port}"),
    }
}

fn handle_conn(mut stream: TcpStream) -> std::io::Result<()> {
    let mut reader = BufReader::new(stream.try_clone()?);
    let mut request_line = String::new();
    if reader.read_line(&mut request_line)? == 0 {
        return Ok(());
    }
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or("GET").to_string();
    let target = parts.next().unwrap_or("/").to_string();

    // Read headers (collect Content-Length).
    let mut content_length = 0usize;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line)? == 0 {
            break;
        }
        let trimmed = line.trim_end();
        if trimmed.is_empty() {
            break;
        }
        if let Some(v) = trimmed.strip_prefix("Content-Length:") {
            content_length = v.trim().parse().unwrap_or(0);
        } else if let Some(v) = trimmed.to_ascii_lowercase().strip_prefix("content-length:") {
            content_length = v.trim().parse().unwrap_or(0);
        }
    }
    let mut body = vec![0u8; content_length];
    if content_length > 0 {
        reader.read_exact(&mut body)?;
    }
    let body = String::from_utf8_lossy(&body).to_string();

    let (path, query) = match target.split_once('?') {
        Some((p, q)) => (p.to_string(), q.to_string()),
        None => (target.clone(), String::new()),
    };

    let resp = route(&method, &path, &query, &body);
    write_response(&mut stream, resp)
}

struct Resp {
    status: u16,
    content_type: &'static str,
    headers: Vec<(String, String)>,
    body: String,
}

impl Resp {
    fn html(body: String) -> Resp {
        Resp {
            status: 200,
            content_type: "text/html; charset=utf-8",
            headers: vec![],
            body,
        }
    }
    fn json(body: String) -> Resp {
        Resp {
            status: 200,
            content_type: "application/json",
            headers: vec![],
            body,
        }
    }
    fn text(status: u16, body: String) -> Resp {
        Resp {
            status,
            content_type: "text/plain; charset=utf-8",
            headers: vec![],
            body,
        }
    }
}

fn status_text(code: u16) -> &'static str {
    match code {
        200 => "OK",
        201 => "Created",
        204 => "No Content",
        301 => "Moved Permanently",
        302 => "Found",
        303 => "See Other",
        307 => "Temporary Redirect",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        409 => "Conflict",
        422 => "Unprocessable Entity",
        429 => "Too Many Requests",
        500 => "Internal Server Error",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        504 => "Gateway Timeout",
        _ => "Status",
    }
}

fn write_response(stream: &mut TcpStream, resp: Resp) -> std::io::Result<()> {
    let mut head = format!(
        "HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\nCache-Control: no-store\r\n",
        resp.status,
        status_text(resp.status),
        resp.content_type,
        resp.body.len(),
    );
    for (k, v) in &resp.headers {
        head.push_str(&format!("{k}: {v}\r\n"));
    }
    head.push_str("\r\n");
    stream.write_all(head.as_bytes())?;
    stream.write_all(resp.body.as_bytes())?;
    stream.flush()
}

/// Parse a urlencoded query/body into key/value pairs (minimal decoding).
fn parse_kv(raw: &str) -> Vec<(String, String)> {
    raw.split('&')
        .filter(|s| !s.is_empty())
        .map(|pair| {
            let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
            (urldecode(k), urldecode(v))
        })
        .collect()
}

fn qget(query: &str, key: &str) -> Option<String> {
    parse_kv(query)
        .into_iter()
        .find(|(k, _)| k == key)
        .map(|(_, v)| v)
}

fn urldecode(s: &str) -> String {
    let bytes = s.replace('+', " ");
    let mut out = String::new();
    let mut chars = bytes.bytes().peekable();
    while let Some(b) = chars.next() {
        if b == b'%' {
            let h1 = chars.next();
            let h2 = chars.next();
            if let (Some(a), Some(c)) = (h1, h2)
                && let (Some(x), Some(y)) = (hex(a), hex(c))
            {
                out.push((x * 16 + y) as char);
                continue;
            }
        } else {
            out.push(b as char);
        }
    }
    out
}

fn hex(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}

fn esc(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

// ── Router ─────────────────────────────────────────────────────────────────────

fn route(method: &str, path: &str, query: &str, body: &str) -> Resp {
    match path {
        "/" | "/app" => Resp::html(page("agentyc fixtures", APP_INDEX)),
        "/app/login" => Resp::html(page("Login", APP_LOGIN)),
        "/app/dashboard" => Resp::html(page("Dashboard", &dashboard(query))),
        "/app/register" => Resp::html(page("Register", APP_REGISTER)),
        "/app/search" => Resp::html(page("Search", APP_SEARCH)),
        "/app/table" => Resp::html(page("Table", &table(query))),
        "/app/shop" => Resp::html(page("Shop", APP_SHOP)),
        "/app/checkout" => Resp::html(page("Checkout", &checkout(query))),
        "/app/spa" => Resp::html(page("SPA", APP_SPA)),
        "/app/feed" => Resp::html(page("Feed", APP_FEED)),
        "/app/modals" => Resp::html(page("Modals", APP_MODALS)),
        "/app/iframe" => Resp::html(page("Iframe", APP_IFRAME)),
        "/app/iframe-child" => Resp::html(page("Iframe Child", APP_IFRAME_CHILD)),
        "/app/shadow" => Resp::html(page("Shadow DOM", APP_SHADOW)),
        "/app/kanban" => Resp::html(page("Kanban", APP_KANBAN)),
        "/app/storage" => Resp::html(page("Storage", APP_STORAGE)),
        "/app/a11y" => Resp::html(page("Accessibility", APP_A11Y)),
        "/app/waits" => Resp::html(page("Waits", APP_WAITS)),

        // ── Dynamic API ──
        "/api/search" => api_search(query),
        "/api/items" => api_items(query),
        "/api/login" => api_login(body),
        "/api/echo" => Resp::json(format!("{{\"echo\":{:?}}}", body)),
        "/json" => Resp::json(
            "{\"app\":\"agentyc-fixtures\",\"ok\":true,\"slideshow\":{\"title\":\"Sample\"}}"
                .to_string(),
        ),
        "/set-cookie" => {
            let name = qget(query, "name").unwrap_or_else(|| "fixture".into());
            let value = qget(query, "value").unwrap_or_else(|| "1".into());
            Resp {
                status: 200,
                content_type: "text/plain; charset=utf-8",
                headers: vec![("Set-Cookie".into(), format!("{name}={value}; Path=/"))],
                body: format!("set {name}={value}"),
            }
        }
        "/redirect" => {
            let to = qget(query, "to").unwrap_or_else(|| "/app".into());
            let code: u16 = qget(query, "code")
                .and_then(|c| c.parse().ok())
                .unwrap_or(302);
            Resp {
                status: code,
                content_type: "text/plain; charset=utf-8",
                headers: vec![("Location".into(), to)],
                body: "redirecting".into(),
            }
        }
        "/slow" => {
            let ms: u64 = qget(query, "ms")
                .and_then(|m| m.parse().ok())
                .unwrap_or(300);
            thread::sleep(Duration::from_millis(ms.min(5000)));
            Resp::text(200, "slow ok".into())
        }
        _ => {
            // /status/{code}
            if let Some(rest) = path.strip_prefix("/status/")
                && let Ok(code) = rest.parse::<u16>()
            {
                let _ = method;
                return Resp::text(code, format!("status {code}"));
            }
            Resp::text(404, "not found".into())
        }
    }
}

// ── Dynamic API handlers ─────────────────────────────────────────────────────

/// Stable product catalog shared (by value) with the scenario generator.
pub const PRODUCTS: &[&str] = &[
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

fn api_search(query: &str) -> Resp {
    let q = qget(query, "q").unwrap_or_default().to_lowercase();
    let matches: Vec<String> = if q.trim().is_empty() {
        vec![]
    } else {
        PRODUCTS
            .iter()
            .filter(|p| p.to_lowercase().contains(&q))
            .map(|p| format!("{:?}", p))
            .collect()
    };
    Resp::json(format!(
        "{{\"q\":{:?},\"results\":[{}]}}",
        q,
        matches.join(",")
    ))
}

fn api_items(query: &str) -> Resp {
    let page: usize = qget(query, "page")
        .and_then(|p| p.parse().ok())
        .unwrap_or(1)
        .max(1);
    let size: usize = qget(query, "size")
        .and_then(|p| p.parse().ok())
        .unwrap_or(10)
        .clamp(1, 100);
    let total = 200usize;
    let start = (page - 1) * size;
    let items: Vec<String> = (start..(start + size).min(total))
        .map(|i| format!("{{\"id\":{},\"name\":\"Item {}\"}}", i + 1, i + 1))
        .collect();
    Resp::json(format!(
        "{{\"page\":{page},\"size\":{size},\"total\":{total},\"items\":[{}]}}",
        items.join(",")
    ))
}

fn api_login(body: &str) -> Resp {
    let fields = parse_kv(body);
    let user = fields
        .iter()
        .find(|(k, _)| k == "username")
        .map(|(_, v)| v.clone())
        .unwrap_or_default();
    let pass = fields
        .iter()
        .find(|(k, _)| k == "password")
        .map(|(_, v)| v.clone())
        .unwrap_or_default();
    if !user.is_empty() && pass == format!("pw_{user}") {
        Resp::json(format!("{{\"ok\":true,\"token\":\"tok_{user}\"}}"))
    } else {
        Resp {
            status: 401,
            content_type: "application/json",
            headers: vec![],
            body: "{\"ok\":false,\"error\":\"invalid\"}".to_string(),
        }
    }
}

// ── Server-rendered apps ──────────────────────────────────────────────────────

fn dashboard(query: &str) -> String {
    let user = qget(query, "u").unwrap_or_else(|| "guest".into());
    format!(
        r#"<h1 id="title">Dashboard</h1>
<p id="welcome">Welcome, {user}</p>
<nav><a href="/app/shop">Shop</a> <a href="/app/table">Reports</a></nav>
<button id="signout" onclick="location.href='/app/login'">Sign out</button>"#,
        user = esc(&user)
    )
}

/// Deterministic 200-row dataset for the data-table app.
fn table_rows() -> Vec<(usize, String, String, u32)> {
    let depts = ["Sales", "Eng", "Ops", "Legal", "HR"];
    let names = [
        "Ava", "Ben", "Cleo", "Dan", "Eve", "Finn", "Gia", "Hugo", "Ivy", "Jon",
    ];
    (1..=200)
        .map(|i| {
            let name = format!("{} {}", names[i % names.len()], i);
            let dept = depts[i % depts.len()].to_string();
            let amount = ((i * 37) % 1000) as u32;
            (i, name, dept, amount)
        })
        .collect()
}

fn table(query: &str) -> String {
    let page: usize = qget(query, "page")
        .and_then(|p| p.parse().ok())
        .unwrap_or(1)
        .max(1);
    let sort = qget(query, "sort").unwrap_or_default();
    let dir = qget(query, "dir").unwrap_or_else(|| "asc".into());
    let filter = qget(query, "q").unwrap_or_default().to_lowercase();
    let size = 10usize;

    let mut rows = table_rows();
    if !filter.is_empty() {
        rows.retain(|(_, name, dept, _)| {
            name.to_lowercase().contains(&filter) || dept.to_lowercase().contains(&filter)
        });
    }
    match sort.as_str() {
        "name" => rows.sort_by(|a, b| a.1.cmp(&b.1)),
        "amount" => rows.sort_by_key(|a| a.3),
        "id" => rows.sort_by_key(|a| a.0),
        _ => {}
    }
    if dir == "desc" {
        rows.reverse();
    }

    let total = rows.len();
    let pages = total.div_ceil(size).max(1);
    let page = page.min(pages);
    let start = (page - 1) * size;
    let slice = &rows[start..(start + size).min(total)];

    let body_rows: String = slice
        .iter()
        .map(|(id, name, dept, amount)| {
            format!(
                "<tr class=\"row\"><td>{id}</td><td>{}</td><td>{}</td><td>{amount}</td></tr>",
                esc(name),
                esc(dept)
            )
        })
        .collect();

    let prev = if page > 1 {
        format!(
            "<a id=\"prev\" href=\"/app/table?page={}&sort={sort}&dir={dir}\">Prev</a>",
            page - 1
        )
    } else {
        "<span>Prev</span>".into()
    };
    let next = if page < pages {
        format!(
            "<a id=\"next\" href=\"/app/table?page={}&sort={sort}&dir={dir}\">Next</a>",
            page + 1
        )
    } else {
        "<span>Next</span>".into()
    };

    format!(
        r#"<h1>Reports</h1>
<p id="status">Page {page} of {pages} ({total} rows)</p>
<table border="1"><thead><tr>
<th><a href="/app/table?sort=id&dir=asc">ID</a></th>
<th><a href="/app/table?sort=name&dir=asc">Name</a></th>
<th>Dept</th>
<th><a href="/app/table?sort=amount&dir=desc">Amount</a></th>
</tr></thead><tbody>{body_rows}</tbody></table>
<div class="pager">{prev} {next}</div>"#
    )
}

fn checkout(query: &str) -> String {
    // ?items=name1|name2&coupon=CODE  (names are product indexes 0..N)
    let prices: Vec<u32> = (0..PRODUCTS.len())
        .map(|i| 10 + (i as u32 % 9) * 5)
        .collect();
    let items_raw = qget(query, "items").unwrap_or_default();
    let coupon = qget(query, "coupon").unwrap_or_default().to_uppercase();
    let idxs: Vec<usize> = items_raw
        .split('|')
        .filter_map(|s| s.parse::<usize>().ok())
        .filter(|i| *i < PRODUCTS.len())
        .collect();
    let subtotal: u32 = idxs.iter().map(|i| prices[*i]).sum();
    let (discount, coupon_msg) = match coupon.as_str() {
        "" => (0u32, String::new()),
        "SAVE10" => (subtotal / 10, "Coupon SAVE10 applied".into()),
        "FREESHIP" => (5.min(subtotal), "Coupon FREESHIP applied".into()),
        _ => (0, "Invalid coupon".into()),
    };
    let total = subtotal.saturating_sub(discount);
    let line_items: String = idxs
        .iter()
        .map(|i| {
            format!(
                "<li class=\"line\">{} — ${}</li>",
                esc(PRODUCTS[*i]),
                prices[*i]
            )
        })
        .collect();
    format!(
        r#"<h1>Checkout</h1>
<ul id="lines">{line_items}</ul>
<p id="subtotal">Subtotal: ${subtotal}</p>
<p id="coupon-msg">{coupon_msg}</p>
<p id="total">Total: ${total}</p>
<button id="place" onclick="document.getElementById('confirm').textContent='Order confirmed #4242'">Place order</button>
<p id="confirm"></p>"#
    )
}

// ── Static apps (HTML/JS) ─────────────────────────────────────────────────────

fn page(title: &str, body: &str) -> String {
    format!(
        r#"<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;max-width:900px}}
button,a.btn{{display:inline-block;min-width:120px;min-height:36px;padding:8px 14px;margin:6px;font-size:15px;cursor:pointer}}
input,select,textarea{{display:block;min-width:240px;min-height:32px;padding:6px;margin:8px 0;font-size:15px}}
.hidden{{display:none}}
.card,.post,.row,.result,.line{{padding:8px;border:1px solid #ccc;margin:4px 0}}
.col{{display:inline-block;vertical-align:top;width:240px;min-height:300px;border:1px solid #999;margin:6px;padding:8px}}
.modal{{position:fixed;top:80px;left:50%;transform:translateX(-50%);background:#fff;border:2px solid #333;padding:20px;z-index:10}}
</style></head><body>
<header><a href="/app">Home</a></header>
<main>{title_h}{body}</main>
</body></html>"#,
        title = esc(title),
        title_h = "",
        body = body
    )
}

const APP_INDEX: &str = r#"<h1>agentyc fixtures</h1>
<ul>
<li><a href="/app/login">Login</a></li>
<li><a href="/app/register">Register</a></li>
<li><a href="/app/search">Search</a></li>
<li><a href="/app/table">Table</a></li>
<li><a href="/app/shop">Shop</a></li>
<li><a href="/app/spa">SPA</a></li>
<li><a href="/app/feed">Feed</a></li>
<li><a href="/app/modals">Modals</a></li>
<li><a href="/app/iframe">Iframe</a></li>
<li><a href="/app/shadow">Shadow DOM</a></li>
<li><a href="/app/kanban">Kanban</a></li>
<li><a href="/app/storage">Storage</a></li>
<li><a href="/app/a11y">Accessibility</a></li>
<li><a href="/app/waits">Waits</a></li>
</ul>"#;

const APP_LOGIN: &str = r#"<h1>Sign in</h1>
<form id="login" onsubmit="return false">
<input id="u" placeholder="Username" autocomplete="off">
<input id="p" type="password" placeholder="Password" autocomplete="off">
<label><input type="checkbox" id="remember"> Remember me</label>
<button id="submit" onclick="doLogin()">Sign in</button>
</form>
<p id="msg"></p>
<script>
function doLogin(){
  var u=document.getElementById('u').value;
  var p=document.getElementById('p').value;
  var msg=document.getElementById('msg');
  if(!u){msg.textContent='Username is required';return;}
  if(!p){msg.textContent='Password is required';return;}
  if(p===('pw_'+u)){ location.href='/app/dashboard?u='+encodeURIComponent(u); return; }
  msg.textContent='Invalid credentials';
}
</script>"#;

const APP_REGISTER: &str = r#"<h1>Create account</h1>
<form id="reg" onsubmit="return false">
<input id="name" placeholder="Full name">
<input id="email" placeholder="Email">
<input id="pw" type="password" placeholder="Password">
<input id="pw2" type="password" placeholder="Confirm password">
<input id="age" placeholder="Age">
<select id="country"><option value="">Country…</option><option>US</option><option>UK</option><option>DE</option><option>JP</option></select>
<label><input type="checkbox" id="terms"> I accept the terms</label>
<button id="create" onclick="doReg()">Create account</button>
</form>
<p id="msg"></p>
<script>
function doReg(){
  var v=function(id){return document.getElementById(id).value;};
  var msg=document.getElementById('msg');
  if(!v('name')){msg.textContent='Name is required';return;}
  if(v('email').indexOf('@')<0){msg.textContent='Enter a valid email';return;}
  if(v('pw').length<8){msg.textContent='Password must be at least 8 characters';return;}
  if(v('pw2')!==v('pw')){msg.textContent='Passwords do not match';return;}
  var age=parseInt(v('age'),10);
  if(isNaN(age)||age<18){msg.textContent='You must be 18 or older';return;}
  if(!document.getElementById('terms').checked){msg.textContent='You must accept the terms';return;}
  msg.textContent='Account created';
}
</script>"#;

const APP_SEARCH: &str = r#"<h1>Search</h1>
<input id="q" placeholder="Search products" autocomplete="off" oninput="onInput()">
<p id="hint">Type to search</p>
<ul id="results"></ul>
<script>
var t=null;
function onInput(){
  clearTimeout(t);
  t=setTimeout(run,150);
}
function run(){
  var q=document.getElementById('q').value;
  var hint=document.getElementById('hint');
  var ul=document.getElementById('results');
  if(!q.trim()){ul.innerHTML='';hint.textContent='Type to search';return;}
  fetch('/api/search?q='+encodeURIComponent(q)).then(function(r){return r.json();}).then(function(d){
    ul.innerHTML='';
    if(!d.results.length){hint.textContent='No results for "'+q+'"';return;}
    hint.textContent=d.results.length+' results';
    d.results.forEach(function(name){
      var li=document.createElement('li');
      li.className='result';
      li.textContent=name;
      ul.appendChild(li);
    });
  });
}
</script>"#;

const APP_SHOP: &str = r#"<h1>Shop</h1>
<p id="count">Cart: 0</p>
<div id="grid">
<div class="card">Aurora Lamp — $10 <button onclick="add('Aurora Lamp')">Add Aurora Lamp</button></div>
<div class="card">Basalt Mug — $15 <button onclick="add('Basalt Mug')">Add Basalt Mug</button></div>
<div class="card">Cedar Chair — $20 <button onclick="add('Cedar Chair')">Add Cedar Chair</button></div>
<div class="card">Delta Keyboard — $25 <button onclick="add('Delta Keyboard')">Add Delta Keyboard</button></div>
</div>
<a class="btn" href="/app/checkout?items=0|1">Go to checkout</a>
<script>
var cart=[];
function add(n){cart.push(n);document.getElementById('count').textContent='Cart: '+cart.length;
  try{localStorage.setItem('cart',JSON.stringify(cart));}catch(e){}}
</script>"#;

const APP_SPA: &str = r##"<h1>SPA</h1>
<nav>
<a href="#/">Home</a> <a href="#/products">Products</a> <a href="#/products/42">Product 42</a>
<a href="#/cart">Cart</a> <a href="#/about">About</a> <a href="#/settings">Settings</a>
</nav>
<button id="push-dashboard" onclick="history.pushState({},'', '/app/spa?view=dashboard');render();">Open dashboard</button>
<div id="view"><h2 id="view-title">Loading…</h2></div>
<script>
function render(){
  var h=location.hash||'#/';
  var title='Home View';
  if(h.indexOf('#/products/')===0) title='Product Detail View';
  else if(h==='#/products') title='Products View';
  else if(h==='#/cart') title='Cart View';
  else if(h==='#/about') title='About View';
  else if(h==='#/settings') title='Settings View';
  else if(location.search.indexOf('view=dashboard')>=0) title='Dashboard View';
  document.getElementById('view-title').textContent=title;
}
window.addEventListener('hashchange',render);
window.addEventListener('popstate',render);
render();
</script>"##;

const APP_FEED: &str = r#"<h1>Feed</h1>
<div id="feed"></div>
<p id="end" class="hidden">End of feed</p>
<script>
var n=0, max=200;
function load(){
  var feed=document.getElementById('feed');
  for(var i=0;i<20 && n<max;i++){
    n++;
    var d=document.createElement('div');
    d.className='post';
    d.textContent='Post '+n;
    feed.appendChild(d);
  }
  if(n>=max){document.getElementById('end').classList.remove('hidden');}
}
load();
window.addEventListener('scroll',function(){
  if(window.innerHeight+window.scrollY>=document.body.offsetHeight-200){load();}
});
</script>"#;

const APP_MODALS: &str = r#"<h1>Modals</h1>
<button id="open" onclick="document.getElementById('m').classList.remove('hidden')">Open dialog</button>
<button id="alert" onclick="alert('alerted');document.getElementById('r').textContent='alert shown'">Native alert</button>
<button id="confirm" onclick="document.getElementById('r').textContent= confirm('Proceed?') ? 'confirmed' : 'cancelled'">Native confirm</button>
<button id="prompt" onclick="var v=prompt('Name?');document.getElementById('r').textContent='hello '+v">Native prompt</button>
<div id="m" class="modal hidden">
  <p>Are you sure?</p>
  <button id="m-confirm" onclick="document.getElementById('r').textContent='dialog confirmed';this.parentElement.classList.add('hidden')">Confirm</button>
  <button id="m-cancel" onclick="document.getElementById('r').textContent='dialog cancelled';this.parentElement.classList.add('hidden')">Cancel</button>
</div>
<p id="r"></p>"#;

const APP_IFRAME: &str = r#"<h1>Iframe host</h1>
<p>Parent content marker: PARENT_OK</p>
<iframe id="child" src="/app/iframe-child" style="width:400px;height:200px"></iframe>"#;

const APP_IFRAME_CHILD: &str = r#"<h2>Child frame</h2>
<input id="note" placeholder="Note">
<button id="save" onclick="document.getElementById('out').textContent='Saved: '+document.getElementById('note').value">Save</button>
<p id="out">CHILD_OK</p>"#;

const APP_SHADOW: &str = r#"<h1>Shadow DOM</h1>
<my-widget></my-widget>
<p id="host-out"></p>
<script>
class MyWidget extends HTMLElement{
  connectedCallback(){
    var root=this.attachShadow({mode:'open'});
    root.innerHTML='<input id="si" placeholder="Shadow input"><button id="sb">Shadow submit</button>';
    var self=this;
    root.getElementById('sb').addEventListener('click',function(){
      document.getElementById('host-out').textContent='shadow: '+root.getElementById('si').value;
    });
  }
}
customElements.define('my-widget',MyWidget);
</script>"#;

const APP_KANBAN: &str = r#"<h1>Kanban</h1>
<div id="board">
<div class="col" id="todo"><h3>To Do</h3><div class="card" draggable="true" id="c1">Card One</div><div class="card" draggable="true" id="c2">Card Two</div></div>
<div class="col" id="doing"><h3>Doing</h3><div class="card" draggable="true" id="c3">Card Three</div></div>
<div class="col" id="done"><h3>Done</h3></div>
</div>
<p id="state"></p>
<script>
var dragged=null;
document.querySelectorAll('.card').forEach(function(c){
  c.addEventListener('dragstart',function(e){dragged=c;});
});
document.querySelectorAll('.col').forEach(function(col){
  col.addEventListener('dragover',function(e){e.preventDefault();});
  col.addEventListener('drop',function(e){e.preventDefault();if(dragged){col.appendChild(dragged);report();}});
});
function report(){
  var s=[];
  document.querySelectorAll('.col').forEach(function(col){
    s.push(col.id+':'+col.querySelectorAll('.card').length);
  });
  document.getElementById('state').textContent=s.join(' ');
}
report();
</script>"#;

const APP_STORAGE: &str = r#"<h1>Storage</h1>
<button id="seed" onclick="localStorage.setItem('seeded','yes');sessionStorage.setItem('s','1');document.getElementById('out').textContent='seeded'">Seed storage</button>
<button id="clear" onclick="localStorage.clear();document.getElementById('out').textContent='cleared'">Clear</button>
<p id="out">ready</p>"#;

const APP_A11Y: &str = r#"<h1>Accessibility</h1>
<label for="a1">Email address</label>
<input id="a1" type="email" placeholder="you@example.com">
<button id="b1" aria-label="Submit form">Go</button>
<div role="alert" id="alert" class="hidden">Form error</div>
<button id="t1">First</button>
<button id="t2">Second</button>
<button id="t3">Third</button>"#;

const APP_WAITS: &str = r#"<h1>Waits</h1>
<button id="load" onclick="setTimeout(function(){document.getElementById('out').textContent='Loaded!'},400)">Load</button>
<button id="net" onclick="fetch('/slow?ms=400').then(function(r){return r.text();}).then(function(t){document.getElementById('out').textContent='Fetched: '+t})">Fetch slow</button>
<button id="flaky" onclick="var o=document.getElementById('out');o.textContent='Loading…';setTimeout(function(){o.textContent='Ready'},500)">Flaky</button>
<p id="out">idle</p>"#;
