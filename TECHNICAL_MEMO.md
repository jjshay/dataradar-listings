# DATARADAR — Technical Memo

**Author:** JJ Shay
**Date:** 2026-04-24
**Audience:** Senior engineer / technical DD reviewer
**Repo:** https://github.com/jjshay/dataradar-listings
**Prod:** https://web-production-15df7.up.railway.app

---

## 1. TL;DR (one page)

DATARADAR is a single-process Flask app (gunicorn on Railway) that wraps a personal eBay art-reseller inventory with a 54k-record comparable-sales database, a 4-LLM consensus pricing endpoint, and a swipe-mode operator UI. State lives in ~20 JSON files under `data/`. No database, no queue, no framework on the frontend — one `app.py` (15,483 LOC) and one `index.html` (5,777 LOC) carry the whole application. Deployed from GitHub `main` with a single `gunicorn app:app` process and a separate Railway cron service for nightly comp re-index.

**Interesting technical bits:**
- 4-LLM consensus (Claude Sonnet 4.6, GPT-4o, Gemini 2.5 Flash, Grok-3) fanned out via `ThreadPoolExecutor(max_workers=4)` with graceful `not_configured`/`error`/`parse_error`/`ok` statuses, per-column median consensus (low / recommended / high), cache keyed by `{listing_id}:{round(comp_median/10)*10}` so new comp data naturally invalidates.
- 0-100 confidence score combining four signals (comp density, LLM stdev, 12-month trend alignment, live eBay competition) with an explainable reasoning chain emitted to the UI.
- O(N) in-memory comp scan over 30 MB of JSON — cold load ~300 ms, warm scans ~50 ms thanks to pre-computed `title_words` in the clean record and a global mtime-gated cache.
- Per-artist prompt fragments (`_ARTIST_PROMPT_FRAGMENTS` — `app.py:4855`) injected into the shared LLM prompt, so Banksy gets a Pest-Control authentication nudge while Bearbrick gets a size/colorway nudge.
- Swipe-mode operator UX (`templates/index.html:4619`) for fast human curation of comps; rejections written to `data/comp_curation_rejections.json` and consulted on every comp lookup inside `lookup_historical_prices()`.

**Debt / risk:**
- Single-file monolith: `app.py` is 15,483 lines, 167 routes; no tests, no CI, no lint gate on push. Type errors exist (pyright flags several non-crashing issues).
- Concurrency model assumes one Railway replica — `llm_price_cache.json`, `price_history.json`, and curation files can race if scaled horizontally.
- Legal cleanliness of the 54k-record comp corpus (WorthPoint/Heritage/eBay/Artsy scrapes consolidated by `consolidate_all.py`) is an open DD question. Nothing is instrumented to re-prove provenance at query time.

---

## 2. System Architecture

**Runtime.** Single Python 3.9+ Flask process served by gunicorn on Railway. Stateless compute, state on the attached Railway volume (JSON files under `data/`). Procfile (`Procfile:1`): `web: gunicorn app:app --bind 0.0.0.0:$PORT`.

**Why still one file.** `app.py` at 15,483 lines is not accidental — it is the explicit product of optimizing for iteration speed with a single operator (solo dev). JSON state means no ORM, no migrations, no schema versioning, and no boundary between "route" and "helper" worth enforcing. Grep is the IDE. When this breaks: (a) a second contributor joins and merge conflicts become a tax, (b) a subsystem needs to be mocked for testing, or (c) route count (currently 167) outgrows cognitive load.

**Routes.** 167 `@app.route` decorators in `app.py`. Frontend is rendered server-side from a single `templates/index.html`; most routes are `/api/...` JSON endpoints consumed by vanilla-JS fetch calls.

**Block diagram.**

```
┌──────────────────────────┐      ┌──────────────────────────┐
│ eBay Trading API (XML)   │──┐   │ eBay Browse API (JSON)   │
│ GetMyeBaySelling         │  │   │ item_summary/search      │
└──────────────────────────┘  │   └──────────────────────────┘
                              │                │
                              ▼                ▼
                  ┌─────────────────────────────────────┐
                  │  EbayAPI.get_all_listings()         │
                  │  (app.py:191, 5-min cache)          │
                  └─────────────────────────────────────┘
                              │
                              ▼
  ┌──────────────────────────────────────────────────────────┐
  │  Flask routes (gunicorn) — app.py (15,483 LOC)           │
  │                                                          │
  │  /api/inventory/full-analytics ──▶ get_full_inventory_   │
  │                                    analytics (app.py:4283)│
  │  /api/inventory/llm-price-review/<id> ──▶ app.py:5161    │
  │  /api/update-price ──▶ app.py:2485                       │
  │  /api/comps/train  ──▶ app.py:5912                       │
  │  /api/drift-alerts ──▶ app.py:5826                       │
  │  /api/inventory/opportunities ──▶ app.py:5417            │
  └──────────────────────────────────────────────────────────┘
                              │
                 ┌────────────┼────────────────────────┐
                 ▼            ▼                        ▼
        ┌───────────────┐  ┌───────────────┐   ┌──────────────────────┐
        │ Comp lookup   │  │ Enrichment    │   │ LLM fan-out          │
        │ (30 MB cache, │  │ pipeline      │   │ ThreadPoolExecutor   │
        │  O(N) scan)   │  │               │   │  max_workers=4       │
        │ app.py:2176   │  │ app.py:4283   │   │ app.py:5257          │
        └──────┬────────┘  └──────┬────────┘   └──────┬───────────────┘
               │                  │                   │
               ▼                  ▼                   ▼
        ┌──────────────────────────────────────────────────────┐
        │  data/*.json  (~20 files, ~188 MB on disk)           │
        │   historical_clean.json (30 MB, 54k records)         │
        │   master_sales.json (76 MB, pre-clean)               │
        │   llm_price_cache.json  auto_enrichment.json         │
        │   price_history.json   supply_snapshots.json  …      │
        └──────────────────────────────────────────────────────┘
                              │
                              ▼
             ┌──────────────────────────────────┐
             │  templates/index.html (5,777 LOC)│
             │  vanilla JS, Chart.js, dark CSS  │
             │  Swipe-mode modal,  Train tab    │
             └──────────────────────────────────┘
```

**Component table.**

| File | Role | Approx LOC |
|---|---|---|
| `app.py` | Flask app, all routes, data-access helpers, LLM wrappers, pricing logic | 15,483 |
| `templates/index.html` | Full SPA + vanilla-JS + Chart.js | 5,777 |
| `comp_engine.py` | Pluggable category-specific comp scoring engine (loaded opportunistically) | ~700 |
| `consolidate_all.py` | Merges ~11 raw scrape dumps into `data/master_sales.json` | ~250 |
| `clean_historical.py` | Filters `master_sales.json` → `data/historical_clean.json` | ~300 |
| `scripts/nightly_reindex.py` | Railway cron entrypoint; runs consolidate + clean | ~80 |
| `scripts/build_artist_summaries.py` | Pre-computes per-artist summaries | ~60 |
| `build_aliases.py` / `build_clusters.py` / `deep_clean.py` | Offline data-prep scripts | ~200 each |
| `run_local.py` | **Not committed** — local dev mock with `/Users/johnshay/…` env path | — |

Dependencies (`requirements.txt`): `Flask`, `requests`, `python-dotenv`, `google-api-python-client`, `google-auth`, `google-auth-oauthlib`, `gunicorn`. Seven deps, all first-order. No ORM, no Celery, no Redis, no Postgres driver.

---

## 3. Data Layer

All persistence is JSON under `data/`. No database.

| File | Size | Role | Mutability |
|---|---|---|---|
| `data/historical_clean.json` | 30 MB | 54k cleaned comp records (post-consolidation + filter) | Written by `scripts/nightly_reindex.py`, read by app via `load_historical_clean()` (`app.py:1799`) |
| `data/master_sales.json` | 76 MB | Pre-clean consolidated sales | Intermediate artifact, output of `consolidate_all.py` |
| `data/llm_price_cache.json` | ~few KB | Consensus-pricing cache keyed `{listing_id}:{round(comp_median/10)*10}` | Written per LLM call at `app.py:5384` via `_save_llm_cache()` (`app.py:4793`) |
| `data/price_history.json` | small | Append-only log of every price change | Written by `/api/update-price` (`app.py:2485`) + bulk reprice via `_append_price_change()` (`app.py:5799`) |
| `data/comp_curation_rejections.json` | small | Operator rejects keyed by `(_item_signature, sha1(name\|price\|date))` | Written by `POST /api/comps/train` (`app.py:5912`) |
| `data/comp_curation_approvals.json` | small | Operator approves | Same as above |
| `data/supply_snapshots.json` | 246 KB | Daily watcher + supply counts per item | Enrichment flow |
| `data/watcher_history.json` | 28 KB | Per-listing watcher trendline | Enrichment flow |
| `data/auto_enrichment.json` | 188 KB | Cached per-listing enrichment (category, market comps, etc.) | Enrichment flow |
| `data/inventory_enriched.json` | 1.3 MB | Full enriched inventory snapshot (also used by `run_local.py` as mock) | Periodically rewritten |
| `data/work_clusters.json` | 9.6 MB | Offline clustering output | Built by `build_clusters.py` |
| `data/work_aliases.json` | 6.5 MB | Canonical-title → variant-title aliases | Built by `build_aliases.py` |
| `data/live_deals_cache.json` | 6 MB | Marketplace scout cache | Background deal-finder |
| `data/promotions_cache.json` | 68 KB | eBay promotions API cache | Promotions route |
| `data/deal_targets.json` | 24 KB | Watchlist of target listings | Manual |
| `data/art_deals.json` / `data/kaws_data.json` / `data/ebay_kaws_active.json` / `data/worthpoint_sf_data.json` | 556 KB / 22 MB / 760 KB / 810 KB | Scraper artifacts / raw pulls | Periodic |

**Rationale for JSON-over-SQLite/Postgres.** Zero-setup, easy visual debugging (`jq .`), atomic-enough writes at current scale (≤200 active eBay listings, ~54k comps read-only in practice), and state has to survive Railway redeploys via the volume — JSON serializes natively. Cold startup loads `historical_clean.json` once per Python process and caches globally (`_historical_clean`, `app.py:1808`) with mtime-invalidation, so nightly re-index is picked up without restart.

**When this breaks:**
1. Two Railway replicas running simultaneously → concurrent writes to `llm_price_cache.json`, `price_history.json`, or the curation files can last-writer-wins and silently lose data.
2. Any query that needs a secondary index (e.g. "all comps by artist in the last 90 days priced >$500") currently does a linear scan.
3. State >500 MB or comps >500k → JSON parse latency dominates (30 MB → ~300 ms; 500 MB → multiple seconds blocking the worker).

---

## 4. The Comp Pipeline

**Sources → consolidation → cleaning → runtime lookup → curation.**

1. **Sources.** `consolidate_all.py` merges ~11 raw scrape dumps (WorthPoint, eBay sold CSVs, Artsy, MutualArt, Heritage, etc.) into `data/master_sales.json` (76 MB).
2. **Cleaning.** `clean_historical.py` filters to the `SELLING_ARTISTS` set, enforces signed-required rules, strips junk titles, normalizes artist labels, and pre-computes a `title_words` token set per record. Output: `data/historical_clean.json` (30 MB, 54k records).
3. **Runtime lookup.** `lookup_historical_prices(title, artist, limit)` at `app.py:2176`:
   - Tokenizes query title; drops a noise set (`{'the','a','an',... 'shepard','fairey','death','nyc','banksy','kaws',...}`) defined inline at `app.py:2178`.
   - If `artist` is supplied, applies an artist gate with a loose substring alias (e.g. "fairey" ↔ "obey").
   - For each of ~54k records, computes `len(title_words & rec_words)` set-overlap; ≥1 word overlap passes.
   - Looks up curation action via `_get_curation_index()` (`app.py:2126`) keyed on `(_item_signature(title, artist), _comp_key(rec))` — `reject` is dropped, `approve` flags the comp with `approved: true`.
   - Sorts by overlap desc, then date. Returns up to `limit` results, each with `{name, price, date, source, url, signed, medium, approved, comp_key, _overlap}`.
   - Cold path: 30 MB JSON parse on first call (~300 ms on Railway standard hobbyist tier). Warm path: ~50 ms (iteration + set-op over 54k rows, Python dicts).
4. **Anchors.** `calculate_comp_anchors(title, artist)` at `app.py:1410` pulls up to 500 comps via `lookup_historical_prices`, applies the signed gate (`app.py:1423` — if "signed" in the title, require `signed=True` comps; fall back to the full pool if fewer than 3 exist), and returns `{count, median, p75, trailing_12mo_median, signed_only, source_count_all}`. Returns `None` if fewer than 3 usable comps.
5. **Nightly re-index.** `scripts/nightly_reindex.py` (`nightly_reindex.py:1`) runs `consolidate_all.py` then `clean_historical.py` via subprocess. Flask auto-picks up the new `historical_clean.json` via the mtime check at `app.py:1807`. Configured as a separate Railway cron service at `0 3 * * *` UTC (daily 8 PM Pacific).
6. **Comp engine v3 (`comp_engine.py`).** A newer, modular category-specific comp scorer exists but is not universally wired into the runtime lookup path — used opportunistically. Pyright flags an unresolved-import issue, but runtime has fallback paths so it never crashes.

---

## 5. The Repricing Engine

Entry: `calculate_suggested_price(base_price, title, artist)` at `app.py:1496`.

**Algorithm.**

```python
multiplier, _ = _event_multiplier(title)        # app.py:1483
anchors = calculate_comp_anchors(title, artist) # app.py:1410
if anchors and anchors['count'] >= 3:
    return round(anchors['p75'] * multiplier, 2)
return base_price * multiplier
```

That's the whole decision: if we have ≥3 comps, price off the 75th percentile; otherwise ride the calendar event boost on whatever base price the listing came in at.

**Why p75 not median.** The operator is a reseller — a realized sale at median is break-even-ish; a realized sale at p75 is where the margin lives. Median anchors the centroid of the distribution; p75 anchors the ceiling of what the market will pay for a comparable item on a good day. Over time, this reads the ceiling, not the average.

**Signed gate.** `calculate_comp_anchors` honors a structural feature of this market: "signed" or "signed & numbered" is a binary premium worth ~2-3x on most artists (Shepard Fairey, Death NYC) and ~3-5x on Banksy. If the title contains "signed", comps are filtered to `signed=True` records; if fewer than 3 signed comps exist, it falls back to the full pool with `signed_only=False` flagged in the output for the UI to render a warning.

**Event multiplier.** `_event_multiplier(title)` (`app.py:1483`) reads active calendar events (artist birthdays, death anniversaries, exhibition openings, etc.) via `get_active_events()` and picks the max-boost tier matching any keyword in the title. Tiers: MINOR +5%, MEDIUM +15%, MAJOR +25%, PEAK +35% (constants at `app.py:102`).

**Detailed variant.** `calculate_suggested_price_detailed` (`app.py:1506`) returns the full evidence payload — the suggested price, the multiplier applied, the source ("comp_p75" vs "base_fallback"), the anchor stats — so the UI can explain *why* we recommended $X.

---

## 6. The 4-LLM Consensus Endpoint

Endpoint: `GET /api/inventory/llm-price-review/<listing_id>` at `app.py:5161`. This is the most interesting subsystem in the codebase — it is also the most expensive per call.

**Flow end-to-end.**

1. **Resolve listing.** `ebay.get_all_listings()` (`app.py:191`) returns the (cached, 5-min) inventory; we pick the match by `listing_id` (`app.py:5178`).
2. **Detect artist.** `_detect_artist(title)` (`app.py:5137`) substring-matches the title against a hardcoded artist vocabulary — Shepard Fairey / Death NYC / Banksy / KAWS / Mr. Brainwash / Bearbrick. Leaks: no artist → `default` path.
3. **Comp anchors.** `calculate_comp_anchors` (see Section 5). Computes `p25` inline for the UI (`app.py:5195`) since the base function doesn't return it.
4. **Cache check.** Cache key = `f"{listing_id}:{round(comp_median/10)*10}"`. On cache hit, return the cached full response (`app.py:5215`). `?force=1` bypasses.
5. **Recent comps.** `lookup_historical_prices(title, artist, limit=5)` → top 5 recent sales for the prompt.
6. **Live competition.** `search_ebay(title, max_price=3x, min_price=0.3x, limit=15)` via eBay Browse API (`app.py:492`) → list of currently-live competing listings. Price-banded at 0.3x–3x of our own price to keep the search tight.
7. **Prompt.** `_build_llm_prompt(listing, comp_stats, recent_comps, active_comps)` at `app.py:4928` composes a shared prompt. Includes the per-artist fragment via `_artist_fragment(artist)` (`app.py:4909`), which looks up `_ARTIST_PROMPT_FRAGMENTS` (`app.py:4855`). Fragments are hand-tuned: Banksy → Pest Control COA mandatory; KAWS → colorway + MedicomToy box context; Bearbrick → size (400% / 1000%) + collab context; Fairey → OBEY series + HPM multiplier; Death NYC → numbered/signed or suspected knockoff.
8. **Fan-out.** `ThreadPoolExecutor(max_workers=4)` at `app.py:5257` dispatches four calls concurrently. Each wrapper returns `{low, high, recommended, price, reason, status}` where `status ∈ {ok, error, parse_error, not_configured}`:

    | Model | Function | Endpoint | Auth | Line |
    |---|---|---|---|---|
    | Claude Sonnet 4.6 | `_llm_claude` | `api.anthropic.com/v1/messages` | `x-api-key` | `app.py:5019` |
    | OpenAI GPT-4o | `_llm_openai` | `api.openai.com/v1/chat/completions` | `Authorization: Bearer` | `app.py:5053` |
    | Gemini 2.5 Flash | `_llm_gemini` | `generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent` | `?key=…` query param | `app.py:5086` |
    | Grok-3 | `_llm_grok` | `api.x.ai/v1/chat/completions` (OpenAI-compatible) | `Authorization: Bearer` | `app.py:5112` |

    Each wrapper: 20 s timeout, try/except for `not_configured` (missing key returns immediately), `error` (non-200 or exception), `parse_error` (LLM returned text but no parseable JSON).

9. **Parse.** `_parse_llm_json(text)` (`app.py:4811`) extracts the first JSON object matching the regex `r'\{[^{}]*("recommended"|"price")[^{}]*\}'` — this is brittle and intentionally so, small surface area to debug. Coerces to int via `_coerce_int()` and enforces `low ≤ recommended ≤ high`. Legacy single-`price` responses are upgraded to synthetic low/high at 0.9x / 1.1x (`app.py:4830`).

10. **Consensus.** Per-column median across `status == 'ok'` models:

    ```python
    consensus = {
      'low':         _median([m['low']         for m in ok_models]),
      'recommended': _median([m['recommended'] for m in ok_models]),
      'high':        _median([m['high']        for m in ok_models]),
    }
    # Back-compat alias
    consensus_median = consensus['recommended']
    ```

    Per-column (not single-price) is the key design call: it preserves *range disagreement*, which is diagnostic — if Claude's high is $800 and GPT-4o's high is $400, the operator sees the disagreement in the UI rather than getting a smoothed-away single number.

11. **Confidence score (0-100).** Computed at `app.py:5309` from four components with an explainable reasoning chain emitted to the UI:

    | Component | Max pts | Logic |
    |---|---|---|
    | Comp density | 40 | `min(40, comp_count * 2)` |
    | LLM agreement | 30 | `max(0, 30 * (1 - min(1, σ/μ * 3.5)))` where σ = population stdev of `recommended` across ok models |
    | 12-month trend alignment | 15 | `max(0, 15 * (1 - min(1, |trailing12mo - allTime| / allTime * 3)))` |
    | Live competition present | 15 | 15 if any active_comps, 0 otherwise |

    Bucketed: ≥70 HIGH, ≥40 MEDIUM, else LOW. The reasoning chain (list of four human-readable strings, `app.py:5357`) is emitted in the response so the UI can render "Confidence: 82 (HIGH) — comp density 27 comps → +40 pts, LLM σ=$18 across 4 models → +28 pts, 12mo trend aligned → +12 pts, 6 active listings → +15 pts."

12. **Cache write.** Full response (including `cached_at` ISO timestamp) written to `data/llm_price_cache.json` at `app.py:5384`.

**Key design decisions.**
- **Per-column median, not single price.** Captures disagreement. Critical when one model outlier-recommends 2x.
- **Cache-by-comp-bucket, not by time.** A price change that doesn't meaningfully move `comp_median` (bucketed to the nearest $10) reuses the cached response. A re-index that shifts `comp_median` by ≥$10 naturally invalidates. No TTL needed.
- **Graceful missing-key fallback.** Three of four LLMs not configured → the shelf still works with one model; the consensus is that single value, confidence gets 0 pts on the LLM-agreement component, but the system does not crash or fall over.
- **Parallelism = wall-clock of slowest model.** ThreadPoolExecutor hides Python's GIL overhead here because each task is I/O-bound in `requests.post()`. Typical total: 5–10 s (Gemini is usually the long pole; Claude ~3 s; GPT-4o ~3 s; Grok ~4 s). Sequential would be 15–30 s.
- **Cost.** ~$0.01–$0.02 per uncached call × effective ~5% cache-miss rate on the active inventory = ~$0.001/item/month at current scale. Still negligible.

**The `load_env` bug (historical, resolved).** Prior to commit `f61985a` ("LLM price review returns ranges + pulls live eBay competition"), `load_env()` (`app.py:68`) only pulled the 5 eBay + sheets keys from `os.environ`, dropping every LLM key on Railway. Symptom: all four model wrappers silently returned `status: not_configured` in production because `ENV.get('ANTHROPIC_API_KEY')` was empty even though Railway had it set. Current implementation at `app.py:80-86` includes `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `XAI_API_KEY`, `GROK_API_KEY`. Lesson: a soft-failure default (return `not_configured` instead of raising) hides config bugs from CI that has no CI. A smoke test that asserts each of the four LLMs responds `status: ok` on a canary listing would have caught it in 5 minutes.

---

## 7. Frontend

One file: `templates/index.html` (5,777 lines, vanilla JS + Chart.js, dark theme via CSS variables `--bg`, `--card`, `--accent`, `--dim`, `--purple`, etc.).

**Tabs.** Dashboard / Inventory / Prices / Promos / Artworks. Each tab lazy-loads its data on first click. Client state lives in module-scope `let` variables:

- `invData` (`index.html:871`) — cached `/api/inventory/full-analytics` response, deduplicated so tab-switches don't re-fetch.
- `swipeItems`, `swipeIdx` (`index.html:4612`) — swipe-mode deck state.
- `llmReviewCache` (`index.html:4615`) — client-side LLM consensus dedup keyed by listing_id.
- `comp_train_state` — per-item curation state for the Train Comps tab.

**Swipe Mode.** `openSwipeMode()` at `index.html:4619` opens a full-screen card modal with three tabs:

1. **Comps** — recent historical sales for the item (`/api/inventory/llm-price-review/<id>` payload's `comp_stats` + evidence).
2. **LLM Consensus** — full 4-LLM table with per-model low/rec/high, confidence score + reasoning chain.
3. **Train Comps** — per-comp approve/reject swipe. `POST /api/comps/train` (`app.py:5912`) writes to `data/comp_curation_rejections.json` or `data/comp_curation_approvals.json`.

**Gestures.** Pointer events (`pointerdown` at `index.html:4683`) for swipe, keyboard shortcuts: `←`/`→` for item nav; `J` reject / `L` approve / `U` undo when on the Train tab.

**Why no framework.** 5,777 lines is manageable; React/Vue adds a build step (currently there is no build step at all — the HTML is served directly from `templates/`), a bundle, and a learning curve. Server-driven initial state + fetch-on-click works. When this breaks: shared components across multiple views, or a team that doesn't want to touch raw DOM.

---

## 8. Deployment

- **Host.** Railway, auto-deploy from GitHub `main`.
- **Procfile** (`Procfile:1`): `web: gunicorn app:app --bind 0.0.0.0:$PORT`. No worker config, default threading.
- **Env.** `load_env()` (`app.py:68`) loads `.env` if present, then overlays `os.environ` for a whitelisted set of keys. Railway only sets env vars; `.env` is for local dev.
- **Nightly re-index.** Separate Railway cron service running `python3 scripts/nightly_reindex.py` at `0 3 * * *` UTC. Logs to Railway console.
- **Local dev.** `python3 run_local.py` (uncommitted) — loads real LLM keys from `/Users/johnshay/jj_shay_takeaways/.env` and mocks eBay from `data/inventory_enriched.json`. Leaks a hard-coded user path.
- **Volumes.** Railway persistent volume mounted at `data/`. Survives redeploys. NOT survived: multi-replica scaling without explicit volume sharing (and JSON files don't tolerate concurrent writes anyway).

---

## 9. Known Debt

Honest list. Each item is real, not theoretical.

1. **~15,500 lines in one file.** `app.py:1` through `app.py:15483`. Grep is fine today. Second contributor will feel it on day one.
2. **Type errors (pyright).** Several non-crashing issues: `search_ebay` float/int mismatch, an unbound `artist` variable around `app.py:4492`, a `max()` overload issue around `app.py:4614`. All smell-bad, none have produced prod incidents.
3. **No tests.** Zero test harness. Every change is verified by `curl` + manual click-through on the staging Railway URL.
4. **No CI.** No lint, type-check, or smoke test on push. The `load_env` LLM-key bug shipped to prod because nothing caught it — four silently-disabled LLMs until someone hit the endpoint and noticed all `status: not_configured`.
5. **Cache coherence on horizontal scale.** Two Railway replicas writing `llm_price_cache.json`, `price_history.json`, or either curation file would last-writer-wins without file locking. Today we run one replica. Scaling is a correctness problem, not just a cost one.
6. **`comp_engine.py` imports flagged unresolved.** Pyright flags an unresolved import; runtime has fallback paths so it doesn't crash. Tech debt masked by graceful degradation.
7. **Hardcoded paths in `run_local.py`.** `/Users/johnshay/jj_shay_takeaways/.env`. Fine for solo dev. Blocks anyone cloning the repo clean.
8. **eBay OAuth refresh is hand-rolled.** `EbayAPI.get_access_token()` (`app.py:125`) manages a 2-hour token with a 5-minute jitter buffer. One place to break silently under edge conditions (clock skew, partial response body, eBay response-shape drift).
9. **No CDN for dashboard assets.** `index.html` is 5,777 lines of inline CSS + JS served from Flask every page load. Fine at current traffic (single operator), not at 10x.
10. **Comp corpus provenance.** No per-record source audit. `consolidate_all.py` merges ~11 raw dumps; a cleaned record loses the upstream licensing context.

---

## 10. Performance Characteristics

Measured on Railway hobby tier (single replica, no warm-up) with ~200 active listings and 54k comp records.

| Endpoint | Cold | Warm | Hot path |
|---|---|---|---|
| `/api/inventory/full-analytics` (`app.py:4283`) | 8–15 s | ~3 s | Per-listing comp scan × 200 listings; each scan = `lookup_historical_prices` (~50 ms warm) |
| `/api/inventory/llm-price-review/<id>` (`app.py:5161`) | ~7 s (4 LLMs round-trip) | <100 ms (cache hit) | `ThreadPoolExecutor` wall-clock = slowest model (usually Gemini at 5–10 s) |
| `/api/drift-alerts` (`app.py:5826`) | ~3 s | ~3 s | Reuses analytics items via Flask test-client internal call (`_fetch_analytics_items`, `app.py:5398`) |
| `/prices` search | <200 ms (artist lookup) | <500 ms (full stats + comps) | `lookup_historical_prices` warm |
| eBay inventory load (`ebay.get_all_listings`, `app.py:191`) | 5–10 s (paginated XML) | ~0 ms (5-min cache) | Trading API pagination |

**Internal Flask test-client pattern.** `_fetch_analytics_items` at `app.py:5398` calls `/api/inventory/full-analytics` via `app.test_client()` to reuse the enrichment pipeline from a different route. Pragmatic, but doubles request overhead when the cache is cold.

---

## 11. Scaling Considerations

**What breaks at 10x.**

- **Comp DB in-memory × replicas.** 30 MB × 10 Railway replicas = 300 MB of duplicated state. Manageable. But O(N) search over 54k records is already the hot path; at 500k records it becomes a bottleneck.
- **JSON file writes.** No file locking on `llm_price_cache.json`, `price_history.json`, curation files. Concurrent replicas = races. Single-replica today, but any horizontal scale requires swapping to SQLite or Postgres first.
- **LLM cost.** $0.02/item × 2,000 items × 10 concurrent users = ~$400/mo. Still cheap, no longer trivial.
- **eBay API rate limits.** Trading API quota is per-user (the Gauntlet Gallery account). Browse API quota is per-app. A multi-tenant deployment would need per-user Trading API auth chains.
- **Static assets.** `index.html` at 5,777 lines served inline from Flask on every page load = full transfer per page view. Fine at current scale, not at 10k DAU.

**What to refactor first (in order).**

1. **Move `llm_price_cache.json` + `price_history.json` to SQLite.** Both are append-mostly / read-rarely; SQLite gives concurrency + transactions while still being file-based (survives a plain Railway volume). ~1 day of work, changes <200 LOC.
2. **Async workers for enrichment.** `get_full_inventory_analytics` (`app.py:4283`) does per-listing comp lookups serially. Move to `ThreadPoolExecutor` or Celery; 3-5x latency win on cold `/api/inventory/full-analytics`.
3. **Per-user auth + multi-tenant state.** Every JSON read needs a namespace prefix; every LLM key has to be BYOK. ~1-2 weeks if done cleanly.
4. **Split `app.py`.** Not for scale — for the second contributor. Natural splits: `routes/inventory.py`, `routes/prices.py`, `routes/llm.py`, `data/store.py`, `ebay/api.py`, `llm/consensus.py`. Pure mechanical refactor, no logic change.
5. **CI (lint + smoke test) on push.** GitHub Actions, 20 minutes to set up. Would have caught the `load_env` bug.

---

## 12. Open Questions (for a DD reviewer to ask)

- **Comp corpus legality.** The 54k records in `historical_clean.json` are consolidated from WorthPoint, Heritage Auctions, Artsy, MutualArt, eBay sold CSVs, etc. via `consolidate_all.py`. What are the ToS implications of each source? Is there a clean-scrape audit trail? Could the corpus be rebuilt on a defensible licensing footing?
- **Realized-margin lift.** What is the measured realized-margin improvement from applying consensus pricing vs. the prior heuristic? We have `price_history.json` (every price change) but no join against sold-price outcomes. Not instrumented.
- **Multi-tenant refactor effort.** See Section 11, item 3. Rough estimate 1-2 weeks; rigorous estimate needs a full namespace-audit of every JSON read/write.
- **eBay API renewal risk.** The `sell.inventory` and `sell.marketing` OAuth scopes are stable today; eBay has deprecated scopes before. What is the blast radius if a scope is renamed or retired?
- **LLM model-drift risk.** `claude-sonnet-4-6`, `gpt-4o`, `gemini-2.5-flash`, `grok-3` — all hardcoded strings in `app.py`. No config, no feature-flag rotation. What happens when `claude-sonnet-4-6` is retired?

---

## 13. File Map (quick reference)

- `app.py` — single Flask service (15,483 LOC, 167 routes)
- `templates/index.html` — single-page frontend (5,777 LOC, vanilla JS + Chart.js)
- `scripts/nightly_reindex.py` — comp rebuild cron entrypoint
- `consolidate_all.py` — merges ~11 scrape dumps → `data/master_sales.json`
- `clean_historical.py` — filters + normalizes → `data/historical_clean.json`
- `comp_engine.py` — category-specific comp scoring engine (~700 LOC, pluggable, import-flagged by pyright but runtime-safe)
- `build_aliases.py` / `build_clusters.py` / `deep_clean.py` — offline data-prep
- `run_local.py` — local dev mock (not committed; leaks hardcoded `/Users/johnshay/…` path)
- `Procfile` — `web: gunicorn app:app --bind 0.0.0.0:$PORT`
- `requirements.txt` — Flask, requests, python-dotenv, google-api-python-client, google-auth, google-auth-oauthlib, gunicorn (7 deps)
- `data/*.json` — all state (≈188 MB on disk; `historical_clean.json` 30 MB, `master_sales.json` 76 MB are the two largest)
- `README.md` — user-facing overview
- `STRATEGY.md` — product strategy
- `DATARADAR_Strategic_Overview.pptx` — 14-slide deck (see commit `fa8d5b9`)

---

## 14. Summary

This is a small-team production app with a real moat (the 54k curated-and-gated comp corpus plus the 4-LLM consensus layer with per-artist prompt tuning) and real debt (one 15k-line file, zero tests, no CI, single-replica JSON persistence). The debt is manageable and mostly mechanical to retire — SQLite for hot cache files, split `app.py` on natural seams, bolt on CI — none of it requires a rewrite. The moat is where the value lives: the comp corpus is not easy to reconstruct from scratch, and the consensus+confidence scoring is a product surface competitors cannot replicate without building the same data layer first. Fund / join / acquire decision turns primarily on comp-corpus provenance (Section 12, item 1) and realized-margin instrumentation (item 2), not on the codebase shape.
