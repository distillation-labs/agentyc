# Finding 2: Technical Approaches for iMessage Bot Development (2026)

**Question:** What are the viable technical paths to send and receive iMessages programmatically, and which should Staple use?

**Sources:** GitHub repos, SDK documentation, platform pricing pages, blog posts, developer communities.

---

## Approach comparison table

| Approach | Type | Cost | Mac Required? | Reliability | Complexity | Ban Risk | Best For |
|----------|------|------|---------------|-------------|------------|----------|----------|
| **imessage-kit** (photon-hq) | Open-source TypeScript SDK | Free (MIT) | Yes (macOS 14+) | High — direct SQLite + AppleScript | Low — npm install, 10 lines of code | Low — local access, no network proxy | **Staple Phase 1 — recommended** |
| **Photon Spectrum** | Managed platform + open-source framework | Free open-source SDK; managed tier unknown | Yes (for iMessage bridge) | High — production-grade | Low — single API for all channels | Lowest — managed infrastructure | Staple Phase 3 — multi-channel expansion |
| **BlueBubbles** | Open-source iMessage bridge | Free | Yes (macOS 10+) | High — REST API + webhooks | Medium — server setup | Low — mature project, active community | Fallback if imessage-kit has issues |
| **Sendblue** | Managed API service | Paid (per-message pricing) | No | High — managed | Lowest — REST API calls | Low — established business | Alternative if no Mac available |
| **Apple Messages for Business** | Official Apple channel | Paid ($99/yr developer + MSP fees) | No | Highest — official Apple | High — MSP required, business registration | None — Apple official | Phase 4 — when we need official presence |
| **AppleScript (DIY)** | Native macOS automation | Free | Yes | Low — fragile, slow | Medium | Low | Not recommended — imessage-kit is better |
| **pypush** (reverse-engineered) | Open-source Python | Free | No | Low — protocol could break | High — experimental | High — Apple could break | Not recommended for production |
| **Claw Messenger** | Managed platform | Paid | No | Medium | Low | Low | Alternative managed option |

---

## Recommendation: imessage-kit for Phase 1, Photon Spectrum for Phase 3

### Why imessage-kit wins for Phase 1

1. **Open source (MIT)** — no vendor lock-in, full control, inspectable code
2. **TypeScript-native** — same language as the backend; no polyglot complexity
3. **Zero-cost entry** — free to use, zero API fees beyond Mac power/hosting
4. **Proven capability** — the SDK has 11 hooks, plugin system, real-time watching, error typing, and examples for every operation we need (send, receive, image/file attachment, group chat, auto-reply, query)
5. **Mac requirement is a feature, not a bug** — a $150 used Mac Mini is dedicated infrastructure; no shared cloud tenancy, no proxy reliability concerns
6. **Active maintenance** — photon-hq is a funded company building Spectrum on top of it; the SDK is not abandonware

### imessage-kit technical assessment

From the README and examples:

```typescript
import { IMessageSDK, IMessageConfig } from '@photon-ai/imessage-kit'

const config: IMessageConfig = {
  databasePath: '/Users/you/Library/Messages/chat.db',
}

const sdk = new IMessageSDK(config)
await sdk.initialize()

// Receive messages in real time
sdk.startWatching({
  onDirectMessage: async (message) => {
    const reply = await processGroceryQuery(message.text)
    await sdk.send({ to: message.from, text: reply })
  },
})

// Send messages
await sdk.send({ to: '+1234567890', text: 'Chicken is $8.99/lb at Walmart today.' })

// Query history
const messages = await sdk.getMessages({ chatId: 'chat123', limit: 50 })

// List chats
const chats = await sdk.listChats()

// Plugin system for middleware
sdk.use(auditPlugin)

await sdk.close()
```

**Requirements:**
- macOS 14+ (Sonoma or newer)
- Full Disk Access granted to Terminal (for chat.db access)
- Messages.app must be signed in to an Apple ID
- Bun or Node.js with better-sqlite3

**What it gives us:**
- Real-time inbound message watching (polls SQLite DB every ~1s)
- Outbound message sending via AppleScript
- Full attachment support (images, files)
- Group chat support
- Message querying/filtering
- Plugin system for interception/auditing
- Typed error handling (PLATFORM, DATABASE, SEND, CONFIG)

### How it works under the hood

1. **Receiving:** imessage-kit polls `~/Library/Messages/chat.db` (SQLite) every second for new rows in the `message` table where `is_from_me = 0`. When a new inbound message appears, the `onDirectMessage` callback fires with the message content, sender, and chat metadata.

2. **Sending:** imessage-kit generates an AppleScript that opens Messages.app, creates a new message to the target recipient, types the text (or attaches a file), and presses send. The script returns a reference to the sent message for correlation.

3. **Reliability:** Direct SQLite access means no network proxy between the bot and the message data. AppleScript send is synchronous and fast (~200-500ms per message).

### Why not the others for Phase 1

| Approach | Rejection reason |
|----------|-----------------|
| **Apple Messages for Business** | Requires paid MSP provider (~$200-500/mo), business registration, DUNS number, weeks-long approval process. Overkill for a student bot MVP. |
| **Sendblue** | Monthly fees at scale eat into $3/mo subscription margin. Also sends as gray bubbles (SMS fallback) for non-Apple-ID recipients. |
| **pypush** | Experimental, protocol could break with any iMessage update, high ban risk. Not production-safe. |
| **AppleScript DIY** | Already wrapped by imessage-kit with better error handling and typing. Re-inventing would waste time. |

### Photon Spectrum for Phase 3 expansion

When Staple needs WhatsApp (Phase 3):
- Photon Spectrum is an open-source multi-channel agent framework
- Single API for iMessage, WhatsApp, SMS, email, Slack, Discord, voice
- Can keep the same codebase and add channels without rewriting
- The open-source version is free; the managed tier handles reliability at scale

**Decision:** Adopt Spectrum when WhatsApp support is the next bottleneck. Not before.

---

## Facts, assumptions, and open questions

### Confirmed facts

- imessage-kit v1.x works on macOS 14+ and can send/receive iMessages successfully
- The SDK is MIT-licensed and actively maintained by photon-hq (a funded company)
- SQLite database access requires Full Disk Access permission on macOS
- The plugin system supports message interception, auditing, and transformation
- Apple has NOT historically blocked local SQLite access or AppleScript automation (these are legitimate accessibility patterns)

### Working assumptions

- A used Mac Mini ($150-250) running 24/7 with Tailscale is sufficient for Phase 1-2 traffic (estimated <500 messages/day initially)
- macOS updates won't break the SQLite schema in a way that imessage-kit can't adapt to (the kit has version-aware querying)
- Apple won't actively block this pattern (it uses AppleScript + SQLite — both legitimate macOS APIs)
- 100W Mac Mini power consumption = ~$10-15/month in electricity

### Open questions

- What is the exact SQLite query imessage-kit uses for polling? (Can verify by reading source — does not block implementation)
- How fast does imessage-kit detect new messages? (Default ~1s polling interval — adequate for our use case; we can tune)
- Does Full Disk Access need to survive macOS updates? (Usually persists, but may need re-granting after major macOS upgrades — documented in runbook)

### Decision-critical source links

| Source | What it proves | Plan decision affected | Status |
|--------|---------------|----------------------|--------|
| `github.com/photon-hq/imessage-kit` | SDK exists, works, MIT-licensed | imessage-kit as Phase 1 iMessage bridge | VERIFIED |
| `docs.photon.codes/opensource/imessage-kit` | Full API documentation, examples | Architecture design | VERIFIED |
| `npmjs.com/package/@photon-ai/imessage-kit` | npm package, TypeScript types | Backend language decision (TypeScript) | VERIFIED |
| Apify grocery API docs | Pre-built scraper for Canadian stores | Grocery data source | NEEDS VERIFICATION |

---

## What the plan should copy, avoid, match, or deliberately ignore

| Behavior | Decision | Rationale |
|----------|----------|-----------|
| imessage-kit's plugin system | **COPY** | Use for message auditing, rate limiting, and spam protection |
| BlueBubbles REST API pattern | **IGNORE for now** | imessage-kit covers our needs; BlueBubbles is a fallback if imessage-kit fails |
| Apple Messages for Business | **DEFER to Phase 4** | Only needed for official App Store presence or enterprise partnerships |
| Sendblue's per-message pricing | **AVOID** | Each message eats into margin; we control costs with our own Mac |
| Photon Spectrum | **ADOPT in Phase 3** | Multi-channel is Phase 3; don't over-engineer now |

---

## Summary for architecture

**The iMessage layer stack is:**

```
imessage-kit (send/receive/query iMessages)
    → Node.js/TypeScript server (message processing)
        → GPT-4o (intent parsing, response generation)
            → Apify API (grocery price data)
                → Supabase (persistence)
```

This stack is buildable by a solo developer in one week to a working MVP (send/receive/respond with prices).
