#!/usr/bin/env python3
"""
Clean and standardize historical sales data for the comp engine.

Rules:
- Only artists you're actually selling
- $100 minimum (no junk comps)
- Signed required for fine art artists (SF, Banksy, MBW)
- Remove reproductions, stickers, posters, merch
- Standardize titles: normalize artist names, extract work name, clean noise
- Build title_words index for fast matching
- Build canonical_work field for clustering

Run: python3 clean_historical.py
Output: data/historical_clean.json
"""

import json
import re
import os
from collections import Counter

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# ============================================================
# ONLY artists you're selling — everything else gets deleted
# ============================================================
SELLING_ARTISTS = {
    'Shepard Fairey': {'min_price': 100, 'require_signed': True, 'category': 'fine_art'},
    'KAWS':           {'min_price': 200, 'require_signed': False, 'category': 'figures'},
    'Death NYC':      {'min_price': 25,  'require_signed': True, 'category': 'fine_art'},
    'Banksy':         {'min_price': 100, 'require_signed': True, 'category': 'fine_art'},
    'Mr. Brainwash':  {'min_price': 100, 'require_signed': True, 'category': 'fine_art'},
    'Bearbrick':      {'min_price': 100, 'require_signed': False, 'category': 'figures'},
    'Space/NASA':     {'min_price': 50,  'require_signed': False, 'category': 'autographs'},
}

# ============================================================
# Junk detection
# ============================================================
JUNK_TERMS = [
    'reproduction', 'repro', 'replica', 'tribute', 'fan art', 'fanart',
    'inspired by', 'in the style of', 'after ', 'reprint', 'digital print',
    'museum poster', 'exhibition poster', 'book plate', 'magazine',
    'not signed', 'unsigned copy', 'offset lithograph', 'canvas print',
    't-shirt', 'tshirt', 'tee ', 'patch', 'pin ', 'pin,', 'magnet',
    'keychain', 'button', 'postcard', 'sticker', 'decal',
    'iphone case', 'phone case', 'skateboard deck',
]

SIGNED_PATTERNS = [
    r'\bsigned\b', r'\bhand[\s-]?signed\b', r'\bautograph',
    r'\bs/n\b', r'\ba\.?p\.?\b', r'\bartist\s+proof\b',
]

NUMBERED_PATTERNS = [
    r'\bnumbered\b', r'\bedition\s+of\b', r'\blimited\s+edition\b',
    r'\b\d{1,4}\s*/\s*\d{1,4}\b',
]

# Title noise words to strip for canonical matching
TITLE_NOISE = {
    'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of',
    'is', 'by', 'with', 'new', 'lot', 'rare', 'free', 'shipping', 'print',
    'signed', 'numbered', 'hand', 'screen', 'edition', 'limited', 'art',
    'original', 'artist', 'proof', 'framed', 'matted', 'coa', 'certificate',
    'authenticity', 'obey', 'giant', 'shepard', 'fairey', 'death', 'nyc',
    'banksy', 'kaws', 'brainwash', 'sold', 'online', 'ebay', 'auction',
    'vintage', 'poster', 'gallery', 'collection', 'show', 'exhibition',
    'le', 'sn', 'ap', 'silkscreen', 'screenprint', 'lithograph',
    'serigraph', 'giclee', 'authentic', 'genuine', 'official',
    'mr', 'thierry', 'guetta', 'mr.', 'invader', 'bearbrick',
    'medicom', 'toy', 'vinyl', 'figure', 'figurine',
}


def detect_artist(name):
    t = name.lower()
    if 'shepard fairey' in t or 'obey giant' in t or ('obey' in t and ('print' in t or 'screen' in t or 'signed' in t)):
        return 'Shepard Fairey'
    elif 'kaws' in t:
        return 'KAWS'
    elif 'death nyc' in t:
        return 'Death NYC'
    elif 'banksy' in t:
        return 'Banksy'
    elif 'brainwash' in t or 'mbw' in t:
        return 'Mr. Brainwash'
    elif 'bearbrick' in t or 'be@rbrick' in t:
        return 'Bearbrick'
    elif any(w in t for w in ['apollo', 'nasa', 'astronaut', 'aldrin', 'armstrong', 'glenn']):
        return 'Space/NASA'
    return None


def is_signed(text):
    t = text.lower()
    return any(re.search(p, t) for p in SIGNED_PATTERNS)


def is_numbered(text):
    t = text.lower()
    return any(re.search(p, t) for p in NUMBERED_PATTERNS)


def has_junk(text):
    t = text.lower()
    return any(j in t for j in JUNK_TERMS)


def extract_title_words(name):
    words = set(w.lower() for w in re.findall(r'\w+', name) if w.lower() not in TITLE_NOISE and len(w) > 2)
    return sorted(list(words))


def extract_canonical_work(name, artist):
    """Extract the artwork title from a messy listing title.
    Remove artist name, noise words, edition info, condition — keep the work name."""
    t = name

    # Remove artist name variations
    for pattern in [
        r'(?i)shepard\s+fairey', r'(?i)obey\s+giant', r'(?i)obey',
        r'(?i)death\s+nyc', r'(?i)banksy', r'(?i)mr\.?\s*brainwash',
        r'(?i)kaws', r'(?i)bearbrick', r'(?i)be@rbrick', r'(?i)medicom',
    ]:
        t = re.sub(pattern, '', t)

    # Remove common noise
    for pattern in [
        r'(?i)\bsigned\b', r'(?i)\bnumbered\b', r'(?i)\blimited\s+edition\b',
        r'(?i)\bedition\s+of\s+\d+', r'(?i)\b\d{1,4}\s*/\s*\d{1,4}\b',
        r'(?i)\bhand[\s-]?signed\b', r'(?i)\bscreen\s*print\b',
        r'(?i)\bsilkscreen\b', r'(?i)\blithograph\b', r'(?i)\bserigraph\b',
        r'(?i)\bletterpress\b', r'(?i)\bgiclee\b',
        r'(?i)\bframed\b', r'(?i)\bmatted\b', r'(?i)\bcoa\b',
        r'(?i)\bartist\s+proof\b', r'(?i)\bap\b', r'(?i)\bs/n\b',
        r'(?i)\ble\b', r'(?i)\brare\b', r'(?i)\bmint\b',
        r'(?i)\b\d{4}\b',  # years
        r'(?i)\b\d{1,3}\s*x\s*\d{1,3}\b',  # dimensions
        r'(?i)\b\d+"\s*x\s*\d+"', # dimensions with quotes
        r'["\'\-–—·•|]',  # punctuation
    ]:
        t = re.sub(pattern, ' ', t)

    # Clean up whitespace
    t = re.sub(r'\s+', ' ', t).strip()

    # Remove very short residual words
    words = [w for w in t.split() if len(w) > 2 and w.lower() not in TITLE_NOISE]
    canonical = ' '.join(words).strip()

    # Normalize to lowercase
    return canonical.lower()[:60] if canonical else ''


def extract_medium(name):
    t = name.lower()
    if any(w in t for w in ['screenprint', 'screen print', 'serigraph', 'silkscreen']):
        return 'screenprint'
    elif 'letterpress' in t:
        return 'letterpress'
    elif 'lithograph' in t:
        return 'lithograph'
    elif 'giclee' in t:
        return 'giclee'
    elif 'stencil' in t or 'spray paint' in t:
        return 'stencil'
    elif any(w in t for w in ['figure', 'figurine', 'vinyl figure', 'sculpture']):
        return 'figure'
    elif 'poster' in t or 'offset' in t:
        return 'poster'
    elif 'painting' in t:
        return 'painting'
    return ''


def clean_data():
    stats = {
        'loaded': 0, 'kept': 0,
        'removed_price': 0, 'removed_unsigned': 0,
        'removed_artist': 0, 'removed_junk': 0,
        'removed_no_title': 0, 'removed_duplicate': 0,
    }

    all_records = []

    # Load all data files
    for filename, label in [
        ('shepard_fairey_data.json', 'SF'),
        ('kaws_data.json', 'KAWS'),
        ('worthpoint_sf_data.json', 'WP'),
    ]:
        path = os.path.join(DATA_DIR, filename)
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            print(f'Loaded {len(data):,} from {filename}')
            stats['loaded'] += len(data)
            for r in data:
                r['_file'] = label
                all_records.append(r)

    print(f'\nTotal raw: {stats["loaded"]:,}')

    # Clean
    cleaned = []
    seen = set()
    by_artist = {}
    canonical_counts = Counter()

    for r in all_records:
        name = r.get('name', '') or r.get('title', '') or ''
        price = r.get('price', 0) or 0
        date = r.get('date', '') or ''
        source = r.get('source', '') or ''
        medium_raw = r.get('medium', '') or ''

        # Skip no title
        if not name or len(name) < 5:
            stats['removed_no_title'] += 1
            continue

        # Detect artist
        artist = r.get('artist', '') or detect_artist(name)
        if not artist or artist not in SELLING_ARTISTS:
            stats['removed_artist'] += 1
            continue

        rules = SELLING_ARTISTS[artist]

        # Price floor ($100 for most, $200 for KAWS, $25 for Death NYC)
        if price < rules['min_price']:
            stats['removed_price'] += 1
            continue

        # Junk terms
        if has_junk(name):
            stats['removed_junk'] += 1
            continue

        # Require signed for fine art
        signed = r.get('signed', False) or is_signed(name)
        numbered = is_numbered(name)
        if rules['require_signed'] and not signed:
            stats['removed_unsigned'] += 1
            continue

        # Deduplicate (same title + same price = same record)
        dedup_key = f"{name[:40].lower()}|{price}"
        if dedup_key in seen:
            stats['removed_duplicate'] += 1
            continue
        seen.add(dedup_key)

        # Extract structured fields
        title_words = extract_title_words(name)
        canonical = extract_canonical_work(name, artist)
        medium = extract_medium(name) or medium_raw.lower()

        # Extract year
        yr = re.search(r'\b(19[5-9]\d|20[0-2]\d)\b', name)
        year = int(yr.group(1)) if yr else None

        record = {
            'name': name[:120],
            'artist': artist,
            'price': round(price, 2),
            'date': date[:10] if date else '',
            'source': source,
            'signed': signed,
            'numbered': numbered,
            'medium': medium,
            'year': year,
            'canonical_work': canonical,
            'title_words': title_words,
            'url': r.get('url', ''),
            'category': rules['category'],
        }
        cleaned.append(record)
        stats['kept'] += 1

        # Track stats
        if artist not in by_artist:
            by_artist[artist] = {'count': 0, 'min': 99999, 'max': 0, 'total': 0, 'signed': 0, 'numbered': 0}
        by_artist[artist]['count'] += 1
        by_artist[artist]['min'] = min(by_artist[artist]['min'], price)
        by_artist[artist]['max'] = max(by_artist[artist]['max'], price)
        by_artist[artist]['total'] += price
        if signed: by_artist[artist]['signed'] += 1
        if numbered: by_artist[artist]['numbered'] += 1

        if canonical:
            canonical_counts[f"{artist}::{canonical}"] += 1

    # Sort by artist then date
    cleaned.sort(key=lambda x: (x['artist'], x.get('date', '') or ''), reverse=True)

    # Save
    out_path = os.path.join(DATA_DIR, 'historical_clean.json')
    with open(out_path, 'w') as f:
        json.dump(cleaned, f)
    size_mb = os.path.getsize(out_path) / 1024 / 1024

    # Report
    print(f'\n{"="*60}')
    print(f'RESULTS:')
    print(f'  Loaded:             {stats["loaded"]:>8,}')
    print(f'  Kept:               {stats["kept"]:>8,} ({round(stats["kept"]/max(stats["loaded"],1)*100)}%)')
    print(f'  Removed (price):    {stats["removed_price"]:>8,}')
    print(f'  Removed (unsigned): {stats["removed_unsigned"]:>8,}')
    print(f'  Removed (artist):   {stats["removed_artist"]:>8,}')
    print(f'  Removed (junk):     {stats["removed_junk"]:>8,}')
    print(f'  Removed (no title): {stats["removed_no_title"]:>8,}')
    print(f'  Removed (dupe):     {stats["removed_duplicate"]:>8,}')
    print(f'  Output: {out_path} ({size_mb:.1f} MB)')

    print(f'\nBy Artist:')
    for artist, s in sorted(by_artist.items(), key=lambda x: -x[1]['count']):
        avg = round(s['total'] / max(s['count'], 1))
        pct_signed = round(s['signed'] / max(s['count'], 1) * 100)
        pct_numbered = round(s['numbered'] / max(s['count'], 1) * 100)
        print(f'  {artist:20} {s["count"]:>6,} records  ${s["min"]:.0f}-${s["max"]:,.0f}  avg ${avg:,}  {pct_signed}% signed  {pct_numbered}% numbered')

    # Top canonical works
    print(f'\nTop 20 Canonical Works (most comps):')
    for work, count in canonical_counts.most_common(20):
        print(f'  {count:>4} — {work}')

    return stats


if __name__ == '__main__':
    clean_data()
