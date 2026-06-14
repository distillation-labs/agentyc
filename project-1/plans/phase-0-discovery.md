# Phase 0: Discovery — Problem Framing and Scope

**Status:** Active
**Owner:** [TBD]
**Purpose:** Define the problem, understand the user, benchmark the landscape, and shape scope before architecture or code begins.

---

## Phase Checklist

- [ ] Problem is framed with evidence, not assumed
- [ ] Persona analysis is complete with specific pain
- [ ] Comparable products reviewed with named sources
- [ ] Scope boundaries are explicit (in, out, deferred)
- [ ] Pre-mortem risks are documented
- [ ] Discarded alternatives are recorded with reasons
- [ ] User interviews planned or completed
- [ ] Brainstorming output captured and organized

---

## Problem Statement

University students are financially devastated by grocery costs. 40% skip meals. 92% are stressed about money. Meanwhile, existing tools require dashboards, logins, and manual effort — students don't use them because they add friction to an already overwhelming life.

**The core insight:** Students already live in iMessage. If a grocery assistant lived there too — in their message thread, responding like a friend — they'd actually use it.

**Evidence:**
- 67% of Canadians say cost of living is worst ever (Abacus Data, Dec 2025)
- 40% of students skipped meals last semester to save money (CASA 2025)
- 92% of students stressed about money (TD 2025)
- Grocery prices rose 70%+ across Canada in 2 years (Statistics Canada)
- Tsenta (YC) proved the iMessage-bot model works and can charge $19-99/mo

---

## Primary Persona

**Name:** Alex (she/they), 20, 2nd-year university student
**Living situation:** Shared off-campus house with 3 roommates
**Kitchen:** Shared, 1 fridge, basic cookware
**Weekly grocery budget:** $50 (self-imposed)
**Current behavior:** Shops at whatever store is closest, buys the same 10 items, has no idea if she's overpaying
**Frustrations:** "I know I'm spending too much on food but I don't have time to figure out which store is cheaper. By the time I check flyers, I've already spent the money."
**Tech habits:** iMessage for everything. Instagram. TikTok. Has not downloaded a new app in 6 months.
**Would download an app?** "No way. But if I could text a number and it tells me what to buy? I'd use that."

### Secondary persona

**Name:** Marcus, 22, 4th-year engineering student
**Living situation:** Campus residence with meal plan + some self-cooking
**Pain point:** Meal plan swipes run out by week 8 of semester. Then he has $0 budget for groceries.
**Need:** "I need to know how to stretch $30 into a week of food."

---

## Current Workflow (without Staple)

```
1. Alex realizes she's hungry / out of food
2. Opens Google Maps → searches "grocery store near me"
3. Picks the closest one (usually a convenience markup)
4. Walks in, buys whatever looks reasonable
5. Spends $30-40 on 5-6 meals worth of food
6. Gets home, realizes she forgot key ingredients
7. Eats out / DoorDash to supplement → spends more
8. At end of week: $80-100 spent, felt like nothing
```

**Friction points:**
- No price awareness before walking into a store
- No meal plan → impulse buys, missing ingredients
- No budget optimization
- No awareness of cheaper alternatives (frozen vs fresh, bulk vs packaged)
- "Convenience store premium" — paying more for the same items

---

## Comparable Software Review

| Product | What they do well | What they do poorly | Table stakes or differentiator | Staple verdict |
|---------|------------------|---------------------|-------------------------------|----------------|
| **Flipp** | Flyer aggregation, price matching | Requires browsing; no AI; no personalization | Table stakes for prices | Must match price accuracy |
| **Mealime** | Meal planning, recipe discovery | No prices; no budget optimization; app-interface | Table stakes for meal plans | Must match meal plan quality |
| **Tsenta (YC)** | iMessage-first interface, natural language, paid subscriptions | Job search (lower frequency) | Differentiator: iMessage as UI | **Must copy** iMessage-first model |
| **HelloFresh** | Convenience, predictable pricing | Expensive ($5-10/meal); packaging waste | Different model | Ignore — different motion |
| **GroceryAI** | WhatsApp ordering, NLP | B2B (enterprise), not consumer | Validates grocery-messaging pattern | Good: validates. Bad: different market |
| **Splitwise** | Bill splitting | Not grocery-specific; no prices | Adjacent | Potential future integration |

---

## Pre-Mortem Analysis

| Failure scenario | Likelihood | Impact | Mitigation | Accepted? |
|----------------|------------|--------|------------|-----------|
| Students don't trust a bot with food decisions | Medium | High — no users | Free trial; "savings guarantee"; testimonials | No — must validate in user testing |
| iMessage bridge breaks after macOS update | Medium | High — service down | Monitor macOS betas; have BlueBubbles fallback ready | Accept with mitigation |
| Grocery price data is stale / inaccurate | Medium | Medium — erodes trust | 2-hour cache TTL; surface "price from X hours ago" | Accept with mitigation |
| Mac Mini dies / loses power | Low | High — messages not sent | Remote restart + UPS + uptime monitoring | Accept with mitigation |
| Students find WhatsApp interface preferable to iMessage | Medium | Low — we add WhatsApp in Phase 3 | Architecture abstracts channel layer | Accept — architectural decision |
| GPT-4o gives wrong nutritional / dietary advice | Low | Medium — user frustration | Include disclaimer; allow manual override | Accept with legal disclaimer |

---

## Scope Boundaries

### In scope (Phase 0-2)
- iMessage-only bot (blue bubbles on iPhone)
- Real-time grocery price lookups for Canadian stores (Loblaws, No Frills, Walmart, FreshCo, Superstore)
- Natural language query understanding ("what's cheap?", "meal for $40", "compare chicken prices")
- Meal plan generation optimized for a budget
- Shopping list generation from meal plans
- User preference storage (dietary restrictions, allergies, store preferences)
- Price comparison across multiple stores
- Stripe subscription integration ($3/mo for unlimited access)
- Price drop alerts ("eggs dropped to $2.99 at No Frills")

### Deferred to Phase 3
- WhatsApp integration
- Nutritional tracking
- Photon Spectrum multi-channel architecture
- Public beta with .edu email verification
- Group chat support (roommate group in iMessage)

### Deferred to Phase 4
- Apple Messages for Business official channel
- US grocery store data
- Auto-ordering / Instacart integration
- Roommate bill splitting
- Bulk-buy pooling ("3 people near you want rice")
- Campus partnerships and white-label

### Out of scope (not planned)
- Dashboard web application
- Native iOS/Android app
- Physical hardware (fridge cam, barcode scanner, etc.)
- Meal delivery / food preparation
- Grocery store operations (inventory, logistics)

---

## Discarded Alternatives

| Alternative | Why considered | Why rejected | Future trigger to revisit |
|------------|---------------|-------------|--------------------------|
| **Build as web app first** | Faster to prototype | Students don't use web apps for daily needs; iMessage is always open | Never — architecture is messaging-first |
| **Build as native iOS app** | More control over UX | Requires App Store, install friction, updates — contradicts "no dashboard" core insight | If iMessage bridge becomes unsustainable |
| **Use SMS instead of iMessage** | No Mac required | Gray bubbles look spammy; no rich content; carrier costs | If iMessage bridge fails and WhatsApp takes too long |
| **Freemium with ads instead of subscription** | Lower barrier to paid | Students hate ads; $3/mo is already low friction | If $3/mo conversion rate is <5% |
| **Partner with grocery stores for data** | More accurate pricing | Slow partnership cycle; limits store coverage; kills agility | Phase 4 — when we have negotiating leverage |

---

## Discovery Task List

- [x] Research competitive landscape → stored in `research/findings-1-competitive-landscape.md`
- [x] Evaluate iMessage bot technical approaches → stored in `research/findings-2-technical-approaches.md`
- [ ] Interview 5 students about their grocery shopping habits and pain points
- [ ] Interview 3 students who use Tsenta (or similar iMessage bots) about their experience
- [ ] Verify Apify Canadian Grocery API pricing and data quality
- [ ] Verify imessage-kit works on target Mac hardware (install + run auto-reply example)
- [ ] Create pricing model spreadsheet ($3/mo + unit economics)
- [ ] Write 10 sample conversations (user → bot → bot response) to validate conversation design
- [ ] Decide on product name (working title: "Staple")
- [ ] Determine initial university target (which campus to launch at)
- [ ] Write user acquisition hypothesis: how will first 100 students find out about this?

---

## Phase 0 Exit Gate

**Scope is clear enough that architecture can start without re-doing discovery.**

Exit criteria:
- [ ] All checklist items complete (checked off above)
- [ ] At least 5 student interviews completed and summarized
- [ ] Apify API verified with working price query
- [ ] imessage-kit verified with working auto-reply on target Mac
- [ ] Scope boundaries documented and agreed

**When these criteria are met, move to Phase 1.**
