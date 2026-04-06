#!/usr/bin/env python3
"""
Build canonical work clusters from historical data.
Groups title variants of the same artwork into one canonical_work_id.

Output: data/work_clusters.json
  {
    "Shepard Fairey::peace goddess": {
      "titles": ["Peace Goddess Gold", "Peace Goddess Red", "PEACE GODDESS"],
      "records": 15,
      "median": 350,
      "range": [120, 1750]
    }
  }
"""

import json
import re
import os
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# Words to strip for clustering (beyond what canonical_work already strips)
CLUSTER_NOISE = {
    'red', 'blue', 'black', 'white', 'gold', 'silver', 'green', 'pink',
    'orange', 'purple', 'grey', 'gray', 'cream', 'brown', 'yellow',
    'variant', 'version', 'colorway', 'color', 'colour',
    'set', 'pair', 'lot', 'group',
    'small', 'large', 'mini', 'big', 'xl',
    'open', 'closed', 'flayed',
    'i', 'ii', 'iii', 'iv', 'v', 'vi',
    '1st', '2nd', '3rd', 'first', 'second', 'third',
}


def make_cluster_key(canonical_work, artist):
    """Further normalize canonical work for clustering — strip colors, variants."""
    words = canonical_work.split()
    # Remove single-char words, color words, variant words
    key_words = [w for w in words if w not in CLUSTER_NOISE and len(w) > 1]
    # Sort for order-independent matching
    key = ' '.join(sorted(key_words))
    return f"{artist}::{key}" if key else ''


def build_clusters():
    path = os.path.join(DATA_DIR, 'historical_clean.json')
    if not os.path.exists(path):
        print("Run clean_historical.py first")
        return

    with open(path) as f:
        data = json.load(f)

    print(f'Loaded {len(data)} records')

    # Phase 1: Group by cluster key
    raw_clusters = defaultdict(list)
    for rec in data:
        cw = rec.get('canonical_work', '')
        artist = rec.get('artist', '')
        if not cw or not artist:
            continue
        cluster_key = make_cluster_key(cw, artist)
        if cluster_key:
            raw_clusters[cluster_key].append(rec)

    # Phase 2: Merge small clusters into larger ones if they share 2+ words
    # (Simple approach: just use the sorted-word key)

    # Phase 3: Build output
    clusters = {}
    for key, recs in raw_clusters.items():
        if len(recs) < 2:
            continue  # Skip singletons

        prices = sorted([r['price'] for r in recs if r.get('price', 0) > 0])
        if not prices:
            continue

        titles = sorted(set(r.get('canonical_work', '')[:50] for r in recs))
        raw_titles = sorted(set(r.get('name', '')[:60] for r in recs))[:5]

        clusters[key] = {
            'titles': titles[:5],
            'sample_raw': raw_titles,
            'count': len(recs),
            'median': prices[len(prices)//2],
            'avg': round(sum(prices) / len(prices)),
            'min': prices[0],
            'max': prices[-1],
            'artist': recs[0].get('artist', ''),
            'dates': sorted(set(r.get('date', '')[:10] for r in recs if r.get('date')))[-5:],
        }

    # Also build a reverse lookup: canonical_work → cluster_key
    reverse_map = {}
    for key, recs in raw_clusters.items():
        for rec in recs:
            cw = rec.get('canonical_work', '')
            if cw:
                reverse_map[f"{rec.get('artist','')}::{cw}"] = key

    # Save
    out = {
        'clusters': clusters,
        'reverse_map': reverse_map,
        'stats': {
            'total_records': len(data),
            'total_clusters': len(clusters),
            'records_in_clusters': sum(c['count'] for c in clusters.values()),
            'avg_cluster_size': round(sum(c['count'] for c in clusters.values()) / max(len(clusters), 1), 1),
        }
    }

    out_path = os.path.join(DATA_DIR, 'work_clusters.json')
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2)

    print(f'\nClusters: {len(clusters)} (from {len(raw_clusters)} raw groups)')
    print(f'Records in clusters: {out["stats"]["records_in_clusters"]} / {len(data)}')
    print(f'Avg cluster size: {out["stats"]["avg_cluster_size"]}')
    print(f'Reverse map: {len(reverse_map)} entries')
    print(f'Output: {out_path}')

    # Top clusters
    print(f'\nTop 25 clusters:')
    for key, c in sorted(clusters.items(), key=lambda x: -x[1]['count'])[:25]:
        print(f'  {c["count"]:>4} comps  ${c["median"]:>7,} med  ${c["min"]:>6,}-${c["max"]:>7,}  {key[:55]}')


if __name__ == '__main__':
    build_clusters()
