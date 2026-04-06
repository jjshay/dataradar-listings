#!/usr/bin/env python3
"""
Clean and optimize historical sales data for the comp engine.
Removes: under $85, unsigned (for SF/Banksy), non-tracked artists, junk titles.
Creates: data/historical_clean.json — optimized index for fast comp lookup.
"""

import json
import re
import os
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# Artists we track
TRACKED_ARTISTS = {
    'Shepard Fairey', 'KAWS', 'Death NYC', 'Banksy', 'Mr. Brainwash',
    'Bearbrick', 'Invader', 'Murakami', 'Arsham', 'Nara', 'Futura',
    'Stik', 'Retna', 'Warhol', 'Basquiat', 'Haring', 'Hirst', 'Brantley',
}

# Category-specific rules
CATEGORY_RULES = {
    'Shepard Fairey': {'min_price': 85, 'require_signed': True, 'reject_mediums': ['sticker', 'pin', 'patch']},
    'KAWS': {'min_price': 200, 'require_signed': False, 'reject_mediums': ['sticker', 'pin']},
    'Banksy': {'min_price': 100, 'require_signed': True, 'reject_mediums': ['poster', 'sticker', 'postcard']},
    'Death NYC': {'min_price': 20, 'require_signed': True, 'reject_mediums': []},
    'Mr. Brainwash': {'min_price': 75, 'require_signed': True, 'reject_mediums': ['poster']},
    '_default': {'min_price': 85, 'require_signed': False, 'reject_mediums': []},
}

JUNK_TERMS = [
    'reproduction', 'repro', 'replica', 'tribute', 'fan art', 'inspired by',
    'in the style of', 'custom frame', 'reprint', 'digital print',
    'museum poster', 'exhibition poster', 'book plate', 'magazine',
    't-shirt', 'tshirt', 'patch', 'pin', 'magnet', 'keychain', 'button',
    'not signed', 'unsigned copy', 'offset lithograph',
]

SIGNED_PATTERNS = [
    r'\bsigned\b', r'\bhand signed\b', r'\bhand-signed\b',
    r'\bautograph', r'\bs/n\b', r'\bap\b', r'\ba/p\b',
]

NUMBERED_PATTERNS = [
    r'\bnumbered\b', r'\bedition of\b', r'\blimited edition\b',
    r'\b\d{1,4}\s*/\s*\d{1,4}\b', r'\bartist proof\b',
]

TITLE_NOISE = {
    'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of',
    'is', 'by', 'with', 'new', 'lot', 'rare', 'free', 'shipping', 'print',
    'signed', 'numbered', 'hand', 'screen', 'edition', 'limited', 'art',
    'original', 'artist', 'proof', 'framed', 'matted', 'coa', 'certificate',
    'authenticity', 'obey', 'giant', 'shepard', 'fairey', 'death', 'nyc',
    'banksy', 'kaws', 'brainwash', 'sold', 'online', 'ebay', 'auction',
    'vintage', 'poster', 'gallery', 'collection', 'show', 'exhibition',
}


def detect_artist(name):
    t = name.lower()
    if 'shepard fairey' in t or 'obey giant' in t or 'obey ' in t:
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
    elif 'invader' in t:
        return 'Invader'
    elif 'murakami' in t or 'takashi' in t:
        return 'Murakami'
    elif 'arsham' in t:
        return 'Arsham'
    elif 'nara' in t:
        return 'Nara'
    elif 'stik' in t:
        return 'Stik'
    elif 'warhol' in t:
        return 'Warhol'
    elif 'basquiat' in t:
        return 'Basquiat'
    elif 'haring' in t:
        return 'Haring'
    elif 'hirst' in t:
        return 'Hirst'
    return None


def extract_title_words(name):
    """Extract meaningful title words for matching."""
    words = set(w.lower() for w in re.findall(r'\w+', name) if w.lower() not in TITLE_NOISE and len(w) > 2)
    return words


def is_signed(text):
    t = text.lower()
    return any(re.search(p, t) for p in SIGNED_PATTERNS)


def is_numbered(text):
    t = text.lower()
    return any(re.search(p, t) for p in NUMBERED_PATTERNS)


def has_junk(text):
    t = text.lower()
    return any(j in t for j in JUNK_TERMS)


def clean_data():
    stats = {'loaded': 0, 'kept': 0, 'removed_price': 0, 'removed_unsigned': 0,
             'removed_artist': 0, 'removed_junk': 0, 'removed_no_title': 0}

    all_records = []

    # Load SF data
    sf_path = os.path.join(DATA_DIR, 'shepard_fairey_data.json')
    if os.path.exists(sf_path):
        with open(sf_path) as f:
            sf = json.load(f)
        print(f'Loaded {len(sf)} SF records')
        stats['loaded'] += len(sf)
        for r in sf:
            r['_source_file'] = 'sf'
            all_records.append(r)

    # Load KAWS data
    kaws_path = os.path.join(DATA_DIR, 'kaws_data.json')
    if os.path.exists(kaws_path):
        with open(kaws_path) as f:
            kaws = json.load(f)
        print(f'Loaded {len(kaws)} KAWS records')
        stats['loaded'] += len(kaws)
        for r in kaws:
            r['_source_file'] = 'kaws'
            all_records.append(r)

    # Load WorthPoint data
    wp_path = os.path.join(DATA_DIR, 'worthpoint_sf_data.json')
    if os.path.exists(wp_path):
        with open(wp_path) as f:
            wp = json.load(f)
        print(f'Loaded {len(wp)} WorthPoint records')
        stats['loaded'] += len(wp)
        for r in wp:
            r['_source_file'] = 'wp'
            all_records.append(r)

    print(f'\nTotal raw records: {stats["loaded"]}')

    # Clean
    cleaned = []
    by_artist = {}

    for r in all_records:
        name = r.get('name', '') or r.get('title', '') or ''
        price = r.get('price', 0) or 0
        date = r.get('date', '') or ''
        source = r.get('source', '') or ''
        medium = r.get('medium', '') or ''

        # Skip no title
        if not name or len(name) < 5:
            stats['removed_no_title'] += 1
            continue

        # Detect/validate artist
        artist = r.get('artist', '') or detect_artist(name)
        if not artist or artist not in TRACKED_ARTISTS:
            stats['removed_artist'] += 1
            continue

        # Get category rules
        rules = CATEGORY_RULES.get(artist, CATEGORY_RULES['_default'])

        # Price floor
        if price < rules['min_price']:
            stats['removed_price'] += 1
            continue

        # Junk terms
        if has_junk(name):
            stats['removed_junk'] += 1
            continue

        # Require signed for certain artists
        signed = r.get('signed', False) or is_signed(name)
        numbered = is_numbered(name)
        if rules['require_signed'] and not signed:
            stats['removed_unsigned'] += 1
            continue

        # Reject bad mediums
        medium_lower = medium.lower() if medium else ''
        name_lower = name.lower()
        reject_meds = rules.get('reject_mediums', [])
        if any(rm in medium_lower or rm in name_lower for rm in reject_meds):
            stats['removed_junk'] += 1
            continue

        # Extract title keywords for indexing
        title_words = extract_title_words(name)

        record = {
            'name': name[:100],
            'artist': artist,
            'price': round(price, 2),
            'date': date[:10] if date else '',
            'source': source,
            'signed': signed,
            'numbered': numbered,
            'medium': medium,
            'title_words': sorted(list(title_words)),
            'url': r.get('url', ''),
        }
        cleaned.append(record)
        stats['kept'] += 1

        if artist not in by_artist:
            by_artist[artist] = {'count': 0, 'min': 99999, 'max': 0, 'total': 0}
        by_artist[artist]['count'] += 1
        by_artist[artist]['min'] = min(by_artist[artist]['min'], price)
        by_artist[artist]['max'] = max(by_artist[artist]['max'], price)
        by_artist[artist]['total'] += price

    # Sort by artist then date descending
    cleaned.sort(key=lambda x: (x['artist'], x.get('date', '') or ''), reverse=True)

    # Save
    out_path = os.path.join(DATA_DIR, 'historical_clean.json')
    with open(out_path, 'w') as f:
        json.dump(cleaned, f)
    size_mb = os.path.getsize(out_path) / 1024 / 1024

    print(f'\n{"="*50}')
    print(f'RESULTS:')
    print(f'  Loaded:           {stats["loaded"]:,}')
    print(f'  Kept:             {stats["kept"]:,} ({round(stats["kept"]/max(stats["loaded"],1)*100)}%)')
    print(f'  Removed (price):  {stats["removed_price"]:,}')
    print(f'  Removed (unsigned): {stats["removed_unsigned"]:,}')
    print(f'  Removed (artist): {stats["removed_artist"]:,}')
    print(f'  Removed (junk):   {stats["removed_junk"]:,}')
    print(f'  Removed (no title): {stats["removed_no_title"]:,}')
    print(f'  Output: {out_path} ({size_mb:.1f} MB)')

    print(f'\nBy Artist:')
    for artist, s in sorted(by_artist.items(), key=lambda x: -x[1]['count']):
        avg = round(s['total'] / max(s['count'], 1))
        print(f'  {artist:20} {s["count"]:>6,} records   ${s["min"]:.0f}-${s["max"]:.0f}   avg ${avg}')

    return stats


if __name__ == '__main__':
    clean_data()
