#!/usr/bin/env python3
"""
DATARADAR Listings - eBay Inventory Management with Key-Date Pricing,
Art Market Deals, eBay Deal Finder & Watchlist

A Flask application that helps eBay sellers maximize profits by automatically
adjusting prices based on significant calendar events related to their inventory.

Author: John Shay
License: MIT
"""

from flask import Flask, render_template, jsonify, request
from datetime import datetime, timedelta
import os
import json
import csv
import base64
import requests
import pickle
import re

app = Flask(__name__, template_folder='templates')

# Data directory
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# Remote data URLs (GitHub Release assets for files too large for git)
REMOTE_DATA = {
    'shepard_fairey_data.json': 'https://github.com/jjshay/dataradar-listings/releases/download/v1.1-data/shepard_fairey_data.json',
    'artist_price_summaries.json': 'https://github.com/jjshay/dataradar-listings/releases/download/v1.1-data/artist_price_summaries.json',
}


def ensure_data_file(filename):
    """Download a data file from GitHub Release if not present locally"""
    local_path = os.path.join(DATA_DIR, filename)
    if os.path.exists(local_path):
        return local_path

    url = REMOTE_DATA.get(filename)
    if not url:
        return local_path

    print(f"Downloading {filename} from GitHub Release...")
    try:
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(local_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        size_mb = os.path.getsize(local_path) / (1024 * 1024)
        print(f"  Downloaded {filename} ({size_mb:.1f} MB)")
    except Exception as e:
        print(f"  Failed to download {filename}: {e}")

    return local_path

# =============================================================================
# Configuration
# =============================================================================

def load_env():
    """Load environment variables from .env file, falling back to os.environ"""
    env_vars = {}
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key] = value
    # Fall back to OS environment variables (for Railway, Heroku, etc.)
    for key in ['EBAY_CLIENT_ID', 'EBAY_CLIENT_SECRET', 'EBAY_REFRESH_TOKEN',
                'EBAY_DEV_ID', 'DATARADAR_SHEET_ID']:
        if key not in env_vars and os.environ.get(key):
            env_vars[key] = os.environ[key]
    return env_vars

ENV = load_env()

# eBay API Configuration
EBAY_CONFIG = {
    'client_id': ENV.get('EBAY_CLIENT_ID', ''),
    'client_secret': ENV.get('EBAY_CLIENT_SECRET', ''),
    'refresh_token': ENV.get('EBAY_REFRESH_TOKEN', ''),
    'dev_id': ENV.get('EBAY_DEV_ID', '')
}

# Pricing Tiers
TIER_BOOSTS = {
    'MINOR': 5,    # Small events: +5%
    'MEDIUM': 15,  # Notable events: +15%
    'MAJOR': 25,   # Significant events: +25%
    'PEAK': 35     # Peak demand events: +35%
}

# Default deal price range
DEFAULT_MIN_PRICE = 100
DEFAULT_MAX_PRICE = 700

# =============================================================================
# eBay Trading API (Inventory Management)
# =============================================================================

class EbayAPI:
    """eBay Trading API wrapper"""

    def __init__(self, config):
        self.config = config
        self._token = None
        self._token_expires = None

    def get_access_token(self):
        """Get OAuth access token, refreshing if needed"""
        if self._token and self._token_expires and datetime.now() < self._token_expires:
            return self._token

        credentials = f"{self.config['client_id']}:{self.config['client_secret']}"
        encoded = base64.b64encode(credentials.encode()).decode()

        response = requests.post(
            'https://api.ebay.com/identity/v1/oauth2/token',
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Authorization': f'Basic {encoded}'
            },
            data={
                'grant_type': 'refresh_token',
                'refresh_token': self.config['refresh_token'],
                'scope': 'https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/sell.inventory'
            }
        )

        if response.status_code == 200:
            data = response.json()
            self._token = data.get('access_token')
            expires_in = data.get('expires_in', 7200)
            self._token_expires = datetime.now() + timedelta(seconds=expires_in - 300)
            return self._token

        return None

    def get_listings(self, page=1, per_page=100):
        """Fetch active listings from eBay"""
        token = self.get_access_token()
        if not token:
            return []

        # Using Trading API GetMyeBaySelling
        headers = {
            'X-EBAY-API-SITEID': '0',
            'X-EBAY-API-COMPATIBILITY-LEVEL': '967',
            'X-EBAY-API-CALL-NAME': 'GetMyeBaySelling',
            'X-EBAY-API-IAF-TOKEN': token,
            'Content-Type': 'text/xml'
        }

        xml_request = f'''<?xml version="1.0" encoding="utf-8"?>
        <GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
            <ActiveList>
                <Include>true</Include>
                <Pagination>
                    <EntriesPerPage>{per_page}</EntriesPerPage>
                    <PageNumber>{page}</PageNumber>
                </Pagination>
            </ActiveList>
        </GetMyeBaySellingRequest>'''

        response = requests.post(
            'https://api.ebay.com/ws/api.dll',
            headers=headers,
            data=xml_request
        )

        return self._parse_listings(response.text)

    def _parse_listings(self, xml_response):
        """Parse eBay XML response into listing objects"""
        import xml.etree.ElementTree as ET
        listings = []

        try:
            root = ET.fromstring(xml_response)
            ns = {'ebay': 'urn:ebay:apis:eBLBaseComponents'}

            for item in root.findall('.//ebay:Item', ns):
                listing = {
                    'id': self._get_text(item, 'ebay:ItemID', ns),
                    'title': self._get_text(item, 'ebay:Title', ns),
                    'price': float(self._get_text(item, './/ebay:CurrentPrice', ns) or 0),
                    'quantity': int(self._get_text(item, 'ebay:Quantity', ns) or 0),
                    'image': self._get_text(item, './/ebay:GalleryURL', ns),
                    'url': self._get_text(item, './/ebay:ListingDetails/ebay:ViewItemURL', ns),
                    'format': self._get_text(item, './/ebay:ListingType', ns),
                    'end_time': self._get_text(item, './/ebay:EndTime', ns)
                }
                listings.append(listing)
        except Exception as e:
            print(f"Parse error: {e}")

        return listings

    def _get_text(self, element, path, ns):
        """Safely get text from XML element"""
        el = element.find(path, ns)
        return el.text if el is not None else None

    def update_price(self, item_id, new_price):
        """Update listing price on eBay"""
        token = self.get_access_token()
        if not token:
            return False

        headers = {
            'X-EBAY-API-SITEID': '0',
            'X-EBAY-API-COMPATIBILITY-LEVEL': '967',
            'X-EBAY-API-CALL-NAME': 'ReviseItem',
            'X-EBAY-API-IAF-TOKEN': token,
            'Content-Type': 'text/xml'
        }

        xml_request = f'''<?xml version="1.0" encoding="utf-8"?>
        <ReviseItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
            <Item>
                <ItemID>{item_id}</ItemID>
                <StartPrice>{new_price:.2f}</StartPrice>
            </Item>
        </ReviseItemRequest>'''

        response = requests.post(
            'https://api.ebay.com/ws/api.dll',
            headers=headers,
            data=xml_request
        )

        return 'Success' in response.text


# Initialize eBay Trading API
ebay = EbayAPI(EBAY_CONFIG)

# =============================================================================
# eBay Browse API (Deal Finding)
# =============================================================================

_browse_token = None
_browse_token_expires = None


def get_browse_token():
    """Get client credentials token for eBay Browse API"""
    global _browse_token, _browse_token_expires

    if _browse_token and _browse_token_expires and datetime.now() < _browse_token_expires:
        return _browse_token

    client_id = EBAY_CONFIG['client_id']
    client_secret = EBAY_CONFIG['client_secret']
    if not client_id or not client_secret:
        return None

    credentials = f"{client_id}:{client_secret}"
    encoded_creds = base64.b64encode(credentials.encode()).decode()

    response = requests.post(
        'https://api.ebay.com/identity/v1/oauth2/token',
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': f'Basic {encoded_creds}'
        },
        data={
            'grant_type': 'client_credentials',
            'scope': 'https://api.ebay.com/oauth/api_scope'
        }
    )

    if response.status_code == 200:
        data = response.json()
        _browse_token = data.get('access_token')
        expires_in = data.get('expires_in', 7200)
        _browse_token_expires = datetime.now() + timedelta(seconds=expires_in - 300)
        return _browse_token

    return None


def search_ebay(query, max_price, min_price=0, limit=20):
    """Search eBay for items using Browse API"""
    token = get_browse_token()
    if not token:
        return []

    headers = {
        'Authorization': f'Bearer {token}',
        'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US',
        'Content-Type': 'application/json'
    }

    if min_price > 0:
        price_filter = f'price:[{min_price}..{max_price}]'
    else:
        price_filter = f'price:[..{max_price}]'

    params = {
        'q': query,
        'filter': f'{price_filter},priceCurrency:USD,buyingOptions:{{FIXED_PRICE|AUCTION}}',
        'sort': 'price',
        'limit': limit
    }

    try:
        response = requests.get(
            'https://api.ebay.com/buy/browse/v1/item_summary/search',
            headers=headers,
            params=params
        )

        if response.status_code != 200:
            return []

        data = response.json()
        items = data.get('itemSummaries', [])

        deals = []
        for item in items:
            price_info = item.get('price', {})
            price = float(price_info.get('value', 0))

            if price <= 0 or price > max_price:
                continue

            deals.append({
                'id': item.get('itemId', ''),
                'title': item.get('title', 'Unknown'),
                'price': price,
                'image': item.get('image', {}).get('imageUrl', ''),
                'url': item.get('itemWebUrl', ''),
                'condition': item.get('condition', 'Unknown'),
                'seller': item.get('seller', {}).get('username', 'Unknown'),
                'buying_option': item.get('buyingOptions', [''])[0] if item.get('buyingOptions') else '',
                'location': item.get('itemLocation', {}).get('country', '')
            })

        return deals

    except Exception as e:
        print(f"Search error: {e}")
        return []

# =============================================================================
# Watchlist Management
# =============================================================================

WATCHLIST_FILE = os.path.join(DATA_DIR, 'watchlist.json')


def load_watchlist():
    """Load watchlist from JSON file"""
    try:
        if os.path.exists(WATCHLIST_FILE):
            with open(WATCHLIST_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return []


def save_watchlist(items):
    """Save watchlist to JSON file"""
    with open(WATCHLIST_FILE, 'w') as f:
        json.dump(items, f, indent=2)

# =============================================================================
# Art Deals Data
# =============================================================================

_art_deals = None
_art_deals_loaded = None


def load_art_deals():
    """Load art deals from JSON file with caching"""
    global _art_deals, _art_deals_loaded

    art_path = os.path.join(DATA_DIR, 'art_deals.json')
    if os.path.exists(art_path):
        mtime = os.path.getmtime(art_path)
        if _art_deals is None or _art_deals_loaded != mtime:
            with open(art_path, 'r') as f:
                _art_deals = json.load(f)
            _art_deals_loaded = mtime

    return _art_deals or []

# =============================================================================
# Deal Targets
# =============================================================================

def load_deal_targets():
    """Load deal search targets from JSON file"""
    targets_path = os.path.join(DATA_DIR, 'deal_targets.json')
    if os.path.exists(targets_path):
        with open(targets_path, 'r') as f:
            return json.load(f)
    return []

# =============================================================================
# Pricing Engine
# =============================================================================

def load_pricing_rules():
    """Load pricing rules from JSON file"""
    rules_path = os.path.join(DATA_DIR, 'pricing_rules.json')
    if os.path.exists(rules_path):
        with open(rules_path, 'r') as f:
            return json.load(f)
    return []


def get_active_events(date=None):
    """Get pricing events active on a given date"""
    if date is None:
        date = datetime.now()

    rules = load_pricing_rules()
    active = []
    current_mmdd = date.strftime('%m-%d')

    for rule in rules:
        start = rule.get('start_date', '')
        end = rule.get('end_date', '')

        if start <= current_mmdd <= end:
            active.append(rule)

    return active


def calculate_suggested_price(base_price, title):
    """Calculate suggested price based on active events and item title"""
    active_events = get_active_events()
    max_boost = 0

    title_lower = title.lower()

    for event in active_events:
        keywords = event.get('keywords', [])
        if any(kw.lower() in title_lower for kw in keywords):
            boost = TIER_BOOSTS.get(event.get('tier', 'MINOR'), 0)
            max_boost = max(max_boost, boost)

    if max_boost > 0:
        return base_price * (1 + max_boost / 100)

    return base_price


def get_matching_events(title):
    """Get events that match an item's title"""
    active_events = get_active_events()
    title_lower = title.lower()
    matches = []

    for event in active_events:
        keywords = event.get('keywords', [])
        if any(kw.lower() in title_lower for kw in keywords):
            matches.append(event)

    return matches

# =============================================================================
# Market Pricing (from Master Index)
# =============================================================================

_market_index = None
_market_index_loaded = None

MASTER_INDEX_PATH = os.path.join(DATA_DIR, 'master_pricing_index.json')


def load_market_index():
    """Load or reload the master pricing index"""
    global _market_index, _market_index_loaded

    if os.path.exists(MASTER_INDEX_PATH):
        mtime = os.path.getmtime(MASTER_INDEX_PATH)
        if _market_index is None or _market_index_loaded != mtime:
            with open(MASTER_INDEX_PATH, 'r') as f:
                _market_index = json.load(f)
            _market_index_loaded = mtime

    return _market_index


def categorize_for_market(title):
    """Categorize item to match market index categories"""
    title_lower = title.lower()

    # KAWS categories
    if 'kaws' in title_lower:
        if '1000%' in title_lower or '1000 %' in title_lower:
            return 'KAWS - Bearbrick 1000%'
        if 'bearbrick' in title_lower or 'be@rbrick' in title_lower:
            if '400%' in title_lower:
                return 'KAWS - Bearbrick 400%'
            if '100%' in title_lower:
                return 'KAWS - Bearbrick 100%'
            return 'KAWS - Bearbrick'
        if 'companion' in title_lower:
            return 'KAWS - Companion'
        if 'chum' in title_lower:
            return 'KAWS - Chum'
        if 'bff' in title_lower:
            return 'KAWS - BFF'
        return 'KAWS - Other'

    # Bearbrick (non-KAWS)
    if 'bearbrick' in title_lower or 'be@rbrick' in title_lower:
        if '1000%' in title_lower:
            return 'Bearbrick - 1000%'
        if '400%' in title_lower:
            return 'Bearbrick - 400%'
        if '100%' in title_lower:
            return 'Bearbrick - 100%'
        if 'basquiat' in title_lower:
            return 'Bearbrick - Basquiat'
        return 'Bearbrick - Other'

    # Shepard Fairey / OBEY
    if 'shepard fairey' in title_lower or 'obey giant' in title_lower:
        if 'hope' in title_lower:
            return 'Shepard Fairey - Hope'
        if 'make art not war' in title_lower:
            return 'Shepard Fairey - Make Art Not War'
        if 'peace' in title_lower:
            return 'Shepard Fairey - Peace'
        return 'Shepard Fairey - Print'

    # Death NYC
    if 'death nyc' in title_lower:
        return 'Death NYC - Print'

    # Banksy
    if 'banksy' in title_lower:
        return 'Banksy - Print'

    return None


def get_market_price(title):
    """Get market pricing data for an item based on title"""
    index = load_market_index()
    if not index:
        return None

    category = categorize_for_market(title)
    if not category:
        return None

    categories = index.get('categories', {})
    if category in categories:
        data = categories[category]
        return {
            'category': category,
            'count': data.get('count', 0),
            'sold_count': data.get('sold_count', 0),
            'min_price': data.get('min_price', 0),
            'max_price': data.get('max_price', 0),
            'avg_price': data.get('avg_price', 0),
            'median_price': data.get('median_price', 0),
            'sold_avg': data.get('sold_avg', 0),
            'sold_median': data.get('sold_median', 0)
        }

    return None


def get_price_assessment(current_price, title):
    """Assess if current price is good compared to market"""
    market = get_market_price(title)
    if not market or market['sold_median'] == 0:
        return None

    sold_median = market['sold_median']
    diff_pct = ((current_price - sold_median) / sold_median) * 100

    if current_price < sold_median * 0.7:
        status = 'underpriced'
        suggestion = f"Consider raising to ${sold_median:.0f}"
    elif current_price > sold_median * 1.5:
        status = 'overpriced'
        suggestion = f"Market median is ${sold_median:.0f}"
    else:
        status = 'fair'
        suggestion = None

    return {
        'status': status,
        'market_median': sold_median,
        'market_avg': market['sold_avg'],
        'diff_percent': round(diff_pct, 1),
        'suggestion': suggestion,
        'category': market['category'],
        'sample_size': market['sold_count']
    }


# =============================================================================
# Personal Inventory Data
# =============================================================================

_personal_inventory = None
_personal_inventory_loaded = None


def load_personal_inventory():
    """Load personal inventory from SF enriched JSON + Death NYC CSV, cached by mtime"""
    global _personal_inventory, _personal_inventory_loaded

    sf_path = os.path.join(DATA_DIR, 'inventory_enriched.json')
    dnyc_path = os.path.join(DATA_DIR, 'death_nyc_inventory.csv')

    # Check mtimes for cache invalidation
    sf_mtime = os.path.getmtime(sf_path) if os.path.exists(sf_path) else 0
    dnyc_mtime = os.path.getmtime(dnyc_path) if os.path.exists(dnyc_path) else 0
    combined_mtime = (sf_mtime, dnyc_mtime)

    if _personal_inventory is not None and _personal_inventory_loaded == combined_mtime:
        return _personal_inventory

    items = []

    # Load Shepard Fairey inventory
    if os.path.exists(sf_path):
        with open(sf_path, 'r') as f:
            sf_data = json.load(f)
        for rec in sf_data:
            market = rec.get('market_data', {})
            ebay_supply = rec.get('ebay_supply', {})
            items.append({
                'id': f"sf-{rec.get('id', len(items))}",
                'name': rec.get('name', 'Unknown'),
                'artist': rec.get('artist', 'Shepard Fairey'),
                'source': 'personal',
                'suggested_price': rec.get('suggested_price'),
                'price_range': rec.get('price_range', ''),
                'your_price': rec.get('your_price'),
                'year': rec.get('year'),
                'edition': rec.get('edition'),
                'comparable_sales': rec.get('comparable_sales', 0),
                'recent_sales': rec.get('recent_sales', 0),
                'market_data': market,
                'recommendation': ebay_supply.get('recommendation', 'RESEARCH'),
                'recommendation_reason': ebay_supply.get('reason', ''),
                'ebay_supply': ebay_supply,
                'category': 'Shepard Fairey',
            })

    # Load Death NYC inventory
    if os.path.exists(dnyc_path):
        with open(dnyc_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                price = 0
                try:
                    price = float(row.get('CURRENT_PRICE', 0))
                except (ValueError, TypeError):
                    pass
                suggested = 0
                try:
                    suggested = float(row.get('SUGGESTED_PRICE', 0))
                except (ValueError, TypeError):
                    pass

                items.append({
                    'id': f"dnyc-{row.get('ITEM_ID', len(items))}",
                    'name': row.get('TITLE', 'Unknown'),
                    'artist': 'Death NYC',
                    'source': 'personal',
                    'suggested_price': suggested,
                    'price_range': f"${price:.0f} - ${suggested:.0f}" if price and suggested else '',
                    'your_price': price,
                    'category': row.get('CATEGORY', 'Print'),
                    'subjects': row.get('SUBJECTS', ''),
                    'ebay_link': row.get('EBAY_LINK', ''),
                    'recommendation': 'HOLD',
                    'recommendation_reason': '',
                    'market_data': {},
                    'ebay_supply': {},
                    'comparable_sales': 0,
                    'recent_sales': 0,
                })

    _personal_inventory = items
    _personal_inventory_loaded = combined_mtime
    return items


# =============================================================================
# Historical Price Data
# =============================================================================

_historical_prices = None
_historical_prices_loaded = None


def load_historical_prices():
    """Load Shepard Fairey historical price data, cached by mtime"""
    global _historical_prices, _historical_prices_loaded

    path = ensure_data_file('shepard_fairey_data.json')
    if not os.path.exists(path):
        return []

    mtime = os.path.getmtime(path)
    if _historical_prices is not None and _historical_prices_loaded == mtime:
        return _historical_prices

    with open(path, 'r') as f:
        _historical_prices = json.load(f)
    _historical_prices_loaded = mtime
    return _historical_prices


_worthpoint_data = None
_worthpoint_data_loaded = None


def load_worthpoint_data():
    """Load WorthPoint sold price data, cached by mtime"""
    global _worthpoint_data, _worthpoint_data_loaded

    path = os.path.join(DATA_DIR, 'worthpoint_sf_data.json')
    if not os.path.exists(path):
        return []

    mtime = os.path.getmtime(path)
    if _worthpoint_data is not None and _worthpoint_data_loaded == mtime:
        return _worthpoint_data

    with open(path, 'r') as f:
        _worthpoint_data = json.load(f)
    _worthpoint_data_loaded = mtime
    return _worthpoint_data


_artist_summaries = None
_artist_summaries_loaded = None


def load_artist_summaries():
    """Load pre-computed artist price summaries, cached by mtime"""
    global _artist_summaries, _artist_summaries_loaded

    path = ensure_data_file('artist_price_summaries.json')
    if not os.path.exists(path):
        return {}

    mtime = os.path.getmtime(path)
    if _artist_summaries is not None and _artist_summaries_loaded == mtime:
        return _artist_summaries

    with open(path, 'r') as f:
        _artist_summaries = json.load(f)
    _artist_summaries_loaded = mtime
    return _artist_summaries


def lookup_historical_prices(title, artist='', limit=50):
    """Fuzzy word-overlap matching against historical prices + WorthPoint data"""
    title_words = set(re.findall(r'\w+', title.lower()))
    if len(title_words) < 2:
        return []

    results = []

    # Search main historical prices (Shepard Fairey)
    if not artist or 'fairey' in artist.lower() or 'shepard' in artist.lower():
        historical = load_historical_prices()
        for rec in historical:
            name = rec.get('name', '')
            rec_words = set(re.findall(r'\w+', name.lower()))
            overlap = len(title_words & rec_words)
            if overlap >= 2 and overlap >= len(title_words) * 0.4:
                results.append({
                    'name': name,
                    'price': rec.get('price'),
                    'date': rec.get('date', ''),
                    'source': rec.get('source', 'eBay'),
                    'url': rec.get('url', ''),
                    'signed': rec.get('signed'),
                    'medium': rec.get('medium', ''),
                    '_score': overlap,
                })

        # Also search WorthPoint data
        wp_data = load_worthpoint_data()
        for rec in wp_data:
            wp_title = rec.get('title', '')
            rec_words = set(re.findall(r'\w+', wp_title.lower()))
            overlap = len(title_words & rec_words)
            if overlap >= 2 and overlap >= len(title_words) * 0.4:
                results.append({
                    'name': wp_title,
                    'price': rec.get('price'),
                    'date': rec.get('date_imported', ''),
                    'source': 'WorthPoint',
                    'url': rec.get('url', ''),
                    '_score': overlap,
                })

    # Search artist summaries for non-SF artists
    if artist and 'fairey' not in artist.lower():
        summaries = load_artist_summaries()
        for artist_key, artworks in summaries.items():
            if artist.lower() not in artist_key.lower():
                continue
            for name, stats in artworks.items():
                rec_words = set(re.findall(r'\w+', name.lower()))
                overlap = len(title_words & rec_words)
                if overlap >= 2 and overlap >= len(title_words) * 0.4:
                    for sale in stats.get('recent_sales', []):
                        results.append({
                            'name': name,
                            'price': sale.get('price'),
                            'date': sale.get('date', ''),
                            'source': sale.get('source', 'WorthPoint'),
                            '_score': overlap,
                        })

    # Sort by score desc, then date desc
    results.sort(key=lambda x: (-x['_score'], x.get('date', '') or ''), reverse=False)
    results.sort(key=lambda x: x['_score'], reverse=True)

    # Remove score field and deduplicate
    seen = set()
    deduped = []
    for r in results:
        r.pop('_score', None)
        key = (r.get('name', ''), r.get('price'), r.get('date', ''))
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    # Sort final by date desc
    deduped.sort(key=lambda x: x.get('date', '') or '', reverse=True)
    return deduped[:limit]


# =============================================================================
# Flask Routes - Core
# =============================================================================

@app.route('/')
def index():
    """Render main dashboard"""
    return render_template('index.html')


@app.route('/api/listings')
def get_listings():
    """Get all active eBay listings"""
    search = request.args.get('search', '').lower()
    listings = ebay.get_listings()

    if search:
        listings = [l for l in listings if search in l['title'].lower()]

    # Add suggested prices and market data
    for listing in listings:
        listing['suggested_price'] = calculate_suggested_price(
            listing['price'],
            listing['title']
        )
        listing['matching_events'] = get_matching_events(listing['title'])

        # Add market pricing data
        market = get_market_price(listing['title'])
        if market:
            listing['market_data'] = market
            listing['price_assessment'] = get_price_assessment(
                listing['price'],
                listing['title']
            )

    return jsonify(listings)


@app.route('/api/stats')
def get_stats():
    """Get combined statistics from all features"""
    listings = ebay.get_listings()
    total_value = sum(l['price'] for l in listings)
    active_events = get_active_events()

    # Count underpriced items
    underpriced = 0
    for listing in listings:
        suggested = calculate_suggested_price(listing['price'], listing['title'])
        if suggested > listing['price'] * 1.01:
            underpriced += 1

    # Art deals count
    art_deals = load_art_deals()
    art_count = len(art_deals)

    # Deal targets count
    deal_targets = load_deal_targets()
    target_count = len(deal_targets)

    # Watchlist count
    watchlist = load_watchlist()
    watch_count = len(watchlist)

    # Personal inventory
    inventory = load_personal_inventory()
    inv_count = len(inventory)
    inv_value = sum(i.get('suggested_price') or 0 for i in inventory)

    return jsonify({
        'total_listings': len(listings),
        'total_value': total_value,
        'active_events': len(active_events),
        'underpriced': underpriced,
        'art_deals': art_count,
        'deal_targets': target_count,
        'watchlist_count': watch_count,
        'my_inventory': inv_count,
        'inventory_value': round(inv_value, 2),
    })


@app.route('/api/calendar')
def get_calendar():
    """Get pricing calendar events"""
    rules = load_pricing_rules()
    month = request.args.get('month')
    year = request.args.get('year')

    if month and year:
        month_str = f"{int(month):02d}"
        rules = [r for r in rules if r.get('start_date', '').startswith(month_str)]

    now = datetime.now()
    events = []
    for rule in rules:
        start_mmdd = rule.get('start_date', '')
        end_mmdd = rule.get('end_date', '')
        events.append({
            'event': rule.get('name', ''),
            'tier': rule.get('tier', 'MINOR'),
            'increase': rule.get('increase_percent', 0),
            'item': ', '.join(rule.get('keywords', [])[:3]),
            'start_date': f"{now.year}-{start_mmdd}",
            'end_date': f"{now.year}-{end_mmdd}",
        })

    return jsonify(events)


@app.route('/api/upcoming-dates')
def get_upcoming_dates():
    """Get upcoming pricing events sorted by nearest date"""
    rules = load_pricing_rules()
    now = datetime.now()
    upcoming = []

    for rule in rules:
        start_mmdd = rule.get('start_date', '')
        try:
            event_date = datetime.strptime(f"{now.year}-{start_mmdd}", '%Y-%m-%d')
            end_mmdd = rule.get('end_date', '')
            end_date = datetime.strptime(f"{now.year}-{end_mmdd}", '%Y-%m-%d')
            if end_date < now:
                event_date = datetime.strptime(f"{now.year + 1}-{start_mmdd}", '%Y-%m-%d')
        except ValueError:
            continue

        upcoming.append({
            'month': event_date.strftime('%b').upper(),
            'day': event_date.day,
            'event': rule.get('name', ''),
            'tier': rule.get('tier', 'MINOR'),
            '_sort': event_date,
        })

    upcoming.sort(key=lambda x: x['_sort'])
    for item in upcoming:
        del item['_sort']

    return jsonify(upcoming[:10])


@app.route('/api/underpriced')
def get_underpriced():
    """Get items that should be boosted based on active events"""
    listings = ebay.get_listings()
    underpriced = []

    for listing in listings:
        suggested = calculate_suggested_price(listing['price'], listing['title'])
        if suggested > listing['price'] * 1.01:
            events = get_matching_events(listing['title'])
            boost = int((suggested / listing['price'] - 1) * 100)
            underpriced.append({
                **listing,
                'suggested_price': suggested,
                'boost_percent': boost,
                'matching_events': events
            })

    return jsonify(underpriced)


@app.route('/api/alerts')
def get_alerts():
    """Get system alerts"""
    listings = ebay.get_listings()
    alerts = []

    for listing in listings:
        if listing['price'] < 10:
            alerts.append({
                'type': 'low_price',
                'message': f"Low price: {listing['title'][:40]}...",
                'item': listing
            })

    for listing in listings:
        if listing['price'] > 1000:
            alerts.append({
                'type': 'high_value',
                'message': f"High value item: {listing['title'][:40]}...",
                'item': listing
            })

    return jsonify(alerts)


@app.route('/api/update-price', methods=['POST'])
def update_price():
    """Update item price on eBay"""
    data = request.get_json()
    item_id = data.get('item_id')
    new_price = data.get('price')

    if not item_id or not new_price:
        return jsonify({'success': False, 'error': 'Missing parameters'})

    success = ebay.update_price(item_id, float(new_price))

    return jsonify({'success': success})


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'app': 'dataradar-listings'})


@app.route('/api/market-lookup')
def market_lookup():
    """Look up market pricing for a search term"""
    query = request.args.get('q', '')
    if not query:
        return jsonify({'error': 'Missing query parameter'}), 400

    market = get_market_price(query)
    if market:
        return jsonify({
            'query': query,
            'found': True,
            **market
        })

    return jsonify({
        'query': query,
        'found': False,
        'message': 'No market data found for this item type'
    })


@app.route('/api/market-categories')
def market_categories():
    """Get all available market categories with stats"""
    index = load_market_index()
    if not index:
        return jsonify({'error': 'Market index not loaded'}), 500

    categories = []
    for key, data in index.get('categories', {}).items():
        categories.append({
            'category': key,
            'count': data.get('count', 0),
            'sold_count': data.get('sold_count', 0),
            'avg_price': round(data.get('avg_price', 0), 2),
            'median_price': round(data.get('median_price', 0), 2),
            'sold_median': round(data.get('sold_median', 0), 2)
        })

    categories.sort(key=lambda x: x['count'], reverse=True)

    return jsonify({
        'generated': index.get('generated'),
        'total_items': index.get('total_items', 0),
        'categories': categories
    })


@app.route('/api/price-check', methods=['POST'])
def price_check():
    """Check if a price is good for a given item"""
    data = request.get_json()
    title = data.get('title', '')
    price = data.get('price', 0)

    if not title or not price:
        return jsonify({'error': 'Missing title or price'}), 400

    assessment = get_price_assessment(float(price), title)
    if assessment:
        return jsonify({
            'title': title,
            'your_price': price,
            **assessment
        })

    return jsonify({
        'title': title,
        'your_price': price,
        'status': 'unknown',
        'message': 'No market data available for this item type'
    })


# =============================================================================
# Flask Routes - Personal Inventory & Historical Prices
# =============================================================================

@app.route('/api/my-inventory')
def get_my_inventory():
    """Get full personal inventory list"""
    items = load_personal_inventory()
    artist = request.args.get('artist', '').lower()
    search = request.args.get('search', '').lower()

    if artist:
        items = [i for i in items if artist in i.get('artist', '').lower()]

    if search:
        items = [i for i in items if search in i.get('name', '').lower()]

    return jsonify(items)


@app.route('/api/my-inventory/<item_id>')
def get_inventory_item(item_id):
    """Get single inventory item with full market data"""
    items = load_personal_inventory()
    for item in items:
        if item['id'] == item_id:
            return jsonify(item)
    return jsonify({'error': 'Item not found'}), 404


@app.route('/api/historical-prices')
def get_historical_prices():
    """Fuzzy search historical prices for a given title/artist"""
    title = request.args.get('title', '')
    artist = request.args.get('artist', '')
    limit = request.args.get('limit', 50, type=int)

    if not title:
        return jsonify({'error': 'Missing title parameter'}), 400

    results = lookup_historical_prices(title, artist, limit)
    return jsonify(results)


# =============================================================================
# Flask Routes - Art Deals
# =============================================================================

@app.route('/api/art-deals')
def get_art_deals():
    """Get art market deals with optional filtering"""
    deals = load_art_deals()

    # Optional filters
    artist = request.args.get('artist', '')
    min_price = request.args.get('min_price', type=float)
    max_price = request.args.get('max_price', type=float)
    search = request.args.get('search', '').lower()
    sort_by = request.args.get('sort', 'profit')

    if artist:
        deals = [d for d in deals if d.get('artist', '').lower() == artist.lower()]

    if search:
        deals = [d for d in deals if search in d.get('title', '').lower()]

    if min_price is not None:
        deals = [d for d in deals if d.get('price', 0) >= min_price]

    if max_price is not None:
        deals = [d for d in deals if d.get('price', 0) <= max_price]

    # Sort
    if sort_by == 'profit':
        deals.sort(key=lambda d: d.get('profit', 0), reverse=True)
    elif sort_by == 'price_low':
        deals.sort(key=lambda d: d.get('price', 0))
    elif sort_by == 'price_high':
        deals.sort(key=lambda d: d.get('price', 0), reverse=True)
    elif sort_by == 'discount':
        deals.sort(key=lambda d: d.get('discount_pct', 0), reverse=True)

    return jsonify(deals)


# =============================================================================
# Flask Routes - eBay Deals (Browse API)
# =============================================================================

@app.route('/api/deals/search')
def deals_search():
    """Search eBay for deals using Browse API"""
    query = request.args.get('q', '')
    min_price = float(request.args.get('min_price', DEFAULT_MIN_PRICE))
    max_price = float(request.args.get('max_price', DEFAULT_MAX_PRICE))

    if not query:
        return jsonify([])

    deals = search_ebay(query, max_price, min_price, limit=20)
    return jsonify(deals)


@app.route('/api/deals/targets')
def deals_targets():
    """Get all deal search targets"""
    targets = load_deal_targets()
    return jsonify(targets)


# =============================================================================
# Flask Routes - Watchlist
# =============================================================================

@app.route('/api/watchlist')
def get_watchlist():
    """Get all watchlist items"""
    items = load_watchlist()
    return jsonify(items)


@app.route('/api/watchlist/add', methods=['POST'])
def add_to_watchlist():
    """Add item to watchlist"""
    data = request.get_json()

    item = {
        'id': data.get('id', str(datetime.now().timestamp())),
        'title': data.get('title', ''),
        'price': data.get('price', 0),
        'url': data.get('url', ''),
        'image': data.get('image', ''),
        'notes': data.get('notes', ''),
        'added': datetime.now().isoformat(),
        'status': 'watching'
    }

    items = load_watchlist()

    # Check for duplicates
    if not any(i['id'] == item['id'] for i in items):
        items.append(item)
        save_watchlist(items)

    return jsonify({'success': True, 'count': len(items)})


@app.route('/api/watchlist/remove', methods=['POST'])
def remove_from_watchlist():
    """Remove item from watchlist"""
    data = request.get_json()
    item_id = data.get('id', '')

    items = load_watchlist()
    items = [i for i in items if i['id'] != item_id]
    save_watchlist(items)

    return jsonify({'success': True, 'count': len(items)})


# =============================================================================
# Main
# =============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    debug = os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'
    app.run(debug=debug, host='0.0.0.0', port=port)
