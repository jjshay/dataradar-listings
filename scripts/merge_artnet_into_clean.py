#!/usr/bin/env python3
"""
Merge Artnet PDF records into the existing historical_clean.csv (and .json).

This is a SAFE merge — it preserves all 59,442 existing rows and APPENDS the
new Artnet records (after deduplication). Unlike consolidate_all.py, it does
NOT rebuild from raw sources (which aren't in this repo).

Inputs:
  - data/historical_clean.csv  (existing 59,442 rows, $3K capped)
  - data/artnet_data.json      (output of scripts/parse_artnet_pdfs.py — 494 rows, no cap)

Outputs:
  - data/historical_clean.csv  (existing rows + new Artnet rows)
  - data/historical_clean.json (regenerated from updated CSV)

Backup: data/historical_clean.csv.bak (created if it doesn't exist)
"""
import csv, json, os, shutil
from collections import Counter
from datetime import datetime

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_DIR, 'data')
CSV_PATH = os.path.join(DATA_DIR, 'historical_clean.csv')
JSON_PATH = os.path.join(DATA_DIR, 'historical_clean.json')
ARTNET_PATH = os.path.join(DATA_DIR, 'artnet_data.json')
BACKUP_PATH = CSV_PATH + '.bak'

CSV_COLS = [
    'artist', 'name', 'work_id', 'canonical_work', 'price', 'date', 'source',
    'signed', 'numbered', 'medium', 'colorway', 'height', 'width', 'url',
    'artwork_year', 'authentication', 'category', 'condition',
    'edition_band', 'edition_number', 'edition_size', 'framed', 'is_ap',
]
EXTRA_COLS = ['_artnet_lot', '_artnet_sale', '_artnet_currency',
              '_artnet_est_low', '_artnet_est_high', '_artnet_sold']

import re

def slugify(s):
    return re.sub(r'[^a-z0-9]+', ' ', (s or '').lower()).strip()

def to_csv_row(rec):
    """Convert one parsed Artnet record into the CSV schema."""
    title = rec.get('name', '')
    price = rec.get('price', 0) or 0
    artist = rec.get('artist', '')
    # Date already in YYYY-MM-DD
    date = rec.get('date', '')

    # Colorway from trailing parens
    colorway = ''
    m = re.search(r'\(([^()]+)\)\s*$', title)
    if m:
        colorway = m.group(1).strip()

    return {
        'artist': artist,
        'name': title,
        'work_id': f'{artist}::{slugify(title)}',
        'canonical_work': (title or '').lower(),
        'price': f'{float(price):.2f}',
        'date': date,
        'source': 'Artnet',
        'signed': 'True',  # auction lots are authenticated by definition
        'numbered': 'True' if rec.get('edition_number') or rec.get('edition_size') else '',
        'medium': rec.get('medium', ''),
        'colorway': colorway,
        'height': '',
        'width': '',
        'url': rec.get('url', ''),
        'artwork_year': rec.get('artwork_year', ''),
        'authentication': rec.get('auction_house', ''),
        'category': 'fine_art',
        'condition': '',
        'edition_band': '',
        'edition_number': rec.get('edition_number') or '',
        'edition_size': rec.get('edition_size') or '',
        'framed': '',
        'is_ap': '',
        '_artnet_lot': rec.get('lot', ''),
        '_artnet_sale': rec.get('sale_name', ''),
        '_artnet_currency': rec.get('currency', ''),
        '_artnet_est_low': rec.get('est_low') or '',
        '_artnet_est_high': rec.get('est_high') or '',
        '_artnet_sold': rec.get('sold_price') or '',
    }


def main():
    if not os.path.exists(CSV_PATH):
        raise SystemExit(f'Missing {CSV_PATH}')
    if not os.path.exists(ARTNET_PATH):
        raise SystemExit(f'Missing {ARTNET_PATH} — run scripts/parse_artnet_pdfs.py first')

    # Backup
    if not os.path.exists(BACKUP_PATH):
        shutil.copyfile(CSV_PATH, BACKUP_PATH)
        print(f'Backed up master CSV → {BACKUP_PATH}')

    # Load existing CSV
    with open(CSV_PATH, newline='', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        existing_cols = reader.fieldnames
        existing = list(reader)
    print(f'Existing rows: {len(existing):,}')
    existing_pre = Counter(r['artist'] for r in existing)

    # Build dedup key set for existing rows
    existing_keys = set()
    for r in existing:
        k = (r['artist'].lower(), r['name'].lower(), r.get('date', ''),
             r.get('authentication', ''))
        existing_keys.add(k)

    # Load Artnet
    with open(ARTNET_PATH) as f:
        artnet = json.load(f)
    print(f'Artnet records: {len(artnet)}')

    # Convert + dedup against existing
    fresh = []
    rejected = 0
    for r in artnet:
        row = to_csv_row(r)
        k = (row['artist'].lower(), row['name'].lower(), row.get('date', ''),
             row.get('authentication', ''))
        if k in existing_keys:
            rejected += 1
            continue
        existing_keys.add(k)
        fresh.append(row)
    print(f'Fresh rows after dedup vs existing: {len(fresh)}  (rejected dupes: {rejected})')

    # Final columns (existing + extras at end)
    final_cols = list(existing_cols)
    for c in EXTRA_COLS:
        if c not in final_cols:
            final_cols.append(c)

    # Write merged CSV
    with open(CSV_PATH, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=final_cols, extrasaction='ignore')
        w.writeheader()
        for r in existing:
            w.writerow(r)
        for r in fresh:
            w.writerow(r)
    new_rows = len(existing) + len(fresh)
    new_size_mb = os.path.getsize(CSV_PATH) / 1024 / 1024
    print(f'\nWrote {CSV_PATH}: {new_rows:,} rows ({new_size_mb:.1f} MB)')

    # Regenerate the JSON sidecar (used by app.py)
    # historical_clean.json is the same data in JSON list-of-dicts form
    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        json_rows = []
        for row in reader:
            # Coerce price + booleans to match the existing JSON schema
            try:
                row['price'] = float(row['price']) if row.get('price') else 0
            except Exception:
                row['price'] = 0
            row['signed'] = str(row.get('signed', '')).lower() in ('true', '1', 'yes')
            row['numbered'] = str(row.get('numbered', '')).lower() in ('true', '1', 'yes')
            json_rows.append(row)
    with open(JSON_PATH, 'w') as f:
        json.dump(json_rows, f)
    json_size_mb = os.path.getsize(JSON_PATH) / 1024 / 1024
    print(f'Wrote {JSON_PATH}: {len(json_rows):,} rows ({json_size_mb:.1f} MB)')

    # Final stats
    print(f'\n=== BEFORE / AFTER by artist ===')
    after = Counter()
    max_price_by_artist = {}
    for r in json_rows:
        a = r['artist']
        after[a] += 1
        try:
            p = float(r['price'])
            if p > max_price_by_artist.get(a, 0):
                max_price_by_artist[a] = p
        except Exception:
            pass
    print(f"{'artist':<18}{'before':>8}{'after':>8}{'+new':>7}{'max $':>14}")
    for a in sorted(after.keys(), key=lambda x: -after[x]):
        b = existing_pre.get(a, 0)
        diff = after[a] - b
        mx = max_price_by_artist.get(a, 0)
        print(f"{a:<18}{b:>8,}{after[a]:>8,}{diff:>+7,}{mx:>14,.0f}")

    above_3k = sum(1 for r in json_rows if (r.get('price') or 0) > 3000)
    above_10k = sum(1 for r in json_rows if (r.get('price') or 0) > 10000)
    above_100k = sum(1 for r in json_rows if (r.get('price') or 0) > 100000)
    print(f'\n=== UPPER-TIER COMPS RECOVERED ===')
    print(f'  Rows priced > $3,000:   {above_3k:5,}  (was 0 before merge)')
    print(f'  Rows priced > $10,000:  {above_10k:5,}  (was 0 before merge)')
    print(f'  Rows priced > $100,000: {above_100k:5,}  (was 0 before merge)')


if __name__ == '__main__':
    main()
