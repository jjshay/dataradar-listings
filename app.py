#!/usr/bin/env python3
"""
DATARADAR Listings - eBay Inventory Management with Key-Date Pricing

A Flask application that helps eBay sellers maximize profits by automatically
adjusting prices based on significant calendar events related to their inventory.

Author: John Shay
License: MIT
"""

from flask import Flask, render_template, jsonify, request
from datetime import datetime, timedelta
import os
import json
import base64
import requests
import pickle
import re

app = Flask(__name__, template_folder='templates')

# Master pricing index path (from main DATARADAR)
MASTER_INDEX_PATH = '/Users/johnshay/DATARADAR/master_pricing_index.json'

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

# =============================================================================
# eBay API Integration
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


# Initialize eBay API
ebay = EbayAPI(EBAY_CONFIG)

# =============================================================================
# Pricing Engine
# =============================================================================

def load_pricing_rules():
    """Load pricing rules from JSON file"""
    rules_path = os.path.join(os.path.dirname(__file__), 'pricing_rules.json')
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

def load_market_index():
    """Load or reload the master pricing index"""
    global _market_index, _market_index_loaded

    # Reload if file changed or not loaded
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
# Flask Routes
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
    """Get inventory statistics"""
    listings = ebay.get_listings()
    total_value = sum(l['price'] for l in listings)
    active_events = get_active_events()

    # Count underpriced items
    underpriced = 0
    for listing in listings:
        suggested = calculate_suggested_price(listing['price'], listing['title'])
        if suggested > listing['price'] * 1.01:
            underpriced += 1

    return jsonify({
        'total_listings': len(listings),
        'total_value': total_value,
        'active_events': len(active_events),
        'underpriced': underpriced
    })


@app.route('/api/calendar')
def get_calendar():
    """Get pricing calendar events"""
    rules = load_pricing_rules()
    month = request.args.get('month')
    year = request.args.get('year')

    if month and year:
        # Filter for specific month
        month_str = f"{int(month):02d}"
        rules = [r for r in rules if r.get('start_date', '').startswith(month_str)]

    # Transform rules into the format the frontend expects
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
            # If the event end date has already passed this year, use next year
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
    # Remove sort key before returning
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

    # Low price alerts
    for listing in listings:
        if listing['price'] < 10:
            alerts.append({
                'type': 'low_price',
                'message': f"Low price: {listing['title'][:40]}...",
                'item': listing
            })

    # High value alerts
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

    # Sort by count descending
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
# Main
# =============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    debug = os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'
    app.run(debug=debug, host='0.0.0.0', port=port)
