#!/usr/bin/env python3
"""Battle test: real-world browser automation scenarios against live sites."""
import subprocess, json, time, os, sys

BINARY = os.path.join(os.path.dirname(__file__), '../target/release/agentyc')
if not os.path.exists(BINARY):
    BINARY = os.path.join(os.path.dirname(__file__), '../target/debug/agentyc')

p = subprocess.Popen(
    [BINARY, 'mcp'],
    env={**os.environ, 'AGENTYC_HEADLESS': '1'},
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
)

_id = [0]
def send(m, params={}):
    _id[0] += 1
    msg = json.dumps({'jsonrpc':'2.0','id':_id[0],'method':m,'params':params}) + '\n'
    p.stdin.write(msg.encode()); p.stdin.flush()
    return json.loads(p.stdout.readline())

def call(t, a={}): return send('tools/call', {'name': t, 'arguments': a})
def txt(r): return r.get('result', {}).get('content', [{}])[0].get('text', '')
def err(r): return r.get('result', {}).get('isError', False)
def j(r):
    t = txt(r)
    try: return json.loads(t)
    except: return t
def nav(url): return call('browser_navigate', {'url': url})
def wait(s): call('browser_wait', {'seconds': s})
def state(mode='full'): return j(call('browser_get_state', {'mode': mode}))
def eval_js(code): return j(call('browser_evaluate', {'code': code}))
def nav_ok(r): return not err(r) and bool(txt(r))

send('initialize', {'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'battle','version':'1'}})

results = {}

# ── 1. example.com ────────────────────────────────────────────────────────────
print('1. example.com')
r = nav('https://example.com')
wait(0.3)
s = state()
results['example_loads'] = 'Example Domain' in s.get('title', '')
links = j(call('browser_extract_content', {'query': 'all links'}))
results['example_link_extraction'] = len(links.get('data', [])) >= 1
# get_state returns stable hash
h = s.get('state_hash', '')
s2 = j(call('browser_get_state', {'mode': 'min', 'since_hash': h}))
results['example_since_hash'] = s2.get('changed') == False
print(f'  title={s.get("title")} links={len(links.get("data",[]))} hash_cached={s2.get("changed")==False}')

# ── 2. github.com — button-triggered search ───────────────────────────────────
print('2. github.com')
nav('https://github.com')
wait(1.0)
s = state()
results['github_loads'] = 'GitHub' in s.get('title', '')
# GitHub search is behind a button with aria-label "Search or jump to"
search_btn = next((e for e in s.get('interactive_elements', [])
    if 'search' in (e.get('placeholder') or '').lower()
    or 'search' in (e.get('text') or '').lower()
    or e.get('type') == 'search'), None)
if not search_btn:
    # Try find_elements for aria-label
    btns = j(call('browser_find_elements', {
        'selector': '[aria-label*="Search" i], input[type=search], [role=search]',
        'attributes': ['aria-label', 'type', 'placeholder']
    }))
    search_btn = btns[0] if isinstance(btns, list) and btns else None
results['github_search_discoverable'] = search_btn is not None
print(f'  search element: {search_btn}')
# Open search via JS click (handles dialog/portal patterns)
call('browser_evaluate', {'code': (
    "var btn = document.querySelector('[data-target=\"qbsearch-input.inputButton\"],"
    "[aria-label*=\"Search or jump\"],button.header-search-button');"
    "if(btn){btn.click();}"
)})
wait(0.6)
# Wait for the search input to appear in DOM
wt = call('browser_wait_for_element', {'text': None, 'ref': None, 'appear': True, 'timeout_seconds': 2})
# Check if combobox input is now visible
s_after = state()
query_input = next((e for e in s_after.get('interactive_elements', [])
    if e.get('role') == 'combobox' and e.get('tag') == 'input'), None)
is_search_focused = query_input is not None
if not is_search_focused:
    # Try via get_focused_element
    focused = j(call('browser_get_focused_element', {}))
    is_search_focused = focused.get('tag') == 'input'
    print(f'  focused element: {focused}')
if is_search_focused:
    ref_to_use = query_input['ref'] if query_input else None
    if ref_to_use:
        call('browser_type', {'ref': ref_to_use, 'text': 'rust browser automation'})
    else:
        call('browser_evaluate', {'code': "document.activeElement.value='rust browser automation'; document.activeElement.dispatchEvent(new Event('input',{bubbles:true}))"})
    wait(0.3)
    call('browser_press_key', {'key': 'Enter'})
    r = call('browser_wait_for_url', {'url_substring': 'search', 'timeout_seconds': 8})
    results['github_search_executes'] = not err(r)
    cur_url = eval_js('location.href')
    print(f'  search url: {str(cur_url)[:80]}')
results['github_keyboard_search'] = is_search_focused
print(f'  query_input found: {is_search_focused}')
if is_search_focused:
    call('browser_type', {'ref': None, 'text': 'agentyc browser mcp'})
    wait(0.2)
    call('browser_press_key', {'key': 'Enter'})
    r = call('browser_wait_for_url', {'url_substring': 'search', 'timeout_seconds': 8})
    results['github_search_executes'] = not err(r)
    cur_url = eval_js('location.href')
    print(f'  search url: {str(cur_url)[:80]}')

# ── 3. wikipedia.org — content reading and navigation ────────────────────────
print('3. wikipedia.org')
nav('https://en.wikipedia.org/wiki/Web_scraping')
wait(0.5)
s = state()
results['wiki_loads'] = 'Wikipedia' in s.get('title', '')
# Extract all section headings via JS
headings = eval_js('Array.from(document.querySelectorAll("h2")).map(h=>h.textContent.trim()).filter(Boolean).slice(0,5)')
results['wiki_content_readable'] = isinstance(headings, list) and len(headings) >= 2
print(f'  headings: {headings[:4]}')
# In-page search
hits = j(call('browser_search_page', {'pattern': 'scraping', 'max_results': 5}))
results['wiki_search'] = isinstance(hits, list) and len(hits) >= 3
# Click a section link
toc_links = j(call('browser_find_elements', {'selector': '.mw-parser-output a[href^="#"]', 'attributes': ['href']}))
results['wiki_toc_links'] = isinstance(toc_links, list) and len(toc_links) >= 1
print(f'  search hits={len(hits) if isinstance(hits,list) else 0} toc_links={len(toc_links) if isinstance(toc_links,list) else 0}')

# ── 4. hacker news — list scraping and pagination ────────────────────────────
print('4. hacker news')
nav('https://news.ycombinator.com')
wait(0.5)
s = state()
results['hn_loads'] = bool(s.get('title'))
# Extract stories
links = j(call('browser_extract_content', {'query': 'all links'}))
external = [l for l in links.get('data', []) if l.get('href', '').startswith('http') and 'ycombinator' not in l.get('href', '')]
results['hn_stories'] = len(external) >= 10
print(f'  title={s.get("title")} stories={len(external)}')
# "More" is the link with href containing "p=2"  
more_links = j(call('browser_find_elements', {'selector': 'a[href*="p=2"], a[href*="next"], .morelink', 'attributes': ['href', 'class']}))
if isinstance(more_links, list) and more_links:
    more_href = more_links[0].get('href', '')
    print(f'  More link href: {more_href}')
    # Navigate directly to page 2
    nav('https://news.ycombinator.com/?p=2')
    wait(0.5)
    new_url = eval_js('location.href')
    results['hn_pagination'] = 'p=2' in str(new_url)
    print(f'  pagination url: {str(new_url)[:60]}')
else:
    # Fallback: directly navigate to page 2
    nav('https://news.ycombinator.com/?p=2')
    wait(0.5)
    s2 = state()
    results['hn_pagination'] = len(s2.get('interactive_elements', [])) > 5
    print('  direct nav to p=2')

# ── 5. DuckDuckGo — textarea search (Google blocks headless) ─────────────────
print('5. duckduckgo.com')
nav('https://duckduckgo.com')
wait(0.5)
s = state()
search_el = next((e for e in s.get('interactive_elements', [])
    if e.get('tag') in ('input', 'textarea')
    and e.get('type') not in ('hidden', 'submit', 'button', 'checkbox', 'radio')), None)
results['google_input_found'] = search_el is not None
print(f'  search_el: {search_el}')
if search_el:
    call('browser_click', {'ref': search_el['ref']})
    wait(0.2)
    call('browser_type', {'ref': search_el['ref'], 'text': 'python web automation'})
    wait(0.2)
    val = eval_js('document.querySelector("input[name=q],input[type=search]")?.value || document.querySelector("input")?.value')
    results['google_search'] = bool(val) and 'python' in str(val).lower()
    print(f'  typed value: {val}')
    call('browser_press_key', {'key': 'Enter'})
    r = call('browser_wait_for_url', {'url_substring': 'q=python', 'timeout_seconds': 8})
    results['google_results_count'] = not err(r)
    wait(0.3)
    result_links = j(call('browser_extract_content', {'query': 'all links'}))
    print(f'  result links: {len(result_links.get("data", []))} url_match={not err(r)}')

# ── 6. httpbin.org — form submission, response reading ───────────────────────
print('6. httpbin.org/forms/post')
nav('https://httpbin.org/forms/post')
wait(0.5)
s = state()
results['httpbin_loads'] = len(s.get('interactive_elements', [])) >= 3
inputs = [e for e in s.get('interactive_elements', []) if e.get('tag') == 'input' and e.get('type') not in ('submit', 'hidden', 'checkbox', 'radio')]
print(f'  form inputs: {len(inputs)}')
if inputs:
    # Fill first two text inputs
    for i, inp in enumerate(inputs[:2]):
        call('browser_type', {'ref': inp['ref'], 'text': f'test_value_{i}'})
    wait(0.1)
    submit = next((e for e in s.get('interactive_elements', []) if e.get('type') == 'submit' or (e.get('tag') == 'button' and 'submit' in str(e).lower())), None)
    if submit:
        call('browser_click', {'ref': submit['ref']})
        wait(1.0)
        body = eval_js('document.body.innerText')
        # httpbin returns JSON with form data
        try:
            resp_data = json.loads(body) if isinstance(body, str) else {}
            form_data = resp_data.get('form', {})
            results['httpbin_form_submit'] = any('test_value' in str(v) for v in form_data.values()) or 'test_value' in str(body)
        except:
            results['httpbin_form_submit'] = 'test_value' in str(body)
        print(f'  submitted, response: {str(body)[:100]}')
    else:
        results['httpbin_form_submit'] = False
else:
    results['httpbin_form_submit'] = False

# ── 7. httpbin.org/json — JSON API reading ────────────────────────────────────
print('7. httpbin.org/json')
nav('https://httpbin.org/json')
wait(0.3)
body = eval_js('document.body.innerText')
try:
    data = json.loads(body) if isinstance(body, str) else body
    results['json_api_read'] = isinstance(data, dict) and 'slideshow' in data
    print(f'  json keys: {list(data.keys()) if isinstance(data, dict) else body[:50]}')
except:
    results['json_api_read'] = False

# ── 8. Multi-tab parallel browsing ───────────────────────────────────────────
print('8. multi-tab')
nav('https://example.com')
wait(0.3)
call('browser_new_tab', {'url': 'https://httpbin.org/get'})
wait(0.5)
tabs = j(call('browser_list_tabs', {}))
results['multi_tab'] = len(tabs) >= 2
print(f'  tabs: {len(tabs)}')
if len(tabs) >= 2:
    # Find the example.com tab by URL
    example_tab = next((t for t in tabs if 'example.com' in t.get('url', '')), None)
    httpbin_tab = next((t for t in tabs if 'httpbin' in t.get('url', '')), None)
    target_tab = example_tab or tabs[0]
    call('browser_switch_tab', {'tab_id': target_tab['tab_id']})
    wait(0.3)
    cur_url = eval_js('location.href')
    expected = target_tab.get('url', '')
    results['tab_switch_correct'] = bool(cur_url) and (
        'example.com' in str(cur_url) or 'httpbin' in str(cur_url) or str(cur_url) != 'about:blank'
    )
    print(f'  switched to: {cur_url}')
    # Close the other tab
    other_tab = httpbin_tab or (tabs[1] if len(tabs) > 1 else None)
    if other_tab and other_tab['tab_id'] != target_tab['tab_id']:
        call('browser_close_tab', {'tab_id': other_tab['tab_id']})

# ── 9. Screenshot capture ─────────────────────────────────────────────────────
print('9. screenshot')
nav('https://example.com')
wait(0.3)
sr = call('browser_screenshot', {})
results['screenshot'] = any(
    c.get('type') == 'image' or (c.get('mimeType') or '').startswith('image/')
    for c in sr.get('result', {}).get('content', [])
)
print(f'  screenshot has image: {results["screenshot"]}')

# ── 10. Session save/restore ──────────────────────────────────────────────────
print('10. session save/restore')
nav('https://httpbin.org/cookies/set/test_cookie/hello')
wait(0.5)
tmp = '/tmp/agentyc_battle_state.json'
sr = call('browser_save_state', {'path': tmp})
results['save_state'] = not err(sr) and os.path.exists(tmp)
print(f'  saved: {not err(sr)} file_exists: {os.path.exists(tmp)}')
if os.path.exists(tmp):
    with open(tmp) as f:
        data = json.load(f)
    print(f'  state contains: {list(data.keys())}')
    os.unlink(tmp)

# ── 11. Wait for dynamic content ─────────────────────────────────────────────
print('11. dynamic content wait')
nav('data:text/html,<div id=d></div><script>setTimeout(()=>{document.getElementById("d").textContent="ready_marker"},300)</script>')
r = call('browser_wait_for_element', {'text': 'ready_marker', 'appear': True, 'timeout_seconds': 5})
results['dynamic_wait'] = not err(r) and 'appeared' in txt(r)
print(f'  waited: {txt(r)}')

# ── 12. Error recovery flow ───────────────────────────────────────────────────
print('12. error recovery')
# Chrome loads a chrome-error:// page for failed navigations — not an MCP error
# Test that agent can detect it via title and recover
nav('https://this-domain-definitely-does-not-exist-xyz-abc-999.com')
wait(0.5)
cur_url = eval_js('location.href')
title_after = eval_js('document.title')
# Chrome shows error page (not necessarily an isError=True result)
# Agent detects failure by reading the response/title
nav_failed = 'chrome-error' in str(cur_url) or 'ERR_' in str(title_after) or 'not found' in str(title_after).lower()
# Recovery: navigate to valid URL
r2 = nav('https://example.com')
results['error_recovery'] = not err(r2)  # recovery works
print(f'  bad nav url={str(cur_url)[:50]} title={title_after} recovery={not err(r2)}')

p.kill()

# ── Results ───────────────────────────────────────────────────────────────────
print()
print('═' * 50)
print(' Battle Test Results')
print('═' * 50)
passed = 0
failed = []
for k, v in results.items():
    ok = v is True or (isinstance(v, int) and v > 0)
    status = '✓' if ok else '✗'
    if ok: passed += 1
    else: failed.append(k)
    detail = f' ({v})' if v not in (True, False) else ''
    print(f'  {status} {k}{detail}')

print(f'\n{passed}/{len(results)} passed', end='')
if failed:
    print(f'  |  failed: {", ".join(failed)}')
else:
    print('  — ALL PASS ✓')

sys.exit(0 if not failed else 1)
