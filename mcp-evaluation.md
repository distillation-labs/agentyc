# Traverse MCP Evaluation

## Summary

The MCP browser runtime is now much closer to the target: it handled multi-tab browser control, static pages, Google, DuckDuckGo, and Wikipedia correctly, and the compact-state path is solid. The main blocker I still saw was content extraction, which continued to fail with an API-key error.

## What is working

- Browser session startup and tab management
- Navigation to Gmail and other websites
- `browser_get_state` in `min` / compact mode
- `since_hash` optimization for unchanged state
- `browser_list_tabs`
- `browser_switch_tab`
- `browser_click` on stable, fresh refs
- `browser_type` into Gmail search
- Google search flow
- DuckDuckGo landing-page controls
- Wikipedia search flow
- Harmless navigation across multiple tabs
- Clicking a link on a static page (`example.com` -> IANA) worked correctly

## What is not working

- `browser_extract_content` failed with a 401 invalid API key error
- `browser_click` fails on stale refs unless state is refreshed first
- A disabled Gmail search button could not be clicked until the search input had content

## Issues observed

1. **Extraction failure**
   - `browser_extract_content` returned an OpenAI API-key error instead of page content.
   - This blocks content extraction workflows entirely in the current setup.

2. **Dynamic-page coverage improved, but not universal**
   - Google, DuckDuckGo, and Wikipedia now surfaced real interactive controls and search flows worked.
   - That said, these sites still expose large interaction trees, so compaction matters.

3. **Ref freshness is strict**
   - Reusing a stale element ref correctly fails, which is safe, but requires a fresh state read before retrying.

4. **Some controls are intentionally disabled**
   - Gmail search could not be triggered until text was entered.
   - This is correct behavior, but it means automation must respect UI state rather than assume actionability.

## Accuracy

- High accuracy on static pages and on the public dynamic sites I retested
- Correctly tracked URL changes, active tabs, and page titles
- Correctly handled link activation, search typing, and search submission
- Correctly reported unchanged state when `since_hash` matched

## Context efficiency

- `mode=min` was compact and useful for inspection
- `since_hash` was the best efficiency win; it returned `changed=false` and no element payload when nothing changed
- `get_state` still surfaced enough context to operate safely, even on large pages
- Screenshots are available, but they are heavier than state reads and should be used only when needed
- The compact state is good, but Google/Wikipedia can still generate large interaction sets when the page is busy

## Token efficiency

- Good when using compact state reads
- Good when the page is unchanged and `since_hash` can short-circuit
- Less efficient when interactive-element counts are large, even in compact mode
- Poor when extraction fails and forces retries without usable output

## Speed

- Navigation and state reads felt responsive
- Tab switching was immediate
- Search flows on Google and Wikipedia were quick enough to feel usable
- I did not benchmark wall-clock latency, so this is a qualitative read rather than a measured one

## Overall assessment

The MCP is now accurate and usable for real browser automation on the sites I tested, and the state protocol is efficient enough to be practical. It is still not “perfect” because extraction is broken, but the control plane itself looks close to the goal: fast, context-aware, and mostly token-efficient.
