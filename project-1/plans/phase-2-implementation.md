# Phase 2: Implementation — Build Execution

**Status:** Pending (Phase 1 must be complete first)
**Owner:** [TBD]
**Purpose:** Deliver the complete feature, end to end. Every state, every error path, every edge case.

---

## Phase 2 Checklist

- [ ] Work packages are sequenced with dependencies mapped
- [ ] Each work package has explicit success criteria
- [ ] Reuse opportunities are identified (do not duplicate)
- [ ] File size limits are enforced as code is written
- [ ] Logging discipline is followed
- [ ] Multi-tenancy is enforced from the first query/mutation
- [ ] Provider abstraction is in place before external calls are made
- [ ] All GA-ready capabilities are implemented
- [ ] TypeScript compiles with strict mode enabled
- [ ] All tests pass

---

## Work Package Sequence

```
WP1: Convex Project Setup + Schema
  │ (everything depends on the DB + project)
  ▼
WP2: Bridge — Mac Mini + imessage-kit
  │ (must have messages flowing before Convex can process them)
  ▼
WP3: HTTP Action — processMessage
  │ (bridge messages need a landing point)
  ▼
WP4: GPT-4o Integration via Azure OpenAI
  │ (needs working HTTP action)
  ▼
WP5: Grocery Price Engine (Apify + Canadian stores)
  │ (needs GPT-4o for intent → function calling)
  ▼
WP6: Meal Plan + Shopping List Generation
  │ (needs pricing engine)
  ▼
WP7: User Preferences + Persistence
  │ (can start in parallel with WP5-6)
  ▼
WP8: Rate Limiting + Subscriptions (Stripe)
  │ (needs user persistence)
  ▼
WP9: Price Alerts + Cron Jobs
  │ (needs pricing engine + user persistence)
  ▼
WP10: Next.js Frontend (Landing + Stripe checkout)
  │ (can start in parallel with WP8-9)
  ▼
WP11: Integration Testing + E2E
```

---

## WP1: Convex Project Setup + Schema

**Objective:** Scaffold the Convex project and define the database schema.

**Success criteria:**
- `npx create-next-app` with Convex template working
- `convex dev` runs locally
- All 7 tables defined in `schema.ts` with indexes
- Convex dashboard shows empty tables
- `npx convex deploy` pushes to production

**Likely files:**
- `convex/schema.ts`
- `convex/auth.config.ts`
- `convex.json`

**Key decisions:**
- Use Convex's built-in ID type for all references
- Index `users.phone` for fast lookup on inbound message
- Index `priceHistory` on `[productName, store, fetchedAt]` for price queries
- Use `crons.ts` for scheduled jobs (Convex native cron)

---

## WP2: Bridge — Mac Mini + imessage-kit

**Objective:** Get a working iMessage bot that can send and receive messages.

**Success criteria:**
- Mac Mini set up with macOS 14+
- `imessage-kit` installed via `bun add @photon-ai/imessage-kit`
- `sdk.startWatching()` detects inbound messages from real iPhone
- `sdk.send()` sends message back
- Bridge agent calls Convex HTTP action on each inbound message
- Bridge agent delivers response from Convex back via `sdk.send()`
- Tailscale tunnel working between Mac Mini and internet

**Likely files:**
- `bridge/index.ts`
- `bridge/messageHandler.ts`
- `bridge/queue.ts`
- `bridge/heartbeat.ts`

**Canadian-specific notes:**
- Your personal iPhone + Canadian phone number = first test user
- Use your own grocery runs as the first test cases

---

## WP3: HTTP Action — processMessage

**Objective:** Create the Convex HTTP endpoint that the bridge calls.

**Success criteria:**
- `POST /api/processMessage` accepts `{ from: string, text: string, chatId: string }`
- Returns `{ response: string }`
- Looks up or creates user by phone number
- Calls `processInboundMessage` mutation
- Returns response text

**Likely files:**
- `convex/http.ts`
- `convex/processInboundMessage.ts`

---

## WP4: GPT-4o Integration via Azure OpenAI

**Objective:** Wire up Azure GPT-4o with function calling.

**Success criteria:**
- Azure OpenAI resource created in Azure Portal
- GPT-4o model deployed (e.g. `gpt-4o` deployment name)
- `callGPT4o` Convex action works: sends messages + functions, gets response
- Function calling works for: `getPrices`, `generateMealPlan`, `comparePrices`, `getWeeklyDeals`, `savePreference`
- System prompt keeps bot in character as Canadian grocery assistant
- Response time <3s for simple queries
- Graceful handling of Azure API errors (retry, fallback)

**Azure setup:**
```
1. Azure Portal → Create Azure OpenAI resource
2. Deploy gpt-4o model (use your $100K credits)
3. Get endpoint + API key
4. Store in .env.local: AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT
```

**Likely files:**
- `convex/callGPT4o.ts`
- `convex/prompts.ts`
- `convex/functions.ts`

**System prompt (Canada-specific):**
```
You are Staple, a friendly grocery assistant for Canadian university students.
You help students save money on food by finding the cheapest prices at
Canadian grocery stores: No Frills, Walmart Canada, FreshCo, Loblaws,
and Real Canadian Superstore. All prices are in Canadian dollars.

Your personality:
- Friendly and conversational, like a helpful friend
- Never judgmental about food choices or budget
- Concise — students have short attention spans
- Proactive — suggest cheaper alternatives when relevant

Rules:
- Only recommend Canadian stores you have price data for
- Always provide specific CAD prices from real store data
- If you don't have price data, say so honestly
- Respect dietary restrictions and allergies
- When suggesting meals, consider limited cooking equipment
- Default to discount stores (No Frills, Walmart, FreshCo) first
```

---

## WP5: Grocery Price Engine (Apify + Canadian Stores)

**Objective:** Fetch and cache real Canadian grocery prices.

**Success criteria:**
- `fetchApifyPrices` Convex action returns prices for all 5 Canadian chains
- Product name normalizer maps "chicken breast" → normalized key
- Price cache with 2-hour TTL (stored in `priceHistory` table)
- Fallback: when Apify API fails, return last known price with staleness note
- Cron job `refreshPrices` runs every 2 hours
- Stores: No Frills, Walmart Canada, FreshCo, Loblaws, Real Canadian Superstore

**Likely files:**
- `convex/fetchApifyPrices.ts`
- `convex/pricing.ts`
- `convex/scheduled.ts`

**Edge cases (Canadian-specific):**
- Québec store names may differ (Provigo vs Loblaws) — handle in normalizer
- Prices in CAD are the default; no currency conversion needed
- Sales often run Thursday-Wednesday in Canada — factor into cache refresh timing

---

## WP6: Meal Plan + Shopping List Generation

**Objective:** Generate meal plans optimized for Canadian student budgets.

**Success criteria:**
- GPT-4o generates plan for N days given budget + preferences + real Canadian prices
- Each meal includes: recipe name, key ingredients, estimated CAD cost per serving
- Total cost is at or under the user's stated budget
- Shopping list extracted from meal plan, items grouped by store
- Plans consider limited cooking equipment (dorm kitchen = microwave + kettle)

**Likely files:**
- `convex/generateMealPlanWithAI.ts`
- `convex/mealPlans.ts`
- `convex/prompts.ts` (meal plan section)

---

## WP7: User Preferences + Persistence

**Objective:** Store user preferences so the bot personalizes over time.

**Success criteria:**
- Preferences saved via `updateUserPreferences` mutation
- Preferences loaded and injected into GPT-4o context
- Users can set: dietary restrictions, allergies, preferred stores, weekly budget, household size, cuisine preferences, cooking equipment
- Users can check and clear preferences

**Likely files:**
- `convex/preferences.ts`
- `convex/users.ts`
- `convex/getUserPreferences.ts`

---

## WP8: Rate Limiting + Subscriptions (Stripe)

**Objective:** Monetize with Canadian-friendly pricing.

**Success criteria:**
- Free tier: 10 price checks/week (tracked in `rateLimits` table)
- Paid tier: unlimited at $3/mo CAD
- Stripe Checkout session created with CAD pricing
- Stripe webhook handled for: checkout.session.completed, invoice.paid, invoice.payment_failed, customer.subscription.deleted
- Rate limiter correctly rejects free users who exceed limits with upgrade prompt
- Upgrade/downgrade/cancellation work end-to-end

**Canadian-specific:**
- All pricing in CAD ($3 CAD = ~$2.15 USD)
- Stripe supports CAD as a settlement currency
- HST/GST not charged initially (under $30K revenue threshold)

**Likely files:**
- `convex/stripe.ts`
- `convex/subscriptions.ts`
- `convex/rateLimits.ts`

---

## WP9: Price Alerts + Cron Jobs

**Objective:** Let users set price drop alerts.

**Success criteria:**
- Users set alerts: "tell me when chicken drops below $5/lb"
- `refreshPrices` cron runs every 2 hours
- `checkAlerts` cron runs after each price refresh
- When price drops below threshold, iMessage sent to user
- Users can list and cancel alerts

**Likely files:**
- `convex/alerts.ts`
- `convex/scheduled.ts`
- `convex/getActiveAlerts.ts`

---

## WP10: Next.js Frontend

**Objective:** Thin frontend for Stripe checkout + landing page.

**Success criteria:**
- Landing page: "Text this number to save on groceries" with phone number
- `/pricing`: $3/mo CAD, feature comparison
- `/checkout/success`: confirmation page
- `/checkout/cancel`: "come back anytime"
- `/privacy`: privacy policy
- `/terms`: terms of service
- `/referral/[code]`: referral landing with instructions

**Likely files:**
- `src/app/page.tsx`
- `src/app/pricing/page.tsx`
- `src/app/checkout/success/page.tsx`
- `src/app/checkout/cancel/page.tsx`
- `src/app/privacy/page.tsx`
- `src/app/terms/page.tsx`
- `src/app/referral/[code]/page.tsx`

---

## WP11: Integration Testing + E2E

**Objective:** Validate the entire system.

**Success criteria:**
- Full pipeline: iPhone iMessage → Mac Mini → Convex → GPT-4o → Apify → response
- All 8 intent types return correct responses
- Free tier limits enforced correctly
- Stripe subscription flow works end-to-end
- Price alerts trigger correctly
- 10 sample conversations produce reasonable responses
- All Canadian stores return valid price data

---

## Quality Standards

| Requirement | Standard |
|------------|----------|
| **File size limit** | Hard 400 lines |
| **TypeScript strictness** | `strict: true` in tsconfig |
| **Error handling** | Every Convex action wrapped in try/catch |
| **API keys** | `.env.local` for Convex env vars |
| **Convex deployment** | Every WP deployed with `npx convex deploy` |
| **Git hygiene** | Each WP is a separate commit |

---

## Phase 2 Task List

- [ ] **WP1:** `npx create-next-app` with Convex template
- [ ] **WP1:** Define all 7 tables in `convex/schema.ts`
- [ ] **WP1:** Set up indexes
- [ ] **WP1:** `npx convex dev` → dashboard shows tables
- [ ] **WP2:** Set up Mac Mini, install imessage-kit
- [ ] **WP2:** Run imessage-kit auto-reply example
- [ ] **WP2:** Build bridge agent that calls Convex HTTP action
- [ ] **WP2:** Test: text bot from iPhone → bridge → Convex → response back
- [ ] **WP3:** Create `http.ts` with `processMessage` endpoint
- [ ] **WP3:** Build `processInboundMessage` mutation skeleton
- [ ] **WP3:** Test: POST sample message → returns response
- [ ] **WP4:** Set up Azure OpenAI resource, deploy GPT-4o
- [ ] **WP4:** Build `callGPT4o` Convex action
- [ ] **WP4:** Write system prompt (Canada-specific)
- [ ] **WP4:** Implement function definitions
- [ ] **WP4:** Test: "how much is chicken?" → returns real prices
- [ ] **WP5:** Set up Apify API client
- [ ] **WP5:** Build `fetchApifyPrices` Convex action
- [ ] **WP5:** Build product name normalizer
- [ ] **WP5:** Set up `refreshPrices` cron job
- [ ] **WP5:** Test: prices returned for all 5 Canadian stores
- [ ] **WP6:** Build meal plan generation prompt
- [ ] **WP6:** Build shopping list extraction
- [ ] **WP6:** Test: "meal for $40" → plan under $40 with real Canadian prices
- [ ] **WP7:** Build user + preferences CRUD
- [ ] **WP7:** Inject preferences into GPT-4o context
- [ ] **WP7:** Test: "I'm vegetarian" → saved and respected
- [ ] **WP8:** Set up Stripe with CAD pricing
- [ ] **WP8:** Build rate limiter
- [ ] **WP8:** Handle Stripe webhooks in Convex
- [ ] **WP8:** Test: free user hits limit → upgrade prompt
- [ ] **WP9:** Build price alert CRUD
- [ ] **WP9:** Set up `checkAlerts` cron job
- [ ] **WP9:** Test: "tell me when chicken drops below $5" → alert fires
- [ ] **WP10:** Build landing page
- [ ] **WP10:** Build pricing page
- [ ] **WP10:** Build Stripe checkout flow
- [ ] **WP11:** Run full E2E test: iPhone → response
- [ ] **WP11:** Fix all bugs
- [ ] **WP11:** TypeScript strict mode passes

---

## Phase 2 Exit Gate

**All GA-ready capabilities are implemented, TypeScript compiles, and the full pipeline works end-to-end.**

Exit criteria:
- [ ] All checklist items complete
- [ ] All 11 work packages delivered
- [ ] Full E2E pipeline: iPhone → Mac Mini → Convex → GPT-4o → Apify → iMessage response
- [ ] Canadian stores all returning valid prices
- [ ] Free tier + paid tier both functional
- [ ] TypeScript strict mode, zero errors
- [ ] At least 10 sample conversations produce correct responses

**When these criteria are met, move to Phase 3.**
