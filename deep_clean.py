#!/usr/bin/env python3
"""Deep clean: fill missing fields, remove remaining junk, normalize mediums."""

import json, re, os
from collections import Counter

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# More junk to remove
REMOVE_PATTERNS = [
    r'\bset of \d+', r'\blot of \d+', r'\blot \d+\b',
    r'\bcoin\b', r'\bmedal\b', r'\bstamp\b(?!.*print)',
    r'\bticket\b', r'\bflyer\b', r'\bflier\b',
    r'\bcatalog\b', r'\bcatalogue\b',
    r'\bnewspaper\b', r'\bmagazine\b',
    r'\bpillow\b', r'\bcushion\b',
    r'\bornament\b(?!.*print)',
    r'\bmagnet\b', r'\bcoaster\b',
    r'\bdvd\b', r'\bblu-ray\b', r'\bvhs\b',
    r'\bbutton\b(?!.*print)', r'\bbadge\b(?!.*print)',
    r'\bbook\b(?!plate|let|.*signed.*print)',  # book unless bookplate/booklet/signed print context
    r'\bballon?\b(?!.*print|.*art)',  # balloon unless art context
]

# Signed detection
SIGNED_PATTERNS = [
    r'\bsigned\b', r'\bhand[\s-]?signed\b', r'\bautograph',
    r'\bs/n\b', r'\bap\b', r'\ba\.?p\.?\b', r'\bartist\s+proof\b',
    r'\bjsa\b', r'\bpsa\b', r'\bbas\b', r'\bbeckett\b',
]

# Numbered detection
NUMBERED_PATTERNS = [
    r'\bnumbered\b', r'\bedition\s+of\b', r'\blimited\s+edition\b',
    r'\b\d{1,4}\s*/\s*\d{1,4}\b',
    r'\bap\b', r'\ba\.?p\.?\b', r'\bartist\s+proof\b',
    r'\ble\b\s*/?\s*\d+',
]

# Medium normalization map
MEDIUM_MAP = {
    'screenprint': ['screenprint', 'screen print', 'serigraph', 'silkscreen', 'silk screen', 'Screenprint'],
    'lithograph': ['lithograph', 'litho', 'Lithograph', 'offset lithograph', 'Offset'],
    'letterpress': ['letterpress', 'letter press'],
    'giclee': ['giclee', 'giclée', 'Giclee'],
    'stencil': ['stencil', 'spray paint', 'stencil on'],
    'figure': ['figure', 'figurine', 'vinyl figure', 'sculpture', 'statue', 'companion', 'bff'],
    'print': ['print', 'Print', 'art print', 'fine art print'],
    'poster': ['poster', 'offset poster', 'Poster'],
    'painting': ['painting', 'oil on', 'acrylic on', 'Painting', 'mixed media'],
    'photo': ['photograph', 'photo', 'silver gelatin'],
}


def detect_medium(text):
    t = text.lower()
    for medium, keywords in MEDIUM_MAP.items():
        if any(kw.lower() in t for kw in keywords):
            return medium
    return ''


def detect_signed(text):
    t = text.lower()
    return any(re.search(p, t) for p in SIGNED_PATTERNS)


def detect_numbered(text):
    t = text.lower()
    return any(re.search(p, t) for p in NUMBERED_PATTERNS)


def is_junk(name):
    t = name.lower()
    for pattern in REMOVE_PATTERNS:
        if re.search(pattern, t):
            return True
    return False


def main():
    path = os.path.join(DATA_DIR, 'historical_clean.json')
    with open(path) as f:
        data = json.load(f)

    print(f'Starting: {len(data):,} records')

    removed = 0
    filled_signed = 0
    filled_numbered = 0
    filled_medium = 0
    fixed_medium = 0
    removed_short = 0

    cleaned = []
    for r in data:
        name = r.get('name', '')

        # Remove short names
        if len(name) < 10:
            removed_short += 1
            continue

        # Remove remaining junk
        if is_junk(name):
            removed += 1
            continue

        # Fill signed flag from title
        if not r.get('signed'):
            if detect_signed(name):
                r['signed'] = True
                filled_signed += 1

        # Fill numbered flag from title
        if not r.get('numbered'):
            if detect_numbered(name):
                r['numbered'] = True
                filled_numbered += 1

        # Fill/normalize medium
        current_medium = (r.get('medium', '') or '').strip()

        # Normalize existing medium
        if current_medium:
            normalized = detect_medium(current_medium)
            if normalized and normalized != current_medium:
                r['medium'] = normalized
                fixed_medium += 1
        else:
            # Detect from title
            detected = detect_medium(name)
            if detected:
                r['medium'] = detected
                filled_medium += 1

        cleaned.append(r)

    with open(path, 'w') as f:
        json.dump(cleaned, f)

    mb = os.path.getsize(path) / 1024 / 1024

    print(f'\nResults:')
    print(f'  Removed junk: {removed:,}')
    print(f'  Removed short: {removed_short:,}')
    print(f'  Final: {len(cleaned):,} records ({mb:.1f} MB)')
    print(f'\nFields filled:')
    print(f'  Signed filled: {filled_signed:,}')
    print(f'  Numbered filled: {filled_numbered:,}')
    print(f'  Medium filled: {filled_medium:,}')
    print(f'  Medium normalized: {fixed_medium:,}')

    # Verify
    signed_now = sum(1 for r in cleaned if r.get('signed'))
    numbered_now = sum(1 for r in cleaned if r.get('numbered'))
    medium_now = sum(1 for r in cleaned if r.get('medium'))
    print(f'\nAfter cleanup:')
    print(f'  Signed: {signed_now:,} ({round(signed_now/len(cleaned)*100)}%)')
    print(f'  Numbered: {numbered_now:,} ({round(numbered_now/len(cleaned)*100)}%)')
    print(f'  Medium: {medium_now:,} ({round(medium_now/len(cleaned)*100)}%)')

    mediums = Counter(r.get('medium', '') or 'unknown' for r in cleaned)
    print(f'\nMedium distribution:')
    for m, c in mediums.most_common(12):
        print(f'  {m:20} {c:>6,}')


if __name__ == '__main__':
    main()
