#!/usr/bin/env python3
"""
Build canonical work normalizer + alias table.
1. Strip color/variant/size words from canonical_work to create work_id
2. Cluster all variants under one work_id
3. Build alias lookup: messy canonical → clean work_id
4. Update historical_clean.json with work_id field
"""

import json, re, os
from collections import defaultdict, Counter

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# Words to strip for work_id (colors, variants, sizes, roman numerals)
STRIP_WORDS = {
    # Colors
    'red', 'blue', 'black', 'white', 'gold', 'silver', 'green', 'pink',
    'orange', 'purple', 'grey', 'gray', 'cream', 'brown', 'yellow',
    'burgundy', 'teal', 'coral', 'navy', 'tan', 'crimson', 'ivory',
    'blush', 'bronze', 'copper', 'moss', 'sage', 'rose', 'mono',
    # Variants
    'variant', 'version', 'colorway', 'color', 'colour',
    'open', 'closed', 'flayed', 'dissected', 'resting',
    # Size/editions
    'small', 'large', 'mini', 'xl', 'big', 'oversized',
    'set', 'pair', 'lot', 'group', 'bundle',
    # Roman numerals / ordinals
    'i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii',
    '1st', '2nd', '3rd', 'first', 'second', 'third',
    # Edition markers (already stripped but catch remnants)
    'le', 'ed', 'ap', 'pp',
    # Noise that leaks through
    'new', 'rare', 'htf', 'exclusive', 'special',
    'confirmed', 'order', 'preorder', 'pre',
    'moma', 'brooklyn', 'museum', 'gallery',
    # Size numbers that are just edition runs
    '100', '200', '250', '300', '350', '400', '450', '500', '550', '600', '750', '1000',
}


def make_work_id(canonical_work, artist):
    """Strip color/variant/size words to create a universal work identifier."""
    if not canonical_work:
        return ''
    words = canonical_work.lower().split()
    # Strip noise words
    clean = [w for w in words if w not in STRIP_WORDS and len(w) > 1]
    # Remove pure numbers (edition sizes, years)
    clean = [w for w in clean if not w.isdigit()]
    # Sort for order-independent matching
    work_id = ' '.join(sorted(clean))
    return f"{artist}::{work_id}" if work_id else ''


def main():
    path = os.path.join(DATA_DIR, 'historical_clean.json')
    with open(path) as f:
        data = json.load(f)

    print(f'Records: {len(data):,}')

    # Phase 1: Generate work_id for every record
    work_groups = defaultdict(list)
    for r in data:
        cw = r.get('canonical_work', '')
        artist = r.get('artist', '')
        work_id = make_work_id(cw, artist)
        r['work_id'] = work_id
        if work_id:
            work_groups[work_id].append(r)

    print(f'Unique work_ids: {len(work_groups):,}')
    print(f'Records with work_id: {sum(1 for r in data if r.get("work_id")):,}')

    # Phase 2: Build alias table (canonical_work → work_id)
    alias_table = {}
    for work_id, records in work_groups.items():
        # All unique canonical_work values that map to this work_id
        variants = set(r.get('canonical_work', '') for r in records if r.get('canonical_work'))
        for v in variants:
            artist = records[0].get('artist', '')
            alias_key = f"{artist}::{v}"
            alias_table[alias_key] = work_id

    # Phase 3: Stats per work_id
    work_stats = {}
    for work_id, records in work_groups.items():
        if len(records) < 2:
            continue
        prices = sorted([r['price'] for r in records if r.get('price', 0) > 0])
        if not prices:
            continue
        variants = sorted(set(r.get('canonical_work', '')[:40] for r in records))
        work_stats[work_id] = {
            'count': len(records),
            'median': prices[len(prices)//2],
            'min': prices[0],
            'max': prices[-1],
            'avg': round(sum(prices)/len(prices)),
            'variants': variants[:8],
            'artist': records[0].get('artist', ''),
        }

    # Phase 4: Save
    # Update data
    with open(path, 'w') as f:
        json.dump(data, f)

    # Save alias table
    alias_path = os.path.join(DATA_DIR, 'work_aliases.json')
    output = {
        'alias_table': alias_table,
        'work_stats': work_stats,
        'meta': {
            'total_records': len(data),
            'unique_works': len(work_groups),
            'works_with_2plus': len(work_stats),
            'total_aliases': len(alias_table),
        }
    }
    with open(alias_path, 'w') as f:
        json.dump(output, f, indent=2)

    # Report
    print(f'\n{"="*60}')
    print(f'RESULTS:')
    print(f'  Records: {len(data):,}')
    print(f'  Unique work_ids: {len(work_groups):,}')
    print(f'  Works with 2+ records: {len(work_stats):,}')
    print(f'  Aliases: {len(alias_table):,}')

    # Show improvement: before vs after clustering
    old_clusters = Counter()
    new_clusters = Counter()
    for r in data:
        old_key = f"{r.get('artist','')}::{r.get('canonical_work','')}"
        new_key = r.get('work_id', '')
        old_clusters[old_key] += 1
        if new_key:
            new_clusters[new_key] += 1

    old_multi = sum(1 for c in old_clusters.values() if c >= 2)
    new_multi = sum(1 for c in new_clusters.values() if c >= 2)
    print(f'\n  Old clusters (2+ records): {old_multi:,}')
    print(f'  New clusters (2+ records): {new_multi:,}')
    print(f'  Records in old multi-clusters: {sum(c for c in old_clusters.values() if c >= 2):,}')
    print(f'  Records in new multi-clusters: {sum(c for c in new_clusters.values() if c >= 2):,}')

    # Top new clusters
    print(f'\nTop 25 work_ids:')
    for wid, stats in sorted(work_stats.items(), key=lambda x: -x[1]['count'])[:25]:
        s = stats
        print(f'  {s["count"]:>4} comps  ${s["median"]:>7,} med  {wid[:50]}')
        if len(s['variants']) > 1:
            print(f'        variants: {", ".join(s["variants"][:4])}')


if __name__ == '__main__':
    main()
