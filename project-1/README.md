# Staple — iMessage Grocery Bot for Canadian Students

> **Status:** Phase 0 (Discovery) — Active
> **Plan owner:** [TBD]
> **Launch owner:** [TBD]

---

## One-line summary

A grocery assistant that lives inside iMessage. Canadian students text it like a friend — "what's cheap this week at No Frills?" — and it replies with real grocery prices from Canadian stores, optimized meal plans, and auto-generated shopping lists. No dashboard, no login, no app install.

Built on **Convex + Next.js + Azure (GPT-4o)**. Hosted on Azure with $100K in credits.

## Why now (Canada)

- **67% of Canadians** say cost of living is the worst it has ever been (Abacus Data, Dec 2025)
- **40% of Canadian students skip meals** to save money (CASA 2025)
- **92% of Canadian students are stressed about money** (TD 2025)
- Canadian grocery prices rose **70%+ in 2 years**; a family of 4's monthly food bill rose **$128 in a single year** (Zoocasa, Jan 2026)
- **25% of Canadian students are considering dropping out** due to costs (Embark, 2025)
- Canadian stores uniquely concentrated (Loblaws, No Frills, Walmart, FreshCo, Superstore) — easier to cover with one API
- Tsenta (YC, Canadian company) proved the iMessage-bot model works at $19-99/mo

## What we're building

A **Convex** backend + **Next.js** frontend + **Azure GPT-4o** AI brain + **Apify** grocery pricing engine. The bot lives on a Mac Mini running **imessage-kit** (open-source iMessage SDK). Messages flow: iPhone → Mac Mini → Convex → GPT-4o → Apify → response back to iPhone. All Canadian stores, all Canadian students.

## Primary persona

A Canadian university student (18-24) who:
- Is financially stressed about food costs
- Shops at No Frills, Walmart, FreshCo, or Superstore
- Already uses iMessage for 90% of daily communication
- Has no patience for another app, dashboard, or login
- Cooks in a shared kitchen with limited time and equipment
- Wants to spend $40-60/week on groceries but doesn't know how

## Stores covered (Phase 1)

No Frills | Walmart Canada | FreshCo | Loblaws | Real Canadian Superstore

All covered by the Apify Canadian Grocery API. More added later.

## Scope boundaries

| In scope (Phase 1-2) | Out of scope (deferred) |
|---|---|
| iMessage-only bot (blue bubbles) | WhatsApp integration (Phase 3) |
| Canadian grocery price lookups (5 chains) | US grocery stores |
| Meal plan generation under budget | Auto-ordering / Instacart |
| Shopping list generation | Physical hardware |
| User preferences (dietary, allergies, budget) | Dashboard web app |
| Price comparison across stores | Nutritional tracking (Phase 3) |
| Stripe subscriptions ($3/mo CAD) | Roommate bill splitting (Phase 4) |
| Price drop alerts | Bulk-buy pooling (Phase 4) |

## Success metrics

| Metric | Target | How measured |
|--------|--------|-------------|
| Users who text bot 3+ times in week 1 | >60% | Convex message logs |
| DAU / MAU ratio | >40% | Convex analytics |
| User-reported grocery savings | >20% | In-bot survey at week 2 |
| Paid conversion (free → $3/mo) | >8% | Stripe subscription records |
| Cost per user per month | <$1.50 CAD | Azure billing + Convex stats |

## Risks (Canadian-specific)

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Loblaws/No Frills price data quality from Apify | Medium — inaccurate prices | Cross-validate with weekly flyers |
| Canadian dollar fluctuation on Azure credits | Low — $100K CAD is locked | Credits are USD-denominated |

## Where to find everything

| File | What it contains |
|------|-----------------|
| `README.md` (this file) | Living feature brief — start here |
| `research/findings-1-competitive-landscape.md` | Competitor analysis, benchmark data |
| `research/findings-2-technical-approaches.md` | iMessage approaches + Convex vs Railway analysis |
| `plans/phase-0-discovery.md` | Problem framing, persona, scope | **ACTIVE** |
| `plans/phase-1-architecture.md` | System architecture (Convex + Next.js + Azure) |
| `plans/phase-2-implementation.md` | Work packages, build execution |
| `plans/phase-3-hardening.md` | Edge cases, security, runbook |
| `plans/phase-4-validation-and-rollout.md` | Launch checklist, rollout |

---

## Release posture

**Phase 1-2:** Invite-only. Canadian .edu emails.
**Phase 3:** Public beta — any Canadian student.
**Phase 4:** General availability — $3/mo CAD.

## Architecture at a glance

```
iPhone (iMessage)
    │ blue bubble
    ▼
Mac Mini (imessage-kit SDK)
    │ Convex HTTP Action
    ▼
Convex Cloud (all backend)
├── Database (built-in)
├── Actions: GPT-4o via Azure
├── Actions: Apify Canadian Grocery API
├── Scheduled jobs: price refresh every 2h
└── HTTP: Stripe webhook, bridge health
    │
    ▼
Next.js on Vercel (thin frontend)
    └── Landing, pricing, Stripe checkout
```
