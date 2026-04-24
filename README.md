# DATARADAR

> eBay reselling intelligence dashboard: live inventory + 54k-record comp database + 4-LLM pricing consensus with live-competition grounding, confidence scoring, swipe-mode curation, and bulk repricing.

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-2.0+-green.svg)
![eBay API](https://img.shields.io/badge/eBay-Trading%20API-orange.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

- **Live app:** https://web-production-15df7.up.railway.app/
- **Price Radar:** https://web-production-15df7.up.railway.app/prices
- **Source:** https://github.com/jjshay/dataradar-listings
- **eBay store:** https://www.ebay.com/str/gauntletgallery

---

## Recently shipped

- **Consensus price ranges** — every LLM returns `{low, recommended, high}`, consensus takes the per-column median.
- **Live eBay competition in every prompt** — top 15 active listings (range + median + sample titles) fed to all 4 models.
- **Confidence scoring** — 0-100 score with HIGH/MEDIUM/LOW band and a 4-line reasoning chain on every review.
- **Opportunities + Drift dashboard cards** — green card surfaces top 10 upsides, red card flags items where comp_median has drifted ≥10% from your price.
- **Bulk Consensus Reprice** — filter by upside %, comp count, artist → preview → one-click push; optional "Re-query LLMs" checkbox forces fresh consensus.
- **Swipe Mode with Train Comps** — swipe-to-reject / swipe-to-approve curation loop that teaches the comp lookup which sales to ignore.

---

## What it does

DATARADAR pulls every active eBay listing from the Gauntlet Gallery store, enriches each item with market context (sold history, supply trends, promo rates, impressions), anchors a suggested price against a 54,000-record historical comp database, pulls the top 15 live competing listings from eBay Browse, and runs a 4-LLM consensus review (Claude, GPT-4o, Gemini 2.5 Flash, Grok 3). Every review returns a per-model `{low, recommended, high}` range plus a confidence score (0-100) with a 4-component reasoning chain. A per-artist expert prompt fragment is injected for Fairey / Death NYC / KAWS / Banksy / MBW / Bearbrick to handle edition, colorway, and authentication rules.

From there a human can:

1. **Scan** the Opportunities + Drift Alerts dashboard cards.
2. **Swipe** through the inventory card-by-card — compare comps histogram, LLM consensus grid, and curate the comp pool (reject junk, approve good matches).
3. **Bulk reprice** anything where consensus exceeds current price by a configurable margin — one click pushes new prices to eBay.

Every price change is logged to `data/price_history.json` with its source tag (`user_manual`, `bulk_consensus`, `match_median`, etc.). A standalone Price Radar (`/prices`) exposes the comp database for ad-hoc lookups by title or artist.

The pipeline is: **inventory → enrichment → comp anchoring → live competition → 4-LLM consensus → confidence → action/swipe/bulk → price log**.

---

## Architecture

- **Single Flask app** — `app.py` (~15,500 lines) with Jinja templates in `templates/` (`index.html`, `prices.html`).
- **Data layer** — flat JSON files in `data/` (no DB). 54k-record historical comp index lives in `data/historical_clean.json`.
- **External services** — eBay Trading + Browse API, Anthropic (Claude), OpenAI (GPT-4o), Google (Gemini 2.5 Flash + optional Sheets), xAI (Grok 3).
- **Deploy** — Railway, auto-deploy from `main`. Process defined in `Procfile` (gunicorn).
- **Nightly re-index** — `scripts/nightly_reindex.py` chains `consolidate_all.py` → `clean_historical.py`. Wire as a separate Railway Cron Job service (`0 3 * * *`). Flask picks up fresh data on the next request via mtime check.
- **Auxiliary scripts** — `clean_historical.py`, `deep_clean.py`, `consolidate_all.py`, `build_aliases.py`, `build_clusters.py`, `comp_engine.py` build and maintain the historical DB offline.

```
                 +-----------------------+
   eBay API ---> |   /api/inventory/     |
                 |   full-analytics      |
                 +-----------+-----------+
                             |
     +-----------------------+-----------------------+
     |              |              |                 |
     v              v              v                 v
 enrichment   comp anchoring   action scoring   comp evidence
 (sold/       (historical_    (PROMOTE/         (position,
  supply/      clean.json,     DISCOUNT/         delta_pct,
  promo/       p25/p50/p75)    RELIST/HOLD)     12mo median)
  traffic)          |
                    v
         Live eBay Browse lookup
         (top 15 active comps)
                    |
                    v
         4-LLM consensus (parallel)
         Claude / GPT-4o /
         Gemini 2.5 / Grok 3
         -> {low, recommended, high}
         + confidence 0-100
         + reasoning chain
                    |
        +-----------+------------+-------------+
        |           |            |             |
        v           v            v             v
   Opportunities  Drift      Bulk Consensus  Swipe Mode
   (top 10        Alerts     Reprice         (Comps /
    upsides)      (>=10%     (filter ->       Consensus /
                   drift)     preview ->      Train Comps)
                              apply)
                    |
                    v
            data/price_history.json
            (source: user_manual /
             bulk_consensus /
             match_median / ...)
```

---

## Core features

### 1. Inventory dashboard (`/`)
Backed by `/api/inventory/full-analytics`. Pulls every active listing via eBay Trading, enriches with sold history (`data/sold_history.json`), supply snapshots (`data/supply_snapshots.json`), promo rates (`data/promotions_cache.json`), traffic/impressions, comp evidence (see §5), and a per-item Action Scoring engine returning **PROMOTE / DISCOUNT / RELIST / HOLD**. Response includes a top-level `comp_summary` with coverage + dollar-upside aggregates.

### 2. Smart Price Engine
`calculate_suggested_price()` anchors on comp **p75 x event_multiplier** when there are >= 3 matching comps in the historical DB. Falls back to `base_price x event_multiplier` when comp coverage is thin. Key-date events boost price by tier: MINOR +5%, MEDIUM +15%, MAJOR +25%, PEAK +35%.

### 3. Historical Comp Database
54,000+ deduplicated past sales scraped from WorthPoint, eBay sold listings, and auction archives. Cleaned, deduped, and enriched with `artist`, `work_id`, `colorway`, `signed` flag, `medium`, and `edition_size`. Loaded via `load_historical_clean()` and queried via `lookup_historical_prices(title, artist)`. Rejections from the swipe-mode curation loop (§11) are filtered automatically.

### 4. Price Radar (`/prices`)
Standalone mobile-friendly UI for the comp DB. Search by title or artist; returns median / p25 / p75, per-year trend, top related works, and the full comp list with source, date, condition, and sale price. Backing APIs: `/api/prices/artists`, `/api/prices/search`, `/api/prices/work/<id>`.

### 5. Comp-grounded repricing
`calculate_comp_anchors(title, artist)` returns `{count, median, p75, trailing_12mo_median, signed_only, source_count_all}`. A **signed gate** filters `signed=True` when the title contains "signed". `/api/inventory/full-analytics` attaches the following to every item: `comp_evidence`, `comp_median`, `comp_p75`, `comp_12mo_median`, `comp_count`, `comp_signed_only`, `comp_position` (under/at/over), `comp_delta_pct`. The top-level `comp_summary` aggregates coverage and dollar upside across the store.

### 6. 4-LLM Consensus Review with Price Ranges
`/api/inventory/llm-price-review/<listing_id>` fans out to four models in parallel via `ThreadPoolExecutor`:

| Model   | Provider   | Identifier              |
|---------|------------|-------------------------|
| Claude  | Anthropic  | `claude-sonnet-4-6`     |
| GPT-4o  | OpenAI     | `gpt-4o`                |
| Gemini  | Google     | `gemini-2.5-flash`      |
| Grok    | xAI        | `grok-3`                |

Each model returns `{low, recommended, high, reason, status}`. Consensus is the **per-column median** across valid responses (`consensus.low`, `consensus.recommended`, `consensus.high`). A legacy `consensus_median` scalar is kept for back-compat.

**Live competition** — before calling the models, the endpoint calls `search_ebay()` and feeds the top 15 active listings (price range, median, sample titles) into every prompt so the models price against a real supply snapshot, not just history.

**Per-artist prompt fragments** — `_ARTIST_PROMPT_FRAGMENTS` injects expert context per detected artist: Fairey edition / HPM rules, Death NYC HPM + series flags, KAWS colorway + MedicomToy box rules, Banksy Pest Control authentication, MBW series, Bearbrick sizing and collab premiums.

**Caching** — results stored in `data/llm_price_cache.json` keyed by `(listing_id, comp_median/10)`. Cache invalidates automatically when `comp_median` shifts by >= $10. Pass `?force=1` to bypass. Missing keys or per-model failures degrade gracefully — remaining models still contribute.

### 7. Confidence scoring
Every LLM review returns `confidence_score` (0-100), `confidence_level` (HIGH / MEDIUM / LOW), and a 4-line `reasoning_chain`:

| Component         | Max pts | Signal |
|-------------------|---------|--------|
| Comp density      | 40      | 2 pts per matching comp |
| LLM agreement     | 30      | Penalized by sigma of `recommended` values as a % of median |
| 12mo trend        | 15      | Delta between all-time median and trailing 12mo median |
| Live competition  | 15      | 15 if active eBay listings were found, else 0 |

Bands: HIGH >= 70, MEDIUM 40-69, LOW < 40.

### 8. Opportunities Dashboard
`GET /api/inventory/opportunities` ranks items where `comp_median > your_price` and `comp_count >= 5`. Scored by `upside_dollars * (1 + comp_count/20)`; top 10 returned. Surfaced as a green dashboard card with click-to-swipe deep link.

### 9. Bulk Consensus Reprice
Two-endpoint flow on the Inventory toolbar:

- `GET /api/inventory/bulk-consensus-preview` — filters by `min_upside_pct` (default 10), `min_comp_count` (default 5), `artist` substring. Reads cached LLM consensus; optional `force_llm=1` runs a fresh review for any item missing a cache entry.
- `POST /api/inventory/bulk-consensus-apply` — body `{"items": [{"id", "price", "prev_price?"}]}`. Pushes prices to eBay, logs each as `bulk_consensus` in `data/price_history.json` (or `bulk_consensus_local_dev` when running without a token).

Modal UI: filter inputs, **Re-query LLMs** checkbox for fresh consensus, checkbox-list preview, total upside projection, single "Apply" button.

### 10. Swipe Mode
Inventory toolbar button opens a full-screen card-by-card view with three tabs:

- **Comps** — histogram of comp distribution (your-price bucket highlighted), per-year trend chips, top-10 recent sales.
- **LLM Consensus** — 2x2 model grid with mini range bars (low / recommended / high), confidence badge, "Why?" disclosure surfacing the `reasoning_chain`, and a **Match Consensus** button to push the median recommended.
- **Train Comps** — swipe individual comps left (reject) / right (approve). Feeds the curation endpoints (§11).

Keyboard arrows navigate, touch swipe on mobile, Esc closes.

### 11. Comp curation / training
Three endpoints persist human feedback so `lookup_historical_prices` gets sharper over time:

| Route | Method | Purpose |
|---|---|---|
| `/api/comps/train` | POST | Record `reject` / `approve` / `unmark` on a single comp |
| `/api/comps/train-queue` | GET | Unreviewed comps for an item (`title`, `artist`, `limit`) |
| `/api/comps/train-status` | GET | Approved / rejected / unreviewed counts for an item |

Body for `/api/comps/train`: `{title, artist, action, comp: {name, price, date, source, ...}}`. State persists to `data/comp_curation_rejections.json` and `data/comp_curation_approvals.json`, keyed by `(artist-title signature, sha1 comp_key)`. **Rejections are filtered out of future comp lookups automatically.**

### 12. Price change log + Drift alerts
Every successful `/api/update-price` and every bulk apply appends to `data/price_history.json`:

```
{<listing_id>: [{price, prev_price, at, source}, ...]}
```

Source tags in use: `user_manual`, `bulk_consensus`, `bulk_consensus_local_dev`, `match_median`, `match_p75` (and any free-form label passed to `/api/update-price`).

`GET /api/drift-alerts` flags items where `comp_median` differs from current price by ≥10%. Red/orange dashboard card to visually distinguish from green Opportunities. Each alert includes `last_price_change` and `days_since_change` pulled from the price log.

### 13. Calendar-event pricing engine
Original v1 engine still active inside `_event_multiplier()`. `get_active_events()` + `TIER_BOOSTS` + keyword matching from `data/pricing_rules.json`. Feeds the Smart Price Engine as the event multiplier component.

### 14. Promos / Ad spend management
`/api/promotions` family for per-listing ad rate lookups, AI-suggested rates, and Promoted Listings campaign creation. Cached rates in `data/promotions_cache.json`.

### 15. eBay OAuth flow
Full OAuth 2.0 with refresh-token rotation. Scopes: `sell.inventory`, `sell.marketing`, `sell.account.readonly`, `sell.analytics.readonly`, `sell.fulfillment.readonly`, `sell.negotiation`. Handles token refresh transparently on expiry.

---

## API reference

### Dashboard + inventory
| Route | Method | Purpose | Key params |
|---|---|---|---|
| `/` | GET | Main inventory dashboard UI | — |
| `/api/inventory/full-analytics` | GET | All listings + enrichment + comp anchors + comp evidence + action scores | — |
| `/api/listings` | GET | Raw active listings | — |
| `/api/stats` | GET | Store-level stat rollup | — |
| `/api/alerts` | GET | Underpriced / stale / policy alerts | — |
| `/api/underpriced` | GET | Items below suggested price | — |
| `/api/update-price` | POST | Push revised price to eBay + log to price history | `item_id`, `price`, `source?`, `prev_price?` |
| `/api/drift-alerts` | GET | Items with ≥10% drift between comp_median and your_price | — |
| `/api/listing/price-history/<listing_id>` | GET | Per-listing change history | — |

### LLM consensus + bulk reprice
| Route | Method | Purpose | Key params |
|---|---|---|---|
| `/api/inventory/llm-price-review/<listing_id>` | GET | 4-LLM fan-out, per-column median consensus, confidence score | `?force=1` bypasses cache |
| `/api/inventory/opportunities` | GET | Top 10 pricing upsides (comp_median > your_price, comp_count >= 5) | — |
| `/api/inventory/bulk-consensus-preview` | GET | Filter inventory by upside / comp count / artist; read cached consensus | `min_upside_pct`, `min_comp_count`, `artist`, `force_llm` |
| `/api/inventory/bulk-consensus-apply` | POST | Push approved bulk prices to eBay | Body: `{items: [{id, price}]}` |

### Price Radar (`/prices`)
| Route | Method | Purpose | Key params |
|---|---|---|---|
| `/prices` | GET | Standalone comp search UI | — |
| `/api/prices/artists` | GET | Artist index for autocomplete | — |
| `/api/prices/search` | GET | Title / artist search | `q`, `artist` |
| `/api/prices/work/<id>` | GET | Full comp list + stats for a work_id | — |

### Comp curation / training
| Route | Method | Purpose | Body / params |
|---|---|---|---|
| `/api/comps/train` | POST | Record reject / approve / unmark on a comp | `{title, artist, action, comp}` |
| `/api/comps/train-queue` | GET | Unreviewed comps for an item | `title`, `artist`, `limit=50` |
| `/api/comps/train-status` | GET | Approved / rejected / unreviewed counts | `title`, `artist` |
| `/api/comps/reject` | POST | Legacy per-title rejection (kept for back-compat) | — |
| `/api/comps/rejections` | GET | All legacy rejections | — |
| `/api/comps/rejections/clear` | POST | Wipe legacy rejections | — |

### Calendar + promos
| Route | Method | Purpose |
|---|---|---|
| `/api/calendar` | GET | Active + upcoming pricing events |
| `/api/calendar/unified` | GET | Merged event calendar across sources |
| `/api/promotions` | GET | Per-listing ad rates |
| `/api/promotions/ai-suggest` | POST | LLM ad-rate suggestion |
| `/api/promotions/create-campaign` | POST | Create Promoted Listings campaign |
| `/api/promotions/apply` | POST | Apply campaign to listings |

167 routes total — see `app.py` for the full surface.

---

## Environment variables

| Name | Purpose | Required | Source |
|---|---|---|---|
| `EBAY_CLIENT_ID` | eBay app key | Yes | developer.ebay.com |
| `EBAY_CLIENT_SECRET` | eBay app secret | Yes | developer.ebay.com |
| `EBAY_REFRESH_TOKEN` | OAuth refresh token | Yes | eBay OAuth consent flow |
| `EBAY_DEV_ID` | eBay Trading API dev id | Yes | developer.ebay.com |
| `ANTHROPIC_API_KEY` / `CLAUDE_API_KEY` | Claude `sonnet-4-6` | Optional | console.anthropic.com |
| `OPENAI_API_KEY` | GPT-4o | Optional | platform.openai.com |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini 2.5 Flash | Optional | aistudio.google.com |
| `XAI_API_KEY` / `GROK_API_KEY` | Grok 3 | Optional | x.ai |
| `DATARADAR_SHEET_ID` | Google Sheets pricing rules override | Optional | Google Cloud Console |

LLM keys are all optional — the consensus endpoint gracefully drops any model without a key. With zero LLM keys, the endpoint returns an empty consensus but the rest of the dashboard works.

> **Note on `load_env()`** — earlier versions of the loader only pulled 5 eBay/Sheets keys from `os.environ`, silently dropping all LLM keys on Railway. The current loader reads every key in the table above.

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

**Offline mock mode** — `run_local.py` (not checked in) stubs the eBay client with cached listings from `data/inventory_enriched.json` and pulls LLM keys from `~/jj_shay_takeaways/.env`. Use for live LLM testing + swipe-mode feedback rounds without touching production eBay data. Run with `python3 run_local.py`.

---

## Deployment

Railway auto-deploys from `main`. The `Procfile` runs:

```
web: gunicorn app:app --bind 0.0.0.0:$PORT
```

All env vars live in Railway project variables. No build step — Railway installs `requirements.txt` and boots gunicorn. The `data/` directory ships with the repo, so the 54k-record historical DB is in-process from cold start.

### Nightly comp re-index (Railway Cron Job)

`scripts/nightly_reindex.py` chains `consolidate_all.py` → `clean_historical.py` and rebuilds `data/master_sales.json` + `data/historical_clean.json`. The Flask app auto-picks up the new data on the next request via an mtime check in `load_historical_clean()`.

Wire it up as a separate Railway service:

| Field | Value |
|---|---|
| Service type | Cron Job |
| Root directory | `.` |
| Command | `python3 scripts/nightly_reindex.py` |
| Schedule | `0 3 * * *` (03:00 UTC daily = late evening Pacific) |

---

## Data files

All under `data/`.

| File | Contents |
|---|---|
| `historical_clean.json` | 54k deduped comp records: artist, work_id, title, colorway, signed, medium, edition_size, sold_price, sold_date, source |
| `master_pricing_index.json` | Master artist / work rollup for fast lookup |
| `master_sales.json` | Cross-source raw sales before cleaning |
| `llm_price_cache.json` | Cached 4-LLM consensus payloads keyed by `(listing_id, comp_median/10)` — auto-created on first review |
| `price_history.json` | Per-listing price change log `{price, prev_price, at, source}` |
| `comp_curation_rejections.json` | Swipe-mode rejected comps (artist-title sig → list of sha1 comp keys) — filtered out of future lookups |
| `comp_curation_approvals.json` | Swipe-mode approved comps (same schema) |
| `comp_rejections.json` | Legacy per-title rejection list (pre-swipe) |
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
| `work_aliases.json` | Title-variant alias table (built by `build_aliases.py`) |
| `work_clusters.json` | Clustered work_ids (built by `build_clusters.py`) |
| `kaws_data.json`, `worthpoint_sf_data.json`, `ebay_kaws_active.json` | Source-specific scraped datasets |
| `death_nyc_inventory.csv` | Death NYC artist-specific inventory export |
| `scrape_status.json` | Last-run timestamps for scrapers |
| `active_listings_report.txt` | Last full-store text report |

---

## Recent changes

Latest on `main`:

- `346025b` feat: per-artist prompt fragments + price history log + drift alerts + comp curation swipe (Train Comps tab).
- `71641db` feat: Re-query LLMs checkbox on Bulk Reprice modal (forces fresh consensus for missing cache entries).
- `960e999` feat: Opportunities dashboard card + Bulk Consensus Reprice (preview + apply) + confidence scoring with reasoning chain.
- `f61985a` feat: `/api/inventory/llm-price-review` returns `{low, recommended, high}` ranges + pulls top 15 live eBay listings into every prompt.
- `9c3301f` fix: forward `comp_evidence` fields from inventory loop into the enhanced dict so UI gets real numbers.
- `6194a21` chore: switch Gemini model from `2.0-flash` to `2.5-flash`.
- `6c7455b` fix: Grok API — `grok-2-latest` retired, switched to `grok-3`, accept `GROK_API_KEY` env var.
- `3ada85d` feat: 4-LLM consensus price review + swipe-mode inventory card UI.
- `144c3b6` feat: wire Price Radar comps into inventory repricing + UI (`comp_evidence`, `comp_position`, `comp_delta_pct`).
- `9a37215` feat: `/prices` — standalone historical price database UI.

---

## Roadmap

- **Approval-weighted comps** — let swipe-approved comps count 1.5-2x in lookup medians rather than just filtering rejections.
- **Per-artist confidence calibration** — tune the 40/30/15/15 weights per detected artist based on historical accuracy.
- **Drift alert notifications** — push Slack / email when a new listing crosses the ≥10% drift threshold, not just surface it on the dashboard.
- **Additional data sources** — Heritage Auctions, Christie's, Sotheby's, Phillips into the nightly re-index.
- **Per-artist trend detection** — breakout / cooling signal wired into action scoring.

---

## License

MIT. See [LICENSE](LICENSE).
