# agentyc contributor notes

Agentyc is a lightweight MCP-first browser automation runtime for coding agents. The repository intentionally focuses on deterministic browser control over CDP and MCP stdio integration.

## Scope

- Keep: `agentyc/mcp`, `agentyc/browser`, `agentyc/dom`, `agentyc/tools`, `agentyc/filesystem`, `agentyc/llm`.
- Do not reintroduce autonomous agent loops, standalone interactive CLI flows, cloud skill wrappers, or sandbox execution surfaces.
- Prefer explicit browser actions and clear failure modes over fallback automation.

## Development

```bash
uv venv --python 3.11
source .venv/bin/activate
uv sync --dev
./scripts/lint.sh
./scripts/test.sh
```

## Code guidelines

- Use async Python and Pydantic v2 models.
- Keep MCP tool contracts deterministic and minimal.
- Reuse existing browser/session primitives instead of adding parallel abstractions.
- When changing browser behavior, preserve direct inspectability through state, HTML, screenshots, and explicit errors.

## Docs maintenance

- README.md is the primary public surface. Keep the comparison table (vs browser-use, Playwright MCP), tool inventory, and benchmark table in sync with the actual tool surface and release-gate thresholds.
- Overview + Features + API Reference under docs/ should stay accurate but can defer detail to README.
- When adding a new MCP tool: update README tool tables, docs/features.md, and docs/api.md in the same commit.
- When release-gate thresholds change: update README benchmarks table.

## Reddit Karma Strategy

### Goal
Build Reddit karma for account `u/jxsskalkat` to avoid auto-removal of future Agentyc promotional posts.

### Key Findings
- **New Reddit UI is unreliable for automated commenting** — the `shreddit-composer` web component's Comment button often clicks without submitting. The button is frequently hidden/off-screen.
- **Old Reddit UI (old.reddit.com) works reliably** — simple `<textarea>` + `save` button. Use this for all automated commenting.
- Always verify comment appeared on the page after submitting (search for the comment text).
- Target subreddits: r/AskReddit, r/CasualConversation, r/todayilearned, r/NoStupidQuestions, r/explainlikeimfive, r/aww.
- Strategy: sort by new/rising, comment early, be genuine/funny/sarcastic.

### Workflow for commenting
1. Navigate to `old.reddit.com/r/{subreddit}/comments/{post_id}/`
2. Find the `<textarea>` and `save` button elements
3. Type comment text into textarea
4. Click save button
5. Verify: search page for comment text to confirm it appears
