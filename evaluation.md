# agentyc MCP Evaluation

## Summary

This MCP is useful and practical, but it is not bulletproof. It works well for ordinary browser tasks, form filling, scrolling, searching, and state inspection. It is less dependable on messy real-world flows like Microsoft login, passkeys, and some heavy dynamic sites.

## What Works Well

- Loads and controls normal pages reliably enough.
- Typing into inputs is solid.
- Clicking, hovering, scrolling, and back navigation generally work.
- `get_state` is useful and usually accurate for visible DOM state.
- `min` mode is reasonably token-efficient.
- It handled a heavy Amazon homepage without crashing.
- It handled YouTube playback pages and player controls.
- It can expose useful page structure, refs, and values.

## What I Like

- The tool gives stable refs and enough state to operate without guessing.
- The browser feels usable for a human-like workflow.
- `since_hash` makes repeated inspection cheaper.
- It is good at showing what is actually on the page instead of hallucinating.
- It is flexible enough for both inspection and interaction.

## What I Don’t Like

- Rerenders can stale refs quickly.
- Some navigation operations are flaky, especially around new tabs.
- Network-idle waits can time out on heavy sites.
- Microsoft auth redirected into a passkey/FIDO flow and blocked the password path.
- `extract_content` was brittle when the query was too vague.
- Search-driven actions on Amazon were not as clean as I’d want.

## Reliability

Overall reliability is okay for controlled browsing and form workflows, but only moderate for dynamic, auth-heavy, or multi-step commercial sites.

Observed issues:

- Stale refs after page updates.
- New-tab requests were not always honored cleanly.
- Some pages kept network activity going long enough to make idle waits less useful.
- Passkey-based login flows are a hard stop without external auth support.

## Speed

It is fairly fast for page inspection and simple interactions. It slows down when pages are heavy, dynamic, or loaded with background activity. That is normal, but it means you need to be selective about waits and state reads.

## Token Efficiency

Good when used carefully.

Best practices:

- Use `mode: min` unless you need full state.
- Use `since_hash` for unchanged-page checks.
- Avoid repeated screenshots unless necessary.
- Prefer focused state reads over broad dumps.

If you keep requesting full state and screenshots, it becomes expensive quickly.

## Accuracy

The browser is mostly accurate when describing visible DOM state, selected values, and page structure. It is less reliable when a site is in a transitional state or when the UI depends on async rerenders, auth flows, or hidden browser-specific behavior.

## Real-World Tests

I tested it on:

- Outlook sign-in
- Selenium demo form
- Amazon homepage and search
- YouTube browsing/search and player controls

Results:

- Selenium form: strong
- Outlook sign-in: blocked by passkey flow
- Amazon: usable, but search/navigation felt less predictable
- YouTube: generally workable, but heavy and noisy

## Final Verdict

Good browser MCP, not elite.

It is useful for everyday assisted automation, inspection, and simple workflows. I would not trust it blindly for long unattended flows, login-heavy tasks, or sites that rely on passkeys, complex SPA transitions, or lots of background network churn.

If I had to rate it:

- Speed: 7/10
- Reliability: 5.5/10
- Token efficiency: 7/10 with disciplined use
- Accuracy: 7.5/10 on visible state

Overall: solid for practical use, but not something I’d call highly robust.
