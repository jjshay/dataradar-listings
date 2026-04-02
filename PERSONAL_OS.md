# DATARADAR — Personal Operating System

## What This Is

DATARADAR is a personal operating system for managing an eBay resale business focused on art, collectibles, and signed memorabilia. It connects to eBay's APIs and combines market intelligence, inventory management, promotion cost optimization, and deal discovery into a single dashboard.

**Live at:** http://localhost:5050 (local) | Railway deployment
**Stack:** Python/Flask backend, single-page HTML/JS frontend, eBay API integration
**Data:** JSON file storage in `/data/`, eBay Trading + Browse + Marketing + Analytics APIs

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    DATARADAR Dashboard                   │
│  ┌──────┐  ┌──────┐  ┌───────────┐  ┌─────┐  ┌──────┐ │
│  │ Home │  │Deals │  │ Inventory │  │Dates│  │Promos│ │
│  └──┬───┘  └──┬───┘  └─────┬─────┘  └──┬──┘  └──┬───┘ │
└─────┼─────────┼────────────┼───────────┼────────┼──────┘
      │         │            │           │        │
┌─────┴─────────┴────────────┴───────────┴────────┴──────┐
│                    Flask API Layer                       │
│  40+ endpoints · OAuth · Caching · Analytics Engine     │
└─────┬─────────┬────────────┬───────────┬────────┬──────┘
      │         │            │           │        │
┌─────┴──┐ ┌───┴───┐ ┌──────┴───┐ ┌────┴──┐ ┌───┴────┐
│eBay    │ │eBay   │ │eBay      │ │eBay   │ │Local   │
│Trading │ │Browse │ │Marketing │ │Analyt.│ │JSON    │
│API     │ │API    │ │API       │ │API    │ │Data    │
└────────┘ └───────┘ └──────────┘ └───────┘ └────────┘
```

---

## The Five Tabs

### 1. Home — Command Center
- **Stats grid:** Total deals, inventory count, eBay listings, active events
- **Alerts:** Smart alerts for hot deals (>60% below market), low supply opportunities, pricing errors
- **Upcoming Events:** Next pricing events that affect your categories
- **Top Deals:** Quick preview of highest-profit opportunities

### 2. Deals — Market Intelligence
Full-screen sortable table of 230+ market opportunities across 10 categories.

**Categories:** Apollo/Space, Banksy, D*Face, Hijack, Invader, KAWS, Mr. Brainwash, Shepard Fairey, Signed Guitars, Taylor Swift

**Per-deal data:**
| Column | What It Tells You |
|--------|------------------|
| Price | Current listing price |
| Low / Median / High | Market comp range |
| Profit | Upside potential (median - price) |
| Discount% | How far below market |
| Hotness (0-100) | Combined score: comps + discount + profit |
| Liquidity | How fast this category sells (High/Medium/Low) |
| Comps | Number of comparable sales found |
| Why It's a Deal | Automated reasoning |

**Actions:** Filter by category, sort by any column, search, export CSV, click to see full price history chart.

### 3. Inventory — Sell/Wait Decision Engine
Full analytics table for your 161-item collection with 19 data columns per item.

**The 9 Intelligence Features:**

| # | Feature | Columns | What It Does |
|---|---------|---------|-------------|
| 1 | **Sell-Through Tracking** | Velocity, Sold, DOM | Tracks how fast items sell. Fast/Medium/Slow/Stale based on sold history + days on market |
| 2 | **Margin Calculator** | Net Margin, Ad Cost | Estimates profit after cost basis (40% est), eBay fees (13.12%), and ad costs. **Red warning** when ads eat >50% of margin |
| 3 | **Price Alerts** | Home alerts | Auto-detects hot deals (>60% below market), low supply opportunities, overpriced items |
| 4 | **A/B Test Framework** | API: /api/ab-tests | Create test groups with different ad rates per category. POST to create, GET to review |
| 5 | **Seasonal Automation** | Season column | Links pricing events (Elvis birthday, Beatles anniversary, etc.) to ad rate boost suggestions. Shows "+3% Beatles Breakup" when relevant |
| 6 | **Organic Detection** | Promo column | Identifies items selling without ads (Organic) vs. promoted vs. unpromoted. Stop paying for ads on organic sellers |
| 7 | **Traffic Data** | Impressions, Views | eBay Analytics API integration for per-listing impressions, views, CTR, conversion rate |
| 8 | **Sold Tracking** | Sold, DOM | Pulls sold items from eBay Trading API. Calculates actual days-on-market per item |
| 9 | **Competitor Monitoring** | Supply, Trend | Weekly snapshots of eBay supply per item. Trend arrows show if competition is increasing (raise prices) or decreasing (opportunity) |

**Signal System:**
- **SELL NOW** — Low supply + high demand. Price at top of range.
- **GOOD TO SELL** — Market conditions favorable.
- **SET PRICE** — Needs pricing adjustment.
- **HOLD** — High supply or weak demand. Wait for scarcity.
- **WAIT** — Very high supply. Patience required.

### 4. Dates — Pricing Calendar
Interactive calendar showing 10 seasonal pricing events that affect your inventory:

| Event | Tier | Boost | When |
|-------|------|-------|------|
| Elvis Presley Birthday | MAJOR | +25% | Jan 6-10 |
| Beatles Breakup Anniversary | MEDIUM | +15% | Apr 8-12 |
| John Lennon Birthday | MAJOR | +25% | Oct 7-11 |
| John Lennon Death Anniversary | PEAK | +35% | Dec 6-10 |
| Valentine's Day | MINOR | +15% | Feb 12-15 |
| Marilyn Monroe Birthday | MEDIUM | +15% | May 31-Jun 4 |
| Apollo 11 Anniversary | MAJOR | +25% | Jul 18-22 |
| David Bowie Birthday | MEDIUM | +15% | Jan 8-12 |
| Christmas Season | PEAK | +35% | Dec 15-26 |
| Taylor Swift Birthday | MAJOR | +25% | Dec 12-15 |

Click any day to see active events. Navigate months/years.

### 5. Promos — Ad Spend & Cost Optimization
Three views for understanding and optimizing promotion costs:

**Cost by Product (Table):**
Every listing with: price, promo type (Dynamic/Standard/None), ad rate, standard cost, dynamic cost, total cost, campaign name. Sortable by all columns.

**Analytics:**
- Dynamic vs Standard ad count breakdown
- Item promotions and coupons count
- Cost by Category table
- Most Expensive Products to promote

**AI Tips:**
- High-value unpromoted items
- Listings with ad rates eating margin
- Dynamic vs fixed rate recommendations
- Low-value items with costly promotion

---

## API Reference

### Core Data
| Endpoint | Description |
|----------|-------------|
| `GET /api/listings` | Active eBay listings with market data |
| `GET /api/stats` | Dashboard summary stats |
| `GET /api/my-inventory` | Personal collection (86 Shepard Fairey items) |
| `GET /api/art-deals` | 230 market opportunities |
| `GET /api/market-lookup?q=` | Market pricing for any search term |
| `GET /api/market-categories` | All tracked categories with stats |
| `GET /api/historical-prices?title=&artist=` | Historical sale prices |

### Intelligence (Features 1-9)
| Endpoint | Feature | Description |
|----------|---------|-------------|
| `GET /api/inventory/full-analytics` | All 9 | Combined per-item analytics with velocity, margin, traffic, trends |
| `GET /api/deals/enhanced` | Deals | Deals with hotness, liquidity, reasons |
| `GET /api/sold-items` | #1, #8 | Sold items with days-on-market |
| `GET /api/traffic` | #7 | Per-listing impressions, views, CTR, conversion |
| `GET /api/alerts` | #3 | Smart price and market alerts |
| `GET /api/alerts/rules` | #3 | Custom alert rules (GET/POST) |
| `GET /api/ab-tests` | #4 | A/B test configurations (GET/POST) |
| `GET /api/seasonal-suggestions` | #5 | Event-aware ad rate recommendations |
| `POST /api/supply-snapshot` | #9 | Take competitor supply snapshot |
| `GET /api/supply-trends` | #9 | Supply changes over time |

### Promotions
| Endpoint | Description |
|----------|-------------|
| `GET /api/promotions` | All campaigns, promos, coupons |
| `GET /api/promotions/costs` | Per-product cost breakdown |
| `GET /api/promotions/recommendations` | AI optimization tips |
| `POST /api/promotions/create-campaign` | Create Promoted Listings campaign |
| `POST /api/promotions/update-ad-rate` | Update ad rate for listing |

### Calendar & Pricing
| Endpoint | Description |
|----------|-------------|
| `GET /api/calendar?year=` | Pricing events for year |
| `GET /api/upcoming-dates` | Next 10 events |
| `GET /api/underpriced` | Items below suggested price |
| `POST /api/update-price` | Update eBay listing price |
| `POST /api/update-category-pricing` | Bulk price update by category |

### Auth
| Endpoint | Description |
|----------|-------------|
| `GET /auth/ebay` | Start OAuth consent flow |
| `GET /auth/ebay/callback` | OAuth callback (auto) |
| `GET /api/auth/status` | Check auth + Marketing API status |

---

## Data Files

| File | Size | What |
|------|------|------|
| `inventory_enriched.json` | 1.4 MB | 86 Shepard Fairey items with market data, comps, supply analysis |
| `art_deals.json` | 556 KB | 230 deal opportunities across 10 categories |
| `master_pricing_index.json` | 85 KB | Market index: 9,687 items across art categories |
| `pricing_rules.json` | 3 KB | 10 seasonal pricing events |
| `deal_targets.json` | 3 KB | 37 saved deal search targets |
| `shepard_fairey_data.json` | 59 MB | Complete Shepard Fairey price history |
| `artist_price_summaries.json` | 15 MB | Aggregated pricing by artist |
| `worthpoint_sf_data.json` | 57 MB | WorthPoint historical sales |
| `promotions_cache.json` | — | Cached eBay promotion data (30-min TTL) |
| `sold_history.json` | — | Sold item history (30-min TTL) |
| `supply_snapshots.json` | — | Weekly competitor supply snapshots (12-week rolling) |
| `price_alerts.json` | — | Custom alert rules |
| `ab_tests.json` | — | A/B test configurations |

---

## eBay API Scopes

Connected via OAuth2 with refresh token. Scopes:
- `sell.inventory` — Read/write listings, prices, quantities
- `sell.marketing` — Campaigns, promotions, coupons, ad rates
- `sell.marketing.readonly` — Read promotion data
- `sell.analytics.readonly` — Traffic reports, impressions, conversion
- `sell.account.readonly` — Account settings
- `sell.fulfillment.readonly` — Order data

**To authorize:** Visit `/auth/ebay` → eBay consent → token saved to `.env`

---

## Daily Operating Rhythm

1. **Morning:** Open Home tab. Check alerts for hot deals and low-supply opportunities.
2. **Deal hunting:** Switch to Deals tab. Filter by category, sort by hotness. Export promising deals.
3. **Inventory review:** Check Inventory tab. Sort by Signal — act on SELL NOW items first. Check margin column to make sure ads aren't eating profit.
4. **Promo optimization:** Check Promos tab. Look for items where ad cost > 50% of margin (red). Check organic sellers — remove them from paid campaigns.
5. **Seasonal check:** Before events (check Dates tab), boost ad rates on matching items. After events, pull rates back.
6. **Weekly:** Take supply snapshot (auto on inventory load). Review trend arrows — when competitor supply drops, raise prices.

---

## Export

Every table has an **Export CSV** button. Files include all visible columns plus additional fields:
- `dataradar_deals.csv` — All deal data with URLs, reasons, comps
- `dataradar_inventory_full.csv` — 31 columns per item including velocity, margin, traffic, trends
- `dataradar_promo_costs.csv` — Per-product promotion costs by type

---

## Running

```bash
# Local
cd /Users/johnshay/DATARADAR-Listings
python3 app.py
# → http://localhost:5050

# Deploy to Railway
git push origin main
# Auto-deploys to https://web-production-15df7.up.railway.app
```

**Environment variables needed:**
- `EBAY_CLIENT_ID` — eBay app client ID
- `EBAY_CLIENT_SECRET` — eBay app client secret
- `EBAY_REFRESH_TOKEN` — User OAuth refresh token (get via /auth/ebay)
- `EBAY_DEV_ID` — eBay developer ID
