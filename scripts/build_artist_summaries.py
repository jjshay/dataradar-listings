#!/usr/bin/env python3
"""
Build pre-computed artist price summaries from large data files.
Run once locally before deploy to generate data/artist_price_summaries.json.
"""

import json
import os
import statistics
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
SOURCE_DIR = os.path.expanduser('~/Downloads/shepard-fairey-database')

SOURCE_FILES = {
    'kaws_data.json': 'KAWS',
    'banksy_data.json': 'Banksy',
    'mr_brainwash_data.json': 'Mr. Brainwash',
    'other_artists_data.json': None,  # artist field from data
}


def parse_price(val):
    if isinstance(val, (int, float)):
        return float(val) if val > 0 else None
    return None


def build_summaries():
    summaries = {}

    for filename, default_artist in SOURCE_FILES.items():
        filepath = os.path.join(SOURCE_DIR, filename)
        if not os.path.exists(filepath):
            print(f"Skipping {filename} — not found")
            continue

        print(f"Loading {filename}...")
        with open(filepath, 'r') as f:
            records = json.load(f)
        print(f"  {len(records)} records")

        # Group by artist + artwork name
        by_artwork = defaultdict(list)
        for rec in records:
            artist = rec.get('artist') or default_artist or 'Unknown'
            name = rec.get('name', '').strip()
            if not name:
                continue

            price = parse_price(rec.get('price'))
            date = rec.get('date', '')

            by_artwork[(artist, name)].append({
                'price': price,
                'date': date,
                'source': rec.get('source', ''),
            })

        # Compute summaries per artist
        for (artist, name), sales in by_artwork.items():
            if artist not in summaries:
                summaries[artist] = {}

            prices = [s['price'] for s in sales if s['price'] is not None]
            if not prices:
                continue

            # Sort by date desc for recent sales
            dated_sales = sorted(
                [s for s in sales if s['date'] and s['price'] is not None],
                key=lambda x: x['date'],
                reverse=True
            )
            recent = dated_sales[:5]

            dates = [s['date'] for s in sales if s['date']]
            date_range = f"{min(dates)} to {max(dates)}" if dates else ""

            summaries[artist][name] = {
                'count': len(prices),
                'min': round(min(prices), 2),
                'max': round(max(prices), 2),
                'avg': round(statistics.mean(prices), 2),
                'median': round(statistics.median(prices), 2),
                'date_range': date_range,
                'recent_sales': [
                    {'price': s['price'], 'date': s['date'], 'source': s['source']}
                    for s in recent
                ],
            }

    # Write output
    output_path = os.path.join(DATA_DIR, 'artist_price_summaries.json')
    os.makedirs(DATA_DIR, exist_ok=True)

    print(f"\nWriting summaries for {len(summaries)} artists...")
    for artist, artworks in summaries.items():
        print(f"  {artist}: {len(artworks)} artworks")

    with open(output_path, 'w') as f:
        json.dump(summaries, f, separators=(',', ':'))

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"\nOutput: {output_path} ({size_mb:.1f} MB)")


if __name__ == '__main__':
    build_summaries()
