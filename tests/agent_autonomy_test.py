#!/usr/bin/env python3
"""Autonomous agent capability test for agentyc MCP."""
import subprocess, json, time, sys, os

BINARY = os.path.join(os.path.dirname(__file__), '../target/release/agentyc')
if not os.path.exists(BINARY):
    BINARY = os.path.join(os.path.dirname(__file__), '../target/debug/agentyc')

p = subprocess.Popen(
    [BINARY, 'mcp'],
    env={**os.environ, 'AGENTYC_HEADLESS': '1'},
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
)

_id = [0]
def send(method, params={}):
    _id[0] += 1
    msg = json.dumps({'jsonrpc':'2.0','id':_id[0],'method':method,'params':params}) + '\n'
    p.stdin.write(msg.encode()); p.stdin.flush()
    return json.loads(p.stdout.readline())

def call(tool, args={}): return send('tools/call', {'name': tool, 'arguments': args})
def txt(r): return r.get('result', {}).get('content', [{}])[0].get('text', '')
def is_err(r): return r.get('result', {}).get('isError', False)
def nav(url): return call('browser_navigate', {'url': url})
def wait(s): call('browser_wait', {'seconds': s})
def state(): return json.loads(txt(call('browser_get_state', {'mode': 'full'})))
def eval_js(code): return json.loads(txt(call('browser_evaluate', {'code': code})))
def ref_by(els, **kwargs):
    for e in els:
        match = True
        for k, v in kwargs.items():
            # JSON uses 'type' for input type (not 'input_type')
            ek = 'type' if k == 'input_type' else k
            ev = e.get(ek, '')
            if k == 'placeholder':
                if v.lower() not in (ev or '').lower():
                    match = False; break
            else:
                if ev != v:
                    match = False; break
        if match:
            return e['ref']
    return None

send('initialize', {'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'t','version':'1'}})

results = {}

# ── 1. Error handling: stale ref becomes isError not JSON-RPC exception ──
nav('data:text/html,<button>B</button>')
wait(0.3)
s = state()
old_ref = s['interactive_elements'][0]['ref'] if s['interactive_elements'] else 'e99999'
nav('data:text/html,<p>different page</p>')
wait(0.2)
r = call('browser_click', {'ref': old_ref})
results['stale_ref_is_error_content'] = is_err(r)  # agent can read this
results['stale_ref_has_message'] = len(txt(r)) > 0

# ── 2. Error handling: bad URL is isError ──
r2 = nav('not-a-valid-url')
results['invalid_url_is_error'] = is_err(r2)
results['invalid_url_recoverable'] = len(txt(r2)) > 0

# ── 3. Navigate returns title for agent orientation ──
r3 = nav('data:text/html,<title>Product Dashboard</title><h1>Hello</h1>')
results['navigate_returns_title'] = 'Product Dashboard' in txt(r3)

# ── 4. Complete login form workflow ──
html = (
    "data:text/html,"
    "<form>"
    "<input id='u' type='text' placeholder='Username'>"
    "<input id='p' type='password' placeholder='Password'>"
    "<button type='button' onclick=\"document.title='logged_in'\">Submit</button>"
    "</form>"
)
nav(html)
wait(0.4)
s = state()
els = s['interactive_elements']
user_ref = ref_by(els, placeholder='username') or ref_by(els, input_type='text')
pass_ref = ref_by(els, input_type='password')
btn_ref  = ref_by(els, tag='button')
if user_ref and pass_ref and btn_ref:
    call('browser_type', {'ref': user_ref, 'text': 'admin'})
    call('browser_type', {'ref': pass_ref, 'text': 'secret123'})
    call('browser_click', {'ref': btn_ref})
    wait(0.2)
    results['login_form_flow'] = eval_js('document.title') == 'logged_in'
else:
    results['login_form_flow'] = f'no refs: u={user_ref} p={pass_ref} btn={btn_ref}'

# ── 5. Dynamic content loading + wait ──
html2 = (
    "data:text/html,"
    "<button onclick=\""
    "setTimeout(function(){"
    "document.getElementById('d').innerHTML='<p>item1</p><p>item2</p><p>item3</p>';"
    "},300)\">Load</button>"
    "<div id='d'></div>"
)
nav(html2)
wait(0.3)
s2 = state()
btn2 = ref_by(s2['interactive_elements'], tag='button')
if btn2:
    call('browser_click', {'ref': btn2})
    wt = call('browser_wait_for_element', {'text': 'item1', 'appear': True, 'timeout_seconds': 3})
    results['dynamic_content_load'] = not is_err(wt) and 'appeared' in txt(wt)
    items = json.loads(txt(call('browser_find_elements', {'selector': '#d p'})))
    results['dynamic_content_count'] = isinstance(items, list) and len(items) == 3
else:
    results['dynamic_content_load'] = False
    results['dynamic_content_count'] = False

# ── 6. Extract table data for agent decision-making ──
nav('data:text/html,<table>'
    '<tr><th>Product</th><th>Price</th><th>Stock</th></tr>'
    '<tr><td>Widget A</td><td>$10</td><td>5</td></tr>'
    '<tr><td>Widget B</td><td>$25</td><td>0</td></tr>'
    '<tr><td>Widget C</td><td>$15</td><td>12</td></tr>'
    '</table>')
wait(0.2)
ex = json.loads(txt(call('browser_extract_content', {'query': 'table rows'})))
rows = ex.get('data', [{}])[0].get('rows', []) if ex.get('data') else []
in_stock = [r for r in rows if r[2] != '0']
results['table_extraction'] = len(rows) == 3
results['agent_data_reasoning'] = len(in_stock) == 2  # agent can filter stock > 0

# ── 7. Search page for information ──
nav('data:text/html,'
    '<div style="height:4000px"></div>'
    '<section><h2 id="tos">Terms of Service</h2><p>Section 1: usage...</p></section>')
wait(0.2)
found = call('browser_search_page', {'pattern': 'Terms of Service'})
results['search_page'] = 'Terms of Service' in txt(found)
call('browser_scroll_to_text', {'text': 'Terms of Service'})
wait(0.4)
scroll_y = eval_js('window.scrollY')
results['scroll_to_target'] = isinstance(scroll_y, (int, float)) and scroll_y > 100

# ── 8. SPA hash-based routing ──
nav('data:text/html,<script>setTimeout(()=>{location.hash="#/users/42"},200)</script>')
r8 = call('browser_wait_for_url', {'url_substring': 'users/42', 'timeout_seconds': 3})
results['spa_hash_routing'] = not is_err(r8) and 'matched' in txt(r8).lower()

# ── 9. Multi-tab parallel browsing ──
nav('data:text/html,<title>Tab A</title>')
call('browser_new_tab', {'url': 'data:text/html,<title>Tab B</title>'})
wait(0.3)
tabs = json.loads(txt(call('browser_list_tabs', {})))
results['multi_tab_count'] = len(tabs) >= 2
if len(tabs) >= 2:
    call('browser_switch_tab', {'tab_id': tabs[0]['tab_id']})
    wait(0.1)
    results['tab_switch'] = eval_js('document.title') in ('Tab A', 'Tab B')

# ── 10. JavaScript evaluation for agent reasoning ──
nav('data:text/html,<ul><li class=p>Alpha</li><li class=p>Beta</li><li class=p>Gamma</li></ul>')
wait(0.2)
n = eval_js("document.querySelectorAll('.p').length")
results['js_eval_count'] = n == 3
texts = eval_js("Array.from(document.querySelectorAll('.p')).map(e=>e.textContent)")
results['js_eval_array'] = isinstance(texts, list) and 'Alpha' in texts

# ── 11. Since-hash polling for efficient state checks ──
nav('data:text/html,<button>Static</button>')
wait(0.2)
s11 = json.loads(txt(call('browser_get_state', {'mode': 'min'})))
h = s11['state_hash']
s11b = json.loads(txt(call('browser_get_state', {'mode': 'min', 'since_hash': h})))
results['since_hash_no_reread'] = s11b.get('changed') == False

# ── 12. DOM stability wait for AJAX pages ──
nav('data:text/html,<div id="d">loading...</div>'
    '<script>setTimeout(()=>{document.getElementById("d").textContent="done"},200)</script>')
dom_r = call('browser_wait_for_stable_dom', {'timeout_seconds': 3, 'quiet_ms': 300})
results['dom_stability_wait'] = not is_err(dom_r)

p.kill()

print('=== Autonomous Agent Capability Test ===\n')
passed = 0
failed = []
for k, v in results.items():
    ok = v is True or (isinstance(v, int) and v > 0)
    status = '✓' if ok else '✗'
    if ok: passed += 1
    else: failed.append(k)
    detail = f' ({v})' if v is not True and v is not False else ''
    print(f'  {status} {k}{detail}')

print(f'\n{passed}/{len(results)} tests passed')
if failed:
    print(f'Failed: {failed}')
    sys.exit(1)
