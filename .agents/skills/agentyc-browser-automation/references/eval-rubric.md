# Eval Rubric — Agentyc Browser Automation

## Pass when the skill:

- loads for requests about using Agentyc's browser MCP tools, browser workflow planning, shared-browser coordination, extraction, auth persistence, debugging, or tool choice
- does not load for Agentyc internals work that belongs to `cdp-browser-engineer`, `pytest-async-engineer`, or non-browser tasks
- explains the **smallest correct tool** to use before suggesting `browser_evaluate(...)` or screenshots
- teaches the `read -> ref -> act -> verify` loop clearly
- distinguishes same browser **process/profile reuse** from unsafe co-ownership of one live tab
- routes iframe, storage, network, and debugging questions to the dedicated browser tools instead of generic heuristics
- gives concrete examples that match the actual Agentyc tool names and arguments

## Fail when the skill:

- tells agents to guess with screenshots or long sleeps before cheaper deterministic tools
- implies multiple agents can safely co-own one tab
- omits major tool families such as frames, storage, network inspection, or session persistence
- suggests raw JavaScript for tasks already covered by dedicated browser tools
- uses nonexistent tool names or wrong argument shapes
