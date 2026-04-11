"""
DATARADAR Comp Engine v3 — Modular, category-specific comparable sales pricing.

Architecture:
  1. Normalize raw records into structured fields
  2. Hard filter (category rules gate comps before scoring)
  3. Score & rank surviving comps
  4. ±25% outlier cleanup around preliminary center
  5. Recency-weighted pricing
  6. Confidence + explainability

Two modes:
  - PRICING: strict, high-precision, for inventory valuation
  - DEAL_FINDER: broader recall, labels match quality
"""

import re
import statistics
from datetime import datetime, timezone
from collections import Counter

# =============================================================================
# Category Configs — edit these to tune per-category behavior
# =============================================================================

CATEGORY_CONFIGS = {

    'fine_art': {
        'display_name': 'Fine Art Prints',
        'artist_aliases': {
            'shepard fairy': 'shepard fairey',
            'shep fairey': 'shepard fairey',
            'obey giant': 'shepard fairey',
            'obey': 'shepard fairey',
            'mr brainwash': 'mr. brainwash',
            'mbw': 'mr. brainwash',
            'thierry guetta': 'mr. brainwash',
        },
        'title_stopwords': [
            'rare', 'look', 'l@@k', 'wow', 'must', 'see', 'htf', 'beautiful',
            'awesome', 'amazing', 'coa', 'with', 'certificate', 'authenticity',
            'free', 'shipping', 'fast', 'new', 'mint', 'great', 'nice', 'buy',
            'now', 'hot', 'sale', 'deal', 'invest', 'investment',
        ],
        'negative_terms': [
            'reproduction', 'repro', 'replica', 'tribute', 'fan art', 'fanart',
            'inspired by', 'in the style of', 'after ', 'canvas print',
            'postcard', 'magazine', 'book plate', 'sticker', 'offset',
            'print ad', 'poster only', 't-shirt', 'tshirt', 'tee ', 'pin',
            'patch', 'magnet', 'keychain', 'button', 'not signed', 'unsigned',
            'custom frame only', 'digital print', 'giclee copy',
            'museum poster', 'exhibition poster', 'reprint',
        ],
        'signed_patterns': [
            r'\bsigned\b', r'\bhand[\s-]?signed\b', r'\bautograph',
            r'\bs/n\b', r'\bsigned by\b',
        ],
        'numbered_patterns': [
            r'\bnumbered\b', r'\blimited\s+edition\b', r'\bedition\s+of\b',
            r'\b\d{1,4}\s*/\s*\d{1,4}\b',  # 123/450 style
            r'\bap\b', r'\ba\.?p\.?\b', r'\bartist\s+proof\b',
        ],
        'medium_map': {
            'screenprint': ['screenprint', 'screen print', 'serigraph', 'silkscreen'],
            'lithograph': ['lithograph', 'litho'],
            'giclee': ['giclee', 'giclée'],
            'letterpress': ['letterpress', 'letter press'],
            'stencil': ['stencil', 'spray paint'],
            'poster': ['poster', 'offset'],
            'painting': ['painting', 'oil on', 'acrylic on'],
        },
        'pricing_mode': {
            'require_signed_match': True,
            'require_numbered_match': True,
            'require_medium_match': False,
            'title_threshold': 0.10,  # Lower — let LLM be the final gate
            'min_price': 40,
        },
        'deal_finder_mode': {
            'require_signed_match': False,
            'require_numbered_match': False,
            'require_medium_match': False,
            'title_threshold': 0.06,
            'min_price': 20,
        },
        'scoring_weights': {
            'title': 30,
            'signed': 15,
            'numbered': 15,
            'medium': 10,
            'dimensions': 10,
            'edition_size': 5,
            'recency': 15,
        },
    },

    'figures': {
        'display_name': 'Figures & Toys',
        'artist_aliases': {},
        'title_stopwords': [
            'rare', 'look', 'wow', 'new', 'htf', 'free', 'shipping', 'fast',
            'authentic', 'genuine', '100%',
        ],
        'negative_terms': [
            'knockoff', 'fake', 'custom', 'bootleg', 'replica', 'inspired',
            'repro', 'reproduction', 'diy', 'homemade',
        ],
        'signed_patterns': [r'\bsigned\b', r'\bautograph'],
        'numbered_patterns': [r'\bnumbered\b', r'\b\d{1,4}\s*/\s*\d{1,4}\b'],
        'medium_map': {
            'vinyl': ['vinyl', 'vinyl figure'],
            'plush': ['plush', 'stuffed'],
            'resin': ['resin'],
            'metal': ['metal', 'diecast'],
        },
        'pricing_mode': {
            'require_signed_match': False,
            'require_numbered_match': False,
            'require_medium_match': False,
            'title_threshold': 0.10,
            'min_price': 50,
        },
        'deal_finder_mode': {
            'require_signed_match': False,
            'require_numbered_match': False,
            'require_medium_match': False,
            'title_threshold': 0.05,
            'min_price': 20,
        },
        'scoring_weights': {
            'title': 40,
            'signed': 5,
            'numbered': 5,
            'medium': 10,
            'dimensions': 5,
            'edition_size': 5,
            'recency': 15,
        },
    },

    'autographs': {
        'display_name': 'Autographs & Signed Items',
        'artist_aliases': {},
        'title_stopwords': [
            'rare', 'look', 'wow', 'amazing', 'great', 'free', 'shipping',
        ],
        'negative_terms': [
            'facsimile', 'printed', 'preprint', 'stamped', 'auto pen',
            'secretarial', 'not authenticated', 'no coa', 'unsigned',
        ],
        'signed_patterns': [
            r'\bsigned\b', r'\bautograph', r'\bjsa\b', r'\bpsa\b',
            r'\bbas\b', r'\bbeckett\b', r'\bcoa\b',
        ],
        'numbered_patterns': [r'\bnumbered\b', r'\b\d{1,4}\s*/\s*\d{1,4}\b'],
        'medium_map': {
            'photo': ['photo', 'photograph', '8x10', '11x14'],
            'document': ['letter', 'document', 'contract'],
            'book': ['book', 'bookplate'],
            'album': ['album', 'vinyl', 'record', 'lp'],
            'instrument': ['guitar', 'pickguard', 'drumhead', 'drumstick'],
        },
        'pricing_mode': {
            'require_signed_match': True,
            'require_numbered_match': False,
            'require_medium_match': True,
            'title_threshold': 0.08,
            'min_price': 25,
        },
        'deal_finder_mode': {
            'require_signed_match': False,
            'require_numbered_match': False,
            'require_medium_match': False,
            'title_threshold': 0.05,
            'min_price': 10,
        },
        'scoring_weights': {
            'title': 35,
            'signed': 15,
            'numbered': 5,
            'medium': 15,
            'dimensions': 5,
            'edition_size': 0,
            'recency': 15,
        },
    },

    '_default': {
        'display_name': 'General',
        'artist_aliases': {},
        'title_stopwords': ['rare', 'look', 'wow', 'new', 'used', 'free', 'shipping'],
        'negative_terms': ['reproduction', 'repro', 'fake', 'knockoff'],
        'signed_patterns': [r'\bsigned\b'],
        'numbered_patterns': [r'\bnumbered\b', r'\b\d{1,4}\s*/\s*\d{1,4}\b'],
        'medium_map': {},
        'pricing_mode': {
            'require_signed_match': False,
            'require_numbered_match': False,
            'require_medium_match': False,
            'title_threshold': 0.08,
            'min_price': 10,
        },
        'deal_finder_mode': {
            'require_signed_match': False,
            'require_numbered_match': False,
            'require_medium_match': False,
            'title_threshold': 0.04,
            'min_price': 5,
        },
        'scoring_weights': {
            'title': 40,
            'signed': 10,
            'numbered': 5,
            'medium': 5,
            'dimensions': 5,
            'edition_size': 5,
            'recency': 15,
        },
    },
}

# Map artist names to categories
ARTIST_CATEGORY_MAP = {
    'Shepard Fairey': 'fine_art',
    'Banksy': 'fine_art',
    'Mr. Brainwash': 'fine_art',
    'Death NYC': 'fine_art',
    'Invader': 'fine_art',
    'Stik': 'fine_art',
    'Retna': 'fine_art',
    'Warhol': 'fine_art',
    'Basquiat': 'fine_art',
    'Haring': 'fine_art',
    'Hirst': 'fine_art',
    'Murakami': 'fine_art',
    'Arsham': 'fine_art',
    'Nara': 'fine_art',
    'Futura': 'fine_art',
    'Brantley': 'fine_art',
    'KAWS': 'figures',
    'Bearbrick': 'figures',
    'Signed Apollo': 'autographs',
    'Space/NASA': 'autographs',
    'Beatles/Rock': 'autographs',
    'Signed Music': 'autographs',
    'Pickguard': 'autographs',
}

# Recency weights
RECENCY_WEIGHTS = [
    (30, 1.00),
    (90, 0.85),
    (180, 0.65),
    (365, 0.40),
    (9999, 0.20),
]

PRICE_BAND_PCT = 0.25  # ±25%


# =============================================================================
# Utility functions
# =============================================================================

def normalize_text(text):
    text = (text or '').lower()
    text = text.replace('&amp;', ' and ').replace('&', ' and ')
    text = re.sub(r"[''`\u2018\u2019]", "", text)
    text = re.sub(r'[^a-z0-9\s/.-]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def tokenize(text):
    return [t for t in normalize_text(text).split() if len(t) > 1]


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
        return 0.30
    for threshold, weight in RECENCY_WEIGHTS:
        if age <= threshold:
            return weight
    return 0.20


def source_weight(source):
    """Weight by data source — YOUR sales are most reliable."""
    s = (source or '').lower()
    if 'my sale' in s or 'your sale' in s or 'my sales' in s:
        return 2.0  # Your own sales = best comp
    elif 'ebay sold' in s:
        return 1.5  # Confirmed sold prices
    elif 'worthpoint' in s or 'historical' in s:
        return 1.0  # Database records
    elif 'ebay active' in s:
        return 0.7  # Asking prices, not sold
    return 0.8


def weighted_median(price_weight_pairs):
    pairs = sorted([(p, w) for p, w in price_weight_pairs if p and p > 0 and w > 0])
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


def get_config(artist):
    cat = ARTIST_CATEGORY_MAP.get(artist, '_default')
    return CATEGORY_CONFIGS.get(cat, CATEGORY_CONFIGS['_default'])


def get_category(artist):
    return ARTIST_CATEGORY_MAP.get(artist, '_default')


# =============================================================================
# Record normalization — extract structured fields from raw text
# =============================================================================

WORK_ID_STRIP = {
    'red', 'blue', 'black', 'white', 'gold', 'silver', 'green', 'pink',
    'orange', 'purple', 'grey', 'gray', 'cream', 'brown', 'yellow',
    'burgundy', 'teal', 'blush', 'bronze', 'copper', 'moss', 'rose', 'mono',
    'variant', 'version', 'colorway', 'open', 'closed', 'flayed', 'dissected',
    'small', 'large', 'mini', 'xl', 'set', 'pair', 'lot',
    'i', 'ii', 'iii', 'iv', 'v', 'vi', 'le', 'ed', 'ap',
    'new', 'rare', 'exclusive', 'special', 'confirmed', 'order', 'preorder', 'pre',
    'moma', 'brooklyn', 'museum', 'gallery',
    '100', '200', '250', '300', '350', '400', '450', '500', '550', '600', '750', '1000',
}


def make_work_id(title_clean, artist):
    """Create a universal work identifier — strips colors/variants for matching."""
    words = title_clean.lower().split()
    clean = [w for w in words if w not in WORK_ID_STRIP and len(w) > 1 and not w.isdigit()]
    wid = ' '.join(sorted(clean))
    return f"{artist}::{wid}" if wid else ''


def normalize_record(title, artist='', description='', price=0, sold_date='', source='', url='', **extra):
    """Parse raw listing into structured fields using category-specific rules."""
    config = get_config(artist)
    full_text = f"{title} {description}".lower()

    # Normalize artist (typo correction + aliases)
    artist_norm = normalize_text(artist)
    for alias, canonical in config.get('artist_aliases', {}).items():
        if alias in artist_norm or alias in normalize_text(title):
            artist_norm = canonical
            break

    # Clean title — remove stopwords
    title_norm = normalize_text(title)
    stops = set(config.get('title_stopwords', []))
    title_clean = ' '.join(w for w in title_norm.split() if w not in stops)

    # Extract signed flag
    signed = any(re.search(p, full_text) for p in config.get('signed_patterns', []))

    # Extract numbered flag
    numbered = any(re.search(p, full_text) for p in config.get('numbered_patterns', []))

    # Extract medium
    medium = ''
    for med_name, keywords in config.get('medium_map', {}).items():
        if any(kw in full_text for kw in keywords):
            medium = med_name
            break

    # Extract dimensions
    dim = re.search(r'(\d{1,3}(?:\.\d+)?)\s*[x×]\s*(\d{1,3}(?:\.\d+)?)', full_text)
    width = float(dim.group(1)) if dim else None
    height = float(dim.group(2)) if dim else None

    # Extract edition size
    ed = re.search(r'\b\d{1,4}\s*/\s*(\d{1,4})\b', full_text) or re.search(r'edition\s+of\s+(\d+)', full_text, re.I)
    edition_size = int(ed.group(1)) if ed else None

    # Framed
    framed = None
    if 'unframed' in full_text:
        framed = False
    elif 'framed' in full_text:
        framed = True

    # Condition
    condition = ''
    if any(w in full_text for w in ['mint', 'pristine', 'perfect', 'flawless']):
        condition = 'mint'
    elif any(w in full_text for w in ['excellent', 'near mint', 'nm']):
        condition = 'excellent'
    elif any(w in full_text for w in ['good', 'very good', 'vg']):
        condition = 'good'
    elif any(w in full_text for w in ['fair', 'poor', 'damaged', 'crease', 'tear', 'foxing', 'stain']):
        condition = 'fair'

    # AP detection
    is_ap = bool(re.search(r'\bap\b|\bartist\s*proof\b|\ba\.p\.?\b', full_text))

    # Edition band
    edition_band = ''
    if edition_size:
        if edition_size <= 50: edition_band = 'micro'
        elif edition_size <= 100: edition_band = 'small'
        elif edition_size <= 200: edition_band = 'medium'
        elif edition_size <= 350: edition_band = 'standard'
        elif edition_size <= 500: edition_band = 'large'
        else: edition_band = 'mass'

    # Year
    yr = re.search(r'\b(19[5-9]\d|20[0-2]\d)\b', title)
    year = int(yr.group(1)) if yr else None

    # Generate work_id for matching
    work_id = make_work_id(title_clean, artist_norm or artist)

    return {
        'title_raw': title[:120],
        'title_normalized': title_clean,
        'work_id': work_id,
        'artist_raw': artist,
        'artist_normalized': artist_norm,
        'signed': signed,
        'numbered': numbered,
        'medium': medium,
        'width': width,
        'height': height,
        'edition_size': edition_size,
        'framed': framed,
        'condition': condition,
        'is_ap': is_ap,
        'edition_band': edition_band,
        'year': year,
        'price': price,
        'sold_date': sold_date,
        'source': source,
        'url': url,
        'category': get_category(artist),
    }


# =============================================================================
# Hard filters — gate comps BEFORE scoring
# =============================================================================

def hard_filter(target, comp, config, mode='pricing'):
    """Returns (pass: bool, reject_reason: str|None).
    mode = 'pricing' (strict) or 'deal_finder' (broader)."""
    mode_config = config.get(f'{mode}_mode', config.get('pricing_mode', {}))
    full_text = f"{comp.get('title_raw', '')}".lower()

    # 1. Negative terms
    for neg in config.get('negative_terms', []):
        if neg in full_text:
            return False, f'negative_term: {neg}'

    # 2. Min price
    min_p = mode_config.get('min_price', 0)
    if comp.get('price', 0) > 0 and comp['price'] < min_p:
        return False, f'below_min_${min_p}'

    # 3. Signed match — critical for pricing accuracy
    # Signed items should NOT comp against unsigned (and vice versa)
    if mode_config.get('require_signed_match'):
        if target.get('signed') and not comp.get('signed'):
            return False, 'unsigned_comp_for_signed_item'
        if not target.get('signed') and comp.get('signed'):
            return False, 'signed_comp_for_unsigned_item'

    # 4. Numbered match (pricing mode)
    if mode_config.get('require_numbered_match') and target.get('numbered') and not comp.get('numbered'):
        return False, 'unnumbered'

    # 5. Medium match (if required)
    if mode_config.get('require_medium_match'):
        if target.get('medium') and comp.get('medium') and target['medium'] != comp['medium']:
            return False, f'medium_mismatch: {target["medium"]} vs {comp["medium"]}'

    # 6. Medium hard rejects (print vs figure, etc.)
    if target.get('medium') and comp.get('medium'):
        prints = {'screenprint', 'lithograph', 'giclee', 'letterpress', 'stencil', 'print'}
        objects = {'vinyl', 'plush', 'resin', 'metal', 'figure'}
        if target['medium'] in prints and comp['medium'] in objects:
            return False, f'type_mismatch: print vs object'
        if target['medium'] in objects and comp['medium'] in prints:
            return False, f'type_mismatch: object vs print'
        # Poster vs screenprint — different price tiers
        if target['medium'] == 'screenprint' and comp['medium'] == 'poster':
            return False, 'medium_mismatch: screenprint vs poster'
        if target['medium'] == 'poster' and comp['medium'] == 'screenprint':
            return False, 'medium_mismatch: poster vs screenprint'

    # 6b. Dimensions gating — same artwork in different sizes = different price
    if target.get('width') and comp.get('width') and target.get('height') and comp.get('height'):
        tw, th = sorted([target['width'], target['height']])
        cw, ch = sorted([comp['width'], comp['height']])
        size_diff_w = abs(tw - cw) / max(tw, 1)
        size_diff_h = abs(th - ch) / max(th, 1)
        if size_diff_w > 0.30 or size_diff_h > 0.30:
            return False, f'dimensions_mismatch: {tw}x{th} vs {cw}x{ch}'

    # 6c. Edition size gating — /50 AP vs /450 open = very different value
    if target.get('edition_size') and comp.get('edition_size'):
        size_ratio = max(target['edition_size'], comp['edition_size']) / max(min(target['edition_size'], comp['edition_size']), 1)
        if size_ratio > 3:
            return False, f'edition_mismatch: /{target["edition_size"]} vs /{comp["edition_size"]}'

    # 6c2. Edition BAND gating — micro (/50) should not comp against mass (/600+)
    t_band = target.get('edition_band', '')
    c_band = comp.get('edition_band', '')
    if t_band and c_band:
        band_order = {'micro': 0, 'small': 1, 'medium': 2, 'standard': 3, 'large': 4, 'mass': 5}
        t_idx = band_order.get(t_band, 3)
        c_idx = band_order.get(c_band, 3)
        if abs(t_idx - c_idx) >= 3:
            return False, f'edition_band_mismatch: {t_band} vs {c_band}'

    # 6d. Framed vs unframed (soft — penalize in pricing, don't hard reject)
    # Handled in scoring instead

    # 6e. Condition — reject damaged comps for mint items in pricing mode
    if mode == 'pricing':
        if target.get('condition') in ('mint', 'excellent') and comp.get('condition') == 'fair':
            return False, 'condition_mismatch: mint vs damaged'

    # 7. Title similarity threshold — skip if work_id already matches
    work_id_match = target.get('work_id') and comp.get('work_id') and target['work_id'] == comp['work_id']
    if not work_id_match:
        sim = jaccard(target.get('title_normalized', ''), comp.get('title_normalized', ''))
        threshold = mode_config.get('title_threshold', 0.15)
        if sim < threshold:
            return False, f'title_sim_{sim:.2f}_below_{threshold}'

    # 8. Age limit (soft — only for very old)
    age = days_since(comp.get('sold_date'))
    if age is not None and age > 730:
        return False, f'too_old_{age}d'

    return True, None


# =============================================================================
# Scoring — weighted similarity AFTER hard filter
# =============================================================================

def score_comp(target, comp, config):
    """Score a comp 0-100 based on category-specific weighted similarity."""
    weights = config.get('scoring_weights', {})
    score = 0

    # Work ID match — strongest signal (same artwork regardless of color variant)
    if target.get('work_id') and comp.get('work_id') and target['work_id'] == comp['work_id']:
        score += 25  # Big bonus for same work

    # Title (0-30 typically)
    sim = jaccard(target.get('title_normalized', ''), comp.get('title_normalized', ''))
    score += sim * weights.get('title', 30)

    # Signed match
    if target.get('signed') == comp.get('signed'):
        score += weights.get('signed', 10)

    # Numbered match
    if target.get('numbered') == comp.get('numbered'):
        score += weights.get('numbered', 10)

    # Medium match
    if target.get('medium') and comp.get('medium') and target['medium'] == comp['medium']:
        score += weights.get('medium', 10)

    # Dimensions (within 10% tolerance)
    if target.get('width') and comp.get('width'):
        w_diff = abs(target['width'] - comp['width']) / max(target['width'], 1)
        h_diff = abs((target.get('height') or 0) - (comp.get('height') or 0)) / max(target.get('height') or 1, 1)
        if w_diff < 0.1 and h_diff < 0.1:
            score += weights.get('dimensions', 5)
        elif w_diff < 0.2:
            score += weights.get('dimensions', 5) * 0.5

    # Edition size proximity
    if target.get('edition_size') and comp.get('edition_size'):
        diff = abs(target['edition_size'] - comp['edition_size']) / max(target['edition_size'], 1)
        if diff < 0.1:
            score += weights.get('edition_size', 5)
        elif diff < 0.3:
            score += weights.get('edition_size', 5) * 0.5

    # Edition band match
    if target.get('edition_band') and comp.get('edition_band'):
        if target['edition_band'] == comp['edition_band']:
            score += 5  # Same scarcity tier
        elif abs({'micro':0,'small':1,'medium':2,'standard':3,'large':4,'mass':5}.get(target['edition_band'],3) - {'micro':0,'small':1,'medium':2,'standard':3,'large':4,'mass':5}.get(comp['edition_band'],3)) == 1:
            score += 2  # Adjacent tier

    # AP bonus/match
    if target.get('is_ap') and comp.get('is_ap'):
        score += 8  # Both APs — strong match
    elif target.get('is_ap') != comp.get('is_ap') and (target.get('is_ap') or comp.get('is_ap')):
        score -= 5  # One is AP, other isn't — different price tier

    # Framed match (penalize mismatch, don't block)
    if target.get('framed') is not None and comp.get('framed') is not None:
        if target['framed'] == comp['framed']:
            score += 3
        else:
            score -= 5  # Framed adds $50-200, mixing skews pricing

    # Condition match
    cond_order = {'mint': 4, 'excellent': 3, 'good': 2, 'fair': 1, '': 0}
    t_cond = cond_order.get(target.get('condition', ''), 0)
    c_cond = cond_order.get(comp.get('condition', ''), 0)
    if t_cond and c_cond:
        if t_cond == c_cond:
            score += 3
        elif abs(t_cond - c_cond) >= 2:
            score -= 5  # Big condition gap

    # Phrase matching bonus — "Peace Goddess" as phrase, not just overlapping words
    target_title = target.get('title_normalized', '').lower()
    comp_title = comp.get('title_normalized', '').lower()
    # Check 2-3 word phrases
    target_words_list = target_title.split()
    for i in range(len(target_words_list) - 1):
        phrase = f"{target_words_list[i]} {target_words_list[i+1]}"
        if len(phrase) > 5 and phrase in comp_title:
            score += 8  # Strong phrase match
            break

    # Year match bonus
    if target.get('year') and comp.get('year'):
        year_diff = abs(target['year'] - comp['year'])
        if year_diff == 0: score += 5
        elif year_diff <= 2: score += 3
        elif year_diff <= 5: score += 1
        if year_diff > 10: score -= 5

    # Recency bonus
    age = days_since(comp.get('sold_date'))
    if age is not None:
        if age <= 30: score += weights.get('recency', 15)
        elif age <= 90: score += weights.get('recency', 15) * 0.7
        elif age <= 180: score += weights.get('recency', 15) * 0.4

    # Source bonus (your own sales most valuable)
    sw = source_weight(comp.get('source', ''))
    if sw > 1.0: score += 10  # Bonus for your own sales

    return round(score, 1)


# =============================================================================
# Pricing — recency-weighted with ±25% cleanup
# =============================================================================

def compute_pricing(comps):
    """Compute pricing from accepted comps with IQR trim + recency weighting + ±25% band."""
    if not comps:
        return {'estimated': None, 'low': None, 'high': None, 'median': None,
                'avg': None, 'weighted_median': None, 'outliers_removed': 0}

    prices = sorted([c['price'] for c in comps if c.get('price', 0) > 0])
    if not prices:
        return {'estimated': None, 'low': None, 'high': None, 'median': None,
                'avg': None, 'weighted_median': None, 'outliers_removed': 0}

    # Step 0: IQR trim — remove bottom 25% and top 25% of prices
    if len(prices) >= 4:
        q1_idx = len(prices) // 4
        q3_idx = 3 * len(prices) // 4
        iqr_lo = prices[q1_idx]
        iqr_hi = prices[q3_idx]
        comps = [c for c in comps if c.get('price', 0) >= iqr_lo and c['price'] <= iqr_hi]
        prices = sorted([c['price'] for c in comps if c.get('price', 0) > 0])

    if not prices:
        return {'estimated': None, 'low': None, 'high': None, 'median': None,
                'avg': None, 'weighted_median': None, 'outliers_removed': 0}

    # Step 1: Preliminary center (raw median of IQR-trimmed data)
    center = statistics.median(prices)

    # Step 2: ±25% band cleanup on top of IQR
    lo = center * (1 - PRICE_BAND_PCT)
    hi = center * (1 + PRICE_BAND_PCT)
    in_band = [c for c in comps if c.get('price', 0) > 0 and lo <= c['price'] <= hi]
    outliers = len(comps) - len(in_band)

    if not in_band:
        in_band = comps  # Don't discard everything

    # Step 3: Recency + source weighted pricing
    # Discount eBay active prices by 12% (asking price ≠ sold price)
    def adjusted_price(c):
        p = c['price']
        if 'active' in (c.get('source', '') or '').lower():
            p = p * 0.88  # ~12% discount for asking vs selling
        return p

    pw = [(adjusted_price(c), recency_weight(c.get('sold_date')) * source_weight(c.get('source', ''))) for c in in_band if c.get('price', 0) > 0]
    wm = weighted_median(pw)
    wa = sum(p * w for p, w in pw) / max(sum(w for _, w in pw), 0.01) if pw else None

    clean_prices = [c['price'] for c in in_band if c.get('price', 0) > 0]
    raw_med = statistics.median(clean_prices) if clean_prices else None
    raw_avg = round(sum(clean_prices) / len(clean_prices)) if clean_prices else None

    estimated = round(wm) if wm else round(raw_med) if raw_med else None

    return {
        'estimated': estimated,
        'low': round(estimated * 0.90) if estimated else None,
        'high': round(estimated * 1.10) if estimated else None,
        'median': round(raw_med) if raw_med else None,
        'avg': raw_avg,
        'weighted_median': round(wm) if wm else None,
        'weighted_avg': round(wa) if wa else None,
        'outliers_removed': outliers,
        'center_used': round(center) if center else None,
        'band': f'${round(lo)}-${round(hi)}' if center else None,
    }


# =============================================================================
# Confidence scoring
# =============================================================================

def compute_confidence(comps):
    """Confidence label based on comp count and recency."""
    if not comps:
        return 'none'
    recent_180 = sum(1 for c in comps if (days_since(c.get('sold_date')) or 999) <= 180)
    total = len(comps)
    if total >= 5 and recent_180 >= 4:
        return 'high'
    if total >= 3 and recent_180 >= 2:
        return 'medium'
    if total >= 1:
        return 'low'
    return 'none'


# =============================================================================
# Main pipeline — find_comps
# =============================================================================

def find_comps(target_title, target_artist, target_price, candidate_records,
               mode='pricing', learned_rejections=None):
    """
    Full comp pipeline:
      1. Normalize target + all candidates
      2. Hard filter (category-specific)
      3. Score + rank survivors
      4. ±25% price band cleanup
      5. Recency-weighted pricing
      6. Confidence + explainability

    Args:
        target_title: item title
        target_artist: artist name
        target_price: current/listed price
        candidate_records: list of dicts with 'title'/'name', 'price', 'date'/'sold_date', 'source', 'url'
        mode: 'pricing' (strict) or 'deal_finder' (broader)
        learned_rejections: dict from comp_rejections.json

    Returns:
        dict with comps, rejected, pricing, stats, explanations
    """
    config = get_config(target_artist)
    target = normalize_record(target_title, target_artist, price=target_price)

    # Learned rejection words
    learned_words = set()
    if learned_rejections:
        for rule in learned_rejections.get('learned_rules', []):
            if rule.get('count', 0) >= 3:
                learned_words.add(rule.get('word', ''))

    accepted = []
    rejected = []

    for rec in candidate_records:
        # Normalize
        comp = normalize_record(
            rec.get('title', rec.get('name', '')),
            target_artist,
            description=rec.get('description', ''),
            price=rec.get('price', 0),
            sold_date=rec.get('sold_date', rec.get('date', '')),
            source=rec.get('source', ''),
            url=rec.get('url', ''),
        )

        # Learned rejection check
        if learned_words:
            comp_words = set(tokenize(comp['title_normalized']))
            matched = comp_words & learned_words
            if matched:
                rejected.append({**comp, 'reject_reason': f'learned: {matched}'})
                continue

        # Hard filter
        passes, reason = hard_filter(target, comp, config, mode=mode)
        if not passes:
            rejected.append({**comp, 'reject_reason': reason})
            continue

        # Score
        comp['score'] = score_comp(target, comp, config)
        accepted.append(comp)

    # Sort by score descending
    accepted.sort(key=lambda x: -x['score'])

    # Compute pricing from accepted comps
    pricing = compute_pricing(accepted)

    # Confidence
    confidence = compute_confidence(accepted)

    # Match quality labels for deal finder
    for c in accepted:
        if c['score'] >= 60:
            c['match_quality'] = 'exact'
        elif c['score'] >= 40:
            c['match_quality'] = 'strong'
        elif c['score'] >= 25:
            c['match_quality'] = 'possible'
        else:
            c['match_quality'] = 'weak'

    # Rejection summary
    reject_counts = Counter()
    for r in rejected:
        reason = (r.get('reject_reason', 'unknown') or 'unknown').split(':')[0].split('_below')[0]
        reject_counts[reason] += 1

    return {
        'comps': [{
            'title': c.get('title_raw', '')[:100],
            'price': c['price'],
            'score': c['score'],
            'match_quality': c.get('match_quality', ''),
            'signed': c['signed'],
            'numbered': c['numbered'],
            'medium': c['medium'],
            'year': c.get('year'),
            'work_id': c.get('work_id', ''),
            'sold_date': c.get('sold_date', ''),
            'source': c.get('source', ''),
            'url': c.get('url', ''),
            'source_weight': source_weight(c.get('source', '')),
        } for c in accepted[:20]],
        'rejected': [{
            'title': r.get('title_raw', '')[:80],
            'price': r.get('price', 0),
            'reason': r.get('reject_reason', 'unknown'),
        } for r in rejected[:15]],
        'pricing': pricing,
        'stats': {
            'total_candidates': len(candidate_records),
            'accepted': len(accepted),
            'rejected': len(rejected),
            'confidence': confidence,
            'category': get_category(target_artist),
            'mode': mode,
        },
        'rejection_summary': dict(reject_counts.most_common(10)),
    }
