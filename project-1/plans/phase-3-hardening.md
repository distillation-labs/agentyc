# Phase 3: Hardening — Edge Cases, Security, and Quality

**Status:** Pending (Phase 2 must be complete first)
**Owner:** [TBD]
**Purpose:** Harden edge cases, failure handling, permission behavior, security, observability, and release confidence after core implementation is in place.

---

## Phase 3 Checklist

- [ ] All planned edge cases are handled with explicit error paths
- [ ] Permission and auth boundaries are verified
- [ ] Performance and scalability are verified with realistic load
- [ ] Monitoring and alerts are configured for all critical paths
- [ ] Logging coverage is complete for debugging and audit
- [ ] Security review completed (API keys, user data, iMessage privacy)
- [ ] Failover and disaster recovery procedures documented
- [ ] Runbook or support playbook written
- [ ] Deferred work is documented with explicit owners
- [ ] Defect tracking and regression prevention in place

---

## Edge Cases

### iMessage Bridge Edge Cases

| Edge case | Expected behavior | Implementation |
|-----------|------------------|----------------|
| Mac Mini loses power | Messages sit in iMessage queue; on reboot, bridge resumes and processes backlog | `queue.ts` stores undelivered messages; on reconnect, deliver in order |
| Mac Mini loses internet but Messages.app is running | imessage-kit detects incoming messages but cannot forward to backend; stores locally | Bridge queues messages in SQLite locally, retries every 30s |
| Messages.app crashes | imessage-kit detects Messages.app is not running on send (AppleScript returns error); restarts Messages.app | `IMessageError.SEND` → retry logic that launches Messages.app |
| Two users text simultaneously | Messages processed sequentially to avoid race conditions | Bridge has a message processing queue (FIFO) |
| User sends very long message (>4000 chars) | Truncate before sending to GPT-4o to avoid token overflow | Add `maxLength: 4000` to inbound message processing |
| User sends non-text content (image, sticker, tapback) | Reply: "I can only read text for now!" | Filter in intent classifier; if no text and no attachment, return guidance |
| User sends message in group chat | Bot responds only if explicitly @mentioned or addressed | imessage-kit's `onDirectMessage` handles 1:1; group requires `onGroupMessage` (Phase 4) |
| User blocks the bot number | imessage-kit simply stops receiving messages from that number; no error | No action needed; user will return if they unblock |

### Grocery Price Engine Edge Cases

| Edge case | Expected behavior |
|-----------|------------------|
| Product not found at any store | Reply: "I couldn't find that item in any of our stores. Try a different name?" |
| All APIs are down | Return last cached prices with "Prices from X hours ago" disclaimer |
| Price data is >24 hours old | Reply: "I don't have recent prices for this item. Last seen at $X.XX, Y hours ago." |
| Sale price vs regular price confusion | Reply: "Regular $7.99, on sale for $4.99 until June 20." |
| Products priced per unit that don't match (lb vs kg) | Normalize to the unit type most commonly used for that product; show both if confusing |
| User asks about out-of-season produce | Reply: "That's out of season right now — it's pricey. Try [in-season alternative] instead." |

### AI / GPT-4o Edge Cases

| Edge case | Expected behavior |
|-----------|------------------|
| GPT-4o API is down | Return cached responses or template: "I'm having trouble thinking right now. Try again in a minute?" |
| GPT-4o returns hallucinated prices | Function calling prevents this — prices come from Apify API, not LLM generation |
| User asks off-topic question (not about groceries) | Reply: "I'm just a grocery bot! Try me for prices, meal plans, and deals." |
| User is rude or abusive | Respond politely but firmly: "Let's keep it friendly! What can I help you with?" |
| User asks in a language other than English | Reply in the same language if supported; otherwise: "Sorry, I only speak English right now!" |

### Subscription Edge Cases

| Edge case | Expected behavior |
|-----------|------------------|
| Stripe webhook delivered twice | Idempotency key on Stripe events; Supabase upsert rather than insert |
| Payment fails after successful upgrade | Retry payment; if fails after 3 attempts, downgrade with notification |
| User wants to downgrade mid-cycle | Keep paid access until end of billing period, then downgrade |
| User wants to upgrade mid-cycle | Immediate upgrade, prorated charge |
| User cancels immediately after paying | Full refund within 48 hours (no questions asked policy for trust) |
| User wants to pay annually | Offer $30/year ($2.50/mo — save 17%) |

---

## Security Review

| Concern | Assessment | Mitigation |
|---------|-----------|------------|
| **API keys** (OpenAI, Apify, Stripe, Supabase) | High risk if leaked | Stored in Railway environment variables; never in code; `.env` in `.gitignore` |
| **User phone numbers** | PII — treated as sensitive | Stored in Supabase with row-level security; never logged in plain text |
| **iMessage chat content** | User messages contain food preferences, budgets | Stored only as needed for personalization; user can delete data; never shared |
| **Stripe customer data** | PCI-sensitive | Handled entirely by Stripe; we only store `stripe_customer_id` |
| **Mac Mini physical access** | Attacker could read all iMessages | Mac Mini in locked location; disk encryption enabled; no remote desktop |
| **Tailscale tunnel** | Encrypted tunnel between Mac Mini and cloud | Tailscale uses WireGuard encryption; no open ports on Mac |
| **Rate limiting abuse** | Free user could spam API | 10 queries/week for free; IP + phone number based rate limiting |
| **LLM prompt injection** | User could try to override system prompt | GPT-4o system prompt is hardened; function calling limits what the LLM can do |

---

## Monitoring and Observability

### Logging

Every service logs structured JSON to stdout:

```json
{ "service": "bridge", "level": "info", "message": "Inbound message received", "from": "+1XXXXXXX", "chat_id": "chat123", "duration_ms": 45, "timestamp": "2026-06-13T14:30:00Z" }
```

Log levels:
- `error` — user-visible failure (message not sent, API down)
- `warn` — recoverable issue (API retry, cache miss)
- `info` — normal operation (message received, price fetched)
- `debug` — detailed troubleshooting (SQL query, AppleScript output)

### Metrics to track

| Metric | Where | Alert threshold |
|--------|-------|-----------------|
| Inbound messages per hour | Bridge logs | N/A (baseline) |
| Outbound messages per hour | Bridge logs | N/A (baseline) |
| Average response time | Backend logs | >5s → investigate |
| GPT-4o API error rate | Backend logs | >5% in 5 minutes → alert |
| Apify API error rate | Backend logs | >10% in 5 minutes → alert |
| Mac Mini uptime | Heartbeat endpoint | <99% → alert |
| Free tier users at limit | Subscription module | N/A (track conversions) |
| Stripe webhook failures | Backend logs | Any failure → alert |

### Health Check

- Bridge exposes `/health` endpoint returning `{ status: "ok", uptime: 12345, messages_processed: 500 }`
- Backend exposes `/health` returning `{ status: "ok", gpt4o: "ok", apify: "ok", supabase: "ok", stripe: "ok" }`
- Railway monitors health endpoint every 30s
- If bridge health fails 3 consecutive checks → SMS alert to developer

### Runbook

**"Bot stopped responding"**
1. Check Railway dashboard → is backend healthy?
2. SSH into Mac Mini → `systemctl status bridge` → is bridge running?
3. Check `journalctl -u bridge -n 50` for error messages
4. Check `~/Library/Messages/chat.db` exists and is readable
5. Restart bridge: `systemctl restart bridge`
6. If still failing → restart Mac Mini remotely

**"Prices are stale"**
1. Check Apify API status → is it up?
2. Check `price_history` table → when was last successful fetch?
3. Check Apify API key → is it still valid?
4. If Apify is down → prices fall back to cache with staleness note

**"Stripe payments not processing"**
1. Check Stripe Dashboard → are there failed webhooks?
2. Check backend logs → is `/webhook/stripe` receiving events?
3. Check Stripe webhook endpoint in Dashboard → is it the correct URL?
4. Resend failed webhooks from Stripe Dashboard

---

## WhatsApp Integration (Phase 3 Addition)

When WhatsApp is added in Phase 3, the architecture expands:

```
iMessage ←→ imessage-kit (Mac Mini) ←→ Backend ←→ Photon Spectrum ←→ WhatsApp Cloud API
```

**Why Photon Spectrum for multi-channel:**
- Open-source multi-channel agent framework
- Single abstraction for iMessage, WhatsApp, SMS, email, Slack, Discord
- Keeps the same GPT-4o + pricing engine codebase
- Adds channels without rewriting per-channel adapters

**WhatsApp-specific considerations:**
- WhatsApp Business API requires business verification
- Twilio WhatsApp API has per-message costs (~$0.005-0.03/msg)
- Meta Cloud API is the official WhatsApp integration path
- Students may prefer WhatsApp (globally more popular than iMessage)
- Green bubble vs blue bubble doesn't matter on WhatsApp

---

## Deferred Work (Documented)

| Item | Why deferred | Owner | Trigger to revisit |
|------|-------------|-------|-------------------|
| Apple Messages for Business official channel | Expensive, slow approval, overkill for MVP | [TBD] | When we have 1000+ paid users and campus partnerships |
| US grocery store data | Higher complexity, lower initial TAM for a Canadian launch | [TBD] | When Canadian market is saturated or when user base demands it |
| Auto-ordering / Instacart integration | Complex logistics, liability, partnership negotiation | [TBD] | When user feedback consistently requests "just order it for me" |
| Roommate bill splitting | Crosses into different product category | [TBD] | When >30% of users ask for it |
| Bulk-buy pooling | Requires critical mass of users in same geography | [TBD] | When we have 500+ users at a single campus |
| Group chat support | Complex state management, different UX patterns | [TBD] | When users consistently add bot to group chats |
| Nutritional tracking | Requires food database integration (USDA, etc.) | [TBD] | When requested by >20% of users |

---

## Phase 3 Task List

- [ ] Write and test all edge case handlers (see tables above)
- [ ] Implement message queue for bridge (offline resilience)
- [ ] Implement rate limiting for free tier (10 queries/week)
- [ ] Implement rate limiting for API abuse (IP + phone)
- [ ] Add content length limits and validation
- [ ] Handle non-text message types (images, stickers, tapbacks)
- [ ] Add LLM prompt injection hardening
- [ ] Implement graceful degradation for all external API failures
- [ ] Configure structured logging across bridge and backend
- [ ] Add health check endpoints on both services
- [ ] Set up Railway uptime monitoring
- [ ] Write runbook for common failure scenarios
- [ ] Perform security audit: API keys, user data, access controls
- [ ] Enable Mac Mini disk encryption
- [ ] Set up automatic macOS update deferral (don't auto-update without testing)
- [ ] Write and store emergency contact procedures
- [ ] Document all deferred work with owners and revisit triggers

---

## Phase 3 Exit Gate

**Edge cases, failure handling, security, observability, and release confidence are at production level.**

Exit criteria:
- [ ] All checklist items complete
- [ ] All edge case handlers implemented and tested
- [ ] Security review completed, no critical findings
- [ ] Monitoring and alerts configured and tested
- [ ] Runbook written and accessible
- [ ] Deferred work documented with owners
- [ ] System survives simulated failures (API down, Mac disconnect, Stripe error)

**When these criteria are met, move to Phase 4.**
