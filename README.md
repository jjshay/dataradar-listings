# DATARADAR

> eBay reselling intelligence dashboard: live inventory + 54k-record comp database + 4-LLM pricing consensus.

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-2.0+-green.svg)
![eBay API](https://img.shields.io/badge/eBay-Trading%20API-orange.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

- **Live app:** https://web-production-15df7.up.railway.app/
- **Price Radar:** https://web-production-15df7.up.railway.app/prices
- **Source:** https://github.com/jjshay/dataradar-listings
- **eBay store:** https://www.ebay.com/str/gauntletgallery

---

## What it does

DATARADAR pulls every active eBay listing from the Gauntlet Gallery store, enriches each item with market context (sold history, supply trends, promo rates, impressions), anchors a suggested price against a 54,000-record historical comp database, runs a 4-LLM consensus review (Claude, GPT-4o, Gemini, Grok), and emits a per-item action verdict: **PROMOTE / DISCOUNT / RELIST / HOLD**. A standalone Price Radar (`/prices`) exposes the comp database for ad-hoc lookups by title or artist.

The pipeline is: **inventory → enrichment → comp anchoring → LLM consensus → action score → reprice**.

---

## Architecture

- **Single Flask app** — `app.py` (~14,350 lines) with Jinja templates in `templates/`.
- **Data layer** — flat JSON files in `data/` (no DB). 54k-record historical comp index lives in `data/historical_clean.json`.
- **External services** — eBay Trading API, Anthropic (Claude), OpenAI (GPT-4o), Google (Gemini + optional Sheets), xAI (Grok).
- **Deploy** — Railway, auto-deploy from `main`. Process defined in `Procfile` (gunicorn).
- **Auxiliary scripts** — `clean_historical.py`, `deep_clean.py`, `consolidate_all.py`, `build_aliases.py`, `build_clusters.py`, `comp_engine.py` build and maintain the historical DB offline.

```
                 +-----------------------+
   eBay API ---> |   /api/inventory/     |
                 |   full-analytics      |
                 +-----------+-----------+
                             |
      +----------------------+----------------------+
      |                      |                      |
      v                      v                      v
 enrichment           comp anchoring          action scoring
 (sold/supply/       (historical_clean       (PROMOTE /
  promo/traffic)      .json, p25/p50/p75)     DISCOUNT / RELIST /
                             |                 HOLD)
                             v
                    4-LLM consensus
                    (Claude / GPT-4o /
                     Gemini / Grok, parallel,
                     cached by comp_median/10)
                             |
                             v
                    Swipe Mode UI
                    (Comps histogram +
                     LLM 2x2 grid)
```

---

## Core features

### 1. Inventory dashboard (`/`)
Backed by `/api/inventory/full-analytics`. Pulls every active listing via eBay Trading, enriches with sold history (`data/sold_history.json`), supply snapshots (`data/supply_snapshots.json`), promo rates (`data/promotions_cache.json`), traffic/impressions, and a per-item Action Scoring engine that returns **PROMOTE / DISCOUNT / RELIST / HOLD**.

### 2. Smart Price Engine
`calculate_suggested_price()` anchors on comp **p75 x event_multiplier** when there are >= 3 matching comps in the historical DB. Falls back to `base_price x event_multiplier` when comp coverage is thin. Key-date events boost price by tier: MINOR +5%, MEDIUM +15%, MAJOR +25%, PEAK +35%.

### 3. Historical Comp Database
54,000+ deduplicated past sales scraped from WorthPoint, eBay sold listings, and auction archives. Cleaned, deduped, and enriched with `artist`, `work_id`, `colorway`, `signed` flag, `medium`, and `edition_size`. Loaded via `load_historical_clean()` and queried via `lookup_historical_prices(title, artist)`.

### 4. Price Radar (`/prices`)
Standalone mobile-friendly UI for the comp DB. Search by title or artist; returns median / p25 / p75, per-year trend, top related works, and the full comp list with source, date, condition, and sale price. Backing APIs: `/api/prices/artists`, `/api/prices/search`, `/api/prices/work/<id>`.

### 5. Comp-grounded repricing
`calculate_comp_anchors(title, artist)` returns `{count, median, p75, trailing_12mo_median, signed_only, source_count_all}`. A **signed gate** filters `signed=True` when the title contains "signed". `/api/inventory/full-analytics` attaches `comp_evidence`, `comp_position` (under / at / over), and `comp_delta_pct` to every item. A top-level `comp_summary` aggregates comp coverage and dollar upside across the store.

### 6. 4-LLM Consensus Review
`/api/inventory/llm-price-review/<listing_id>` fans out to four models in parallel via `ThreadPoolExecutor`:

| Model        | Provider   | Identifier         |
|--------------|------------|--------------------|
| Claude       | Anthropic  | claude-sonnet-4-6  |
| GPT-4o       | OpenAI     | gpt-4o             |
| Gemini       | Google     | gemini-2.0-flash   |
| Grok         | xAI        | grok-3             |

Each model returns `{price, reason, status}`. The consensus is the **median of valid prices**. Results are cached in `data/llm_price_cache.json` keyed by `(listing_id, comp_median/10)` — the cache invalidates automatically when `comp_median` shifts by >= $10. Pass `?force=1` to bypass cache. Missing-key or per-model failure degrades gracefully — the remaining models still contribute.

### 7. Swipe Mode
Inventory toolbar button opens a full-screen card-by-card view:

- **Comps tab** — histogram of comp price distribution with your price highlighted, per-year trend chips, top-10 recent sales.
- **LLM Consensus tab** — 2x2 grid with Claude / GPT / Gemini / Grok prices, each model's reason, and a **Match Consensus** button to push the median price.
- Keyboard arrows navigate, touch swipe on mobile, Esc closes.

### 8. Calendar-event pricing engine
Original v1 engine still active inside `_event_multiplier()`. `get_active_events()` + `TIER_BOOSTS` + keyword matching from `data/pricing_rules.json`. Feeds the Smart Price Engine as the event multiplier component.

### 9. Promos / Ad spend management
`/api/promotions` for per-listing ad rate lookups and Promoted Listings campaign creation. Cached rates in `data/promotions_cache.json`.

### 10. eBay OAuth flow
Full OAuth 2.0 with refresh-token rotation. Scopes: `sell.inventory`, `sell.marketing`, `sell.analytics`, `sell.fulfillment`. Handles token refresh transparently on expiry.

---

## API reference

### Dashboard + inventory
| Route | Method | Purpose | Key params |
|---|---|---|---|
| `/` | GET | Main inventory dashboard UI | — |
| `/api/inventory/full-analytics` | GET | All listings + enrichment + comp anchors + action scores | — |
| `/api/listings` | GET | Raw active listings | — |
| `/api/stats` | GET | Store-level stat rollup | — |
| `/api/alerts` | GET | Underpriced / stale / policy alerts | — |
| `/api/underpriced` | GET | Items below suggested price | — |
| `/api/update-price` | POST | Push revised price to eBay | `item_id`, `new_price` |

### LLM consensus
| Route | Method | Purpose | Key params |
|---|---|---|---|
| `/api/inventory/llm-price-review/<listing_id>` | GET | 4-LLM fan-out, median consensus | `?force=1` bypasses cache |

### Price Radar (`/prices`)
| Route | Method | Purpose | Key params |
|---|---|---|---|
| `/prices` | GET | Standalone comp search UI | — |
| `/api/prices/artists` | GET | Artist index for autocomplete | — |
| `/api/prices/search` | GET | Title / artist search | `q`, `artist` |
| `/api/prices/work/<id>` | GET | Full comp list + stats for a work_id | — |

### Calendar + promos
| Route | Method | Purpose | Key params |
|---|---|---|---|
| `/api/calendar` | GET | Active + upcoming pricing events | — |
| `/api/promotions` | GET/POST | Ad rate lookup + campaign creation | — |

---

## Environment variables

| Name | Purpose | Required | Source |
|---|---|---|---|
| `EBAY_CLIENT_ID` | eBay app key | Yes | developer.ebay.com |
| `EBAY_CLIENT_SECRET` | eBay app secret | Yes | developer.ebay.com |
| `EBAY_REFRESH_TOKEN` | OAuth refresh token | Yes | eBay OAuth consent flow |
| `EBAY_DEV_ID` | eBay Trading API dev id | Yes | developer.ebay.com |
| `ANTHROPIC_API_KEY` | Claude sonnet-4-6 | Optional | console.anthropic.com |
| `OPENAI_API_KEY` | GPT-4o | Optional | platform.openai.com |
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | Gemini 2.0 Flash | Optional | aistudio.google.com |
| `GROK_API_KEY` / `XAI_API_KEY` | Grok 3 | Optional | x.ai |
| `DATARADAR_SHEET_ID` | Google Sheets pricing rules override | Optional | Google Cloud Console |

LLM keys are all optional — the consensus endpoint gracefully drops any model without a key. With zero LLM keys, the endpoint returns an empty consensus but the rest of the dashboard works.

---

## Local development

```bash
git clone https://github.com/jjshay/dataradar-listings.git
cd dataradar-listings

python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env        # fill in eBay keys + optional LLM keys

python app.py               # dev server on http://localhost:5050
```

`requirements.txt` is deliberately minimal — Flask, requests, python-dotenv, google-api-python-client, google-auth, google-auth-oauthlib, gunicorn. LLM calls are plain `requests` to each vendor's HTTP API, no SDKs.

---

## Deployment

Railway auto-deploys from `main`. The `Procfile` runs:

```
web: gunicorn app:app --bind 0.0.0.0:$PORT
```

All env vars live in Railway project variables. No build step — Railway installs `requirements.txt` and boots gunicorn. The `data/` directory ships with the repo, so the 54k-record historical DB is in-process from cold start.

---

## Data files

All under `data/`.

| File | Contents |
|---|---|
| `historical_clean.json` | 54k deduped comp records: artist, work_id, title, colorway, signed, medium, edition_size, sold_price, sold_date, source |
| `master_pricing_index.json` | Master artist / work rollup for fast lookup |
| `master_sales.json` | Cross-source raw sales before cleaning |
| `inventory_enriched.json` | Last enrichment snapshot for active listings |
| `sold_history.json` | Per-listing recent sold history |
| `supply_snapshots.json` | Active-supply trend over time |
| `promotions_cache.json` | Ad-rate cache per listing |
| `cost_basis.json` | Per-listing acquisition cost |
| `pricing_rules.json` | Calendar event keywords + tier multipliers |
| `reprice_config.json` | Repricing thresholds / guardrails |
| `scheduler_config.json` | Cron-style scheduled job config |
| `automation_config.json` | Auto-promote / auto-discount rules |
| `category_strategies.json` | Per-category pricing strategy overrides |
| `auto_enrichment.json` | Persisted enrichment defaults |
| `notifications.json` | In-app notification queue |
| `saved_searches.json` | Saved marketplace-scout searches |
| `watcher_history.json` | Buy-side watcher hits |
| `deal_targets.json` | Buy-side targets (artists, price bands) |
| `art_deals.json` | Captured underpriced listings |
| `live_deals_cache.json` | Short-TTL cache for live deal scouts |
| `comp_rejections.json` | Manually rejected comps (never re-included) |
| `work_aliases.json` | Title-variant alias table (built by `build_aliases.py`) |
| `work_clusters.json` | Clustered work_ids (built by `build_clusters.py`) |
| `kaws_data.json`, `worthpoint_sf_data.json`, `ebay_kaws_active.json` | Source-specific scraped datasets |
| `death_nyc_inventory.csv` | Death NYC artist-specific inventory export |
| `scrape_status.json` | Last-run timestamps for scrapers |
| `active_listings_report.txt` | Last full-store text report |

Note: `data/llm_price_cache.json` is created on first LLM-consensus call — it holds cached `{listing_id, comp_median/10}` → consensus payloads and auto-invalidates when `comp_median` moves >= $10.

---

## Recent changes

Latest on `main`:

- `6c7455b` fix: Grok API — `grok-2-latest` retired, switched to `grok-3`, accept `GROK_API_KEY` env var.
- `3ada85d` feat: 4-LLM consensus price review + swipe-mode inventory card UI.
- `144c3b6` feat: wire Price Radar comps into inventory repricing + UI (`comp_evidence`, `comp_position`, `comp_delta_pct`).
- `9a37215` feat: `/prices` — standalone historical price database UI.
- `b5d73c3` v5.0 — edition-size extraction, band gating, AP matching.
- `3cd7837` — master DB cleaned: 78,859 → 63,903 records.
- `27feeaf` v4.9 — ingested all sources: 253k raw → 78,859 clean.

---

## Roadmap

- OpenAI + Gemini quota top-up to restore full 4-model consensus in production.
- Bulk batch repricing with LLM-consensus approval flow (queue → review → push).
- Scheduled nightly comp re-index against fresh sold data.
- Additional data sources: Heritage Auctions, Christie's, Sotheby's, Phillips.
- Per-artist trend detection (breakout / cooling) wired into action scoring.

---

## License

MIT. See [LICENSE](LICENSE).
