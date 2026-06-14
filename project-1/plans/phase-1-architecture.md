# Phase 1: Architecture — Technical Design and Decisions

**Status:** Pending (Phase 0 must be complete first)
**Owner:** [TBD]
**Purpose:** Make the implementation shape explicit — including architecture decisions, ownership boundaries, data model, contracts, and risk documentation.

---

## Phase 1 Checklist

- [ ] Architecture decisions are documented with rationale and alternatives
- [ ] Official-doc preflight completed for all decision-critical external behavior
- [ ] Server/client ownership decided for every meaningful flow
- [ ] Data model and schema are designed (not just implied)
- [ ] State machine or workflow logic is defined
- [ ] Provider abstraction contract exists if external service is involved
- [ ] Multi-tenancy is enforced at every boundary
- [ ] File layout and modularity plan is explicit
- [ ] File size discipline plan is in place (400-line hard limit)
- [ ] Trade-offs are documented with accepted costs
- [ ] Risk register exists with mitigations
- [ ] Testing strategy covers happy path, failure modes, and edge cases
- [ ] Discarded alternatives are recorded with reasons

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                         USER'S IPHONE                            │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   Messages.app (iMessage)                  │   │
│  │                                                           │   │
│  │  "yo what's cheap this week?"    →  imessage-kit SDK      │   │
│  │  ← "Chicken is $8.99/lb at..."     (polled from chat.db) │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────────┬─────────────────────────────────────┘
                             │ blue bubble (iMessage protocol)
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│              MAC MINI (LOCAL — in your apartment)                 │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              imessage-kit (Node.js process)                │   │
│  │                                                           │   │
│  │  sdk.startWatching()    → polls chat.db every ~1s        │   │
│  │  sdk.send()             → AppleScript → Messages.app     │   │
│  │  sdk.getMessages()      → reads SQLite directly          │   │
│  │  sdk.listChats()        → all active conversations       │   │
│  └────────────────────┬─────────────────────────────────────┘   │
│                       │ Convex HTTP Action (Tailscale tunnel)   │
│                       │ POST /api/processMessage                │
│                       ▼                                          │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │           Bridge Agent (thin TypeScript layer)            │   │
│  │                                                           │   │
│  │  - Receives incoming messages from imessage-kit           │   │
│  │  - Calls Convex HTTP action with { from, text, chatId }  │   │
│  │  - Gets response text back from Convex                   │   │
│  │  - Calls imessage-kit.send() to deliver reply            │   │
│  │  - Queues messages locally if Convex is unreachable      │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────────┬─────────────────────────────────────┘
                             │ HTTPS (Tailscale tunnel)
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                    CONVEX CLOUD (ALL BACKEND)                    │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              HTTP Actions (entry points)                  │   │
│  │                                                           │   │
│  │  POST /api/processMessage  ← bridge sends messages here  │   │
│  │  POST /api/stripeWebhook  ← Stripe events                │   │
│  │  GET  /api/health          ← Mac Mini heartbeat          │   │
│  └────────────────────┬─────────────────────────────────────┘   │
│                       │                                          │
│  ┌────────────────────▼─────────────────────────────────────┐   │
│  │              Mutations (data writes)                      │   │
│  │                                                           │   │
│  │  processInboundMessage()   → main message pipeline        │   │
│  │  storePriceData()          → cron: refresh prices         │   │
│  │  checkPriceAlerts()        → cron: fire alerts            │   │
│  │  createCheckoutSession()   → Stripe checkout link         │   │
│  │  handleSubscriptionChange()→ upgrade/downgrade/cancel     │   │
│  │  updateUserPreferences()   → save dietary prefs           │   │
│  └────────────────────┬─────────────────────────────────────┘   │
│                       │                                          │
│  ┌────────────────────▼─────────────────────────────────────┐   │
│  │              Actions (external API calls)                 │   │
│  │                                                           │   │
│  │  callGPT4o(prompt, functions)  → Azure OpenAI GPT-4o     │   │
│  │  fetchApifyPrices(products)    → Apify Grocery API       │   │
│  │  generateMealPlan(budget, prefs)→ GPT-4o + pricing       │   │
│  │  comparePrices(item, stores[])  → multi-store query      │   │
│  └────────────────────┬─────────────────────────────────────┘   │
│                       │                                          │
│  ┌────────────────────▼─────────────────────────────────────┐   │
│  │              Queries (data reads)                         │   │
│  │                                                           │   │
│  │  getUser(id)               → user + preferences           │   │
│  │  getUserByPhone(phone)     → lookup by phone              │   │
│  │  getRecentPrices(product)  → cached price history         │   │
│  │  getActiveAlerts(userId)   → user's price alerts          │   │
│  │  getMealPlanHistory(userId)→ past meal plans              │   │
│  │  getRateLimitStatus(userId)→ free tier usage              │   │
│  └────────────────────┬─────────────────────────────────────┘   │
│                       │                                          │
│  ┌────────────────────▼─────────────────────────────────────┐   │
│  │              Convex Database (built-in)                   │   │
│  │                                                           │   │
│  │  users          |  user_preferences  |  price_history    │   │
│  │  conversations  |  meal_plans        |  shopping_lists   │   │
│  │  subscriptions  |  price_alerts      |  rate_limits      │   │
│  └─────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
         │                              │
         ▼                              ▼
┌──────────────────┐    ┌──────────────────────────────┐
│  Next.js (Vercel) │    │  Azure OpenAI (GPT-4o)       │
│                   │    │                              │
│  /                │    │  Chat completions            │
│  /pricing         │    │  Function calling            │
│  /checkout/*      │    │  Structured output (JSON)    │
│  /privacy         │    │  $100K in credits covers it  │
│  /terms           │    └──────────────────────────────┘
│  /referral/[code] │           │
└──────────────────┘           ▼
                    ┌──────────────────────────────┐
                    │  Apify Canadian Grocery API   │
                    │                              │
                    │  No Frills | Loblaws          │
                    │  Walmart | FreshCo           │
                    │  Real Canadian Superstore     │
                    └──────────────────────────────┘
```

---

## Key Architecture Decisions

| Decision | Chosen option | Alternatives considered | Rationale | Trade-offs accepted |
|----------|--------------|----------------------|-----------|-------------------|
| **Backend platform** | **Convex** | Railway + Express + Supabase, Firebase, PlanetScale | All-in-one: DB, server functions, HTTP, cron, auth. Same TypeScript front to back. Built-in real-time. Generous free tier. | Less control over infra; vendor lock-in (acceptable for early stage) |
| **Frontend** | **Next.js** on Vercel | Just Convex (no frontend), plain HTML | Need Stripe checkout pages, landing page. Next.js integrates cleanly with Convex (convex/nextjs package). | Minimal — frontend is just 5 pages |
| **AI model** | **GPT-4o** via Azure OpenAI | DeepSeek V4 Flash, Claude, Gemini | Mature function calling. Reliable structured output. Excellent system prompt adherence. $100K in Azure credits = $0 cost. | GPT-4o is overkill for simple price lookups (Phase 2: route simple queries to DeepSeek V4 Flash) |
| **LLM hosting** | **Azure OpenAI** | Direct OpenAI API, Anthropic, DeepSeek API | $100K in Microsoft credits = effectively free. Already have the account. | Must set up Azure OpenAI resource and deployment |
| **iMessage integration** | **imessage-kit** (local macOS) | Apple Messages for Business, Sendblue, BlueBubbles | Free, open-source, TypeScript-native, full control | Requires dedicated Mac hardware |
| **Grocery data** | **Apify Canadian Grocery API** | Custom scrapers, direct retailer APIs | Pre-built for Canadian stores. Covers No Frills, Walmart, Loblaws, FreshCo, Superstore. | Costs ~$25/mo CAD |
| **Payments** | **Stripe** | Lemon Squeezy, Paddle | Standard for SaaS. $3/mo CAD subscriptions. Webhook-driven. | 2.9% + $0.30 per transaction |
| **Target market** | **Canada only** | US, UK, Australia | Canadian stores are concentrated (5 chains cover 80% of market). Canadian grocery inflation is severe. You are in Canada = dogfood it yourself. | Smaller TAM |
| **User auth** | **Phone number** (via Convex auth) | Email, OAuth, Apple ID | Matches iMessage context. Students won't create an account. Phone = identity. | SMS costs (~$0.01/verification) |
| **Tunneling** | **Tailscale** | ngrok, Cloudflare Tunnel | Free, persistent, encrypted. No open ports. | Requires Tailscale on both Mac and cloud |

---

## Data Model (Convex Schema)

### `users`
| Field | Type | Description |
|-------|------|-------------|
| `_id` | `Id<"users">` | Convex auto-generated |
| `phone` | `string` | E.164 format (e.g. +15145551234) — indexed |
| `createdAt` | `number` | Timestamp |
| `lastActiveAt` | `number` | Last inbound message |
| `stripeCustomerId` | `string` (optional) | Stripe customer reference |
| `subscriptionTier` | `"free" | "paid"` | Current plan |
| `referralCode` | `string` | Unique 8-char code |
| `referredBy` | `Id<"users">` (optional) | Who referred them |

### `userPreferences`
| Field | Type | Description |
|-------|------|-------------|
| `_id` | `Id<"userPreferences">` | |
| `userId` | `Id<"users">` | References user |
| `dietaryRestrictions` | `string[]` | e.g. ["vegetarian", "gluten-free"] |
| `allergies` | `string[]` | e.g. ["peanuts", "shellfish"] |
| `preferredStores` | `string[]` | e.g. ["No Frills", "Walmart"] |
| `weeklyBudgetCents` | `number` | In cents ($5000 = $50 CAD) |
| `householdSize` | `number` | People to cook for |
| `cuisinePreferences` | `string[]` | e.g. ["italian", "mexican"] |
| `cookingEquipment` | `string[]` | e.g. ["microwave", "stove"] |

### `conversations`
| Field | Type | Description |
|-------|------|-------------|
| `_id` | `Id<"conversations">` | |
| `userId` | `Id<"users">` | References user |
| `imessageChatId` | `string` | iMessage chat identifier |
| `lastMessageAt` | `number` | |
| `messageCount` | `number` | |
| `isActive` | `boolean` | |

### `priceHistory`
| Field | Type | Description |
|-------|------|-------------|
| `_id` | `Id<"priceHistory">` | |
| `productName` | `string` | Normalized name (e.g. "chicken_breast") |
| `store` | `string` | Store name |
| `priceCents` | `number` | Price in cents |
| `unit` | `string` | e.g. "lb", "kg", "each" |
| `isSale` | `boolean` | Sale price? |
| `saleEndsAt` | `number` (optional) | When sale ends |
| `fetchedAt` | `number` | When scraped |

Index: `by_product_store` on `[productName, store, fetchedAt]`

### `mealPlans`
| Field | Type | Description |
|-------|------|-------------|
| `_id` | `Id<"mealPlans">` | |
| `userId` | `Id<"users">` | |
| `createdAt` | `number` | |
| `weekStart` | `string` | ISO date of Monday |
| `budgetCents` | `number` | |
| `plan` | `string` | JSON string of meals |
| `wasUsed` | `boolean` | |

### `shoppingLists`
| Field | Type | Description |
|-------|------|-------------|
| `_id` | `Id<"shoppingLists">` | |
| `mealPlanId` | `Id<"mealPlans">` | |
| `userId` | `Id<"users">` | |
| `createdAt` | `number` | |
| `items` | `string` | JSON array of shopping items |
| `totalCents` | `number` | |

### `priceAlerts`
| Field | Type | Description |
|-------|------|-------------|
| `_id` | `Id<"priceAlerts">` | |
| `userId` | `Id<"users">` | |
| `productName` | `string` | Product to watch |
| `targetPriceCents` | `number` | Alert below this |
| `store` | `string` (optional) | Specific store |
| `isActive` | `boolean` | |
| `lastTriggeredAt` | `number` (optional) | |

### `rateLimits`
| Field | Type | Description |
|-------|------|-------------|
| `_id` | `Id<"rateLimits">` | |
| `userId` | `Id<"users">` | |
| `weekStart` | `string` | Monday date for this week |
| `queryCount` | `number` | Queries used this week |

---

## Convex Function Map

### HTTP Actions (entry points called by external services)

| HTTP Action | Method | Called by | Purpose |
|------------|--------|-----------|---------|
| `processMessage` | POST | Mac Mini bridge | Inbound iMessage → process → return response |
| `stripeWebhook` | POST | Stripe | Subscription events |
| `health` | GET | Uptime monitor (Cronitor) | Bridge health check |

### Mutations (data writes — can be called from Next.js or scheduled jobs)

| Mutation | Purpose |
|----------|---------|
| `processInboundMessage` | Main pipeline: route message, call GPT-4o, fetch prices, log conversation, return response |
| `storePriceData` | Cron: pull latest prices from Apify, store in DB |
| `checkPriceAlerts` | Cron: compare new prices against user alerts, send notifications |
| `updateUserPreferences` | Save dietary restrictions, budget, store prefs |
| `createCheckoutSession` | Generate Stripe checkout link |
| `handleSubscriptionChange` | Update tier on Stripe event |
| `cancelSubscription` | Downgrade to free |

### Actions (external API calls — run on Convex's serverless infra)

| Action | Purpose |
|--------|---------|
| `callGPT4o` | Azure OpenAI GPT-4o chat completions with function calling |
| `fetchApifyPrices` | Query Apify Canadian Grocery API |
| `generateMealPlanWithAI` | GPT-4o + pricing → meal plan with real costs |

### Queries (data reads — called from Next.js or bridge)

| Query | Purpose |
|-------|---------|
| `getUserByPhone` | Lookup user on inbound message |
| `getRecentPrices` | Cached price lookups |
| `getActiveAlerts` | User's active price alerts |
| `getRateLimitStatus` | Free tier usage this week |
| `getUserPreferences` | Load user's stored preferences |

### Scheduled Jobs (Convex cron)

| Job | Frequency | Purpose |
|-----|-----------|---------|
| `refreshPrices` | Every 2 hours | Pull latest prices from Apify for top 100 products |
| `checkAlerts` | Every 2 hours (after refreshPrices) | Compare new prices against alerts |
| `cleanupOldData` | Daily at 3 AM | Delete price history > 90 days |

---

## State Machine: Message Processing Pipeline

```
INCOMING (from bridge HTTP action)
       │
       ▼
ROUTE ──────────► lookup user by phone
       │               │
       │               ├── exists → load preferences + rate limits
       │               └── new → create user, return welcome
       │
       ▼
CLASSIFY INTENT (via GPT-4o function calling)
       │
       ├── PRICE_CHECK  ──► fetchApifyPrices → format response
       ├── MEAL_PLAN    ──► load prefs → generateMealPlanWithAI
       ├── COMPARE      ──► fetchApifyPrices(multi-store) → format
       ├── DEAL_ALERT   ──► fetchApifyPrices(sale items) → format
       ├── SET_PREF     ──► updateUserPreferences → confirm
       ├── LIST_BUILD   ──► save to shoppingLists → confirm
       ├── SET_ALERT    ──► create priceAlert → confirm
       ├── UPGRADE      ──► createCheckoutSession → return link
       ├── HELP         ──► return help text
       └── GENERAL_CHAT ──► GPT-4o chat → respond naturally
       │
       ▼
ENFORCE LIMITS
       ├── free tier, <10 queries this week → process & increment
       ├── free tier, >=10 queries → return upgrade prompt
       └── paid tier → process unlimited
       │
       ▼
LOG CONVERSATION (store in conversations table)
       │
       ▼
RETURN RESPONSE TEXT → bridge delivers via imessage-kit.send()
```

---

## File Layout

```
project-1/
├── README.md
├── research/
│   ├── findings-1-competitive-landscape.md
│   └── findings-2-technical-approaches.md
├── plans/
│   ├── phase-0-discovery.md
│   ├── phase-1-architecture.md     ◀ You are here
│   ├── phase-2-implementation.md
│   ├── phase-3-hardening.md
│   └── phase-4-validation-and-rollout.md
│
├── convex/                          ← ALL backend logic (Convex)
│   ├── schema.ts                    ← Database schema (7 tables)
│   ├── auth.config.ts               ← Phone number auth
│   │
│   ├── http.ts                      ← HTTP actions (entry points)
│   │   ├── processMessage           ← Bridge posts here
│   │   ├── stripeWebhook            ← Stripe events
│   │   └── health                   ← Bridge heartbeat
│   │
│   ├── processInboundMessage.ts     ← Main message pipeline mutation
│   ├── users.ts                     ← User CRUD mutations
│   ├── preferences.ts               ← Preference mutations
│   ├── pricing.ts                   ← Price engine (Apify wrapper)
│   ├── mealPlans.ts                 ← Meal plan mutations
│   ├── alerts.ts                    ← Price alert mutations
│   └── rateLimits.ts               ← Rate limit tracking
│   │
│   ├── callGPT4o.ts                ← Azure OpenAI action
│   ├── fetchApifyPrices.ts         ← Apify API action
│   ├── generateMealPlanWithAI.ts   ← AI-powered meal plan action
│   │
│   ├── getUserByPhone.ts           ← User query
│   ├── getRecentPrices.ts          ← Price cache query
│   ├── getActiveAlerts.ts          ← Alert query
│   ├── getRateLimitStatus.ts       ← Rate limit query
│   └── getUserPreferences.ts       ← Preference query
│   │
│   ├── stripe.ts                    ← Stripe integration
│   ├── subscriptions.ts             ← Subscription management
│   │
│   ├── scheduled.ts                 ← Cron jobs
│   │   ├── refreshPrices           ← Every 2 hours
│   │   ├── checkAlerts             ← Every 2 hours
│   │   └── cleanupOldData          ← Daily 3 AM
│   │
│   ├── prompts.ts                   ← GPT-4o system prompts
│   └── functions.ts                 ← GPT-4o function definitions
│
├── src/                             ← Next.js frontend
│   ├── app/
│   │   ├── page.tsx                 ← Landing page
│   │   ├── pricing/page.tsx
│   │   ├── checkout/success/page.tsx
│   │   ├── checkout/cancel/page.tsx
│   │   ├── privacy/page.tsx
│   │   ├── terms/page.tsx
│   │   └── referral/[code]/page.tsx
│   └── convex/
│       └── ClientProvider.tsx       ← Convex client config
│
├── bridge/                          ← Mac Mini only
│   ├── index.ts                     ← imessage-kit entry
│   ├── messageHandler.ts            ← Routes to Convex HTTP
│   ├── queue.ts                     ← Offline message queue
│   └── heartbeat.ts                 ← Health check to Convex
│
├── convex.json                      ← Convex project config
├── next.config.ts
├── package.json
├── tsconfig.json
└── .env.local                       ← API keys (never committed)
```

**File size discipline:** Hard 400-line limit per file. If a file approaches 350 lines, extract into a submodule.

---

## Server/Client Ownership

| Surface | Owner | Rationale |
|---------|-------|-----------|
| iMessage send/receive | Mac Mini (local) | Requires local macOS; cannot run in cloud |
| Message routing + processing | Convex mutation | All backend logic in one TypeScript codebase |
| Azure GPT-4o calls | Convex action | Serverless functions call external API |
| Apify price fetching | Convex action | Serverless functions call external API |
| User preferences | Convex database | Built-in, indexed |
| Subscription management | Convex mutation + Stripe webhook | Stripe-driven, Convex handles state |
| Price alerts | Convex scheduled job | Cron-driven, runs on Convex infra |
| Landing + pricing pages | Next.js on Vercel | Thin frontend, mostly static |

---

## Canadian Store Coverage

| Store | Apify API coverage | Notes |
|-------|-------------------|-------|
| No Frills | ✅ | Discount chain — key for budget-conscious students |
| Walmart Canada | ✅ | Price matching enabled |
| FreshCo | ✅ | Discount chain (Ontario/West) |
| Loblaws | ✅ | Full-price chain — baseline for comparison |
| Real Canadian Superstore | ✅ | (West Canada) |

This covers **~80% of Canadian grocery market** with one API. No Frills + Walmart = where students actually shop.

---

## Testing Strategy

| Layer | Strategy | Coverage target |
|-------|----------|----------------|
| **Unit: Convex mutations** | Mock GPT-4o + Apify calls | 100% of intent branches |
| **Unit: Rate limiter** | Test free/paid limits exactly | Boundary conditions |
| **Integration: ProcessMessage** | Send test messages through full Convex pipeline | Happy path + failure paths |
| **Integration: Stripe webhook** | Test all event types with Stripe test mode | All webhook events |
| **E2E: Real iMessage** | Text from iPhone, verify response <5s | Daily in Phase 2 |
| **E2E: Cron jobs** | Trigger scheduled jobs manually | Verify prices stored, alerts fired |

---

## Official-Doc Preflight

| Question | Why it matters | Source | Status |
|----------|---------------|--------|--------|
| Does Convex support HTTP actions? | Bridge needs to POST messages | Convex docs | VERIFIED (convex/http.ts) |
| Does Convex support cron/scheduled jobs? | Price refresh + alert checking | Convex docs | VERIFIED (crons.ts) |
| Can Convex actions call external APIs? | GPT-4o, Apify, Stripe | Convex docs | VERIFIED |
| Does Azure OpenAI support GPT-4o with function calling? | Structured output for meal plans | Azure docs | VERIFIED |
| What Canadian stores does Apify cover? | Data source | Apify marketplace | NEEDS VERIFICATION |
| Can Convex auth use phone numbers? | User identity | Convex docs | NEEDS VERIFICATION |

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| imessage-kit breaks on macOS update | Medium | High | Monitor betas; BlueBubbles fallback |
| Mac Mini power/network down | Low | Medium | Local queue; auto-reconnect; alert to dev |
| Apify Canadian store coverage changes | Low | Medium | Add fallback scraping per store |
| GPT-4o costs eat into Azure credits | Low | Low | $100K is massive; monitor monthly usage |
| Convex free tier limits at scale | Medium | Medium | Upgrade to Convex paid tier (~$25/mo) |
| Students don't text a bot | Medium | High | Validate in Phase 0 interviews first |

---

## Phase 1 Exit Gate

**Architecture decisions are documented, source-backed, and explicit enough that a coding agent can implement without re-deciding architecture.**

Exit criteria:
- [ ] All checklist items complete
- [ ] Data model created in Convex schema
- [ ] All function signatures defined (HTTP, mutation, action, query, cron)
- [ ] File layout approved with 400-line discipline
- [ ] Official-doc preflight resolved (no blockers)
- [ ] Testing strategy reviewed and agreed
- [ ] Canadian store list confirmed with Apify documentation

**When these criteria are met, move to Phase 2.**
