#!/usr/bin/env python3
"""
Consolidate ALL historical sales data into one master database.

Sources:
1. shepard_fairey_data.json (132k SF records)
2. kaws_data.json (44k KAWS records)
3. worthpoint_sf_data.json (2.7k WP records)
4. 202 wp_backup files (74k+ WP scraped records)
5. ebay_active_FINAL CSVs (eBay sold data)
6. data_Autographs CSV (558 autograph records)
7. data_DEATH NYC CSV (2500 Death NYC records)
8. data_mutualart CSV (135 MutualArt records)
9. data_fianlll sold CSV (120 MutualArt sold)
10. artsy CSVs (169 Artsy records)
11. dr.json (1162 records)

Output: data/master_sales.json (deduplicated, normalized, cleaned)
Then rebuilds historical_clean.json from master.
"""

import json, csv, re, os, glob
from collections import Counter
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
DOWNLOADS = '/Users/johnshay/Downloads'

all_records = []
source_counts = Counter()


def add_records(records, source_label):
    global all_records, source_counts
    for r in records:
        r['_source'] = source_label
        all_records.append(r)
    source_counts[source_label] += len(records)
    print(f'  {source_label}: {len(records):,} records')


def detect_artist(text):
    t = (text or '').lower()
    if 'shepard fairey' in t or 'obey giant' in t or ('obey' in t and ('print' in t or 'signed' in t or 'screen' in t)):
        return 'Shepard Fairey'
    elif 'kaws' in t: return 'KAWS'
    elif 'death nyc' in t: return 'Death NYC'
    elif 'banksy' in t: return 'Banksy'
    elif 'brainwash' in t or 'mbw' in t: return 'Mr. Brainwash'
    elif 'bearbrick' in t or 'be@rbrick' in t: return 'Bearbrick'
    elif any(w in t for w in ['apollo', 'nasa', 'astronaut', 'aldrin', 'armstrong', 'glenn']): return 'Space/NASA'
    return None


def parse_price(val):
    if not val: return 0
    if isinstance(val, (int, float)): return float(val)
    # Extract number from string like "$350.00" or "Sold for: $42.00"
    m = re.search(r'\$?([\d,]+\.?\d*)', str(val).replace(',', ''))
    return float(m.group(1)) if m else 0


def parse_date(val):
    if not val: return ''
    val = str(val).strip()[:10]
    # Already YYYY-MM-DD
    if re.match(r'\d{4}-\d{2}-\d{2}', val): return val
    # Try other formats
    for fmt in ['%b %d, %Y', '%m/%d/%Y', '%Y/%m/%d']:
        try:
            return datetime.strptime(val, fmt).strftime('%Y-%m-%d')
        except: pass
    return ''


# ============================================================
print('Loading all sources...\n')

# 1. Existing master files
for fname, label in [
    ('shepard_fairey_data.json', 'SF Master'),
    ('kaws_data.json', 'KAWS Master'),
    ('worthpoint_sf_data.json', 'WorthPoint SF'),
]:
    path = os.path.join(DATA_DIR, fname)
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        normalized = []
        for r in data:
            normalized.append({
                'name': (r.get('name', '') or r.get('title', ''))[:120],
                'artist': r.get('artist', '') or detect_artist(r.get('name', '') or r.get('title', '')),
                'price': parse_price(r.get('price')),
                'date': parse_date(r.get('date', '')),
                'source': r.get('source', label),
                'medium': r.get('medium', ''),
                'signed': r.get('signed', False),
                'url': r.get('url', ''),
            })
        add_records(normalized, label)

# 2. WorthPoint backup files
wp_files = sorted(glob.glob(f'{DOWNLOADS}/wp_backup_*.json'))
wp_records = []
for f in wp_files:
    try:
        with open(f) as fh:
            data = json.load(fh)
        if isinstance(data, list):
            for r in data:
                title = r.get('title', '') or ''
                # Clean WP title (has embedded "Sold for: $XX" etc)
                clean_title = re.sub(r'\n.*', '', title).strip()
                price = parse_price(title)  # Price often in title text
                if not price:
                    price = parse_price(r.get('price'))
                wp_records.append({
                    'name': clean_title[:120],
                    'artist': detect_artist(clean_title),
                    'price': price,
                    'date': parse_date(r.get('scrapedAt', '')[:10] if r.get('scrapedAt') else ''),
                    'source': 'WorthPoint',
                    'url': r.get('url', ''),
                })
    except: pass
add_records(wp_records, 'WP Backups')

# 3. dr.json
dr_path = f'{DOWNLOADS}/dr.json'
if os.path.exists(dr_path):
    with open(dr_path) as f:
        data = json.load(f)
    if isinstance(data, list):
        dr_recs = []
        for r in data:
            title = re.sub(r'\n.*', '', r.get('title', '') or '').strip()
            price = parse_price(title) or parse_price(r.get('price'))
            dr_recs.append({
                'name': title[:120],
                'artist': detect_artist(title),
                'price': price,
                'date': '',
                'source': 'WorthPoint',
                'url': r.get('url', ''),
            })
        add_records(dr_recs, 'dr.json')

# 4. eBay Active FINAL CSVs (these are actually sold data)
ebay_csvs = sorted(glob.glob(f'{DOWNLOADS}/ebay_active_FINAL_*.csv'), key=lambda x: -os.path.getsize(x))
ebay_records = []
for f in ebay_csvs:
    try:
        with open(f, newline='', encoding='utf-8', errors='ignore') as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                title = row.get('title', '')
                price = parse_price(row.get('price_numeric') or row.get('price'))
                date = parse_date(row.get('sold_date', ''))
                if title and price > 0:
                    ebay_records.append({
                        'name': title[:120],
                        'artist': detect_artist(title),
                        'price': price,
                        'date': date,
                        'source': 'eBay',
                        'medium': '',
                        'url': '',
                    })
    except: pass
add_records(ebay_records, 'eBay CSVs')

# 5. Autographs CSV
auto_path = f'{DOWNLOADS}/data_Autographs_2025-04-23T08_28_09.145Z.csv'
if os.path.exists(auto_path):
    auto_recs = []
    with open(auto_path, newline='', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get('Name', '') or row.get('\ufeff"Date"', '')
            price = parse_price(row.get('Price', ''))
            if name and price > 0:
                auto_recs.append({
                    'name': name[:120],
                    'artist': detect_artist(name) or 'Space/NASA',
                    'price': price,
                    'date': '',
                    'source': row.get('Source', 'Autograph'),
                    'url': '',
                })
    add_records(auto_recs, 'Autographs CSV')

# 6. Death NYC CSV
dnc_path = f'{DOWNLOADS}/data_DFEATH NYC HELP_2025-04-25T22_52_28.649Z.csv'
if os.path.exists(dnc_path):
    dnc_recs = []
    with open(dnc_path, newline='', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get('propertyName1', '')
            if name:
                dnc_recs.append({
                    'name': name[:120],
                    'artist': 'Death NYC',
                    'price': 0,  # No price in this CSV
                    'date': '',
                    'source': 'Death NYC Shop',
                    'url': row.get('propertyName1_link', ''),
                })
    add_records(dnc_recs, 'Death NYC CSV')

# 7. MutualArt CSV
ma_path = f'{DOWNLOADS}/data_mutualart1_2025-04-23T08_05_18.799Z.csv'
if os.path.exists(ma_path):
    ma_recs = []
    with open(ma_path, newline='', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f)
        for row in reader:
            price = parse_price(row.get('Price', ''))
            edition = row.get('Edition', '')
            source_val = row.get('Source', 'MutualArt')
            if price > 0:
                ma_recs.append({
                    'name': f"Shepard Fairey {edition}"[:120],
                    'artist': 'Shepard Fairey',
                    'price': price,
                    'date': parse_date(row.get('\ufeff"Date"', '')),
                    'source': source_val,
                    'url': row.get('Edition_link', ''),
                })
    add_records(ma_recs, 'MutualArt')

# 8. Artsy CSVs (legacy schema — line1/line2/price)
for f in glob.glob(f'{DOWNLOADS}/artsy_*.csv'):
    artsy_recs = []
    with open(f, newline='', encoding='utf-8', errors='ignore') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            # Price Radar schema (new scraper) has explicit columns
            if 'artist' in row and 'name' in row:
                continue  # handled by section 9 below
            title = f"{row.get('line1','')} {row.get('line2','')}".strip()
            price = parse_price(row.get('price', ''))
            if title and price > 0:
                artsy_recs.append({
                    'name': title[:120],
                    'artist': detect_artist(title) or 'Shepard Fairey',
                    'price': price,
                    'date': '',
                    'source': 'Artsy',
                    'url': row.get('url', ''),
                })
    if artsy_recs:
        add_records(artsy_recs, f'Artsy {os.path.basename(f)}')


# ============================================================
# 9. NEW: Price Radar schema CSVs from the 4-scraper kit
#    (ebay-scraper, artnet-scraper, artsy-scraper, liveauctioneers-scraper)
#    + Artnet PDF data parsed by scripts/parse_artnet_pdfs.py
# ============================================================

def ingest_price_radar_csv(path, source_label, mark_signed_for_auction=True):
    """Ingest a CSV in the Price Radar schema produced by the 4 scrapers."""
    recs = []
    with open(path, newline='', encoding='utf-8', errors='ignore') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            artist = (row.get('artist') or '').strip()
            name = (row.get('name') or '').strip()
            if not artist or not name:
                continue
            price = parse_price(row.get('price', ''))
            if not price or price <= 0:
                continue
            # Auction-source records: mark signed=True (auction lots are
            # authenticated by definition) so they survive clean_historical's
            # require_signed filter for Fairey/Banksy/MBW/Death NYC.
            is_auction = source_label in ('Artnet', 'LiveAuctioneers') or \
                         (row.get('_artsy_kind') == 'auction')
            recs.append({
                'name': name[:120],
                'artist': artist,
                'price': price,
                'date': parse_date(row.get('date', '')),
                'source': source_label,
                'medium': row.get('medium', ''),
                'signed': True if (mark_signed_for_auction and is_auction) else
                          (str(row.get('signed', '')).lower() in ('true', '1', 'yes')),
                'url': row.get('url', ''),
            })
    return recs

# 9a. eBay scraper CSVs (ebay_<mode>_<keyword>_<ts>.csv)
for f in sorted(glob.glob(f'{DOWNLOADS}/ebay_*.csv')):
    base = os.path.basename(f)
    # Sniff for Price Radar schema (has 'itemId' + 'priceLow' OR 'artist'+'name')
    with open(f, encoding='utf-8', errors='ignore') as fh:
        head = fh.readline()
    if 'artist' in head and 'name' in head:
        recs = ingest_price_radar_csv(f, 'eBay')
        if recs: add_records(recs, f'eBay scraper {base}')

# 9b. Artnet scraper CSVs (web scraper, distinct from PDF data)
for f in sorted(glob.glob(f'{DOWNLOADS}/artnet_*.csv')):
    base = os.path.basename(f)
    with open(f, encoding='utf-8', errors='ignore') as fh:
        head = fh.readline()
    if 'artist' in head and 'name' in head:
        recs = ingest_price_radar_csv(f, 'Artnet')
        if recs: add_records(recs, f'Artnet scraper {base}')

# 9c. Artsy scraper CSVs (new Price Radar schema)
for f in sorted(glob.glob(f'{DOWNLOADS}/artsy_*.csv')):
    base = os.path.basename(f)
    with open(f, encoding='utf-8', errors='ignore') as fh:
        head = fh.readline()
    if 'artist' in head and 'name' in head and '_artsy_kind' in head:
        recs = ingest_price_radar_csv(f, 'Artsy')
        if recs: add_records(recs, f'Artsy scraper {base}')

# 9d. LiveAuctioneers scraper CSVs
for f in sorted(glob.glob(f'{DOWNLOADS}/liveauctioneers_*.csv')):
    base = os.path.basename(f)
    with open(f, encoding='utf-8', errors='ignore') as fh:
        head = fh.readline()
    if 'artist' in head and 'name' in head:
        recs = ingest_price_radar_csv(f, 'LiveAuctioneers')
        if recs: add_records(recs, f'LiveAuctioneers {base}')

# 9e. Artnet PDF data (output of scripts/parse_artnet_pdfs.py)
artnet_pdf_path = os.path.join(DATA_DIR, 'artnet_data.json')
if os.path.exists(artnet_pdf_path):
    with open(artnet_pdf_path) as f:
        artnet_data = json.load(f)
    # Records already in consolidator schema (signed=True for auction lots);
    # just normalize date and add.
    artnet_recs = []
    for r in artnet_data:
        if r.get('name') and (r.get('price') or 0) > 0:
            artnet_recs.append({
                'name': r['name'][:120],
                'artist': r.get('artist', ''),
                'price': float(r['price']),
                'date': parse_date(r.get('date', '')),
                'source': 'Artnet',
                'medium': r.get('medium', ''),
                'signed': True,
                'url': r.get('url', ''),
            })
    if artnet_recs:
        add_records(artnet_recs, 'Artnet PDFs')


# ============================================================
# Deduplicate
print(f'\n{"="*60}')
print(f'Total raw: {len(all_records):,}')

seen = set()
deduped = []
for r in all_records:
    name = (r.get('name', '') or '')[:40].lower()
    price = round(r.get('price', 0), 2)
    key = f"{name}|{price}"
    if key not in seen:
        seen.add(key)
        deduped.append(r)

print(f'After dedup: {len(deduped):,}')

# Remove records with no artist or no name
deduped = [r for r in deduped if r.get('name') and len(r['name']) > 3]
print(f'After name filter: {len(deduped):,}')

# Artist distribution
artist_counts = Counter()
for r in deduped:
    artist_counts[r.get('artist') or 'Unknown'] += 1

print(f'\nBy artist:')
for a, c in artist_counts.most_common():
    print(f'  {a}: {c:,}')

# Source distribution
src_counts = Counter()
for r in deduped:
    src_counts[r.get('source', 'Unknown')] += 1
print(f'\nBy source:')
for s, c in src_counts.most_common(10):
    print(f'  {s}: {c:,}')

# Save master
master_path = os.path.join(DATA_DIR, 'master_sales.json')
with open(master_path, 'w') as f:
    json.dump(deduped, f)
size_mb = os.path.getsize(master_path) / 1024 / 1024
print(f'\nSaved: {master_path} ({size_mb:.1f} MB, {len(deduped):,} records)')

# Now run the cleaner on the master data
print(f'\n{"="*60}')
print('Running cleaner on master data...')

# Import and modify the cleaner to use master_sales.json
from clean_historical import SELLING_ARTISTS, JUNK_TERMS, SIGNED_PATTERNS, NUMBERED_PATTERNS, TITLE_NOISE
from clean_historical import is_signed, is_numbered, has_junk, extract_title_words, extract_canonical_work, extract_medium

stats = {'kept': 0, 'removed_price': 0, 'removed_unsigned': 0, 'removed_artist': 0, 'removed_junk': 0, 'removed_dupe': 0}
cleaned = []
clean_seen = set()

for r in deduped:
    name = r.get('name', '')
    price = r.get('price', 0) or 0
    artist = r.get('artist', '') or detect_artist(name)

    if not artist or artist not in SELLING_ARTISTS:
        stats['removed_artist'] += 1
        continue

    rules = SELLING_ARTISTS[artist]
    if price < rules['min_price']:
        stats['removed_price'] += 1
        continue

    if has_junk(name):
        stats['removed_junk'] += 1
        continue

    signed = r.get('signed', False) or is_signed(name)
    if rules['require_signed'] and not signed:
        stats['removed_unsigned'] += 1
        continue

    # Dedup again for clean
    clean_key = f"{name[:40].lower()}|{price}"
    if clean_key in clean_seen:
        stats['removed_dupe'] += 1
        continue
    clean_seen.add(clean_key)

    cleaned.append({
        'name': name[:120],
        'artist': artist,
        'price': round(price, 2),
        'date': r.get('date', '')[:10] if r.get('date') else '',
        'source': r.get('source', ''),
        'signed': signed,
        'numbered': is_numbered(name),
        'medium': extract_medium(name) or r.get('medium', ''),
        'canonical_work': extract_canonical_work(name, artist),
        'title_words': extract_title_words(name),
        'url': r.get('url', ''),
        'category': rules['category'],
    })
    stats['kept'] += 1

cleaned.sort(key=lambda x: (x['artist'], x.get('date', '') or ''), reverse=True)

clean_path = os.path.join(DATA_DIR, 'historical_clean.json')
with open(clean_path, 'w') as f:
    json.dump(cleaned, f)
clean_mb = os.path.getsize(clean_path) / 1024 / 1024

print(f'\nClean results:')
print(f'  Kept: {stats["kept"]:,}')
print(f'  Removed (price): {stats["removed_price"]:,}')
print(f'  Removed (unsigned): {stats["removed_unsigned"]:,}')
print(f'  Removed (artist): {stats["removed_artist"]:,}')
print(f'  Removed (junk): {stats["removed_junk"]:,}')
print(f'  Removed (dupe): {stats["removed_dupe"]:,}')
print(f'  Output: {clean_path} ({clean_mb:.1f} MB)')

clean_artists = Counter()
for r in cleaned:
    clean_artists[r['artist']] += 1
print(f'\nClean by artist:')
for a, c in clean_artists.most_common():
    print(f'  {a}: {c:,}')


if __name__ == '__main__':
    pass
