# Bounded divergent decision exploration

This reference defines how evidence-saturated planning uses the ADHD pattern without turning exploration into an unbounded information dump. It is a decision-space tool, not a replacement for research, architecture, or user judgment.

## Purpose

Use isolated parallel generators to surface materially different ways to solve a meaningful open decision before research and commitment narrow the space. Then switch to a separate critic posture, verify the surviving mechanisms with sources, and close the decision with one recommendation and an explicit stop rationale.

The intended sequence is:

```text
reframe anchors → parallel divergence → score and cluster
→ verify candidate mechanisms → close the decision → archive the rest
```

A divergent idea is a hypothesis, not a fact. It must not enter architecture as if it were verified.

## When to run it

Run this protocol for a Feature or Initiative decision when at least two of these are true:

- Two or more technically viable approaches exist.
- Choosing poorly would create meaningful rework, lock-in, security exposure, operational burden, or user harm.
- The user is worried that a better option may be missed.
- The decision crosses modules, providers, data boundaries, permissions, or rollout strategy.
- The problem is open-ended and no canonical answer is sufficient.

Skip it for:

- Tweak work or obvious one-file fixes.
- Lookup questions or versioned API facts that can be verified directly.
- A known-root-cause bug with one correct repair.
- A user who explicitly asks for a quick, standard, canonical, or single recommendation.
- Decisions already closed by a verified project constraint or prior decision record.

## Isolation invariant

The generator branches must be separate Agent/Task calls launched in parallel. Each branch receives only:

- The decision statement.
- Real constraints and user-provided context.
- One frame prompt.
- The generator-only instruction below.

A branch must not see another branch's output during divergence. Sequentially writing several viewpoints in one context is not equivalent: the first answer becomes an anchor for every later answer. If the host cannot provide isolated parallel sub-agents, use the reduced-mode procedure below and label the result as reduced confidence; do not claim that full divergent exploration ran.

## Step 0: Frame the decision

Write a decision card before spawning branches:

```yaml
decision_id: D-01
question: "Which approach should we choose for [capability]?"
job_to_be_done: "The user/system must [outcome]."
immutable_constraints:
  - "[constraint that cannot be violated]"
working_constraints:
  - "[constraint still needing verification]"
known_context: "[relevant repository and user context]"
why_now: "[cost of deferring or choosing poorly]"
exit_condition: "[what a closed decision must establish]"
```

Strip incidental anchors such as the current framework, existing provider, or current file layout from the divergent wording unless they are genuine constraints. Preserve legal, budgetary, physical, protocol, compatibility, and user-declared constraints. Keep the original question for later fit and evidence checks.

## Step 1: Choose frames

Use 3–5 frames for a contained decision and 5–7 for a high-impact Initiative. Prefer structurally different postures, not a list of near-identical experts. A useful default set is:

| Frame | What it forces the branch to ask |
|---|---|
| Simplest viable / $0 budget | What is the smallest reversible mechanism that preserves the load-bearing outcome? |
| Adversary / failure hunter | How could the obvious design be abused, fail, or create an unsafe boundary? |
| Regulator / auditor | What must be provable, traceable, reviewable, or explicitly refused? |
| On-call at 3 a.m. | Which design minimizes pages, ambiguity, recovery time, and support burden? |
| Remove the anchor | What changes if the current framework, database, provider, or request model is not treated as fixed? |
| User or operator | What would make the real workflow faster, clearer, and less error-prone? |
| Cross-domain transplant | What mechanism from logistics, biology, game design, markets, or hardware transfers usefully? |
| Future maintainer | What remains understandable and changeable after the original team leaves? |

Always include at least one frame that challenges the current architecture and one that tests operational or user consequences. Mark deliberately wild ideas as wild; do not present them as equally likely recommendations.

## Generator instruction

Give each branch the same instruction, with only the frame changed:

```text
You are in DIVERGENT mode. You are a generator, not a critic.
Generate 4–6 short, materially distinct approaches under this frame.
The first three textbook answers are already known and are not enough.
Push into the non-obvious middle, but preserve the stated constraints.
Do not rank, reject, hedge, or write an implementation plan.
Each idea must be one phrase or one sentence and may include a short rationale.
Output JSON only:
[{"text":"...","rationale":"..."}]
```

Do not ask a branch to research, browse, or prove its ideas. Research belongs after the branches return so generation is not prematurely constrained by the first source found.

## Step 2: Critic pass

After every branch returns, run a separate critic call. The critic receives the original decision, all ideas, and the constraints; it does not receive a branch's private generation context. Score each idea:

- **Fit (0–10):** addresses the actual job and immutable constraints.
- **Viability (0–10):** could be implemented and operated in this repository.
- **Evidence potential (0–10):** can the load-bearing claims be verified from authoritative sources or a cheap local test?
- **Reversibility (0–10):** how safely can we change course if wrong?
- **Operational burden (0–10):** inverse score; lower burden is better.
- **Novelty (0–10):** meaningfully different from the default approach.

Do not let novelty outrank correctness. A useful default ranking is:

```text
0.30 fit + 0.25 viability + 0.20 evidence potential
+ 0.15 reversibility + 0.10 novelty
- operational-burden penalty
```

Adjust weights only when the decision record explains why. A numeric score is a sorting aid, not a claim of scientific precision.

For any seductive candidate, record a **trap** with the specific mechanism that breaks it: unsupported assumption, hidden cost, unbounded complexity, provider limitation, security failure, migration hazard, or user workflow mismatch. Also record one concrete strength for every shortlisted or rejected idea so the record is balanced rather than dismissive.

Cluster ideas by underlying angle, for example:

- remove-the-provider plays
- durable-log and replay plays
- push-work-to-client plays
- reversible rollout plays
- operational-simplicity plays
- measure-before-migrate plays

## Step 3: Verify survivors

Select 2–4 candidates, including at least one non-obvious viable candidate when one exists. For each survivor create a verification queue:

```yaml
candidate_id: C-01
idea: "[short description]"
load_bearing_claims:
  - claim: "[claim that must be true]"
    source_class: "official-docs | repository | standard | implementation | user-evidence"
    cheapest_test: "[exact research or local validation step]"
    falsifier: "[what would reject it]"
status: unverified
```

Run the research-saturation protocol before treating a candidate as viable. Verify versioned provider behavior, limits, errors, licensing, operational prerequisites, security boundaries, and migration constraints. If a candidate cannot be verified, downgrade it or keep it as a clearly labeled speculative alternative.

## Step 4: Deepen only after verification starts

For each surviving candidate, produce:

1. A 4–8 sentence implementation sketch.
2. The load-bearing assumption and how it could fail.
3. The first concrete repository or benchmark step.
4. Three to five variations, hybrids, or future extensions.
5. The evidence IDs that support the sketch.
6. A recommendation condition: when this candidate would win and when it would not.

Do not deepen every idea. Deepening every leaf recreates the user's overload and spends the research budget where it cannot affect the decision.

## Decision closure: the relief layer

After verification, close each decision in a short, front-loaded section of `research/decision-closure.md`:

```markdown
## D-01 — [question]

**Recommendation:** [one option]

**Why this wins now:** [two or three sentences tied to constraints and evidence IDs]

**Trade-offs accepted:**
- [cost deliberately accepted]

**Alternatives rejected:**
- [option] — [specific reason and evidence]

**Traps avoided:**
- [seductive option] — [failure mechanism]

**Confidence:** high / medium / low — [why]

**What would change the decision:**
- [observable condition, new requirement, or source finding]

**Stop rationale:** We have covered the applicable source classes, verified the
load-bearing claims, tested the meaningful alternatives, and completed two
no-new-decision-fact discovery passes. Remaining uncertainty is [bounded gap]
and does not justify delaying the plan because [reason].
```

This section is the explicit permission to stop. The archive may retain the full wide set, scores, clusters, source ledger, and rejected candidates, but the main plan should not keep reopening them without new evidence.

## Reduced mode when sub-agents are unavailable

Do not fake isolation. Record:

```yaml
exploration_mode: reduced
reason: "No isolated parallel Agent/Task surface was available."
impact: "Candidate diversity and anti-anchoring confidence are lower."
mitigation: "Use the frame matrix sequentially, run a stronger adversarial critic,
and keep the decision provisional until source verification and a local test pass."
```

## Anti-patterns

- Treating every strange idea as a serious candidate.
- Allowing the critic to edit or suppress ideas during divergence.
- Using unsupported LLM-generated ideas as architecture facts.
- Deepening before scoring and verifying.
- Presenting 30 ideas without a recommendation.
- Reopening a closed decision because a merely possible alternative exists.
- Calling exploration exhaustive without a declared saturation rule.
