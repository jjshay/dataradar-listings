#!/usr/bin/env python3
"""
Parse Artnet Price Database PDF exports into data/artnet_data.json.

Inputs: /Users/johnshay/Downloads/Recents/pdb-export-*.pdf
        /Users/johnshay/Desktop/AI Library/Reports/04-06-2025-artnet-price-database-*.pdf

Output: data/artnet_data.json — list of records in the schema consolidate_all.py
expects: { name, artist, price, date, source, medium, signed, url, ...extras }

Strategy:
  - Use pdfplumber to read each PDF, then split into per-lot blocks
  - Each block starts when we see an artist name line (KAWS/Shepard Fairey/etc.)
  - Pattern-match within each block for: title, medium, dimensions, year,
    sale_date, auction_house, sale_name, lot, est_low, est_high, sold_price
  - Mark signed=True (auction lots are inherently authenticated) so they survive
    the clean_historical.py 'require_signed' filter.

Run: python3 scripts/parse_artnet_pdfs.py
"""
import json, os, re, glob, sys
from collections import Counter
from datetime import datetime

try:
    import pdfplumber
except ImportError:
    print("ERROR: pdfplumber not installed. Run: pip install pdfplumber", file=sys.stderr)
    sys.exit(1)

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_DIR, 'data')
OUT_PATH = os.path.join(DATA_DIR, 'artnet_data.json')

PDF_GLOBS = [
    '/Users/johnshay/Downloads/Recents/pdb-export-*.pdf',
    '/Users/johnshay/Desktop/AI Library/Reports/04-06-2025-artnet-price-database-*.pdf',
]

ARTIST_CANON = {
    'kaws': 'KAWS',
    'kaws x medicom': 'KAWS',
    'shepard fairey': 'Shepard Fairey',
    'banksy': 'Banksy',
    'mr. brainwash': 'Mr. Brainwash',
    'mr brainwash': 'Mr. Brainwash',
    'thierry guetta': 'Mr. Brainwash',
    'death nyc': 'Death NYC',
    'bearbrick': 'Bearbrick',
    'be@rbrick': 'Bearbrick',
    'andy warhol': 'Andy Warhol',
    'warhol': 'Andy Warhol',
}

ARTIST_LINE_RE = re.compile(
    r'^\s*(KAWS(?:\s+x\s+MEDICOM)?|Shepard Fairey|SHEPARD FAIREY|Banksy|BANKSY|'
    r'Mr\.?\s*Brainwash|MR\.?\s*BRAINWASH|Thierry Guetta|Death NYC|DEATH NYC|'
    r'BE@RBRICK|Bearbrick|BEARBRICK|Andy Warhol|ANDY WARHOL)\s*$',
    re.IGNORECASE,
)
DATE_RE = re.compile(r'^\s*(\d{1,2}\s+[A-Za-z]+\s+20\d{2})\b')
LOT_RE = re.compile(r'\[Lot\s+([^\]]+)\]')
EST_RE = re.compile(r'est\.\s*([\d,]+)\s*-\s*([\d,]+)\s+([A-Z]{3})')
SOLD_TAIL_RE = re.compile(r'\b([\d,]+)\s+([A-Z]{3})\s*$')
NO_EST_RE = re.compile(r'No estimate received\s+([\d,]+)\s+([A-Z]{3})')
DIM_CM_RE = re.compile(
    r'Height\s+([\d.]+)(?:\s+x\s+Width\s+([\d.]+))?(?:\s+x\s+Depth\s+([\d.]+))?\s+cm'
)
YEAR_RE = re.compile(r'^\s*(19\d{2}|20\d{2})\s*$')
EDITION_SIZE_RE = re.compile(r'edition of\s+(\d+)', re.IGNORECASE)
EDITION_NUM_RE = re.compile(r'\b(\d+)\s*/\s*(\d+)\b')

KNOWN_HOUSES = (
    "Heritage Auctions", "Christie's", "Sotheby's", "Phillips", "Bonhams",
    "Los Angeles Modern Auctions", "LAMA", "Yongle Auction Co., Ltd.",
    "Wright", "Doyle", "Swann", "Stair", "Freeman's", "Rago", "Hindman",
    "Artcurial", "Tajan", "Dreweatts", "Lyon & Turnbull", "Lempertz",
    "Van Ham", "Ketterer", "Dorotheum", "Mainichi Auction", "SBI Art Auction",
    "Est-Ouest Auctions", "Shinwa Auction", "Poly Auction",
    "China Guardian", "Beijing Council", "Ravenel",
)
KNOWN_MEDIUMS = (
    "painted cast vinyl", "vinyl", "screenprint", "screen print", "lithograph",
    "offset lithograph", "etching", "woodcut", "linocut", "monotype",
    "acrylic", "oil", "spray paint", "mixed media", "collage", "drawing",
    "photograph", "print", "sculpture", "bronze", "fiberglass", "resin",
    "polyurethane", "porcelain", "ceramic", "wood", "canvas",
)


def find_artist(line):
    s = line.strip()
    m = ARTIST_LINE_RE.match(s)
    if not m:
        return None
    raw = m.group(1).strip().lower()
    return ARTIST_CANON.get(raw, raw.title())


def split_blocks(pdf_path):
    blocks = []
    cur_artist = None
    cur_lines = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                txt = page.extract_text(x_tolerance=2, y_tolerance=3) or ''
                for raw in txt.splitlines():
                    line = raw.rstrip()
                    if not line.strip():
                        continue
                    a = find_artist(line)
                    if a:
                        if cur_artist and cur_lines:
                            blocks.append((cur_artist, cur_lines))
                        cur_artist = a
                        cur_lines = []
                        continue
                    if cur_artist:
                        cur_lines.append(line)
            if cur_artist and cur_lines:
                blocks.append((cur_artist, cur_lines))
    except Exception as e:
        print(f"  warn: {os.path.basename(pdf_path)} parse failed: {e}", file=sys.stderr)
    return blocks


def parse_block(artist, lines):
    rec = {
        'artist': artist, 'title': '', 'medium': '', 'dim_cm': None, 'year': '',
        'sale_date': '', 'auction_house': '', 'sale_name': '', 'lot': '',
        'est_low': None, 'est_high': None, 'currency': '',
        'sold_price': None, 'edition_size': None, 'edition_number': None,
    }
    title_parts = []
    saw_dim = False
    sale_name_parts = []

    for line in lines:
        s = line.strip()
        if not s:
            continue
        if not rec['sale_date']:
            m = DATE_RE.match(s)
            if m:
                rec['sale_date'] = m.group(1)
                continue
        m = DIM_CM_RE.search(s)
        if m and rec['dim_cm'] is None:
            rec['dim_cm'] = (m.group(1), m.group(2) or '', m.group(3) or '')
            saw_dim = True
            continue
        if 'in.' in s and ('Height' in s or 'Width' in s):
            continue
        m = YEAR_RE.match(s)
        if m and not rec['year']:
            rec['year'] = m.group(1)
            continue
        m = NO_EST_RE.search(s)
        if m:
            rec['sold_price'] = float(m.group(1).replace(',', ''))
            rec['currency'] = m.group(2)
            continue
        m = EST_RE.search(s)
        if m:
            rec['est_low'] = float(m.group(1).replace(',', ''))
            rec['est_high'] = float(m.group(2).replace(',', ''))
            rec['currency'] = m.group(3)
            tail = s[m.end():].strip()
            ms = SOLD_TAIL_RE.search(tail)
            if ms:
                rec['sold_price'] = float(ms.group(1).replace(',', ''))
            continue
        m = SOLD_TAIL_RE.search(s)
        if m and 'est.' not in s and rec['sold_price'] is None and rec['sale_date']:
            if not any(k in s.lower() for k in ('height', 'width', 'depth', ' cm', 'in.')):
                rec['sold_price'] = float(m.group(1).replace(',', ''))
                if not rec['currency']:
                    rec['currency'] = m.group(2)
                continue
        m = LOT_RE.search(s)
        if m:
            rec['lot'] = m.group(1).strip()
            pre = s[: m.start()].strip(' –-')
            if pre:
                sale_name_parts.append(pre)
            continue
        for h in KNOWN_HOUSES:
            if s.startswith(h) or s == h:
                rec['auction_house'] = h
                break
        else:
            if 'Auction' in s and not rec['auction_house'] and not LOT_RE.search(s):
                if s.endswith(('Auction', 'Auctions', 'Co., Ltd.', 'Auctioneers')):
                    rec['auction_house'] = s
                    continue
            if any(w in s for w in ('Auction', 'Sale', 'Showcase', 'Modern', 'Contemporary', 'Art', 'Prints', 'Editions')) and not rec['sale_name']:
                sale_name_parts.append(s)
                continue
        sl = s.lower()
        if not rec['medium'] and any(m_ in sl for m_ in KNOWN_MEDIUMS):
            rec['medium'] = s
            continue
        m = EDITION_SIZE_RE.search(s)
        if m:
            rec['edition_size'] = int(m.group(1))
        m = EDITION_NUM_RE.search(s)
        if m and not rec['edition_number']:
            try:
                rec['edition_number'] = int(m.group(1))
                if not rec['edition_size']:
                    rec['edition_size'] = int(m.group(2))
            except Exception:
                pass
        # Title lines appear AFTER artist+date but BEFORE dimensions.
        # Anything unmatched by the above branches and not yet past the dim line
        # is a title fragment.
        if not saw_dim:
            if '↑' in s or '↓' in s:
                continue
            if s.lower().startswith('edition:'):
                continue
            # Skip the "In X days" / "X months ago" tag that often follows the date
            if re.match(r'^\s*(In\s+\d+\s+\w+|\d+\s+\w+\s+ago|Today|Tomorrow|Yesterday)\s*$', s):
                continue
            # Skip standalone numbers (lot number bits, etc.)
            if re.match(r'^\s*[\d,]+\s*$', s):
                continue
            title_parts.append(s)

    rec['title'] = ' '.join(title_parts).strip()
    rec['sale_name'] = ' '.join(sale_name_parts).strip()
    return rec


def to_consolidator_row(rec):
    """Convert into the schema consolidate_all.py's add_records() expects."""
    title = rec['title']
    price = rec['sold_price']
    if price is None and rec['est_low'] is not None and rec['est_high'] is not None:
        price = (rec['est_low'] + rec['est_high']) / 2
    date_iso = ''
    if rec['sale_date']:
        try:
            date_iso = datetime.strptime(rec['sale_date'], '%d %B %Y').strftime('%Y-%m-%d')
        except Exception:
            date_iso = rec['sale_date']
    return {
        'name': title[:120],
        'artist': rec['artist'],
        'price': float(price) if price is not None else 0,
        'date': date_iso,
        'source': 'Artnet',
        'medium': rec['medium'],
        # Auction lots are by definition authenticated — mark signed=True
        # so they survive clean_historical.py's require_signed filter
        'signed': True,
        'url': '',
        'auction_house': rec['auction_house'],
        'sale_name': rec['sale_name'],
        'lot': rec['lot'],
        'currency': rec['currency'],
        'est_low': rec['est_low'],
        'est_high': rec['est_high'],
        'sold_price': rec['sold_price'],
        'artwork_year': rec['year'],
        'edition_size': rec['edition_size'],
        'edition_number': rec['edition_number'],
    }


def main():
    pdfs = []
    for pat in PDF_GLOBS:
        pdfs.extend(sorted(glob.glob(pat)))
    pdfs = sorted(set(pdfs))
    print(f'Found {len(pdfs)} Artnet PDFs')
    if not pdfs:
        print('No PDFs to parse.')
        return

    all_records = []
    for p in pdfs:
        blocks = split_blocks(p)
        recs = [parse_block(a, lines) for a, lines in blocks]
        recs = [r for r in recs if r['title'] and r['sale_date']]
        rows = [to_consolidator_row(r) for r in recs]
        print(f'  {os.path.basename(p):60s} blocks={len(blocks):3d} rows={len(rows):3d}')
        all_records.extend(rows)

    # Dedup by artist+title+date+lot
    seen = {}
    for r in all_records:
        k = (r['artist'].lower(), r['name'].lower(), r['date'], r.get('lot', ''))
        if k not in seen:
            seen[k] = r
    deduped = list(seen.values())

    by_artist = Counter(r['artist'] for r in deduped)
    print(f'\nTotal parsed: {len(all_records)}, deduped: {len(deduped)}')
    print('By artist:')
    for a, c in by_artist.most_common():
        print(f'  {c:5d}  {a}')

    prices = sorted([r['price'] for r in deduped if r['price'] > 0], reverse=True)
    if prices:
        print(f'\nPrice top 5: {prices[:5]}')
        print(f'Price > $3K count: {sum(1 for p in prices if p > 3000)}  (these are the rows missing from current Price Radar)')

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_PATH, 'w') as f:
        json.dump(deduped, f, indent=2, default=str)
    print(f'\nWrote {OUT_PATH} ({os.path.getsize(OUT_PATH) / 1024:.1f} KB)')


if __name__ == '__main__':
    main()
