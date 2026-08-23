# Planning memo templates

Use the smallest template that fully resolves the work. Do not create ceremony that does not reduce ambiguity or release risk.

## Small plan

```markdown
# [Change]

- **Owner:** [named person]
- **Primary outcome:** [observable result]
- **Decision:** [what we will do]
- **Evidence:** [repository locations or source IDs]

## Build now
- [task]

## Explicitly not doing
- [item] — [reason]

## Alternatives considered
- [option] — [why rejected]

## Risks and edge cases
- [risk] — [mitigation or accepted rationale]

## Validation
- `[exact command]`
- [manual or browser check]

## Done when
- [acceptance criteria]
```

A Small plan may use a lightweight alternatives matrix instead of the full ADHD protocol unless the decision is genuinely open or risky. It must still state what is being built, what is not, why the choice was made, and how completion is verified.

## Decision record

```markdown
# Decision D-[N] — [question]

- **Owner:** [named person]
- **Status:** proposed / accepted / rejected / superseded
- **Date:** YYYY-MM-DD

## Context
[Problem, user need, constraints, and what happens if we do nothing.]

## Options
| Option | Fit | Evidence | Reversibility | Burden | Key risk |
|---|---:|---:|---:|---:|---|
| [A] | [ ] | [ ] | [ ] | [ ] | [ ] |
| [B] | [ ] | [ ] | [ ] | [ ] | [ ] |

## Decision
[Chosen option.]

## Rationale and evidence
- [reason] — [source ID or repository location]

## Alternatives rejected
- [option] — [specific failure or trade-off]

## Accepted trade-offs
- [cost]

## Revisit trigger
- [new evidence or constraint that would justify reopening]
```

## Open questions batch

When user-specific knowledge is required, do not drip questions one at a time. Create one document:

```markdown
# Open questions for [feature]

## Q-01 — [question]

- **Blocks:** [phase/decision]
- **Unknown:** [specific gap]
- **Why it matters:** [impact]
- **Research already completed:** [source IDs, Firecrawl MCP/Aside pages, local checks]
- **Options:** [A / B / C and trade-offs]
- **Recommendation:** [default if no preference]
- **Answer needed from:** [person]
```

Continue all non-blocked research and planning while waiting. Do not pretend the plan is final when a blocking answer is missing.
