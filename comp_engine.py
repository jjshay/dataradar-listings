"""
Modular Comp Engine — Category-specific matching, recency weighting, ±25% band
Replaces the old fuzzy word-overlap matching with structured field parsing + scoring.
"""

import re
import statistics
import random
from datetime import datetime, timezone
from collections import Counter


# =============================================================================
# Utilities
# =============================================================================

def normalize_text(text):
    text = (text or '').lower()
    text = text.replace('&amp;', '&').replace('&', ' and ')
    text = re.sub(r"[''`]", "", text)
    text = re.sub(r'[^a-z0-9\s/.-]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def tokenize(text):
    return [t for t in normalize_text(text).split() if t and len(t) > 1]


def jaccard(a, b):
    sa, sb = set(tokenize(a)), set(tokenize(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def days_since(date_str):
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(str(date_str).replace('Z', '+00:00').replace('.000', ''))
        return max(0, (datetime.now(dt.tzinfo) - dt).days)
    except Exception:
        try:
            dt = datetime.strptime(str(date_str)[:10], '%Y-%m-%d')
            return max(0, (datetime.now() - dt).days)
        except Exception:
            return None


def recency_weight(date_str):
    age = days_since(date_str)
    if age is None:
        return 0.3
    if age <= 30: return 1.0
    if age <= 90: return 0.85
    if age <= 180: return 0.65
    if age <= 365: return 0.40
    return 0.20


def weighted_median(price_weight_pairs):
    if not price_weight_pairs:
        return None
    pairs = sorted([(p, w) for p, w in price_weight_pairs if p and w > 0])
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    mid = total / 2
    running = 0
    for price, weight in pairs:
        running += weight
        if running >= mid:
            return price
    return pairs[-1][0]


# =============================================================================
# Category Configs — Add new categories here
# =============================================================================

CATEGORY_CONFIGS = {
    'Shepard Fairey': {
        'artist_aliases': ['obey', 'obey giant', 'shepard fairy', 'shep fairey'],
        'title_stopwords': ['rare', 'look', 'wow', 'must', 'htf', 'beautiful', 'awesome',
                            'coa', 'with', 'certificate', 'authenticity', 'free shipping'],
        'negative_terms': ['reproduction', 'repro', 'canvas print', 'postcard', 'magazine',
                           'book plate', 'sticker', 'offset', 'print ad', 'poster only',
                           't-shirt', 'tshirt', 'pin', 'patch', 'magnet', 'keychain', 'button'],
        'require_signed': True,
        'require_numbered': True,
        'signed_patterns': [r'\bsigned\b', r'\bhand signed\b', r'\bhand-signed\b', r'\bautograph', r'\bs/n\b'],
        'numbered_patterns': [r'\bnumbered\b', r'\bedition of\b', r'\b\d{1,4}\s*/\s*\d{1,4}\b', r'\blimited\s+edition\b', r'\bap\b', r'\ba/p\b', r'\bartist proof\b'],
        'min_price': 40,
        'title_match_threshold': 0.25,
        'price_band_pct': 0.25,
    },
    'KAWS': {
        'artist_aliases': [],
        'title_stopwords': ['rare', 'look', 'wow', 'new', 'htf', 'free shipping'],
        'negative_terms': ['knockoff', 'fake', 'custom', 'bootleg', 'replica', 'inspired'],
        'require_signed': False,
        'require_numbered': False,
        'signed_patterns': [r'\bsigned\b', r'\bautograph'],
        'numbered_patterns': [r'\bnumbered\b', r'\b\d{1,4}\s*/\s*\d{1,4}\b'],
        'require_any': ['authentic', 'medicom', 'original', 'sealed', 'open edition', 'companion', 'bff', 'together', 'gone', 'holiday'],
        'min_price': 200,
        'title_match_threshold': 0.20,
        'price_band_pct': 0.25,
    },
    'Banksy': {
        'artist_aliases': [],
        'title_stopwords': ['rare', 'look', 'wow', 'amazing'],
        'negative_terms': ['reproduction', 'repro', 'canvas', 'poster', 'postcard', 'sticker', 'unofficial'],
        'require_signed': True,
        'require_numbered': False,
        'signed_patterns': [r'\bsigned\b', r'\bautograph', r'\bpow\b', r'\bgdp\b', r'\bwalled off\b', r'\bdismaland\b'],
        'numbered_patterns': [r'\bnumbered\b', r'\b\d{1,4}\s*/\s*\d{1,4}\b', r'\blimited\b'],
        'min_price': 100,
        'title_match_threshold': 0.25,
        'price_band_pct': 0.25,
    },
    'Death NYC': {
        'artist_aliases': [],
        'title_stopwords': ['rare', 'look', 'free shipping'],
        'negative_terms': ['fake', 'copy'],
        'require_signed': True,
        'require_numbered': False,
        'signed_patterns': [r'\bsigned\b'],
        'numbered_patterns': [r'\bnumbered\b', r'\b\d{1,4}\s*/\s*\d{1,4}\b'],
        'min_price': 15,
        'title_match_threshold': 0.20,
        'price_band_pct': 0.30,
    },
    '_default': {
        'artist_aliases': [],
        'title_stopwords': ['rare', 'look', 'wow', 'new', 'used', 'free shipping'],
        'negative_terms': ['reproduction', 'repro', 'fake', 'knockoff'],
        'require_signed': False,
        'require_numbered': False,
        'signed_patterns': [r'\bsigned\b'],
        'numbered_patterns': [r'\bnumbered\b', r'\b\d{1,4}\s*/\s*\d{1,4}\b'],
        'min_price': 10,
        'title_match_threshold': 0.20,
        'price_band_pct': 0.25,
    },
}


def get_config(artist):
    return CATEGORY_CONFIGS.get(artist, CATEGORY_CONFIGS['_default'])


# =============================================================================
# Record Normalization
# =============================================================================

def normalize_record(title, artist='', description='', price=0, sold_date='', source='', url=''):
    """Parse a raw listing into structured fields."""
    config = get_config(artist)
    full_text = f"{title} {description}".lower()

    # Normalize title — remove stopwords
    title_norm = normalize_text(title)
    stopwords = set(config.get('title_stopwords', []))
    title_clean = ' '.join(w for w in title_norm.split() if w not in stopwords)

    # Detect signed
    signed = any(re.search(p, full_text) for p in config.get('signed_patterns', []))

    # Detect numbered
    numbered = any(re.search(p, full_text) for p in config.get('numbered_patterns', []))

    # Detect medium
    medium = 'unknown'
    if any(w in full_text for w in ['screenprint', 'screen print', 'serigraph', 'silkscreen']):
        medium = 'screenprint'
    elif any(w in full_text for w in ['lithograph', 'litho']):
        medium = 'lithograph'
    elif any(w in full_text for w in ['giclee', 'giclée']):
        medium = 'giclee'
    elif any(w in full_text for w in ['figure', 'figurine', 'vinyl figure', 'sculpture', 'statue']):
        medium = 'figure'
    elif any(w in full_text for w in ['poster', 'offset']):
        medium = 'poster'
    elif any(w in full_text for w in ['sticker']):
        medium = 'sticker'

    # Detect dimensions
    dim_match = re.search(r'(\d{1,3}(?:\.\d+)?)\s*[x×]\s*(\d{1,3}(?:\.\d+)?)', full_text)
    width = float(dim_match.group(1)) if dim_match else None
    height = float(dim_match.group(2)) if dim_match else None

    # Edition size
    ed_match = re.search(r'\b\d{1,4}\s*/\s*(\d{1,4})\b', full_text) or re.search(r'edition of\s+(\d+)', full_text, re.I)
    edition_size = int(ed_match.group(1)) if ed_match else None

    # Framed
    framed = 'framed' in full_text and 'unframed' not in full_text

    return {
        'title_raw': title,
        'title_normalized': title_clean,
        'artist': artist,
        'signed': signed,
        'numbered': numbered,
        'medium': medium,
        'width': width,
        'height': height,
        'edition_size': edition_size,
        'framed': framed,
        'price': price,
        'sold_date': sold_date,
        'source': source,
        'url': url,
    }


# =============================================================================
# Hard Filter — Gate comps before scoring
# =============================================================================

def hard_filter(target, comp, config):
    """Returns (pass, reject_reason). If pass=False, comp is rejected."""

    full_text = f"{comp.get('title_raw', '')} {comp.get('description', '')}".lower()

    # Negative terms
    for neg in config.get('negative_terms', []):
        if neg in full_text:
            return False, f'negative: {neg}'

    # Min price
    min_p = config.get('min_price', 0)
    if comp.get('price', 0) > 0 and comp['price'] < min_p:
        return False, f'below min ${min_p}'

    # Require signed (for SF, Banksy, etc.)
    if config.get('require_signed') and target.get('signed') and not comp.get('signed'):
        return False, 'not signed'

    # Require numbered
    if config.get('require_numbered') and target.get('numbered') and not comp.get('numbered'):
        return False, 'not numbered'

    # Require any (for KAWS authenticity)
    if config.get('require_any'):
        if not any(kw in full_text for kw in config['require_any']):
            return False, 'missing required keyword'

    # Title similarity threshold
    sim = jaccard(target.get('title_normalized', ''), comp.get('title_normalized', ''))
    threshold = config.get('title_match_threshold', 0.20)
    if sim < threshold:
        return False, f'title sim {sim:.2f} < {threshold}'

    # Medium mismatch (soft — only reject if clearly wrong type)
    if target.get('medium') and comp.get('medium'):
        if target['medium'] in ('screenprint', 'lithograph', 'giclee') and comp['medium'] in ('sticker', 'poster', 'figure'):
            return False, f'medium mismatch: {target["medium"]} vs {comp["medium"]}'
        if target['medium'] == 'figure' and comp['medium'] in ('screenprint', 'lithograph', 'poster'):
            return False, f'medium mismatch: figure vs print'

    return True, None


# =============================================================================
# Scoring — Weighted similarity
# =============================================================================

def score_comp(target, comp):
    """Score a comp 0-100 based on similarity to target."""
    score = 0

    # Title similarity (0-30)
    sim = jaccard(target.get('title_normalized', ''), comp.get('title_normalized', ''))
    score += sim * 30

    # Signed match (15)
    if target.get('signed') == comp.get('signed'):
        score += 15

    # Numbered match (15)
    if target.get('numbered') == comp.get('numbered'):
        score += 15

    # Medium match (10)
    if target.get('medium') and comp.get('medium') and target['medium'] == comp['medium']:
        score += 10

    # Dimensions match (10)
    if target.get('width') and comp.get('width'):
        w_diff = abs(target['width'] - comp['width']) / max(target['width'], 1)
        h_diff = abs((target.get('height') or 0) - (comp.get('height') or 0)) / max(target.get('height') or 1, 1)
        if w_diff < 0.1 and h_diff < 0.1:
            score += 10
        elif w_diff < 0.2 and h_diff < 0.2:
            score += 5

    # Edition size proximity (5)
    if target.get('edition_size') and comp.get('edition_size'):
        diff = abs(target['edition_size'] - comp['edition_size']) / max(target['edition_size'], 1)
        if diff < 0.1:
            score += 5

    # Recency bonus (15)
    age = days_since(comp.get('sold_date'))
    if age is not None:
        if age <= 30: score += 15
        elif age <= 90: score += 10
        elif age <= 180: score += 5

    return round(score, 1)


# =============================================================================
# Main Comp Pipeline
# =============================================================================

def find_comps(target_title, target_artist, target_price, candidate_records, learned_rejections=None):
    """
    Full comp pipeline:
    1. Normalize target + candidates
    2. Hard filter (category-specific)
    3. Score remaining
    4. ±25% price band cleanup
    5. Recency-weighted pricing

    Returns: {comps, rejected, pricing, confidence, notes}
    """
    config = get_config(target_artist)
    target = normalize_record(target_title, target_artist, price=target_price)

    accepted = []
    rejected = []

    # Load learned rejections if provided
    learned_words = set()
    if learned_rejections:
        for rule in learned_rejections.get('learned_rules', []):
            if rule.get('count', 0) >= 3:
                learned_words.add(rule.get('word', ''))

    for rec in candidate_records:
        comp = normalize_record(
            rec.get('title', rec.get('name', '')),
            target_artist,
            description=rec.get('description', ''),
            price=rec.get('price', 0),
            sold_date=rec.get('sold_date', rec.get('date', '')),
            source=rec.get('source', ''),
            url=rec.get('url', ''),
        )

        # Check learned rejections
        if learned_words:
            comp_words = set(tokenize(comp['title_normalized']))
            if comp_words & learned_words:
                rejected.append({**comp, 'reject_reason': f'learned rejection: {comp_words & learned_words}'})
                continue

        # Hard filter
        passes, reason = hard_filter(target, comp, config)
        if not passes:
            rejected.append({**comp, 'reject_reason': reason})
            continue

        # Score
        comp['score'] = score_comp(target, comp)
        accepted.append(comp)

    # Sort by score
    accepted.sort(key=lambda x: -x['score'])

    # ±25% price band cleanup
    prices = [c['price'] for c in accepted if c.get('price', 0) > 0]
    if len(prices) >= 3:
        center = statistics.median(prices)
        band = config.get('price_band_pct', 0.25)
        lo, hi = center * (1 - band), center * (1 + band)
        in_band = []
        out_band = []
        for c in accepted:
            if c.get('price', 0) > 0 and lo <= c['price'] <= hi:
                in_band.append(c)
            elif c.get('price', 0) > 0:
                out_band.append({**c, 'reject_reason': f'outlier: ${c["price"]:.0f} outside ${lo:.0f}-${hi:.0f}'})
            else:
                in_band.append(c)  # No price = keep
        accepted = in_band
        rejected.extend(out_band)

    # Recency-weighted pricing
    price_weights = [(c['price'], recency_weight(c.get('sold_date'))) for c in accepted if c.get('price', 0) > 0]
    wm = weighted_median(price_weights)
    raw_prices = [c['price'] for c in accepted if c.get('price', 0) > 0]
    raw_median = statistics.median(raw_prices) if raw_prices else None
    raw_avg = round(sum(raw_prices) / len(raw_prices)) if raw_prices else None

    # Confidence
    recent_count = sum(1 for c in accepted if (days_since(c.get('sold_date')) or 999) <= 180)
    if len(accepted) >= 5 and recent_count >= 4:
        confidence = 'high'
    elif len(accepted) >= 3:
        confidence = 'medium'
    elif len(accepted) >= 1:
        confidence = 'low'
    else:
        confidence = 'none'

    estimated = round(wm) if wm else None

    return {
        'comps': [{
            'title': c.get('title_raw', '')[:80],
            'price': c['price'],
            'score': c['score'],
            'signed': c['signed'],
            'numbered': c['numbered'],
            'medium': c['medium'],
            'sold_date': c.get('sold_date', ''),
            'source': c.get('source', ''),
            'url': c.get('url', ''),
        } for c in accepted[:20]],
        'rejected': [{
            'title': r.get('title_raw', '')[:60],
            'price': r.get('price', 0),
            'reason': r.get('reject_reason', 'unknown'),
        } for r in rejected[:15]],
        'pricing': {
            'estimated': estimated,
            'low': round(estimated * 0.90) if estimated else None,
            'high': round(estimated * 1.10) if estimated else None,
            'median': round(raw_median) if raw_median else None,
            'avg': raw_avg,
            'weighted_median': round(wm) if wm else None,
        },
        'stats': {
            'total_candidates': len(candidate_records),
            'accepted': len(accepted),
            'rejected': len(rejected),
            'confidence': confidence,
        },
    }
