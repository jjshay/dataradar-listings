# DATARADAR — Strategy

## One-line thesis

**Every eBay listing, priced by 54,000 comps and 4 AI models, in real time.**

## Context — why this exists

The resale market for art, prints, and collectibles is a $30B+ global category that still runs on gut pricing. Operators screenshot eBay sold listings, eyeball three comps, and guess. Existing "tools" — Terapeak, 3dcart, WorthPoint, Sell HQ — deliver flat median tables and leave the reasoning to the human. None of them reason across distributions, condition variance, signature presence, or live competition. None of them cross-check their own answer against a second opinion, let alone four.

DATARADAR was built in ~3 weeks to solve one operator's pricing problem: Gauntlet Gallery's 200-SKU Shepard Fairey / Death NYC / KAWS inventory was being priced by feel, and feel was leaving 10-20% on the table per item. The fix was a 4-LLM consensus engine grounded in a 54,000-record historical comp database, with live eBay competition injected into every prompt. In the process of beating gut-pricing, it became something bigger: a proprietary pricing stack with a real data moat that could stand alone as a product.

## The asset stack

What DATARADAR *is* today, as assets on a balance sheet:

| Asset | Description | Why it matters |
|---|---|---|
| **54,000-record comp DB** | Scraped, deduped, cleaned from WorthPoint, eBay sold, auction archives | Takes 9-18 months to replicate from scratch. The moat. |
| **4-LLM consensus pipeline** | Claude Sonnet 4.6 + GPT-4o + Gemini 2.5 Flash + Grok 3, with low/recommended/high ranges + 0-100 confidence score | Methodologically rare. Most competitors use single-model scoring. |
| **Per-artist prompt library** | Fragments for Fairey, Death NYC, KAWS, Banksy, MBW, Bearbrick — encoding edition size, signature rules, colorway matrix | Hard-won domain knowledge expressed as prompts. Impossible to guess without operating the business. |
| **Curation / training loop** | Swipe UI (left=reject / right=approve) feeds directly back into `lookup_historical_prices` | Each human pass makes the DB cleaner. Compounding data quality. |
| **Live eBay Browse API integration** | Current competing listings fed into every pricing call | Consensus prices reflect *today's* market, not last month's median. |
| **Production deployment** | Railway-hosted Flask app, nightly cron re-index, ~15,500 LoC, live at web-production-15df7.up.railway.app | Not a notebook — an actual running system with real inventory. |
| **Operator feedback channel** | Gauntlet Gallery's active eBay store (~200 listings) | Live P&L validation on every pricing decision. |

## Who uses a thing like this

Three tiers, increasing in size and in exit optionality.

### Tier 1 — JJ (current operator)
Pain: Mis-pricing on a $300 Fairey print costs $30-60 per flip; across 200 listings that's $6k-12k/yr of leaked margin. Willingness to pay: infinite (it's internal). Addressable "market": Gauntlet Gallery's own revenue line. The tool has already paid for itself.

### Tier 2 — Other high-volume art/collectibles resellers
Pain: Every serious reseller in the Fairey / KAWS / Banksy / Bearbrick lanes has the same pricing-by-feel problem. They manually compare three comps and guess. Willingness to pay: $99-299/mo based on comparable vertical SaaS (Terapeak at $25/mo for surface data; Vendoo at $30; nothing comp-grounded + LLM-reasoned at any price). Addressable market: *rough estimate* 5,000-8,000 high-volume alt-art/collectibles sellers across US + UK + EU doing >$50k/yr in GMV. Bottom-up: 500 subs × $149 = **$894k ARR**, which is a real venture-backable line of business.

### Tier 3 — Marketplaces + auction houses
Pain: 1stDibs, Heritage, StockX, Rally, Sotheby's Platforms, Artsy, eBay itself all want to offer "smart pricing" to their sellers and none of them have a comp-DB + consensus-LLM asset ready to go. Willingness to pay: acquisition. Addressable market: ~20 corp-dev desks across the marketplace + auction stack. Valuation anchors in the Roadmap → Exit section below.

## Three strategic paths

### Path A — OWN IT (private moat)
Keep DATARADAR internal. Use it to grow Gauntlet Gallery from $6-figure to $7-figure annual GMV. Never expose the prompt library or the comp DB. **Pros:** no competitors get free calibration, no customer support overhead, JJ captures 100% of the margin improvement. **Cons:** revenue ceiling is Gauntlet Gallery's inventory capacity. Tool worth ~$50-150k/yr in captured arbitrage, capped.

### Path B — LICENSE IT (SaaS)
Wrap the existing app in multi-tenant auth + Stripe billing. Tier it:

| Tier | Price | Features | Target user |
|---|---|---|---|
| Solo | $49/mo | 100 listings/mo, single-artist focus | Part-time reseller |
| Pro | $149/mo | 1,000 listings/mo, all 6+ artist fragments, drift alerts | $100k+ GMV seller |
| Agency | $299/mo | Unlimited, multi-account, API access, custom artists | Reselling agencies, consignment shops |

**Revenue math (rough):**
- 100 subs × avg $99 = **$119k ARR** (doable in 6 months via LinkedIn + targeted outreach)
- 500 subs × avg $149 = **$894k ARR** (18-month target)
- 1,500 subs × avg $179 = **$3.2M ARR** (24-36 months, requires category expansion)

Go-to-market: JJ's existing LinkedIn audience (multi-AI-consensus content is already warm), partnerships with authentication services (PSA, Beckett, Heritage Authentication), a "DATARADAR for Mercari" expansion, and referral from Gauntlet Gallery's own brand.

### Path C — SELL IT (acqui-hire or IP sale)
Who buys:
- **1stDibs** — needs better seller pricing tools; public company with M&A mandate
- **Heritage Auctions** — no AI pricing story; comp DB would plug directly into their consignment desk
- **StockX** — already leans on algorithmic pricing; would want the art/print vertical
- **Rally / Masterworks / Otis legacy buyers** — fractional platforms need sharper auction price formation
- **eBay (Terapeak team)** — would absorb DB + engine as a Pro-Seller upgrade
- **Sotheby's Platforms / Artsy** — art-native buyers

Valuation anchors (*rough, public comps*):
- Pre-ARR, DB-only acqui-hire: **$0.5M - $2M** + JJ on a 2-year earnout
- $500k ARR, growing: **3-5x ARR** → **$1.5M - $2.5M**
- $1M+ ARR, 2x YoY: **5-8x ARR** → **$5M - $8M**

The DB alone is the floor. The DB + prompt library + curation loop is the middle. The DB + SaaS traction is the ceiling.

## Roadmap

### Phase 1 (weeks 2-6): Validate + vertical depth
- Add 3-4 more artist fragments: D*Face, OSGEMEOS, Keith Haring, a full KAWS colorway × size matrix
- Ship approval-weighted comps (currently informational — boost statistically; approved comps get 2x weight in price consensus)
- Bolt on the buy-side scout (marketplace-scout skill prototype) — same DB now powers both "sell at optimum" AND "buy under comp"
- Launch "Inside DATARADAR" LinkedIn series — weekly post showing one real pricing insight the tool surfaced that week

### Phase 2 (months 2-4): Productize + beta
- UX pass for non-operators (mom test)
- Multi-tenant auth (email + password, namespace per user)
- Stripe billing wired to three-tier plan
- Invite 5-10 beta resellers from JJ's LinkedIn audience (free for 60 days, feedback required)
- Instrument every pricing decision → margin realized to generate the case-study data set

### Phase 3 (months 4-12): Scale OR Exit
Choose after Phase 2 data lands. If beta retention > 70% and willingness-to-pay holds at $149:
- **SaaS track:** push to $5-10k MRR before considering outside capital
- **Exit track:** warm intros to corp dev at Heritage, 1stDibs, eBay, Rally, StockX; build the acquirer deck around the comp DB + curation loop, not the UI

Either path: **keep the comp DB proprietary**, **keep expanding it**, **keep JJ's name on the artist-prompt IP**.

## Moats & risks

### Moats
- **The 54k-record comp DB.** 9-18 months to replicate cleanly. The literal moat.
- **Per-artist prompt library.** Knowing that Fairey OBEY prints have a specific numbering convention — or that Death NYC signatures shift between Sharpie and silver paint pen per edition — is *lived* knowledge. No generalist tool will encode this.
- **Curation loop.** Every reject tightens the model. Over 6-12 months, DATARADAR's comps become measurably cleaner than anything a scraper-only competitor produces.
- **4-LLM consensus protocol.** Not technically hard. Methodologically rare. JJ has already earned LinkedIn mindshare on this pattern — free category positioning.

### Risks

| Risk | Severity | Mitigation |
|---|---|---|
| eBay Trading API policy revoked | High | Add Mercari, Whatnot, COA.io, Heritage Live as data sources — multi-marketplace from Phase 2 |
| WorthPoint / scrape sources rate-limit | Medium | Partner with an auction data provider; open a user-contribution loop (beta sellers' sales feed back) |
| LLM cost drift (providers raise prices) | Medium | Cache tiers already in place; add model fallback; self-host small models for easy cases |
| Competitive entry (Terapeak adds LLM) | Medium | Move fast on vertical depth — generalists won't build artist-specific logic |
| Key-person risk (JJ is the domain expert) | Medium | Document the prompt-library rationale; codify curation heuristics so they outlast the operator |

## Metrics to watch

| Metric | Current (best guess) | 6-month target |
|---|---|---|
| Active listings priced | ~200 (JJ's inventory) | 2,000 (10 beta users) |
| Comps in DB | 54,000 | 80,000 |
| Realized margin lift from applied consensus | unknown | +12% avg |
| LLM consensus variance (σ / median) | untracked | < 8% |
| Curation coverage on top 20 inventory items | ~partial | 50%+ approved+rejected |
| Beta user retention (Phase 2) | N/A | > 70% month-over-month |
| MRR (Phase 3 SaaS track) | $0 | $5-10k |

## Summary call

DATARADAR is not a side project. It's a proprietary pricing stack with a real data moat, built and live in production, already generating P&L lift for Gauntlet Gallery. The three paths — own, license, sell — are all credible; the moats are real; the 12-month roadmap is defined. The only open question is which path to run, and the answer should fall out of the next 60 days of beta validation. Run Phase 1 now. Decide Path A/B/C by the end of Phase 2. Don't let a $2M asset keep being treated like a $0 side-tool.
