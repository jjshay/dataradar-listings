#!/usr/bin/env python3
"""
DATARADAR Listings - eBay Inventory Management with Key-Date Pricing,
Art Market Deals, eBay Deal Finder & Watchlist

A Flask application that helps eBay sellers maximize profits by automatically
adjusting prices based on significant calendar events related to their inventory.

Author: John Shay
License: MIT
"""

from flask import Flask, render_template, jsonify, request, redirect, session
import urllib.parse
from datetime import datetime, timedelta
import os
import json
import csv
import base64
import requests
import pickle
import re

app = Flask(__name__, template_folder='templates')
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dataradar-dev-key-change-in-prod')

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

# eBay API Configuration (loaded from .env file)
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
                'scope': 'https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope/sell.marketing https://api.ebay.com/oauth/api_scope/sell.negotiation'
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
        """Fetch active listings from eBay — single page"""
        token = self.get_access_token()
        if not token:
            return []

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

    _listings_cache = None
    _listings_cache_time = None

    def get_all_listings(self):
        """Fetch ALL active listings from eBay (paginated, cached 5 min)"""
        now = datetime.now()
        if self._listings_cache and self._listings_cache_time and (now - self._listings_cache_time).seconds < 300:
            return self._listings_cache

        all_listings = []
        page = 1
        while True:
            batch = self.get_listings(page=page, per_page=200)
            if not batch:
                break
            all_listings.extend(batch)
            if len(batch) < 200:
                break
            page += 1

        self._listings_cache = all_listings
        self._listings_cache_time = now
        return all_listings

    _sold_cache = None
    _sold_cache_time = None
    _sold_cache_days = 0

    def get_sold_items_cached(self, days_back=60):
        """Cached version of get_sold_items"""
        now = datetime.now()
        if self._sold_cache and self._sold_cache_time and self._sold_cache_days == days_back and (now - self._sold_cache_time).seconds < 300:
            return self._sold_cache

        result = self.get_sold_items(days_back)
        self._sold_cache = result
        self._sold_cache_time = now
        self._sold_cache_days = days_back
        return result

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
                    'start_time': self._get_text(item, './/ebay:StartTime', ns) or self._get_text(item, './/ebay:ListingDetails/ebay:StartTime', ns),
                    'end_time': self._get_text(item, './/ebay:EndTime', ns),
                    'watchers': int(self._get_text(item, './/ebay:WatchCount', ns) or 0),
                }
                listings.append(listing)
        except Exception as e:
            print(f"Parse error: {e}")

        return listings

    def _get_text(self, element, path, ns):
        """Safely get text from XML element"""
        el = element.find(path, ns)
        return el.text if el is not None else None

    def get_sold_items(self, days_back=90, page=1, per_page=100):
        """Fetch recently sold items from eBay"""
        token = self.get_access_token()
        if not token:
            return []

        headers = {
            'X-EBAY-API-SITEID': '0',
            'X-EBAY-API-COMPATIBILITY-LEVEL': '967',
            'X-EBAY-API-CALL-NAME': 'GetMyeBaySelling',
            'X-EBAY-API-IAF-TOKEN': token,
            'Content-Type': 'text/xml'
        }

        end_time = datetime.now()
        start_time = end_time - timedelta(days=days_back)

        xml_request = f'''<?xml version="1.0" encoding="utf-8"?>
        <GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
            <SoldList>
                <Include>true</Include>
                <DurationInDays>{days_back}</DurationInDays>
                <Pagination>
                    <EntriesPerPage>{per_page}</EntriesPerPage>
                    <PageNumber>{page}</PageNumber>
                </Pagination>
            </SoldList>
        </GetMyeBaySellingRequest>'''

        response = requests.post(
            'https://api.ebay.com/ws/api.dll',
            headers=headers,
            data=xml_request
        )

        return self._parse_sold_items(response.text)

    def _parse_sold_items(self, xml_response):
        """Parse sold items from XML — handles OrderTransaction structure"""
        import xml.etree.ElementTree as ET
        sold = []

        try:
            root = ET.fromstring(xml_response)
            ns = {'ebay': 'urn:ebay:apis:eBLBaseComponents'}

            for ot in root.findall('.//ebay:SoldList//ebay:OrderTransaction', ns):
                txn = ot.find('ebay:Transaction', ns) or ot.find('ebay:Order', ns)
                if txn is None:
                    continue
                item_el = txn.find('ebay:Item', ns) or txn.find('.//ebay:Item', ns)
                if item_el is None:
                    continue

                item_id = self._get_text(item_el, 'ebay:ItemID', ns) or ''
                title = self._get_text(item_el, 'ebay:Title', ns) or ''

                price_el = txn.find('.//ebay:TransactionPrice', ns) or item_el.find('.//ebay:BuyItNowPrice', ns)
                price = float(price_el.text) if price_el is not None and price_el.text else 0

                start_time = self._get_text(item_el, './/ebay:StartTime', ns) or ''
                end_time = self._get_text(item_el, './/ebay:EndTime', ns) or self._get_text(txn, 'ebay:CreatedDate', ns) or ''

                qty_el = txn.find('.//ebay:QuantityPurchased', ns)
                qty = int(qty_el.text) if qty_el is not None and qty_el.text else 1

                listing = {
                    'id': item_id,
                    'title': title,
                    'price': price,
                    'quantity_sold': qty,
                    'start_time': start_time[:19] if start_time else '',
                    'end_time': end_time[:19] if end_time else '',
                    'days_on_market': None,
                }

                if start_time and end_time:
                    try:
                        s = datetime.fromisoformat(start_time[:19])
                        e = datetime.fromisoformat(end_time[:19])
                        listing['days_on_market'] = (e - s).days
                    except Exception:
                        pass

                sold.append(listing)
        except Exception as e:
            print(f"Parse sold error: {e}")

        return sold

    def get_traffic_report(self):
        """Fetch listing traffic data using Seller Hub / Analytics API"""
        token = self.get_access_token()
        if not token:
            return {}

        headers = {
            'Authorization': f'Bearer {token}',
            'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US',
            'Content-Type': 'application/json',
        }

        # Use the sell/analytics API for traffic data
        end_date = datetime.now().strftime('%Y-%m-%dT23:59:59.000Z')
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%dT00:00:00.000Z')

        traffic = {}

        try:
            # Get traffic report via analytics API
            resp = requests.get(
                'https://api.ebay.com/sell/analytics/v1/traffic_report',
                headers=headers,
                params={
                    'dimension': 'LISTING',
                    'metric': 'LISTING_IMPRESSION_TOTAL,LISTING_VIEWS_TOTAL,CLICK_THROUGH_RATE,SALES_CONVERSION_RATE,TRANSACTION',
                    'filter': f'date_range:[{start_date}..{end_date}]',
                }
            )

            if resp.status_code == 200:
                data = resp.json()
                for record in data.get('records', []):
                    dim_values = record.get('dimensionValues', [])
                    metric_values = record.get('metricValues', [])
                    if dim_values:
                        listing_id = dim_values[0].get('value', '')
                        metrics = {}
                        metric_keys = ['impressions', 'views', 'ctr', 'conversion_rate', 'transactions']
                        for j, mv in enumerate(metric_values):
                            if j < len(metric_keys):
                                metrics[metric_keys[j]] = float(mv.get('value', 0) or 0)
                        traffic[listing_id] = metrics
            else:
                print(f"Traffic API {resp.status_code}: {resp.text[:200]}")

        except Exception as e:
            print(f"Traffic report error: {e}")

        return traffic

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
    """Search eBay for items using Browse API — paginates automatically for limit > 200"""
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

    all_deals = []
    page_size = min(limit, 200)  # eBay max per page is 200
    offset = 0
    max_pages = max(1, (limit + page_size - 1) // page_size)  # ceil division

    for page in range(max_pages):
        params = {
            'q': query,
            'filter': f'{price_filter},priceCurrency:USD,buyingOptions:{{FIXED_PRICE|AUCTION}}',
            'sort': 'price',
            'limit': page_size,
            'offset': offset,
        }

        try:
            response = requests.get(
                'https://api.ebay.com/buy/browse/v1/item_summary/search',
                headers=headers,
                params=params
            )

            if response.status_code != 200:
                break

            data = response.json()
            items = data.get('itemSummaries', [])

            if not items:
                break  # No more results

            for item in items:
                price_info = item.get('price', {})
                price = float(price_info.get('value', 0))

                if price <= 0 or price > max_price:
                    continue

                listed_date = item.get('itemCreationDate', '')
                if listed_date:
                    listed_date = listed_date[:10]

                all_deals.append({
                    'id': item.get('itemId', ''),
                    'title': item.get('title', 'Unknown'),
                    'price': price,
                    'image': item.get('image', {}).get('imageUrl', ''),
                    'url': item.get('itemWebUrl', ''),
                    'condition': item.get('condition', 'Unknown'),
                    'seller': item.get('seller', {}).get('username', 'Unknown'),
                    'buying_option': item.get('buyingOptions', [''])[0] if item.get('buyingOptions') else '',
                    'location': item.get('itemLocation', {}).get('country', ''),
                    'listed_date': listed_date,
                })

            # Check if more pages exist
            total_available = data.get('total', 0)
            offset += page_size
            if offset >= total_available or len(all_deals) >= limit:
                break

        except Exception as e:
            print(f"Search error page {page}: {e}")
            break

    return all_deals[:limit]

# =============================================================================
# eBay Marketing API (Promotions, Campaigns, Coupons)
# =============================================================================

PROMOTIONS_FILE = os.path.join(DATA_DIR, 'promotions_cache.json')
_promotions_cache = None
_promotions_cache_time = None
PROMO_CACHE_TTL = 1800  # 30 minutes


def get_marketing_headers():
    """Get auth headers for eBay Marketing API (requires user token)"""
    token = ebay.get_access_token()
    if not token:
        return None
    return {
        'Authorization': f'Bearer {token}',
        'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }


def fetch_ad_campaigns():
    """Fetch all ad campaigns (Promoted Listings)"""
    headers = get_marketing_headers()
    if not headers:
        return []

    campaigns = []
    offset = 0
    limit = 100

    while True:
        try:
            resp = requests.get(
                'https://api.ebay.com/sell/marketing/v1/ad_campaign',
                headers=headers,
                params={'limit': limit, 'offset': offset}
            )
            if resp.status_code != 200:
                print(f"Campaign fetch error {resp.status_code}: {resp.text[:200]}")
                break

            data = resp.json()
            batch = data.get('campaigns', [])
            campaigns.extend(batch)

            total = data.get('total', 0)
            if offset + limit >= total:
                break
            offset += limit
        except Exception as e:
            print(f"Campaign fetch error: {e}")
            break

    return campaigns


def fetch_campaign_ads(campaign_id):
    """Fetch all ads (listings) within a campaign"""
    headers = get_marketing_headers()
    if not headers:
        return []

    ads = []
    offset = 0
    limit = 100

    while True:
        try:
            resp = requests.get(
                f'https://api.ebay.com/sell/marketing/v1/ad_campaign/{campaign_id}/ad',
                headers=headers,
                params={'limit': limit, 'offset': offset}
            )
            if resp.status_code != 200:
                break

            data = resp.json()
            batch = data.get('ads', [])
            ads.extend(batch)

            total = data.get('total', 0)
            if offset + limit >= total:
                break
            offset += limit
        except Exception as e:
            print(f"Ads fetch error: {e}")
            break

    return ads


def fetch_item_promotions():
    """Fetch item promotions (volume pricing, markdown sales, order discounts)"""
    headers = get_marketing_headers()
    if not headers:
        return []

    promotions = []
    offset = 0
    limit = 100

    while True:
        try:
            resp = requests.get(
                'https://api.ebay.com/sell/marketing/v1/promotion',
                headers=headers,
                params={'limit': limit, 'offset': offset, 'marketplace_id': 'EBAY_US'}
            )
            if resp.status_code != 200:
                print(f"Promotion fetch error {resp.status_code}: {resp.text[:200]}")
                break

            data = resp.json()
            batch = data.get('promotions', [])
            promotions.extend(batch)

            total = data.get('total', 0)
            if offset + limit >= total:
                break
            offset += limit
        except Exception as e:
            print(f"Promotion fetch error: {e}")
            break

    return promotions


def fetch_coupons():
    """Fetch all coupons"""
    headers = get_marketing_headers()
    if not headers:
        return []

    coupons = []
    offset = 0
    limit = 100

    while True:
        try:
            resp = requests.get(
                'https://api.ebay.com/sell/marketing/v1/coupon',
                headers=headers,
                params={'limit': limit, 'offset': offset}
            )
            if resp.status_code != 200:
                break

            data = resp.json()
            batch = data.get('coupons', [])
            coupons.extend(batch)

            total = data.get('total', 0)
            if offset + limit >= total:
                break
            offset += limit
        except Exception as e:
            print(f"Coupon fetch error: {e}")
            break

    return coupons


def load_promotions_cache():
    """Load promotions data from cache file"""
    global _promotions_cache, _promotions_cache_time

    if _promotions_cache and _promotions_cache_time:
        age = (datetime.now() - _promotions_cache_time).total_seconds()
        if age < PROMO_CACHE_TTL:
            return _promotions_cache

    if os.path.exists(PROMOTIONS_FILE):
        try:
            with open(PROMOTIONS_FILE, 'r') as f:
                data = json.load(f)
            fetched = data.get('last_fetched', '')
            if fetched:
                fetched_dt = datetime.fromisoformat(fetched)
                age = (datetime.now() - fetched_dt).total_seconds()
                if age < PROMO_CACHE_TTL:
                    _promotions_cache = data
                    _promotions_cache_time = fetched_dt
                    return data
        except Exception:
            pass

    return None


def save_promotions_cache(data):
    """Save promotions data to cache file"""
    global _promotions_cache, _promotions_cache_time
    data['last_fetched'] = datetime.now().isoformat()
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PROMOTIONS_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    _promotions_cache = data
    _promotions_cache_time = datetime.now()


def fetch_all_promotions(force=False):
    """Fetch all promotion data from eBay, using cache if fresh"""
    if not force:
        cached = load_promotions_cache()
        if cached:
            return cached

    # Fetch campaigns
    campaigns = fetch_ad_campaigns()

    # Fetch ads for each active campaign
    per_listing = {}
    for campaign in campaigns:
        campaign_id = campaign.get('campaignId', '')
        campaign_name = campaign.get('campaignName', '')
        campaign_status = campaign.get('campaignStatus', '')
        funding = campaign.get('fundingStrategy', {})
        funding_model = funding.get('fundingModel', 'COST_PER_SALE')
        bid_percentage = funding.get('bidPercentage', '0')

        if campaign_status not in ('RUNNING', 'SCHEDULED', 'PAUSED'):
            continue

        ads = fetch_campaign_ads(campaign_id)
        campaign['ads'] = ads
        campaign['ad_count'] = len(ads)

        for ad in ads:
            listing_id = ad.get('listingId', '')
            ad_rate = float(ad.get('bidPercentage', bid_percentage) or 0)
            ad_status = ad.get('status', '')

            per_listing[listing_id] = {
                'listing_id': listing_id,
                'campaign_id': campaign_id,
                'campaign_name': campaign_name,
                'ad_rate': ad_rate,
                'funding_model': funding_model,
                'ad_status': ad_status,
                'ad_id': ad.get('adId', ''),
            }

    # Fetch item promotions
    item_promos = fetch_item_promotions()

    # Fetch coupons
    coupons = fetch_coupons()

    result = {
        'campaigns': campaigns,
        'item_promotions': item_promos,
        'coupons': coupons,
        'per_listing': per_listing,
        'summary': {
            'total_campaigns': len(campaigns),
            'active_campaigns': len([c for c in campaigns if c.get('campaignStatus') == 'RUNNING']),
            'total_promoted_listings': len(per_listing),
            'total_item_promos': len(item_promos),
            'total_coupons': len(coupons),
        }
    }

    save_promotions_cache(result)
    return result


def generate_promo_recommendations(promo_data, listings):
    """Generate AI-driven optimization recommendations"""
    recommendations = []
    per_listing = promo_data.get('per_listing', {})
    campaigns = promo_data.get('campaigns', [])

    # Build lookup of listing prices
    listing_prices = {l['id']: l for l in listings}

    # 1. Find high-value unpromoted listings
    unpromoted = []
    for listing in listings:
        if listing['id'] not in per_listing and listing['price'] >= 50:
            unpromoted.append(listing)

    if unpromoted:
        unpromoted.sort(key=lambda x: x['price'], reverse=True)
        top_unpromoted = unpromoted[:5]
        total_unpromoted_value = sum(l['price'] for l in unpromoted)
        recommendations.append({
            'type': 'promote_high_value',
            'priority': 'high',
            'title': f'{len(unpromoted)} high-value items not promoted',
            'description': f'${total_unpromoted_value:,.0f} in inventory has no ad promotion. Consider adding top items to a Promoted Listings campaign.',
            'items': [{'id': l['id'], 'title': l['title'][:60], 'price': l['price']} for l in top_unpromoted],
            'action': 'Create or add to campaign'
        })

    # 2. Flag listings with high ad rates (>8%)
    high_rate = []
    for lid, info in per_listing.items():
        if info['ad_rate'] > 8:
            listing = listing_prices.get(lid)
            if listing:
                high_rate.append({**info, 'title': listing['title'][:60], 'price': listing['price']})

    if high_rate:
        high_rate.sort(key=lambda x: x['ad_rate'], reverse=True)
        recommendations.append({
            'type': 'high_ad_rate',
            'priority': 'medium',
            'title': f'{len(high_rate)} listings with ad rate >8%',
            'description': 'High ad rates eat into margins. Consider switching to dynamic rate or lowering fixed rates for items that sell organically.',
            'items': [{'id': h['listing_id'], 'title': h['title'], 'ad_rate': h['ad_rate'], 'price': h.get('price', 0)} for h in high_rate[:5]],
            'action': 'Review ad rates'
        })

    # 3. Compare dynamic vs fixed rate campaigns
    dynamic_campaigns = [c for c in campaigns if c.get('fundingStrategy', {}).get('fundingModel') == 'COST_PER_SALE' and 'DYNAMIC' in str(c.get('fundingStrategy', {}))]
    fixed_campaigns = [c for c in campaigns if c not in dynamic_campaigns]

    if fixed_campaigns and not dynamic_campaigns:
        recommendations.append({
            'type': 'try_dynamic',
            'priority': 'medium',
            'title': 'Consider dynamic ad rates',
            'description': 'All campaigns use fixed rates. eBay\'s dynamic (suggested) rates auto-optimize based on item competitiveness and can improve visibility without overpaying.',
            'items': [],
            'action': 'Create dynamic rate campaign'
        })

    # 4. Check for low-value items being promoted (ad cost > margin)
    low_margin = []
    for lid, info in per_listing.items():
        listing = listing_prices.get(lid)
        if listing and listing['price'] < 25 and info['ad_rate'] > 3:
            estimated_fee = listing['price'] * (info['ad_rate'] / 100)
            low_margin.append({
                'id': lid,
                'title': listing['title'][:60],
                'price': listing['price'],
                'ad_rate': info['ad_rate'],
                'estimated_fee': round(estimated_fee, 2)
            })

    if low_margin:
        recommendations.append({
            'type': 'low_margin_promoted',
            'priority': 'high',
            'title': f'{len(low_margin)} low-value items with costly promotion',
            'description': 'Promoting items under $25 at >3% rate may not be profitable after eBay fees + ad fees.',
            'items': low_margin[:5],
            'action': 'Remove from campaign or lower rate'
        })

    # 5. No promotions at all
    if not campaigns and not promo_data.get('item_promotions') and not promo_data.get('coupons'):
        total_value = sum(l['price'] for l in listings)
        recommendations.append({
            'type': 'no_promotions',
            'priority': 'high',
            'title': 'No active promotions',
            'description': f'You have ${total_value:,.0f} in active listings with zero promotions. Promoted Listings Standard (cost-per-sale) is low risk — you only pay when items sell.',
            'items': [],
            'action': 'Start with Promoted Listings Standard'
        })

    recommendations.sort(key=lambda x: {'high': 0, 'medium': 1, 'low': 2}.get(x['priority'], 3))
    return recommendations


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
# Feature: Sold Items Tracking (#8), Sell-Through (#1), Traffic (#7)
# =============================================================================

SOLD_HISTORY_FILE = os.path.join(DATA_DIR, 'sold_history.json')
SUPPLY_SNAPSHOTS_FILE = os.path.join(DATA_DIR, 'supply_snapshots.json')
ALERTS_FILE = os.path.join(DATA_DIR, 'price_alerts.json')
AB_TESTS_FILE = os.path.join(DATA_DIR, 'ab_tests.json')

_sold_cache = None
_sold_cache_time = None
_traffic_cache = None
_traffic_cache_time = None


def fetch_and_cache_sold():
    """Fetch sold items and cache to disk"""
    global _sold_cache, _sold_cache_time

    if _sold_cache and _sold_cache_time and (datetime.now() - _sold_cache_time).seconds < 1800:
        return _sold_cache

    # Try cache file first
    if os.path.exists(SOLD_HISTORY_FILE):
        try:
            with open(SOLD_HISTORY_FILE, 'r') as f:
                data = json.load(f)
            fetched = data.get('last_fetched', '')
            if fetched:
                age = (datetime.now() - datetime.fromisoformat(fetched)).total_seconds()
                if age < 1800:
                    _sold_cache = data
                    _sold_cache_time = datetime.now()
                    return data
        except Exception:
            pass

    # Fetch from eBay
    sold_items = ebay.get_sold_items(days_back=90)

    data = {
        'last_fetched': datetime.now().isoformat(),
        'items': sold_items,
        'count': len(sold_items),
    }

    # Merge with historical data
    if os.path.exists(SOLD_HISTORY_FILE):
        try:
            with open(SOLD_HISTORY_FILE, 'r') as f:
                old = json.load(f)
            old_ids = {i['id'] for i in old.get('items', [])}
            for item in sold_items:
                if item['id'] not in old_ids:
                    old.get('items', []).append(item)
            data['items'] = old.get('items', []) + [i for i in sold_items if i['id'] not in old_ids]
            data['items'] = sold_items  # Use fresh data as authoritative
        except Exception:
            pass

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SOLD_HISTORY_FILE, 'w') as f:
        json.dump(data, f, indent=2)

    _sold_cache = data
    _sold_cache_time = datetime.now()
    return data


def fetch_and_cache_traffic():
    """Fetch traffic data and cache"""
    global _traffic_cache, _traffic_cache_time

    if _traffic_cache and _traffic_cache_time and (datetime.now() - _traffic_cache_time).seconds < 1800:
        return _traffic_cache

    traffic = ebay.get_traffic_report()
    _traffic_cache = traffic
    _traffic_cache_time = datetime.now()
    return traffic


def load_supply_snapshots():
    """Load supply snapshot history"""
    if os.path.exists(SUPPLY_SNAPSHOTS_FILE):
        with open(SUPPLY_SNAPSHOTS_FILE, 'r') as f:
            return json.load(f)
    return {'snapshots': [], 'last_snapshot': None}


def save_supply_snapshot(inventory_items):
    """Take a snapshot of current supply levels (#9)"""
    data = load_supply_snapshots()
    today = datetime.now().strftime('%Y-%m-%d')

    # Don't snapshot more than once per day
    if data.get('last_snapshot') == today:
        return data

    snapshot = {
        'date': today,
        'items': {}
    }

    for item in inventory_items:
        supply = item.get('ebay_supply', {})
        snapshot['items'][item['id']] = {
            'name': item['name'][:60],
            'ebay_count': supply.get('ebay_count', 0),
            'ebay_avg_price': supply.get('ebay_avg_price', 0),
        }

    data['snapshots'].append(snapshot)
    # Keep last 12 weeks
    data['snapshots'] = data['snapshots'][-12:]
    data['last_snapshot'] = today

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SUPPLY_SNAPSHOTS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

    return data


def get_supply_trends(item_id):
    """Get supply trend for an item over time (#9)"""
    data = load_supply_snapshots()
    trend = []
    for snap in data.get('snapshots', []):
        item_data = snap.get('items', {}).get(item_id)
        if item_data:
            trend.append({
                'date': snap['date'],
                'count': item_data.get('ebay_count', 0),
                'avg_price': item_data.get('ebay_avg_price', 0),
            })
    return trend


def load_alerts():
    """Load price alert rules (#3)"""
    if os.path.exists(ALERTS_FILE):
        with open(ALERTS_FILE, 'r') as f:
            return json.load(f)
    return {'rules': [], 'triggered': []}


def save_alerts(data):
    """Save alert rules"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ALERTS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def check_alerts(deals, inventory):
    """Check all alert conditions and return triggered alerts (#3)"""
    alert_data = load_alerts()
    triggered = []

    # Auto-generated alerts: deals below market by >60%
    for deal in deals:
        if deal.get('discount_pct', 0) >= 60 and deal.get('hotness', 0) >= 50:
            triggered.append({
                'type': 'hot_deal',
                'severity': 'high',
                'title': f"Hot deal: {deal['title'][:50]}",
                'message': f"{deal.get('discount_pct',0):.0f}% below median, ${deal.get('profit',0):,.0f} profit potential",
                'item_id': deal.get('id', ''),
                'category': deal.get('artist', ''),
            })

    # Inventory alerts: items where market shifted
    for item in inventory:
        supply = item.get('ebay_supply', {})
        suggested = item.get('suggested_price', 0)
        market_median = item.get('market_data', {}).get('median', 0)

        # Low supply alert — price opportunity
        ebay_count = supply.get('ebay_count', 0)
        if ebay_count <= 3 and suggested > 100:
            triggered.append({
                'type': 'low_supply',
                'severity': 'medium',
                'title': f"Low supply: {item['name'][:50]}",
                'message': f"Only {ebay_count} on eBay. Consider raising price to ${suggested:,.0f}+",
                'item_id': item.get('id', ''),
                'category': item.get('artist', ''),
            })

        # Overpriced alert
        if suggested and market_median and suggested > market_median * 1.5:
            triggered.append({
                'type': 'overpriced',
                'severity': 'low',
                'title': f"Above market: {item['name'][:50]}",
                'message': f"Suggested ${suggested:,.0f} vs median ${market_median:,.0f}",
                'item_id': item.get('id', ''),
                'category': item.get('artist', ''),
            })

    # Custom rules
    for rule in alert_data.get('rules', []):
        # Future: evaluate custom user rules
        pass

    return triggered


def load_ab_tests():
    """Load A/B test configurations (#4)"""
    if os.path.exists(AB_TESTS_FILE):
        with open(AB_TESTS_FILE, 'r') as f:
            return json.load(f)
    return {'tests': [], 'results': []}


def save_ab_tests(data):
    """Save A/B test configs"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(AB_TESTS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def get_seasonal_promo_suggestions():
    """Get ad rate suggestions based on active pricing events (#5)"""
    events = get_active_events()
    suggestions = []

    if not events:
        suggestions.append({
            'type': 'quiet_period',
            'message': 'No active pricing events. Consider reducing ad rates to save costs.',
            'suggested_rate_change': -1,  # reduce by 1%
        })
        return suggestions

    for event in events:
        tier = event.get('tier', 'MINOR')
        boost_map = {'MINOR': 1, 'MEDIUM': 2, 'MAJOR': 3, 'PEAK': 5}
        rate_boost = boost_map.get(tier, 1)
        keywords = event.get('keywords', [])

        suggestions.append({
            'type': 'event_boost',
            'event': event.get('name', ''),
            'tier': tier,
            'keywords': keywords,
            'message': f"Active event: {event.get('name', '')} ({tier}). Boost ad rates by +{rate_boost}% for matching items.",
            'suggested_rate_change': rate_boost,
        })

    return suggestions


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

# KAWS historical data
_kaws_data = None
_kaws_data_loaded = None


def load_kaws_data():
    """Load KAWS historical price data (44k+ items)"""
    global _kaws_data, _kaws_data_loaded

    path = os.path.join(DATA_DIR, 'kaws_data.json')
    if not os.path.exists(path):
        return []

    mtime = os.path.getmtime(path)
    if _kaws_data is not None and _kaws_data_loaded == mtime:
        return _kaws_data

    with open(path, 'r') as f:
        _kaws_data = json.load(f)
    _kaws_data_loaded = mtime
    return _kaws_data
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


def extract_item_attributes(title):
    """Extract key pricing attributes from a title — signed, numbered, size, etc."""
    t = title.lower()
    attrs = {
        'signed': any(w in t for w in ['signed', 'autograph', 'hand signed', 'hand-signed']),
        'numbered': any(w in t for w in ['numbered', '/50', '/100', '/150', '/200', '/250', '/300', '/350', '/400', '/450', '/500', 'ed/', 'edition of']),
        'framed': 'framed' in t,
        'coa': any(w in t for w in ['coa', 'certificate', 'jsa', 'psa', 'bas', 'beckett']),
        'original': any(w in t for w in ['original', 'one of a kind', '1/1']),
        'print': any(w in t for w in ['print', 'screenprint', 'screen print', 'lithograph', 'serigraph', 'giclee', 'silkscreen']),
        'figure': any(w in t for w in ['figure', 'figurine', 'vinyl figure', 'companion', 'statue', 'sculpture']),
        'poster': any(w in t for w in ['poster', 'offset']),
    }
    return attrs


def attribute_match_score(item_attrs, comp_attrs):
    """Score how well a comp's attributes match the item. Higher = better match.
    Critical attributes (signed, numbered) must match or the comp is penalized."""
    score = 0
    # Signed is critical — a signed item should only match signed comps
    if item_attrs['signed'] == comp_attrs['signed']:
        score += 30
    elif item_attrs['signed'] and not comp_attrs['signed']:
        score -= 50  # Heavy penalty: our item is signed but comp is not
    elif not item_attrs['signed'] and comp_attrs['signed']:
        score -= 20  # Our item unsigned, comp is signed = inflated comp

    # Numbered is important
    if item_attrs['numbered'] == comp_attrs['numbered']:
        score += 20
    elif item_attrs['numbered'] and not comp_attrs['numbered']:
        score -= 30

    # Medium match (print vs figure vs poster)
    for medium in ['print', 'figure', 'poster', 'original']:
        if item_attrs[medium] == comp_attrs[medium]:
            score += 5
        elif item_attrs[medium] != comp_attrs[medium] and (item_attrs[medium] or comp_attrs[medium]):
            score -= 15  # Wrong medium type

    # Framed and COA are bonuses
    if item_attrs['framed'] == comp_attrs['framed']:
        score += 5
    if item_attrs['coa'] == comp_attrs['coa']:
        score += 5

    return score


# Artist-specific quality gates — items must have these attributes to be valid comps/deals
ARTIST_REQUIRED_ATTRS = {
    'Shepard Fairey': {
        'require_any': ['signed', 'hand signed', 'hand-signed', 'autograph', 's/n', 'numbered', '/50', '/100', '/150', '/200', '/250', '/300', '/350', '/400', '/450', '/500'],
        'reject': ['unsigned', 'not signed', 'poster only', 'offset print', 'reproduction'],
    },
    'Banksy': {
        'require_any': ['signed', 'numbered', 'authenticated', 'pow', 'gdp', 'walled off', 'dismaland'],
        'reject': ['unsigned', 'not signed', 'poster'],
    },
    'KAWS': {
        'require_any': ['authentic', 'medicom', 'original', 'sealed', 'open edition', 'companion', 'bff', 'together', 'gone', 'holiday', 'signed'],
        'reject': ['knockoff', 'fake', 'custom', 'bootleg'],
    },
}


def passes_artist_quality_gate(title, artist):
    """Check if an item passes the artist-specific quality gate"""
    gate = ARTIST_REQUIRED_ATTRS.get(artist)
    if not gate:
        return True  # No gate for this artist

    t = title.lower()

    # Check rejects first
    for reject in gate.get('reject', []):
        if reject in t:
            return False

    # Check requires
    requires = gate.get('require_any', [])
    if requires:
        return any(req in t for req in requires)

    return True


FAKE_INDICATORS = [
    'no certificate', 'no coa', 'reproduction', 'replica', 'tribute',
    'fan art', 'fanart', 'inspired by', 'in the style of', 'after ',
    'not signed', 'unsigned', 'custom frame', 'art print', 'reprint',
    'digital print', 'giclee copy', 'museum poster', 'exhibition poster',
    'sketch no certificate', 'not authenticated', 'unverified',
]

FAKE_PRICE_THRESHOLDS = {
    'KAWS': 200,        # Real KAWS figures/prints rarely under $200
    'KAWS Figurine': 200,
    'KAWS Print': 500,
    'Banksy': 300,      # Real Banksy prints rarely under $300
    'Invader': 200,
    'Shepard Fairey': 50,
    'Mr. Brainwash': 75,
    'Bearbrick': 60,
    'Bearbrick 1000%': 200,
    'Bearbrick 400%': 60,
    'Murakami': 150,
    'Arsham': 150,
    'Nara': 200,
    'Futura': 150,
    'Stik': 200,
    'Warhol': 300,
    'Basquiat': 300,
    'Haring': 300,
    'Hirst': 300,
    'Brantley': 100,
    'Beatles/Rock': 100,
    'Signed Apollo': 25,
}


def is_likely_fake(title, price, category=''):
    """Detect likely fake/counterfeit items based on title and price"""
    t = title.lower()

    # Check for fake indicator phrases
    for indicator in FAKE_INDICATORS:
        if indicator in t:
            return True, f'Title contains "{indicator}"'

    # Check price threshold for category
    threshold = FAKE_PRICE_THRESHOLDS.get(category, 0)
    if threshold and price < threshold:
        # Low price + no authentication markers
        has_auth = any(w in t for w in ['signed', 'numbered', 'coa', 'jsa', 'psa', 'bas', 'beckett', 'authenticated'])
        if not has_auth:
            return True, f'Below ${threshold} with no authentication for {category}'

    # Death NYC items showing as other categories
    if 'death nyc' in t and category != 'Death NYC':
        return True, 'Death NYC item miscategorized'

    return False, ''


def lookup_historical_prices(title, artist='', limit=50):
    """Strict matching — requires artist match + title word overlap"""
    # Extract meaningful title words (remove artist name, common words)
    noise = {'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of', 'is', 'by', 'with',
             'new', 'lot', 'rare', 'free', 'shipping', 'print', 'signed', 'numbered', 'hand',
             'obey', 'giant', 'screen', 'edition', 'limited', 'art', 'original', 'artist',
             'proof', 'framed', 'matted', 'coa', 'certificate', 'authenticity',
             'shepard', 'fairey', 'death', 'nyc', 'banksy', 'kaws', 'brainwash',
             'sold', 'date', 'source', 'online', 'marketplace', 'ebay', 'auction',
             'vintage', 'poster', 'gallery', 'show', 'exhibition', 'collection'}
    title_words = set(w for w in re.findall(r'\w+', title.lower()) if w not in noise and len(w) > 2)

    if len(title_words) < 1:
        return []

    results = []
    item_attrs = extract_item_attributes(title)

    def match_record(name, rec_artist=''):
        """Check if a record matches — artist must match, then title words"""
        # Artist gate: if we know the artist, the comp MUST be same artist
        if artist:
            name_lower = name.lower()
            artist_lower = artist.lower()
            # Check if comp is from the same artist
            artist_in_name = False
            if 'fairey' in artist_lower and ('fairey' in name_lower or 'obey' in name_lower):
                artist_in_name = True
            elif 'death nyc' in artist_lower and 'death nyc' in name_lower:
                artist_in_name = True
            elif 'kaws' in artist_lower and 'kaws' in name_lower:
                artist_in_name = True
            elif 'banksy' in artist_lower and 'banksy' in name_lower:
                artist_in_name = True
            elif 'brainwash' in artist_lower and 'brainwash' in name_lower:
                artist_in_name = True
            elif artist_lower.split()[-1] in name_lower:
                artist_in_name = True

            if not artist_in_name:
                return 0  # Wrong artist — reject

        rec_words = set(w for w in re.findall(r'\w+', name.lower()) if w not in noise and len(w) > 2)
        overlap = len(title_words & rec_words)

        # Need at least 2 meaningful word overlaps to be a real comp
        # Single word matches like "records" or "artist" are too generic
        if overlap >= 2:
            return overlap
        return 0

    # Search main historical prices (Shepard Fairey)
    if not artist or 'fairey' in artist.lower() or 'shepard' in artist.lower():
        historical = load_historical_prices()
        for rec in historical:
            name = rec.get('name', '')
            score = match_record(name, rec.get('artist', ''))
            if score > 0:
                comp_attrs = extract_item_attributes(name)
                attr_score = attribute_match_score(item_attrs, comp_attrs)
                results.append({
                    'name': name,
                    'price': rec.get('price'),
                    'date': rec.get('date', ''),
                    'source': rec.get('source', 'eBay'),
                    'url': rec.get('url', ''),
                    'signed': rec.get('signed'),
                    'medium': rec.get('medium', ''),
                    '_score': score + attr_score,
                    '_attr_match': attr_score >= 0,
                })

        # Also search WorthPoint data
        wp_data = load_worthpoint_data()
        for rec in wp_data:
            wp_title = rec.get('title', '')
            score = match_record(wp_title)
            if score > 0:
                comp_attrs = extract_item_attributes(wp_title)
                attr_score = attribute_match_score(item_attrs, comp_attrs)
                results.append({
                    'name': wp_title,
                    'price': rec.get('price'),
                    'date': rec.get('date_imported', ''),
                    'source': 'WorthPoint',
                    'url': rec.get('url', ''),
                    '_score': score + attr_score,
                    '_attr_match': attr_score >= 0,
                })

    # Search KAWS historical data (44k+ items)
    if not artist or 'kaws' in artist.lower() or 'kaws' in title.lower():
        kaws_data = load_kaws_data()
        for rec in kaws_data:
            name = rec.get('name', '')
            score = match_record(name)
            if score > 0:
                comp_attrs = extract_item_attributes(name)
                attr_score = attribute_match_score(item_attrs, comp_attrs)
                results.append({
                    'name': name,
                    'price': rec.get('price'),
                    'date': rec.get('date', ''),
                    'source': rec.get('source', 'WorthPoint'),
                    'url': rec.get('url', ''),
                    'medium': rec.get('medium', ''),
                    '_score': score + attr_score,
                    '_attr_match': attr_score >= 0,
                })

    # Search artist summaries for non-SF artists
    if artist and 'fairey' not in artist.lower():
        summaries = load_artist_summaries()
        for artist_key, artworks in summaries.items():
            if artist.lower() not in artist_key.lower():
                continue
            for name, stats in artworks.items():
                score = match_record(name)
                if score > 0:
                    comp_attrs = extract_item_attributes(name)
                    attr_score = attribute_match_score(item_attrs, comp_attrs)
                    for sale in stats.get('recent_sales', []):
                        results.append({
                            'name': name,
                            'price': sale.get('price'),
                            'date': sale.get('date', ''),
                            'source': sale.get('source', 'WorthPoint'),
                            '_score': score + attr_score,
                            '_attr_match': attr_score >= 0,
                        })

    # Filter: prioritize attribute-matched comps, remove bad matches
    good_results = [r for r in results if r.get('_attr_match', True)]
    if len(good_results) >= 3:
        results = good_results  # Enough good matches, drop bad ones

    # Sort by score desc, then date desc
    results.sort(key=lambda x: x['_score'], reverse=True)

    # Remove internal fields and deduplicate
    seen = set()
    deduped = []
    for r in results:
        r.pop('_score', None)
        r.pop('_attr_match', None)
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
    listings = ebay.get_all_listings()

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
    listings = ebay.get_all_listings()
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
    year = int(request.args.get('year', datetime.now().year))
    month = request.args.get('month')

    events = []
    for rule in rules:
        start_mmdd = rule.get('start_date', '')
        end_mmdd = rule.get('end_date', '')

        # Handle events that cross year boundaries
        if end_mmdd < start_mmdd:
            # e.g., Christmas season 12-20 to 01-05
            events.append({
                'event': rule.get('name', ''),
                'tier': rule.get('tier', 'MINOR'),
                'increase': rule.get('increase_percent', 0),
                'item': ', '.join(rule.get('keywords', [])[:3]),
                'start_date': f"{year}-{start_mmdd}",
                'end_date': f"{year + 1}-{end_mmdd}",
            })
        else:
            events.append({
                'event': rule.get('name', ''),
                'tier': rule.get('tier', 'MINOR'),
                'increase': rule.get('increase_percent', 0),
                'item': ', '.join(rule.get('keywords', [])[:3]),
                'start_date': f"{year}-{start_mmdd}",
                'end_date': f"{year}-{end_mmdd}",
            })

    if month:
        month_str = f"{int(month):02d}"
        events = [e for e in events
                  if e['start_date'][5:7] == month_str or e['end_date'][5:7] == month_str]

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
    listings = ebay.get_all_listings()
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
    """Get smart price and market alerts (#3)"""
    deals = load_art_deals()
    inventory = load_personal_inventory()
    triggered = check_alerts(deals, inventory)

    # Also add basic listing alerts
    listings = ebay.get_all_listings()
    for listing in listings:
        if listing['price'] < 10:
            triggered.append({
                'type': 'low_price',
                'severity': 'medium',
                'title': f"Low price: {listing['title'][:40]}",
                'message': f"${listing['price']:.2f} — possible pricing error",
                'item_id': listing['id'],
                'category': '',
            })
        if listing['price'] > 1000:
            triggered.append({
                'type': 'high_value',
                'severity': 'low',
                'title': f"High value: {listing['title'][:40]}",
                'message': f"${listing['price']:,.0f} — ensure promotion is active",
                'item_id': listing['id'],
                'category': '',
            })

    return jsonify(triggered)


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


@app.route('/manifest.json')
def pwa_manifest():
    """PWA manifest for installable app"""
    return jsonify({
        'name': 'DATARADAR',
        'short_name': 'DATARADAR',
        'start_url': '/',
        'display': 'standalone',
        'background_color': '#000000',
        'theme_color': '#000000',
        'description': 'eBay selling optimization for art and collectibles',
    })


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


@app.route('/api/deals/targets/update', methods=['POST'])
def update_deal_targets():
    """Add, edit, or remove deal search targets"""
    data = request.get_json()
    action = data.get('action', '')

    targets_path = os.path.join(DATA_DIR, 'deal_targets.json')
    targets = load_deal_targets()

    if action == 'add':
        targets.append({
            'query': data.get('query', ''),
            'min_price': data.get('min_price', 0),
            'max_price': data.get('max_price', 5000),
            'category': data.get('category', 'Other'),
            'active': True,
        })
    elif action == 'remove':
        idx = data.get('index', -1)
        if 0 <= idx < len(targets):
            targets.pop(idx)
    elif action == 'toggle':
        idx = data.get('index', -1)
        if 0 <= idx < len(targets):
            targets[idx]['active'] = not targets[idx].get('active', True)
    elif action == 'replace_all':
        targets = data.get('targets', [])

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(targets_path, 'w') as f:
        json.dump(targets, f, indent=2)

    # Clear live deals cache
    global _live_deals_cache
    _live_deals_cache = None

    return jsonify({'success': True, 'total': len(targets)})


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


LIVE_DEALS_FILE = os.path.join(DATA_DIR, 'live_deals_cache.json')
_live_deals_cache = None
_live_deals_time = None

# Background scraper state
SCRAPE_STATUS_FILE = os.path.join(DATA_DIR, 'scrape_status.json')
_scrape_running = False


def _save_scrape_status(status):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SCRAPE_STATUS_FILE, 'w') as f:
        json.dump(status, f, indent=2)


def _load_scrape_status():
    if os.path.exists(SCRAPE_STATUS_FILE):
        try:
            with open(SCRAPE_STATUS_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {'running': False, 'progress': 0, 'total': 0, 'found': 0, 'last_query': '', 'last_run': None, 'errors': 0}


def run_background_scrape():
    """Run full scrape of all deal targets with pagination — called in background thread"""
    global _scrape_running, _live_deals_cache, _live_deals_time

    if _scrape_running:
        return

    _scrape_running = True
    targets = load_deal_targets()
    active_targets = [t for t in targets if t.get('active', True)]
    all_deals = []
    errors = 0

    status = {'running': True, 'progress': 0, 'total': len(active_targets), 'found': 0, 'last_query': '', 'started': datetime.now().isoformat(), 'errors': 0}
    _save_scrape_status(status)

    for idx, target in enumerate(active_targets):
        query = target.get('query', '')
        min_price = target.get('min_price', 0)
        max_price = target.get('max_price', 500)
        category = target.get('category', 'Other')

        status['progress'] = idx + 1
        status['last_query'] = query
        status['pct'] = round((idx + 1) / max(len(active_targets), 1) * 100)
        _save_scrape_status(status)

        try:
            # Pull up to 400 per target (2 pages of 200)
            results = search_ebay(query, max_price, min_price, limit=400)
            for r in results:
                r['category'] = category
                r['search_query'] = query
                fake, reason = is_likely_fake(r.get('title', ''), r.get('price', 0), category)
                if fake:
                    continue
                if not passes_artist_quality_gate(r.get('title', ''), category):
                    continue
                all_deals.append(r)
        except Exception as e:
            errors += 1
            print(f"Scrape error for '{query}': {e}")

        status['found'] = len(all_deals)
        status['errors'] = errors

    # Deduplicate by item ID
    seen = set()
    unique = []
    for d in all_deals:
        did = d.get('id', '')
        if did and did not in seen:
            seen.add(did)
            unique.append(d)

    # Save to cache
    result = {
        'deals': unique,
        'total': len(unique),
        'categories': sorted(list(set(d['category'] for d in unique))),
        'searched': len(active_targets),
        'fetched': datetime.now().isoformat(),
    }

    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LIVE_DEALS_FILE, 'w') as f:
            json.dump(result, f)
    except Exception:
        pass

    _live_deals_cache = result
    _live_deals_time = datetime.now()
    _scrape_running = False

    status = {
        'running': False,
        'progress': len(active_targets),
        'total': len(active_targets),
        'found': len(unique),
        'pct': 100,
        'last_run': datetime.now().isoformat(),
        'duration_sec': round((datetime.now() - datetime.fromisoformat(status['started'])).total_seconds()),
        'errors': errors,
        'last_query': 'Complete',
    }
    _save_scrape_status(status)
    print(f"Scrape complete: {len(unique)} deals from {len(active_targets)} targets in {status['duration_sec']}s")


@app.route('/api/scrape/start', methods=['POST'])
def start_scrape():
    """Start a background full scrape of all deal targets"""
    import threading
    if _scrape_running:
        return jsonify({'error': 'Scrape already running', 'status': _load_scrape_status()})

    thread = threading.Thread(target=run_background_scrape, daemon=True)
    thread.start()
    return jsonify({'started': True, 'targets': len([t for t in load_deal_targets() if t.get('active', True)])})


@app.route('/api/scrape/status')
def scrape_status():
    """Get current scrape progress"""
    return jsonify(_load_scrape_status())


@app.route('/api/deals/live')
def get_live_deals():
    """Search eBay LIVE for deals from all deal targets. Cached for 4 hours."""
    global _live_deals_cache, _live_deals_time
    force = request.args.get('refresh', '').lower() == 'true'

    # Check cache — 4 hour TTL (was 30 min)
    if not force and _live_deals_cache and _live_deals_time and (datetime.now() - _live_deals_time).seconds < 14400:
        return jsonify(_live_deals_cache)

    if not force and os.path.exists(LIVE_DEALS_FILE):
        try:
            with open(LIVE_DEALS_FILE, 'r') as f:
                cached = json.load(f)
            if cached.get('fetched'):
                age = (datetime.now() - datetime.fromisoformat(cached['fetched'])).total_seconds()
                if age < 1800:
                    _live_deals_cache = cached
                    _live_deals_time = datetime.now()
                    return jsonify(cached)
        except Exception:
            pass

    targets = load_deal_targets()
    all_deals = []

    for target in targets:
        if target.get('active') is False:
            continue
        query = target.get('query', '')
        min_price = target.get('min_price', 0)
        max_price = target.get('max_price', 500)
        category = target.get('category', 'Other')

        try:
            results = search_ebay(query, max_price, min_price, limit=400)
            for r in results:
                r['category'] = category
                r['search_query'] = query
                fake, reason = is_likely_fake(r.get('title', ''), r.get('price', 0), category)
                if fake:
                    continue
                if not passes_artist_quality_gate(r.get('title', ''), category):
                    continue
                all_deals.append(r)
        except Exception as e:
            print(f"Search error for '{query}': {e}")

    # Deduplicate by item ID
    seen = set()
    unique = []
    for d in all_deals:
        did = d.get('id', '')
        if did and did not in seen:
            seen.add(did)
            unique.append(d)

    result = {
        'deals': unique,
        'total': len(unique),
        'categories': sorted(list(set(d['category'] for d in unique))),
        'searched': len(targets),
        'fetched': datetime.now().isoformat(),
    }

    # Save cache
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LIVE_DEALS_FILE, 'w') as f:
            json.dump(result, f)
    except Exception:
        pass

    _live_deals_cache = result
    _live_deals_time = datetime.now()
    return jsonify(result)


@app.route('/api/deals/enhanced')
def get_enhanced_deals():
    """Get deals with full market context — static + live eBay merged"""
    # Load static art deals — filter to active categories only
    active_targets = load_deal_targets()
    active_categories = set(t.get('category', '') for t in active_targets if t.get('active', True))

    static_deals = load_art_deals()
    static_deals = [d for d in static_deals if 'ebay.com' in (d.get('url') or '')]
    static_deals = [d for d in static_deals if not is_likely_fake(d.get('title', ''), d.get('price', 0), d.get('artist', ''))[0]]
    # Only keep deals whose artist matches an active category
    if active_categories:
        static_deals = [d for d in static_deals if d.get('artist', '') in active_categories or any(
            ac.lower() in d.get('artist', '').lower() or d.get('artist', '').lower() in ac.lower()
            for ac in active_categories
        )]

    # Load live deals from Browse API (all new categories)
    live_deals = []
    try:
        live_cache = None
        if os.path.exists(LIVE_DEALS_FILE):
            with open(LIVE_DEALS_FILE, 'r') as f:
                live_cache = json.load(f)
        if live_cache and live_cache.get('deals'):
            for ld in live_cache['deals']:
                live_deals.append({
                    'title': ld.get('title', ''),
                    'artist': ld.get('category', 'Other'),
                    'price': ld.get('price', 0),
                    'url': ld.get('url', ''),
                    'low': 0,
                    'median': 0,
                    'high': 0,
                    'sales_count': 0,
                    'profit': 0,
                    'discount_pct': 0,
                    'history': [],
                    'condition': ld.get('condition', ''),
                    'seller': ld.get('seller', ''),
                    '_live': True,
                })
    except Exception:
        pass

    # Merge: static deals have comps/market data, live deals are fresh from eBay
    seen_titles = set()
    deals = []
    for d in static_deals:
        key = d.get('title', '').lower()[:50]
        if key not in seen_titles:
            seen_titles.add(key)
            deals.append(d)
    for d in live_deals:
        key = d.get('title', '').lower()[:50]
        if key not in seen_titles:
            seen_titles.add(key)
            deals.append(d)

    index = load_market_index()
    categories = index.get('categories', {}) if index else {}

    enhanced = []
    for d in deals:
        artist = d.get('artist', 'Unknown')
        price = d.get('price', 0)
        median = d.get('median', 0)
        sales_count = d.get('sales_count', 0)
        profit = d.get('profit', 0)
        discount_pct = d.get('discount_pct', 0)
        history = d.get('history', [])

        # Find last comp date
        last_comp_date = ''
        for h in sorted(history, key=lambda x: x.get('date', ''), reverse=True):
            if h.get('date') and h['date'] != 'Unknown':
                last_comp_date = h['date']
                break

        # Calculate hotness score (0-100)
        # Based on: sales volume, discount depth, profit potential
        hotness = 0
        if sales_count >= 10:
            hotness += 30
        elif sales_count >= 5:
            hotness += 20
        elif sales_count >= 2:
            hotness += 10

        if discount_pct >= 60:
            hotness += 30
        elif discount_pct >= 40:
            hotness += 20
        elif discount_pct >= 20:
            hotness += 10

        if profit > 500:
            hotness += 25
        elif profit > 200:
            hotness += 15
        elif profit > 50:
            hotness += 10

        # Recent sales recency bonus
        if history:
            recent_dates = [h.get('date', '') for h in history if h.get('date')]
            if recent_dates:
                hotness += 15  # has price history

        hotness = min(100, hotness)

        # Liquidity: how many comps, how fast things sell
        if sales_count >= 10:
            liquidity = 'High'
        elif sales_count >= 5:
            liquidity = 'Medium'
        elif sales_count >= 2:
            liquidity = 'Low'
        else:
            liquidity = 'Very Low'

        # Why it's a deal
        reasons = []
        if discount_pct >= 50:
            reasons.append(f'{discount_pct:.0f}% below market median')
        elif discount_pct >= 20:
            reasons.append(f'{discount_pct:.0f}% below median')

        if profit > 500:
            reasons.append(f'${profit:,.0f} profit potential')
        elif profit > 100:
            reasons.append(f'${profit:,.0f} upside')

        if sales_count >= 5:
            reasons.append(f'{sales_count} recent comps confirm value')

        if not reasons:
            reasons.append('Priced below comparable sales')

        enhanced.append({
            **d,
            'category': artist,
            'hotness': hotness,
            'hotness_label': 'Hot' if hotness >= 60 else 'Warm' if hotness >= 30 else 'Cool',
            'liquidity': liquidity,
            'liquidity_score': sales_count,
            'reasons': reasons,
            'comp_count': sales_count,
            'last_comp_date': last_comp_date,
            'price_range': {
                'low': d.get('low', 0),
                'median': median,
                'high': d.get('high', 0),
            },
        })

    # Sort by hotness then profit
    enhanced.sort(key=lambda x: (-x['hotness'], -x.get('profit', 0)))

    # Build category summary
    cat_summary = {}
    for d in enhanced:
        cat = d['category']
        if cat not in cat_summary:
            cat_summary[cat] = {'count': 0, 'avg_hotness': 0, 'total_profit': 0}
        cat_summary[cat]['count'] += 1
        cat_summary[cat]['avg_hotness'] += d['hotness']
        cat_summary[cat]['total_profit'] += d.get('profit', 0)

    for cat in cat_summary:
        cat_summary[cat]['avg_hotness'] = round(cat_summary[cat]['avg_hotness'] / cat_summary[cat]['count'])

    return jsonify({
        'deals': enhanced,
        'categories': cat_summary,
        'total': len(enhanced),
    })


@app.route('/api/deals/product-search')
def deal_product_search():
    """Search for a product on eBay, find comps, calculate profit — full deal analysis.
    Returns product cards with images, comparable sales, BUY/PASS verdict."""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'error': 'Missing query'}), 400

    # Detect artist from query
    q_lower = query.lower()
    artist = ''
    artist_map = [
        (['bearbrick', 'be@rbrick'], 'Bearbrick'),
        (['shepard fairey', 'obey giant', 'obey print'], 'Shepard Fairey'),
        (['death nyc'], 'Death NYC'),
        (['kaws'], 'KAWS'),
        (['banksy'], 'Banksy'),
        (['brainwash', 'mbw'], 'Mr. Brainwash'),
        (['invader mosaic', 'invader signed', 'invader print', 'invader alias', 'invader rubik'], 'Invader'),
        (['murakami', 'takashi'], 'Murakami'),
        (['arsham', 'daniel arsham'], 'Arsham'),
        (['nara', 'yoshitomo'], 'Nara'),
        (['futura 2000', 'futura pointman'], 'Futura'),
        (['stik signed', 'stik print', 'stik holding'], 'Stik'),
        (['retna signed', 'retna print', 'retna original'], 'Retna'),
        (['warhol', 'andy warhol'], 'Warhol'),
        (['basquiat'], 'Basquiat'),
        (['keith haring', 'haring pop'], 'Haring'),
        (['damien hirst', 'hirst spot', 'hirst butterfly', 'hirst currency'], 'Hirst'),
        (['hebru brantley', 'brantley flyboy'], 'Brantley'),
        (['apollo', 'astronaut signed', 'nasa signed', 'buzz aldrin', 'neil armstrong'], 'Signed Apollo'),
        (['beatles signed', 'lennon signed', 'elvis signed', 'bowie signed'], 'Beatles/Rock'),
    ]
    for keywords, name in artist_map:
        if any(kw in q_lower for kw in keywords):
            artist = name
            break

    min_price = float(request.args.get('min_price', 0))
    max_price = float(request.args.get('max_price', 10000))

    # Search eBay for products matching query — pull lots of results
    products = search_ebay(query, max_price, min_price, limit=200)

    # Filter fakes + artist quality gate (e.g., SF must be signed/numbered)
    filtered = []
    for p in products:
        fake, reason = is_likely_fake(p.get('title', ''), p.get('price', 0), artist)
        if fake:
            continue
        if not passes_artist_quality_gate(p.get('title', ''), artist):
            continue
        filtered.append(p)

    # Deduplicate by title similarity
    seen = set()
    unique = []
    for p in filtered:
        key = re.sub(r'[^a-z0-9]', '', p.get('title', '').lower())[:40]
        if key not in seen:
            seen.add(key)
            unique.append(p)

    # For each product, get comp data and calculate profit
    enriched = []
    for p in unique[:60]:  # Cap at 60 products with comp enrichment
        title = p.get('title', '')
        price = p.get('price', 0)

        # Get historical comps
        historical = lookup_historical_prices(title, artist, 15)
        h_prices = [h['price'] for h in historical if h.get('price', 0) > 0]

        # Get smart comps from eBay active listings (lightweight — no LLM)
        noise = {'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of', 'is', 'by', 'with',
                 'new', 'lot', 'rare', 'free', 'shipping', 'print', 'signed', 'numbered', 'hand',
                 'screen', 'edition', 'limited', 'art', 'original', 'artist', 'proof', 'framed',
                 'obey', 'giant', 'authentic', 'vinyl', 'figure', 'open'}
        title_words = [w for w in re.findall(r'\w+', title) if w.lower() not in noise and len(w) > 2]
        title_word_set = set(w.lower() for w in title_words)

        # Search for sold comps with key title words
        comp_query = f"{artist} {' '.join(title_words[:3])}" if artist else ' '.join(title_words[:4])
        comp_min = FAKE_PRICE_THRESHOLDS.get(artist, 15)
        active_comps = []
        if comp_query.strip():
            try:
                raw_comps = search_ebay(comp_query, max(price * 4, 500), comp_min, limit=15)
                # Pre-filter: word overlap + quality gate + learned rejections
                rej_data = load_comp_rejections()
                for c in raw_comps:
                    ct = c.get('title', '')
                    c_words = set(w.lower() for w in re.findall(r'\w+', ct) if len(w) > 2)
                    overlap = title_word_set & c_words
                    is_rej, _ = comp_matches_learned_rejection(ct, rej_data)
                    if len(overlap) >= 1 and passes_artist_quality_gate(ct, artist) and not is_rej:
                        active_comps.append(c)
            except Exception:
                pass

        # Combine all comp prices
        all_prices = h_prices + [c['price'] for c in active_comps if c.get('price', 0) > 0]

        # IQR outlier removal
        if len(all_prices) >= 4:
            sp = sorted(all_prices)
            q1, q3 = sp[len(sp)//4], sp[3*len(sp)//4]
            iqr = q3 - q1
            all_prices = [x for x in all_prices if q1 - 1.5*iqr <= x <= q3 + 1.5*iqr]

        comp_count = len(all_prices)
        if all_prices:
            sp = sorted(all_prices)
            median_val = sp[len(sp)//2]
            avg_val = round(sum(sp)/len(sp))
            low_val = sp[0]
            high_val = sp[-1]
        else:
            median_val = avg_val = low_val = high_val = 0

        # Calculate profit potential
        profit = round(median_val - price) if median_val > 0 else 0
        discount_pct = round((1 - price / median_val) * 100) if median_val > 0 else 0

        # Hotness score
        hotness = 0
        if comp_count >= 8: hotness += 30
        elif comp_count >= 4: hotness += 20
        elif comp_count >= 2: hotness += 10
        if discount_pct >= 50: hotness += 30
        elif discount_pct >= 30: hotness += 20
        elif discount_pct >= 15: hotness += 10
        if profit > 500: hotness += 25
        elif profit > 200: hotness += 15
        elif profit > 50: hotness += 10
        if h_prices: hotness += 15  # Has historical data
        hotness = min(100, hotness)

        # Liquidity
        if comp_count >= 8: liquidity = 'High'
        elif comp_count >= 4: liquidity = 'Medium'
        elif comp_count >= 2: liquidity = 'Low'
        else: liquidity = 'Unknown'

        # Verdict
        if profit > 100 and discount_pct > 25 and comp_count >= 3:
            verdict = 'BUY'
        elif profit > 50 and discount_pct > 15 and comp_count >= 2:
            verdict = 'CONSIDER'
        elif profit < 0:
            verdict = 'OVERPRICED'
        elif comp_count < 2:
            verdict = 'RESEARCH'
        else:
            verdict = 'PASS'

        # Reasons
        reasons = []
        if discount_pct > 40: reasons.append(f'{discount_pct}% below median')
        elif discount_pct > 15: reasons.append(f'{discount_pct}% below market')
        if profit > 200: reasons.append(f'${profit} profit potential')
        elif profit > 50: reasons.append(f'${profit} upside')
        if comp_count >= 5: reasons.append(f'{comp_count} comps confirm value')
        if not reasons:
            if profit > 0: reasons.append('Priced below comparable sales')
            elif profit == 0: reasons.append('No comp data — needs research')
            else: reasons.append(f'${abs(profit)} over market')

        # Item attributes
        attrs = extract_item_attributes(title)

        enriched.append({
            'title': title,
            'price': price,
            'image': p.get('image', ''),
            'url': p.get('url', ''),
            'condition': p.get('condition', ''),
            'seller': p.get('seller', ''),
            'buying_option': p.get('buying_option', ''),
            'listed_date': p.get('listed_date', ''),
            'artist': artist,
            'verdict': verdict,
            'hotness': hotness,
            'liquidity': liquidity,
            'profit': profit,
            'discount_pct': discount_pct,
            'median': median_val,
            'avg': avg_val,
            'comp_low': low_val,
            'comp_high': high_val,
            'comp_count': comp_count,
            'historical_count': len(h_prices),
            'active_comp_count': len(active_comps),
            'reasons': reasons,
            'signed': attrs['signed'],
            'numbered': attrs['numbered'],
            'comps': [{'title': c.get('title', '')[:60], 'price': c['price'], 'url': c.get('url', '')} for c in active_comps[:5]],
            'historical_comps': [{'name': h.get('name', '')[:60], 'price': h['price'], 'date': h.get('date', ''), 'source': h.get('source', '')} for h in historical[:5]],
        })

    # Sort by verdict priority then hotness
    verdict_order = {'BUY': 0, 'CONSIDER': 1, 'RESEARCH': 2, 'PASS': 3, 'OVERPRICED': 4}
    enriched.sort(key=lambda x: (verdict_order.get(x['verdict'], 3), -x['hotness'], -x['profit']))

    return jsonify({
        'products': enriched,
        'total': len(enriched),
        'query': query,
        'artist': artist,
    })


@app.route('/api/inventory/enhanced')
def get_enhanced_inventory():
    """Get inventory with sell/wait signals and sorting metadata"""
    items = load_personal_inventory()
    listings = ebay.get_all_listings()
    listing_map = {l['title'].lower(): l for l in listings}

    enhanced = []
    for item in items:
        supply = item.get('ebay_supply', {})
        market = item.get('market_data', {})
        rec = supply.get('recommendation', item.get('recommendation', 'RESEARCH'))
        reason = supply.get('reason', item.get('recommendation_reason', ''))

        # Determine sell/wait signal
        if rec in ('SELL NOW', 'GOOD TO SELL'):
            signal = 'SELL'
            signal_strength = 'strong' if rec == 'SELL NOW' else 'moderate'
        elif rec in ('HOLD', 'WAIT'):
            signal = 'WAIT'
            signal_strength = 'strong' if rec == 'WAIT' else 'moderate'
        elif rec == 'SET PRICE':
            signal = 'PRICE'
            signal_strength = 'moderate'
        else:
            signal = 'RESEARCH'
            signal_strength = 'low'

        # Supply/demand context
        ebay_count = supply.get('ebay_count', 0)
        ebay_avg = supply.get('ebay_avg_price', 0)

        suggested = item.get('suggested_price', 0)
        your_price = item.get('your_price', 0)

        # Market health
        comp_count = item.get('comparable_sales', 0)
        recent_sales = item.get('recent_sales', 0)

        enhanced.append({
            'id': item['id'],
            'name': item['name'],
            'artist': item.get('artist', 'Unknown'),
            'category': item.get('category', item.get('artist', 'Unknown')),
            'signal': signal,
            'signal_strength': signal_strength,
            'recommendation': rec,
            'reason': reason,
            'suggested_price': suggested,
            'your_price': your_price,
            'price_range': item.get('price_range', ''),
            'market_median': market.get('median', 0),
            'market_avg': market.get('avg', 0),
            'market_min': market.get('min', 0),
            'market_max': market.get('max', 0),
            'comp_count': comp_count,
            'recent_sales': recent_sales,
            'ebay_supply': ebay_count,
            'ebay_avg_price': ebay_avg,
            'competing': supply.get('competing_listings', []),
            'price_history': market.get('price_history', []),
        })

    # Sort: SELL NOW first, then GOOD TO SELL, etc.
    order = {'SELL NOW': 0, 'GOOD TO SELL': 1, 'SET PRICE': 2, 'HOLD': 3, 'WAIT': 4, 'RESEARCH': 5}
    enhanced.sort(key=lambda x: order.get(x['recommendation'], 5))

    # Build signal summary
    signals = {}
    for item in enhanced:
        s = item['signal']
        if s not in signals:
            signals[s] = {'count': 0, 'value': 0}
        signals[s]['count'] += 1
        signals[s]['value'] += item['suggested_price'] or 0

    return jsonify({
        'items': enhanced,
        'signals': signals,
        'total': len(enhanced),
        'total_value': sum(i['suggested_price'] or 0 for i in enhanced),
    })


@app.route('/api/update-category-pricing', methods=['POST'])
def update_category_pricing():
    """Bulk update prices for items by category"""
    data = request.get_json()
    item_ids = data.get('item_ids', [])
    adjust_type = data.get('adjustment_type', '')
    adjust_value = data.get('adjustment_value', 0)

    if not item_ids or not adjust_type or adjust_value == 0:
        return jsonify({'success': False, 'error': 'Missing parameters'})

    updated = 0
    failed = 0
    listings = ebay.get_all_listings()
    listing_map = {l['id']: l for l in listings}

    for item_id in item_ids:
        listing = listing_map.get(item_id)
        if not listing:
            failed += 1
            continue

        old_price = listing['price']

        if adjust_type == 'percent_increase':
            new_price = old_price * (1 + adjust_value / 100)
        elif adjust_type == 'percent_decrease':
            new_price = old_price * (1 - adjust_value / 100)
        elif adjust_type == 'fixed_increase':
            new_price = old_price + adjust_value
        elif adjust_type == 'fixed_decrease':
            new_price = old_price - adjust_value
        elif adjust_type == 'set_price':
            new_price = adjust_value
        else:
            failed += 1
            continue

        new_price = max(0.99, round(new_price, 2))

        if ebay.update_price(item_id, new_price):
            updated += 1
        else:
            failed += 1

    return jsonify({'success': True, 'updated': updated, 'failed': failed})


# =============================================================================
# Flask Routes — Features 1-9 (Analytics & Intelligence)
# =============================================================================

@app.route('/api/sold-items')
def get_sold_items_api():
    """Get sold items with days-on-market (#1, #8)"""
    data = fetch_and_cache_sold()
    return jsonify(data)


@app.route('/api/traffic')
def get_traffic_api():
    """Get listing traffic data — impressions, views, CTR, conversion (#7)"""
    traffic = fetch_and_cache_traffic()
    return jsonify(traffic)



@app.route('/api/alerts/rules', methods=['GET', 'POST'])
def manage_alert_rules():
    """Manage alert rules (#3)"""
    if request.method == 'POST':
        data = request.get_json()
        alerts = load_alerts()
        alerts['rules'].append(data)
        save_alerts(alerts)
        return jsonify({'success': True})
    return jsonify(load_alerts())


@app.route('/api/ab-tests', methods=['GET', 'POST'])
def manage_ab_tests():
    """Manage A/B test configurations (#4)"""
    if request.method == 'POST':
        data = request.get_json()
        tests = load_ab_tests()
        test = {
            'id': f"test-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'name': data.get('name', 'Unnamed Test'),
            'category': data.get('category', 'all'),
            'groups': data.get('groups', [
                {'name': 'Control', 'ad_rate': 2.0},
                {'name': 'Test A', 'ad_rate': 4.0},
                {'name': 'Test B', 'ad_rate': 6.0},
            ]),
            'status': 'active',
            'created': datetime.now().isoformat(),
            'results': {},
        }
        tests['tests'].append(test)
        save_ab_tests(tests)
        return jsonify({'success': True, 'test': test})
    return jsonify(load_ab_tests())


@app.route('/api/seasonal-suggestions')
def get_seasonal_suggestions():
    """Get seasonal promo rate suggestions (#5)"""
    suggestions = get_seasonal_promo_suggestions()
    return jsonify(suggestions)


@app.route('/api/supply-snapshot', methods=['POST'])
def take_supply_snapshot():
    """Take a supply snapshot for competitor monitoring (#9)"""
    inventory = load_personal_inventory()
    data = save_supply_snapshot(inventory)
    return jsonify({'success': True, 'snapshots': len(data.get('snapshots', []))})


@app.route('/api/supply-trends')
def get_supply_trends_api():
    """Get supply trends over time (#9)"""
    item_id = request.args.get('item_id')
    if item_id:
        return jsonify(get_supply_trends(item_id))

    # Return all trends summary
    data = load_supply_snapshots()
    if not data.get('snapshots'):
        return jsonify({'snapshots': 0, 'message': 'No snapshots yet. Take first snapshot.'})

    latest = data['snapshots'][-1] if data['snapshots'] else {}
    oldest = data['snapshots'][0] if data['snapshots'] else {}

    trends = {}
    for item_id, latest_data in latest.get('items', {}).items():
        oldest_data = oldest.get('items', {}).get(item_id, {})
        old_count = oldest_data.get('ebay_count', latest_data.get('ebay_count', 0))
        new_count = latest_data.get('ebay_count', 0)
        change = new_count - old_count

        if change > 0:
            direction = 'increasing'
        elif change < 0:
            direction = 'decreasing'
        else:
            direction = 'stable'

        trends[item_id] = {
            'name': latest_data.get('name', ''),
            'current_supply': new_count,
            'previous_supply': old_count,
            'change': change,
            'direction': direction,
        }

    return jsonify({
        'snapshots': len(data['snapshots']),
        'period': f"{oldest.get('date', '?')} to {latest.get('date', '?')}",
        'trends': trends,
    })


def compute_smart_price(item):
    """V2 Pricing Engine — multi-layer with outlier removal, recency weighting,
    range-based pricing, and hard floor at last sale.

    Layer 1: Statistical — IQR outlier removal, recency-weighted average
    Layer 2: Attribute filtering — only use comps that match signed/numbered/medium
    Layer 3: Range calculation — low/mid/high with confidence band
    Layer 4: Hard floor — NEVER below last sale price

    Returns price suggestion + full breakdown.
    """
    import statistics

    market = item.get('market_data', {})
    supply = item.get('ebay_supply', {})
    history = market.get('price_history', [])
    ebay_avg = supply.get('ebay_avg_price', 0)
    ebay_count = supply.get('ebay_count', 0)
    old_suggested = market.get('suggested_price', 0) or item.get('suggested_price', 0)
    item_name = item.get('name', '')

    # ── Layer 1: Collect all prices with dates ──
    all_comps = []
    for h in history:
        p = h.get('price')
        d = h.get('date', '')
        if p and p > 0:
            all_comps.append({'price': p, 'date': d, 'source': h.get('source', '')})

    # ── Layer 2: IQR outlier removal ──
    raw_prices = [c['price'] for c in all_comps]
    clean_prices = raw_prices
    removed_outliers = []

    if len(raw_prices) >= 4:
        sorted_p = sorted(raw_prices)
        q1 = sorted_p[len(sorted_p) // 4]
        q3 = sorted_p[3 * len(sorted_p) // 4]
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        clean_prices = [p for p in raw_prices if lower <= p <= upper]
        removed_outliers = [p for p in raw_prices if p < lower or p > upper]
    elif len(raw_prices) >= 2:
        # With few comps, use median-based filtering (remove if >3x or <0.3x median)
        med = statistics.median(raw_prices)
        clean_prices = [p for p in raw_prices if med * 0.3 <= p <= med * 3]
        removed_outliers = [p for p in raw_prices if p not in clean_prices]

    # ── Layer 3: Recency weighting ──
    # Recent comps (2024+) get 3x weight, moderate (2022-2023) 2x, old (before 2022) 1x
    weighted_prices = []
    for c in all_comps:
        p = c['price']
        if p not in clean_prices and p in removed_outliers:
            continue  # Skip outliers
        d = c.get('date', '')
        if d >= '2024-01-01':
            weighted_prices.extend([p] * 3)  # 3x weight
        elif d >= '2022-01-01':
            weighted_prices.extend([p] * 2)  # 2x weight
        else:
            weighted_prices.append(p)  # 1x weight

    if not weighted_prices:
        weighted_prices = clean_prices if clean_prices else raw_prices

    # ── Layer 4: Find last sale (most recent) — THIS IS THE FLOOR ──
    last_sale_price = 0
    last_sale_date = ''
    if all_comps:
        sorted_comps = sorted(all_comps, key=lambda c: c.get('date', ''), reverse=True)
        for c in sorted_comps:
            if c['price'] > 0 and c.get('date', '') and c['date'] != 'Unknown':
                last_sale_price = c['price']
                last_sale_date = c['date']
                break

    # ── Layer 5: Compute range ──
    price_low = 0
    price_mid = 0
    price_high = 0

    if weighted_prices:
        sorted_wp = sorted(weighted_prices)
        price_low = sorted_wp[max(0, len(sorted_wp) // 4)]  # 25th percentile
        price_mid = statistics.median(weighted_prices)
        price_high = sorted_wp[min(len(sorted_wp) - 1, 3 * len(sorted_wp) // 4)]  # 75th percentile
    elif old_suggested:
        price_mid = old_suggested
        price_low = old_suggested * 0.85
        price_high = old_suggested * 1.15

    # ── Layer 6: Supply/demand adjustment ──
    adjustment = 0
    adjustment_reason = ''
    if ebay_count <= 2 and price_mid > 0:
        adjustment = round(price_mid * 0.10, 2)
        adjustment_reason = f'Low supply ({ebay_count}) +10%'
    elif ebay_count >= 20 and price_mid > 0:
        adjustment = round(price_mid * -0.08, 2)
        adjustment_reason = f'High supply ({ebay_count}) -8%'

    # ── Layer 7: Final price — never below last sale ──
    suggested = round(price_mid + adjustment, 2)

    # HARD FLOOR: never suggest below last sale price
    if last_sale_price > 0 and suggested < last_sale_price:
        suggested = last_sale_price
        adjustment_reason = (adjustment_reason + ' | ' if adjustment_reason else '') + f'Floor: last sale ${last_sale_price:.0f}'

    # Confidence
    data_points = len(clean_prices)
    has_recent = any(c.get('date', '') >= '2024-01-01' for c in all_comps)
    if data_points >= 5 and has_recent:
        confidence = 'high'
    elif data_points >= 3:
        confidence = 'medium'
    else:
        confidence = 'low'

    return {
        'smart_price': suggested,
        'base_price': round(price_mid, 2),
        'adjustment': adjustment,
        'adjustment_reason': adjustment_reason,
        'components': {
            'range_low': {'price': round(price_low, 2), 'weight': 25},
            'range_mid': {'price': round(price_mid, 2), 'weight': 50},
            'range_high': {'price': round(price_high, 2), 'weight': 25},
            'last_sale': {'price': last_sale_price, 'weight': 0, 'date': last_sale_date},
            'ebay_current': {'price': round(ebay_avg, 2), 'weight': 0},
        },
        'last_sale': last_sale_price,
        'last_sale_date': last_sale_date,
        'old_suggested': old_suggested,
        'confidence': confidence,
        'price_range': {'low': round(price_low, 2), 'mid': round(price_mid, 2), 'high': round(price_high, 2)},
        'stats': {
            'total_comps': len(raw_prices),
            'clean_comps': len(clean_prices),
            'outliers_removed': len(removed_outliers),
            'outlier_values': removed_outliers[:5],
            'recency_weighted': len(weighted_prices),
            'has_recent': has_recent,
        },
    }


# Category velocity calibrated from ACTUAL sales data (60-day history)
# Shepard Fairey avg 16d, Death NYC 15d, Banksy 4d, Space 22d, Music 12d
CATEGORY_VELOCITY = {
    'Shepard Fairey': {'blazing': 5, 'fast': 12, 'moderate': 21, 'slow': 35},  # avg 16d
    'Death NYC': {'blazing': 5, 'fast': 12, 'moderate': 21, 'slow': 35},       # avg 15d
    'KAWS': {'blazing': 3, 'fast': 10, 'moderate': 21, 'slow': 40},
    'Banksy': {'blazing': 2, 'fast': 5, 'moderate': 10, 'slow': 21},           # avg 4d — sells fast
    'Mr. Brainwash': {'blazing': 5, 'fast': 14, 'moderate': 25, 'slow': 40},
    'Bearbrick': {'blazing': 5, 'fast': 14, 'moderate': 25, 'slow': 40},
    'Space/NASA': {'blazing': 7, 'fast': 18, 'moderate': 30, 'slow': 60},      # avg 22d — high-value slow
    'Signed Music': {'blazing': 3, 'fast': 10, 'moderate': 18, 'slow': 30},    # avg 12d
    'Pickguard': {'blazing': 5, 'fast': 14, 'moderate': 25, 'slow': 45},
    'Other': {'blazing': 7, 'fast': 18, 'moderate': 30, 'slow': 60},           # avg 21d
}


def get_velocity_rating(artist, dom, times_sold, ebay_count):
    """5-level velocity: Blazing, Fast, Moderate, Slow, Stale"""
    thresholds = CATEGORY_VELOCITY.get(artist, CATEGORY_VELOCITY['Other'])

    if times_sold == 0 and ebay_count > 15:
        return 'Stale'

    if dom is None:
        # No DOM data — estimate from sales count
        if times_sold >= 3:
            return 'Fast'
        elif times_sold >= 1:
            return 'Moderate'
        else:
            return 'Slow'

    if dom <= thresholds['blazing']:
        return 'Blazing'
    elif dom <= thresholds['fast']:
        return 'Fast'
    elif dom <= thresholds['moderate']:
        return 'Moderate'
    elif dom <= thresholds['slow']:
        return 'Slow'
    else:
        return 'Stale'


@app.route('/api/pricing/llm-review', methods=['POST'])
def llm_review_pricing():
    """Get dual-LLM review of comps and pricing for a specific item"""
    data = request.get_json()
    title = data.get('title', '')
    current_price = data.get('price', 0)
    comps = data.get('comps', [])

    if not comps:
        # Fetch comps
        artist = data.get('artist', '')
        comps_raw = lookup_historical_prices(title, artist, 20)
        comps = [{'name': c.get('name', ''), 'price': c.get('price', 0), 'date': c.get('date', '')} for c in comps_raw]

    comp_text = '\n'.join([f"  ${c['price']:.0f} — {c['name'][:50]} ({c.get('date', '?')})" for c in comps[:15]])

    prompt = f"""You are a collectibles pricing expert. Review these comparable sales for an eBay listing and suggest a price RANGE.

ITEM: {title}
CURRENT PRICE: ${current_price:.2f}

COMPARABLE SALES:
{comp_text}

RULES:
1. REMOVE OUTLIERS — if most comps are $200-$400 and one is $25, that $25 is a different product (sticker, knockoff, etc). Ignore it.
2. WEIGHT RECENT — recent sales matter more than old ones.
3. NEVER suggest below the most recent legitimate sale price.
4. If comps are old (2+ years), prices have likely increased 10-20%.
5. Consider if the item is signed, numbered, limited edition — those command premiums.

Respond in this exact JSON format:
{{"price_low": <number>, "price_mid": <number>, "price_high": <number>, "outliers_removed": [<prices>], "rationale": "<2-3 sentences>", "floor_price": <number>}}"""

    results = []

    # Claude
    claude_key = ENV.get('CLAUDE_API_KEY', '')
    if claude_key:
        try:
            resp = requests.post('https://api.anthropic.com/v1/messages',
                headers={'x-api-key': claude_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                json={'model': 'claude-sonnet-4-5-20241022', 'max_tokens': 300, 'messages': [{'role': 'user', 'content': prompt}]},
                timeout=20)
            if resp.status_code == 200:
                text = resp.json().get('content', [{}])[0].get('text', '')
                match = re.search(r'\{[^}]+\}', text, re.DOTALL)
                if match:
                    results.append({'model': 'Claude', **json.loads(match.group())})
        except Exception as e:
            print(f"Claude pricing error: {e}")

    # OpenAI
    openai_key = ENV.get('OPENAI_API_KEY', '')
    if openai_key:
        try:
            resp = requests.post('https://api.openai.com/v1/chat/completions',
                headers={'Authorization': f'Bearer {openai_key}', 'Content-Type': 'application/json'},
                json={'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 300},
                timeout=20)
            if resp.status_code == 200:
                text = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
                match = re.search(r'\{[^}]+\}', text, re.DOTALL)
                if match:
                    results.append({'model': 'GPT-4o', **json.loads(match.group())})
        except Exception as e:
            print(f"GPT pricing error: {e}")

    if results:
        # Consensus
        avg_low = sum(r.get('price_low', 0) for r in results) / len(results)
        avg_mid = sum(r.get('price_mid', 0) for r in results) / len(results)
        avg_high = sum(r.get('price_high', 0) for r in results) / len(results)
        floor = max(r.get('floor_price', 0) for r in results)

        # Enforce floor
        if avg_mid < floor:
            avg_mid = floor

        return jsonify({
            'consensus': {
                'price_low': round(avg_low, 2),
                'price_mid': round(avg_mid, 2),
                'price_high': round(avg_high, 2),
                'floor': round(floor, 2),
            },
            'models': results,
            'comps_reviewed': len(comps),
        })

    return jsonify({'error': 'No LLM responses'}), 500


@app.route('/api/inventory/full-analytics')
def get_full_inventory_analytics():
    """Inventory = eBay active listings as source of truth, enriched with market data"""
    enriched_inventory = load_personal_inventory()
    listings = ebay.get_all_listings()
    sold_data = fetch_and_cache_sold()
    traffic_data = fetch_and_cache_traffic()
    promo_data = fetch_all_promotions()
    per_listing_promos = promo_data.get('per_listing', {})
    seasonal = get_seasonal_promo_suggestions()
    supply_data = load_supply_snapshots()

    # Build enrichment lookup — fuzzy match enriched items to eBay listings
    # Use word overlap matching since names are different formats
    enriched_by_words = {}
    for item in enriched_inventory:
        name_words = set(re.findall(r'\w+', item['name'].lower()))
        # Remove very common words
        name_words -= {'the', 'a', 'and', 'of', 'in', 'print', 'signed', 'obey', 'giant', 'shepard', 'fairey', 'numbered', 'edition'}
        if len(name_words) >= 2:
            key = frozenset(list(name_words)[:6])
            enriched_by_words[key] = item

    def find_enrichment(title):
        """Find best enrichment match for an eBay title"""
        title_words = set(re.findall(r'\w+', title.lower()))
        title_words -= {'the', 'a', 'and', 'of', 'in', 'print', 'signed', 'obey', 'giant', 'shepard', 'fairey', 'numbered', 'edition', 'new', 'rare', 'limited'}
        best_match = None
        best_overlap = 0
        for key, item in enriched_by_words.items():
            overlap = len(title_words & key)
            if overlap >= 2 and overlap > best_overlap:
                best_overlap = overlap
                best_match = item
        return best_match

    # Auto-enrichment cache
    ENRICHMENT_FILE = os.path.join(DATA_DIR, 'auto_enrichment.json')
    enrichment_cache = {}
    if os.path.exists(ENRICHMENT_FILE):
        try:
            with open(ENRICHMENT_FILE, 'r') as f:
                enrichment_cache = json.load(f)
        except Exception:
            pass

    # Build inventory from eBay listings (source of truth)
    inventory = []
    for listing in listings:
        title_lower = listing['title'].lower()

        # Detect artist/category
        if 'shepard fairey' in title_lower or 'obey' in title_lower:
            artist = 'Shepard Fairey'
        elif 'death nyc' in title_lower:
            artist = 'Death NYC'
        elif 'bearbrick' in title_lower or 'be@rbrick' in title_lower:
            artist = 'Bearbrick'
        elif 'kaws' in title_lower:
            artist = 'KAWS'
        elif 'banksy' in title_lower:
            artist = 'Banksy'
        elif 'brainwash' in title_lower or 'mbw' in title_lower:
            artist = 'Mr. Brainwash'
        elif 'apollo' in title_lower or 'nasa' in title_lower or 'astronaut' in title_lower:
            artist = 'Space/NASA'
        elif 'pickguard' in title_lower:
            artist = 'Pickguard'
        elif ('vinyl' in title_lower or 'record' in title_lower or 'album' in title_lower) and 'signed' in title_lower:
            artist = 'Signed Music'
        else:
            artist = 'Other'

        # Try to find enrichment data
        enriched = find_enrichment(listing['title'])
        cached = enrichment_cache.get(listing['id'], {})

        if enriched:
            md = enriched.get('market_data', {})
            es = enriched.get('ebay_supply', {})
            rec = es.get('recommendation', enriched.get('recommendation', 'RESEARCH'))
            reason = es.get('reason', enriched.get('recommendation_reason', ''))
            suggested = enriched.get('suggested_price', 0)
            comp_count = enriched.get('comparable_sales', 0)
            recent = enriched.get('recent_sales', 0)
            price_range = enriched.get('price_range', '')
        elif cached:
            md = cached.get('market_data', {})
            es = cached.get('ebay_supply', {})
            rec = cached.get('recommendation', 'RESEARCH')
            reason = cached.get('recommendation_reason', '')
            suggested = md.get('suggested_price', listing['price'])
            comp_count = md.get('count', 0)
            recent = 0
            price_range = f"${md.get('min',0):.0f} - ${md.get('max',0):.0f}" if md.get('min') else ''
        else:
            md = {}
            es = {}
            rec = 'RESEARCH'
            reason = 'No enrichment data yet — click Enrich Inventory'
            suggested = listing['price']
            comp_count = 0
            recent = 0
            price_range = ''

        inventory.append({
            'id': listing['id'],
            'name': listing['title'],
            'artist': artist,
            'category': artist,
            'source': 'eBay',
            'suggested_price': suggested,
            'your_price': listing['price'],
            'price_range': price_range,
            'market_data': md,
            'ebay_supply': es,
            'recommendation': rec,
            'recommendation_reason': reason,
            'comparable_sales': comp_count,
            'recent_sales': recent,
            '_ebay_listing': listing,
        })

    # Build sold lookup
    sold_items = sold_data.get('items', [])
    sold_by_title = {}
    for s in sold_items:
        title_key = (s.get('title', '') or '').lower()[:40]
        if title_key not in sold_by_title:
            sold_by_title[title_key] = []
        sold_by_title[title_key].append(s)

    # Build listing lookup
    listing_map = {}
    for l in listings:
        listing_map[l['id']] = l
        listing_map[l['title'].lower()[:40]] = l

    # Take supply snapshot (once per day)
    save_supply_snapshot(enriched_inventory)

    # Get latest supply snapshot for trends
    latest_snap = supply_data['snapshots'][-1] if supply_data.get('snapshots') else {}
    oldest_snap = supply_data['snapshots'][0] if len(supply_data.get('snapshots', [])) > 1 else {}

    enhanced = []
    for item in inventory:
        supply = item.get('ebay_supply', {})
        market = item.get('market_data', {})
        rec = supply.get('recommendation', item.get('recommendation', 'RESEARCH'))
        reason = supply.get('reason', item.get('recommendation_reason', ''))

        # Signal
        if rec in ('SELL NOW', 'GOOD TO SELL'):
            signal = 'SELL'
        elif rec in ('HOLD', 'WAIT'):
            signal = 'WAIT'
        elif rec == 'SET PRICE':
            signal = 'PRICE'
        else:
            signal = 'RESEARCH'

        # Smart pricing engine
        pricing = compute_smart_price(item)
        suggested = pricing['smart_price'] or item.get('suggested_price', 0)

        ebay_count = supply.get('ebay_count', 0)
        ebay_avg = supply.get('ebay_avg_price', 0)
        market_median = market.get('median', 0)

        # #1: Sell-through / velocity
        title_key = item['name'].lower()[:40]
        sold_matches = sold_by_title.get(title_key, [])
        times_sold = len(sold_matches)
        avg_days_on_market = None
        if sold_matches:
            dom_values = [s['days_on_market'] for s in sold_matches if s.get('days_on_market')]
            avg_days_on_market = round(sum(dom_values) / len(dom_values)) if dom_values else None

        velocity = get_velocity_rating(artist, avg_days_on_market, times_sold, ebay_count)

        # #2: Margin estimate
        # Estimate cost basis as 40% of suggested (typical resale margin)
        cost_basis = suggested * 0.4 if suggested else 0
        ebay_fee_rate = 0.1312  # ~13.12% eBay final value fee
        ebay_fee = suggested * ebay_fee_rate if suggested else 0

        # Get promo cost for this item
        ebay_listing = item.get('_ebay_listing')
        listing = listing_map.get(title_key) or (ebay_listing if ebay_listing else {})
        lid = listing.get('id', '') or str(item.get('id', ''))
        promo_info = per_listing_promos.get(lid, {})
        ad_rate = promo_info.get('ad_rate', 0)
        ad_cost = suggested * (ad_rate / 100) if ad_rate else 0

        gross_profit = suggested - cost_basis - ebay_fee if suggested else 0
        net_profit = gross_profit - ad_cost
        margin_pct = (net_profit / suggested * 100) if suggested else 0
        ad_eats_margin = ad_cost > gross_profit * 0.5 if gross_profit > 0 else False

        # #6: Organic performer detection
        is_promoted = ad_rate > 0
        sells_organically = times_sold > 0 and not is_promoted
        organic_flag = 'organic' if sells_organically else 'promoted' if is_promoted else 'none'

        # #7: Traffic data
        traffic = traffic_data.get(lid, {})
        impressions = traffic.get('impressions', 0)
        views = traffic.get('views', 0)
        ctr = traffic.get('ctr', 0)
        conversion_rate = traffic.get('conversion_rate', 0)
        transactions = traffic.get('transactions', 0)

        # #9: Supply trend
        supply_trend = 'stable'
        supply_change = 0
        if oldest_snap and latest_snap:
            old_supply = oldest_snap.get('items', {}).get(str(item['id']), {}).get('ebay_count', ebay_count)
            supply_change = ebay_count - old_supply
            if supply_change > 2:
                supply_trend = 'increasing'
            elif supply_change < -2:
                supply_trend = 'decreasing'

        # #5: Seasonal suggestion for this item
        seasonal_boost = 0
        seasonal_event = ''
        for sug in seasonal:
            if sug.get('type') == 'event_boost':
                kws = sug.get('keywords', [])
                if any(kw.lower() in item['name'].lower() for kw in kws):
                    seasonal_boost = sug.get('suggested_rate_change', 0)
                    seasonal_event = sug.get('event', '')
                    break

        # === ACTION SCORING ENGINE ===
        # Score each action: PROMOTE, DISCOUNT, RELIST, HOLD
        promote_score = 0
        discount_score = 0
        relist_score = 0
        hold_score = 0

        # Calculate days listed from eBay start_time
        start_time = listing.get('start_time', '') or ''
        days_listed = 0
        if start_time:
            try:
                st = datetime.fromisoformat(start_time.replace('Z', '+00:00').replace('.000', ''))
                days_listed = (datetime.now(st.tzinfo) - st).days
            except Exception:
                try:
                    st = datetime.strptime(start_time[:19], '%Y-%m-%dT%H:%M:%S')
                    days_listed = (datetime.now() - st).days
                except Exception:
                    pass

        # PROMOTE: high impressions but low conversion + margin cushion
        if impressions > 50 and conversion_rate < 1.0 and margin_pct > 25:
            promote_score += 40
        if impressions > 100 and times_sold == 0:
            promote_score += 20
        if ad_rate == 0 and impressions > 30:
            promote_score += 15  # not promoted yet but getting views
        if seasonal_boost > 0:
            promote_score += 15  # seasonal event active

        # DISCOUNT: stale + good traffic but no sales + price above market
        if days_listed > 21 and times_sold == 0:
            discount_score += 25
        if days_listed > 45:
            discount_score += 20
        if market_median > 0 and suggested > market_median * 1.08:
            discount_score += 20  # priced above market
        if impressions > 30 and conversion_rate < 0.5:
            discount_score += 15  # views but no conversion
        if margin_pct > 35:
            discount_score += 10  # margin cushion exists

        # RELIST: very low visibility, stale
        if impressions < 15 and days_listed > 30:
            relist_score += 35
        if impressions < 5 and days_listed > 14:
            relist_score += 25
        if ad_rate > 0 and impressions < 20 and days_listed > 21:
            relist_score += 20  # paying for ads, getting nothing

        # HOLD: good signals, competitive price, watchers growing
        listing_watchers = listing.get('watchers', 0) or 0
        if listing_watchers > 0:
            hold_score += 25
        if market_median > 0 and abs(suggested - market_median) / market_median < 0.05:
            hold_score += 20  # priced right at market
        if velocity in ('Blazing', 'Fast'):
            hold_score += 25
        if times_sold > 0 and avg_days_on_market and avg_days_on_market < 14:
            hold_score += 15
        if margin_pct > 40:
            hold_score += 10

        # Pick the action
        scores = {'PROMOTE': promote_score, 'DISCOUNT': discount_score, 'RELIST': relist_score, 'HOLD': hold_score}
        action = max(scores, key=scores.get)
        action_score = scores[action]
        if action_score < 15:
            action = 'HOLD'  # default to hold if no strong signal

        # Action reason
        action_reasons = []
        if action == 'PROMOTE':
            if impressions > 50 and conversion_rate < 1.0: action_reasons.append(f'{impressions} impressions but {conversion_rate}% conv')
            if ad_rate == 0: action_reasons.append('Not promoted yet')
            if seasonal_boost > 0: action_reasons.append(f'Seasonal: {seasonal_event}')
        elif action == 'DISCOUNT':
            if days_listed > 21: action_reasons.append(f'{days_listed}d stale')
            if market_median > 0 and suggested > market_median * 1.08: action_reasons.append(f'${int(suggested-market_median)} over market')
            if margin_pct > 35: action_reasons.append(f'{margin_pct:.0f}% margin room')
        elif action == 'RELIST':
            if impressions < 15: action_reasons.append(f'Only {impressions} impressions')
            if days_listed > 30: action_reasons.append(f'{days_listed}d with no traction')
        elif action == 'HOLD':
            if listing_watchers > 0: action_reasons.append(f'{listing_watchers} watchers')
            if velocity in ('Blazing', 'Fast'): action_reasons.append(f'{velocity} velocity')

        # Capital efficiency
        capital_locked = cost_basis if cost_basis > 0 else suggested * 0.4
        roi_est = (net_profit / capital_locked * 100) if capital_locked > 0 else 0
        days_inventory = days_listed or 1
        annualized_roi = (roi_est / days_inventory * 365) if days_inventory > 0 else 0

        enhanced.append({
            'id': item['id'],
            'name': item['name'],
            'artist': item.get('artist', 'Unknown'),
            'category': item.get('category', item.get('artist', 'Unknown')),
            'signal': signal,
            'recommendation': rec,
            'reason': reason,
            'suggested_price': suggested,
            'price_range': item.get('price_range', ''),
            'market_median': market_median,
            'market_avg': market.get('avg', 0),
            'market_min': market.get('min', 0),
            'market_max': market.get('max', 0),
            'comp_count': item.get('comparable_sales', 0),
            'recent_sales': item.get('recent_sales', 0),
            'ebay_supply': ebay_count,
            'ebay_avg_price': ebay_avg,
            # #1: Velocity
            'velocity': velocity,
            'times_sold': times_sold,
            'avg_days_on_market': avg_days_on_market,
            # #2: Margin
            'cost_basis_est': round(cost_basis, 2),
            'ebay_fee': round(ebay_fee, 2),
            'ad_cost': round(ad_cost, 2),
            'ad_rate': ad_rate,
            'gross_profit': round(gross_profit, 2),
            'net_profit': round(net_profit, 2),
            'margin_pct': round(margin_pct, 1),
            'ad_eats_margin': ad_eats_margin,
            # #6: Organic
            'promo_status': organic_flag,
            # #7: Traffic
            'impressions': impressions,
            'views': views,
            'ctr': round(ctr, 2),
            'conversion_rate': round(conversion_rate, 2),
            'transactions': transactions,
            # #9: Supply trend
            'supply_trend': supply_trend,
            'supply_change': supply_change,
            # #5: Seasonal
            'seasonal_boost': seasonal_boost,
            'seasonal_event': seasonal_event,
            # Pricing breakdown
            'last_sale': pricing['last_sale'],
            'last_sale_date': pricing['last_sale_date'],
            'price_confidence': pricing['confidence'],
            'price_adjustment': pricing['adjustment'],
            'price_adj_reason': pricing['adjustment_reason'],
            'old_suggested': pricing['old_suggested'],
            'pricing_components': pricing['components'],
            # Action scoring
            'action': action,
            'action_score': action_score,
            'action_reason': ' · '.join(action_reasons) if action_reasons else '',
            'scores': scores,
            # Capital efficiency
            'capital_locked': round(capital_locked, 2),
            'roi_est': round(roi_est, 1),
            'annualized_roi': round(annualized_roi, 1),
            'days_listed': days_listed,
            'watchers': listing_watchers,
            'your_price': listing.get('price', 0),
        })

    # Sort by signal priority
    order = {'SELL NOW': 0, 'GOOD TO SELL': 1, 'SET PRICE': 2, 'HOLD': 3, 'WAIT': 4, 'RESEARCH': 5}
    enhanced.sort(key=lambda x: order.get(x['recommendation'], 5))

    # Summary
    total_value = sum(i['suggested_price'] or 0 for i in enhanced)
    total_ad_cost = sum(i['ad_cost'] for i in enhanced)
    total_net = sum(i['net_profit'] for i in enhanced)
    total_capital = sum(i['capital_locked'] for i in enhanced)

    # Action distribution
    action_counts = {}
    for i in enhanced:
        a = i['action']
        action_counts[a] = action_counts.get(a, 0) + 1

    # Capital efficiency
    avg_roi = round(sum(i['roi_est'] for i in enhanced) / max(len(enhanced), 1), 1)
    avg_days = round(sum(i['days_listed'] for i in enhanced) / max(len(enhanced), 1))

    return jsonify({
        'items': enhanced,
        'total': len(enhanced),
        'total_value': round(total_value, 2),
        'total_ad_cost': round(total_ad_cost, 2),
        'total_net_profit': round(total_net, 2),
        'total_capital_locked': round(total_capital, 2),
        'velocity_summary': {
            'fast': len([i for i in enhanced if i['velocity'] in ('Blazing', 'Fast')]),
            'medium': len([i for i in enhanced if i['velocity'] in ('Moderate', 'Medium')]),
            'slow': len([i for i in enhanced if i['velocity'] == 'Slow']),
            'stale': len([i for i in enhanced if i['velocity'] == 'Stale']),
        },
        'action_summary': action_counts,
        'capital_efficiency': {
            'total_locked': round(total_capital),
            'avg_roi_pct': avg_roi,
            'avg_days_listed': avg_days,
            'inventory_turnover': round(365 / max(avg_days, 1), 1),
        },
        'margin_warnings': len([i for i in enhanced if i['ad_eats_margin']]),
        'organic_performers': len([i for i in enhanced if i['promo_status'] == 'organic']),
        'seasonal_suggestions': seasonal,
    })


# =============================================================================
# eBay OAuth Authorization Flow
# =============================================================================

EBAY_AUTH_URL = 'https://auth.ebay.com/oauth2/authorize'
EBAY_OAUTH_SCOPES = ' '.join([
    'https://api.ebay.com/oauth/api_scope',
    'https://api.ebay.com/oauth/api_scope/sell.inventory',
    'https://api.ebay.com/oauth/api_scope/sell.marketing',
    'https://api.ebay.com/oauth/api_scope/sell.marketing.readonly',
    'https://api.ebay.com/oauth/api_scope/sell.account.readonly',
    'https://api.ebay.com/oauth/api_scope/sell.analytics.readonly',
    'https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly',
    'https://api.ebay.com/oauth/api_scope/sell.negotiation',
])


@app.route('/auth/ebay')
def ebay_auth_start():
    """Redirect to eBay OAuth consent page"""
    client_id = EBAY_CONFIG['client_id']
    if not client_id:
        return jsonify({'error': 'EBAY_CLIENT_ID not configured'}), 500

    # Build callback URL
    callback_url = request.host_url.rstrip('/') + '/auth/ebay/callback'

    params = {
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': callback_url,
        'scope': EBAY_OAUTH_SCOPES,
        'prompt': 'login',
    }

    auth_url = f"{EBAY_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return redirect(auth_url)


@app.route('/auth/ebay/callback')
def ebay_auth_callback():
    """Handle eBay OAuth callback — exchange code for tokens"""
    code = request.args.get('code')
    error = request.args.get('error')

    if error:
        return f"""<html><body style="background:#000;color:#fff;font-family:system-ui;padding:40px;">
            <h2>eBay Auth Error</h2><p>{error}: {request.args.get('error_description', '')}</p>
            <a href="/" style="color:#0a84ff;">Back to Dashboard</a></body></html>"""

    if not code:
        return redirect('/auth/ebay')

    # Exchange authorization code for tokens
    client_id = EBAY_CONFIG['client_id']
    client_secret = EBAY_CONFIG['client_secret']
    credentials = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(credentials.encode()).decode()

    callback_url = request.host_url.rstrip('/') + '/auth/ebay/callback'

    try:
        resp = requests.post(
            'https://api.ebay.com/identity/v1/oauth2/token',
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Authorization': f'Basic {encoded}'
            },
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': callback_url,
            }
        )

        if resp.status_code != 200:
            return f"""<html><body style="background:#000;color:#fff;font-family:system-ui;padding:40px;">
                <h2>Token Exchange Failed</h2><pre>{resp.text[:500]}</pre>
                <a href="/" style="color:#0a84ff;">Back to Dashboard</a></body></html>"""

        token_data = resp.json()
        new_refresh_token = token_data.get('refresh_token', '')
        access_token = token_data.get('access_token', '')
        expires_in = token_data.get('expires_in', 7200)

        if new_refresh_token:
            # Save refresh token to .env file
            env_path = os.path.join(os.path.dirname(__file__), '.env')
            env_lines = []
            if os.path.exists(env_path):
                with open(env_path, 'r') as f:
                    env_lines = f.readlines()

            # Update or add EBAY_REFRESH_TOKEN
            found = False
            for i, line in enumerate(env_lines):
                if line.startswith('EBAY_REFRESH_TOKEN='):
                    env_lines[i] = f'EBAY_REFRESH_TOKEN={new_refresh_token}\n'
                    found = True
                    break
            if not found:
                env_lines.append(f'EBAY_REFRESH_TOKEN={new_refresh_token}\n')

            with open(env_path, 'w') as f:
                f.writelines(env_lines)

            # Update in-memory config
            EBAY_CONFIG['refresh_token'] = new_refresh_token
            ebay._token = access_token
            ebay._token_expires = datetime.now() + timedelta(seconds=expires_in - 300)

            # Invalidate promo cache
            global _promotions_cache
            _promotions_cache = None

        return f"""<html><body style="background:#000;color:#fff;font-family:system-ui;padding:40px;text-align:center;">
            <h2 style="color:#30d158;">eBay Authorization Successful</h2>
            <p>Marketing API scopes are now active.</p>
            <p style="color:#8e8e93;font-size:14px;">Refresh token saved. You can now use Promotions features.</p>
            <a href="/" style="color:#0a84ff;font-size:18px;">Back to Dashboard</a></body></html>"""

    except Exception as e:
        return f"""<html><body style="background:#000;color:#fff;font-family:system-ui;padding:40px;">
            <h2>Error</h2><p>{str(e)}</p>
            <a href="/" style="color:#0a84ff;">Back to Dashboard</a></body></html>"""


@app.route('/api/auth/status')
def auth_status():
    """Check if eBay auth is working and what scopes are active"""
    token = ebay.get_access_token()
    has_token = token is not None
    has_refresh = bool(EBAY_CONFIG.get('refresh_token'))

    # Quick test of marketing API
    marketing_ok = False
    if token:
        headers = {
            'Authorization': f'Bearer {token}',
            'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US',
        }
        try:
            resp = requests.get(
                'https://api.ebay.com/sell/marketing/v1/ad_campaign?limit=1',
                headers=headers
            )
            marketing_ok = resp.status_code == 200
        except Exception:
            pass

    return jsonify({
        'has_refresh_token': has_refresh,
        'access_token_valid': has_token,
        'marketing_api': marketing_ok,
        'auth_url': '/auth/ebay',
    })


# =============================================================================
# Flask Routes - Promotions
# =============================================================================

@app.route('/api/promotions')
def get_promotions():
    """Get all promotion data (campaigns, item promos, coupons)"""
    force = request.args.get('refresh', '').lower() == 'true'
    try:
        data = fetch_all_promotions(force=force)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/promotions/costs')
def get_promotion_costs():
    """Get per-product cost analysis with all promo cost buckets"""
    promo_data = fetch_all_promotions()
    listings = ebay.get_all_listings()

    per_listing_promos = promo_data.get('per_listing', {})
    item_promos = promo_data.get('item_promotions', [])
    coupons = promo_data.get('coupons', [])

    # Build per-product cost picture — every listing gets a row
    cost_breakdown = []
    total_estimated_ad_cost = 0

    for listing in listings:
        lid = listing['id']
        price = listing['price']
        title = listing['title']
        category = categorize_for_market(title) or 'Uncategorized'

        promo_info = per_listing_promos.get(lid, {})
        ad_rate = promo_info.get('ad_rate', 0)
        funding_model = promo_info.get('funding_model', 'NONE')
        is_dynamic = 'DYNAMIC' in str(promo_info) or funding_model == 'COST_PER_CLICK'

        # Calculate costs per bucket
        promoted_fee = price * (ad_rate / 100) if ad_rate else 0
        dynamic_cost = promoted_fee if is_dynamic else 0
        standard_cost = promoted_fee if not is_dynamic and ad_rate > 0 else 0

        # Check if listing is in any item promotion (deal/markdown)
        deal_cost = 0  # would come from actual promo discount data
        discount_cost = 0
        coupon_cost = 0

        total_cost = promoted_fee + deal_cost + discount_cost + coupon_cost
        total_estimated_ad_cost += total_cost

        cost_breakdown.append({
            'listing_id': lid,
            'title': title[:80],
            'category': category,
            'price': price,
            'ad_rate': ad_rate,
            'funding_model': 'Dynamic' if is_dynamic else 'Standard' if ad_rate > 0 else 'None',
            'campaign_name': promo_info.get('campaign_name', ''),
            'dynamic_cost': round(dynamic_cost, 2),
            'standard_cost': round(standard_cost, 2),
            'deal_cost': round(deal_cost, 2),
            'discount_cost': round(discount_cost, 2),
            'coupon_cost': round(coupon_cost, 2),
            'total_cost': round(total_cost, 2),
            'is_promoted': ad_rate > 0,
            'ad_status': promo_info.get('ad_status', 'Not Promoted'),
        })

    cost_breakdown.sort(key=lambda x: x['total_cost'], reverse=True)

    # Summary stats
    promoted = [c for c in cost_breakdown if c['is_promoted']]
    total_value = sum(c['price'] for c in cost_breakdown)
    promoted_value = sum(c['price'] for c in promoted)
    avg_ad_rate = sum(c['ad_rate'] for c in promoted) / len(promoted) if promoted else 0

    # Category breakdown
    cat_costs = {}
    for c in cost_breakdown:
        cat = c['category']
        if cat not in cat_costs:
            cat_costs[cat] = {'items': 0, 'total_cost': 0, 'total_value': 0, 'promoted': 0}
        cat_costs[cat]['items'] += 1
        cat_costs[cat]['total_cost'] += c['total_cost']
        cat_costs[cat]['total_value'] += c['price']
        if c['is_promoted']:
            cat_costs[cat]['promoted'] += 1

    # Most expensive products
    top_expensive = sorted(cost_breakdown, key=lambda x: x['total_cost'], reverse=True)[:10]

    return jsonify({
        'cost_breakdown': cost_breakdown,
        'category_costs': cat_costs,
        'top_expensive': top_expensive,
        'summary': {
            'total_listings': len(cost_breakdown),
            'promoted_listings': len(promoted),
            'unpromoted_listings': len(cost_breakdown) - len(promoted),
            'total_inventory_value': round(total_value, 2),
            'promoted_value': round(promoted_value, 2),
            'total_estimated_ad_cost': round(total_estimated_ad_cost, 2),
            'avg_ad_rate': round(avg_ad_rate, 1),
            'total_dynamic': sum(1 for c in cost_breakdown if c['funding_model'] == 'Dynamic'),
            'total_standard': sum(1 for c in cost_breakdown if c['funding_model'] == 'Standard'),
            'active_campaigns': promo_data.get('summary', {}).get('active_campaigns', 0),
            'total_item_promos': promo_data.get('summary', {}).get('total_item_promos', 0),
            'total_coupons': promo_data.get('summary', {}).get('total_coupons', 0),
        }
    })


def suggest_promo_rate(item):
    """Rule-based AI: suggest optimal promo type and rate, calendar-aware"""
    price = item.get('price', 0)
    title = item.get('title', '')
    category = item.get('category', '')
    title_lower = title.lower()

    attrs = extract_item_attributes(title)
    is_signed = attrs.get('signed', False)
    is_numbered = attrs.get('numbered', False)
    is_high_value = price > 300

    # ── Calendar awareness ──
    rules = load_pricing_rules()
    now = datetime.now()
    mmdd_now = now.strftime('%m-%d')

    event_boost = 0
    event_name = ''
    event_tier = ''
    days_until = None

    for rule in rules:
        keywords = rule.get('keywords', [])
        if not any(kw.lower() in title_lower for kw in keywords):
            continue

        start_mmdd = rule.get('start_date', '')
        end_mmdd = rule.get('end_date', '')
        tier = rule.get('tier', 'MINOR')

        # Check if event is active now
        if start_mmdd <= mmdd_now <= end_mmdd:
            tier_boost = {'MINOR': 2, 'MEDIUM': 3, 'MAJOR': 5, 'PEAK': 8}
            event_boost = tier_boost.get(tier, 2)
            event_name = rule.get('name', '')
            event_tier = tier
            days_until = 0
            break

        # Check if event is upcoming (within 30 days)
        try:
            event_date = datetime.strptime(f"{now.year}-{start_mmdd}", '%Y-%m-%d')
            if event_date < now:
                event_date = datetime.strptime(f"{now.year + 1}-{start_mmdd}", '%Y-%m-%d')
            delta = (event_date - now).days
            if 0 < delta <= 30:
                # Pre-event ramp up: closer = bigger boost
                tier_boost = {'MINOR': 1, 'MEDIUM': 2, 'MAJOR': 3, 'PEAK': 5}
                # Scale by proximity: full boost at 7 days, half at 30 days
                proximity_factor = max(0.5, 1.0 - (delta - 7) / 30)
                event_boost = round(tier_boost.get(tier, 1) * proximity_factor, 1)
                event_name = rule.get('name', '')
                event_tier = tier
                days_until = delta
                break
        except ValueError:
            continue

    # ── Base suggestion ──
    suggested_type = 'none'
    suggested_rate = 0
    rationale = ''

    if price < 50:
        suggested_type = 'none'
        suggested_rate = 0
        rationale = f'At ${price:.0f}, ad costs eat margin. Sell organically.'

    elif price < 100:
        suggested_type = 'standard_cps'
        suggested_rate = 2.0
        rationale = f'Low-value. 2% CPS for basic visibility. Fee: ${price * 0.02:.2f}'

    elif is_signed and is_numbered and price > 200:
        suggested_type = 'dynamic_cps'
        suggested_rate = 3.0
        rationale = f'Signed + numbered — high demand. Dynamic CPS optimizes rate. Fee cap: ${price * 0.03:.2f}'

    elif is_signed and price > 150:
        suggested_type = 'standard_cps'
        suggested_rate = 4.0
        rationale = f'Signed at ${price:.0f}. 4% CPS balances visibility vs margin. Fee: ${price * 0.04:.2f}'

    elif is_high_value:
        suggested_type = 'dynamic_cps'
        suggested_rate = 2.5
        rationale = f'High-value (${price:.0f}). Dynamic 2.5% max. Fee cap: ${price * 0.025:.2f}'

    else:
        suggested_type = 'standard_cps'
        suggested_rate = 3.0
        rationale = f'Standard. 3% CPS sweet spot. Fee: ${price * 0.03:.2f}'

    # ── Apply calendar boost ──
    if event_boost > 0:
        suggested_rate = round(suggested_rate + event_boost, 1)
        if suggested_type == 'none':
            suggested_type = 'standard_cps'

        if days_until == 0:
            rationale += f' | EVENT ACTIVE: {event_name} ({event_tier}) — boosted +{event_boost}% to {suggested_rate}%'
        else:
            rationale += f' | UPCOMING: {event_name} in {days_until}d ({event_tier}) — pre-ramp +{event_boost}% to {suggested_rate}%'

    # ── Markdown suggestion ──
    suggested_markdown = 0
    markdown_rationale = 'No markdown needed'

    if event_boost > 0 and days_until is not None and days_until <= 7:
        # Event is imminent or active — markdown to drive volume
        suggested_markdown = 10 if event_tier in ('MAJOR', 'PEAK') else 5
        markdown_rationale = f'{event_name} {"is active" if days_until == 0 else f"in {days_until} days"} — {suggested_markdown}% markdown to drive event traffic'
    elif price > 200 and not is_signed:
        suggested_markdown = 10
        markdown_rationale = f'Unsigned at ${price:.0f} — 10% off to ${price * 0.9:.0f} accelerates sale'
    elif price > 500:
        suggested_markdown = 5
        markdown_rationale = f'Premium — 5% off to ${price * 0.95:.0f} signals urgency'

    return {
        'suggested_type': suggested_type,
        'suggested_rate': suggested_rate,
        'rationale': rationale,
        'suggested_markdown': suggested_markdown,
        'markdown_rationale': markdown_rationale,
        'estimated_fee': round(price * (suggested_rate / 100), 2),
        'event_name': event_name,
        'event_tier': event_tier,
        'days_until_event': days_until,
    }


@app.route('/api/promotions/suggestions')
def get_promo_suggestions():
    """Get AI-driven promo suggestions for all listings"""
    listings = ebay.get_all_listings()
    suggestions = []
    for listing in listings:
        sug = suggest_promo_rate(listing)
        suggestions.append({
            'listing_id': listing['id'],
            'title': listing['title'][:80],
            'price': listing['price'],
            'category': categorize_for_market(listing['title']) or 'Other',
            **sug,
        })
    return jsonify({'suggestions': suggestions, 'total': len(suggestions)})


@app.route('/api/promotions/ai-suggest', methods=['POST'])
def ai_suggest_promo():
    """Multi-LLM consensus for a specific item's optimal promo strategy"""
    data = request.get_json()
    title = data.get('title', '')
    price = data.get('price', 0)
    category = data.get('category', '')

    prompt = f"""You are an eBay selling optimization expert. Given this listing, suggest the optimal promotion strategy.

Item: {title}
Current Price: ${price:.2f}
Category: {category}

eBay promotion options:
1. Promoted Listings Standard (CPS) — you pay a % of sale price only when item sells via ad click. Typical rates: 2-8%.
2. Promoted Listings Advanced (CPC) — pay per click, regardless of sale. Good for high-value items.
3. Markdown Sale — reduce price by X% for a limited time. Shows crossed-out price.
4. No promotion — let it sell organically.

Respond in this exact JSON format:
{{"ad_type": "standard_cps|dynamic_cps|cpc|none", "ad_rate": <number>, "markdown_pct": <number>, "rationale": "<2 sentences max>"}}"""

    results = []

    # Claude
    claude_key = ENV.get('CLAUDE_API_KEY', '')
    if claude_key:
        try:
            resp = requests.post('https://api.anthropic.com/v1/messages',
                headers={'x-api-key': claude_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                json={'model': 'claude-sonnet-4-5-20241022', 'max_tokens': 200, 'messages': [{'role': 'user', 'content': prompt}]},
                timeout=15)
            if resp.status_code == 200:
                text = resp.json().get('content', [{}])[0].get('text', '')
                try:
                    import re as _re
                    match = _re.search(r'\{[^}]+\}', text)
                    if match:
                        results.append({'model': 'Claude', **json.loads(match.group())})
                except Exception:
                    pass
        except Exception as e:
            print(f"Claude error: {e}")

    # OpenAI
    openai_key = ENV.get('OPENAI_API_KEY', '')
    if openai_key:
        try:
            resp = requests.post('https://api.openai.com/v1/chat/completions',
                headers={'Authorization': f'Bearer {openai_key}', 'Content-Type': 'application/json'},
                json={'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 200},
                timeout=15)
            if resp.status_code == 200:
                text = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
                try:
                    match = re.search(r'\{[^}]+\}', text)
                    if match:
                        results.append({'model': 'GPT-4o', **json.loads(match.group())})
                except Exception:
                    pass
        except Exception as e:
            print(f"OpenAI error: {e}")

    # Gemini
    gemini_key = ENV.get('GEMINI_API_KEY', '')
    if gemini_key:
        try:
            resp = requests.post(f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}',
                headers={'Content-Type': 'application/json'},
                json={'contents': [{'parts': [{'text': prompt}]}]},
                timeout=15)
            if resp.status_code == 200:
                text = resp.json().get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                try:
                    match = re.search(r'\{[^}]+\}', text)
                    if match:
                        results.append({'model': 'Gemini', **json.loads(match.group())})
                except Exception:
                    pass
        except Exception as e:
            print(f"Gemini error: {e}")

    # Build consensus
    if results:
        avg_rate = sum(r.get('ad_rate', 0) for r in results) / len(results)
        avg_markdown = sum(r.get('markdown_pct', 0) for r in results) / len(results)
        types = [r.get('ad_type', 'none') for r in results]
        # Most common type
        from collections import Counter
        consensus_type = Counter(types).most_common(1)[0][0]
        rationales = [f"{r['model']}: {r.get('rationale', '')}" for r in results]

        return jsonify({
            'consensus': {
                'ad_type': consensus_type,
                'ad_rate': round(avg_rate, 1),
                'markdown_pct': round(avg_markdown),
                'rationale': ' | '.join(rationales),
            },
            'models': results,
            'model_count': len(results),
        })

    return jsonify({'error': 'No AI responses received', 'models': []}), 500


@app.route('/api/promotions/apply', methods=['POST'])
def apply_promo_changes():
    """Apply promotion changes to eBay — one campaign per rate group"""
    data = request.get_json()
    changes = data.get('changes', [])

    headers = get_marketing_headers()
    if not headers:
        return jsonify({'error': 'eBay auth failed'}), 401

    applied = 0
    failed = 0
    errors = []
    campaigns_created = 0

    # Group changes by ad_type + ad_rate to minimize campaigns
    groups = {}
    for change in changes:
        ad_type = change.get('ad_type', 'none')
        ad_rate = change.get('ad_rate', 0)
        # eBay minimum ad rate is typically 2% for most categories
        if ad_rate > 0 and ad_rate < 2:
            ad_rate = 2.0
        if ad_type in ('standard_cps', 'dynamic_cps') and ad_rate > 0:
            key = f"{ad_type}_{ad_rate}"
            if key not in groups:
                groups[key] = {'type': ad_type, 'rate': ad_rate, 'listings': []}
            groups[key]['listings'].append(change.get('listing_id'))
        else:
            applied += 1  # No ad needed

    # Create one campaign per rate group
    for key, group in groups.items():
        try:
            campaign_body = {
                'campaignName': f'DATARADAR {group["type"].replace("_"," ").title()} {group["rate"]}% {datetime.now().strftime("%m/%d")}',
                'marketplaceId': 'EBAY_US',
                'startDate': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                'fundingStrategy': {
                    'fundingModel': 'COST_PER_SALE',
                    'bidPercentage': str(group['rate']),
                }
            }
            if group['type'] == 'dynamic_cps':
                campaign_body['fundingStrategy']['adRateStrategy'] = 'DYNAMIC'

            resp = requests.post(
                'https://api.ebay.com/sell/marketing/v1/ad_campaign',
                headers=headers, json=campaign_body)

            if resp.status_code in (200, 201):
                campaign_url = resp.headers.get('Location', '')
                campaign_id = campaign_url.split('/')[-1] if campaign_url else ''
                campaigns_created += 1

                if campaign_id:
                    for lid in group['listings']:
                        try:
                            ad_resp = requests.post(
                                f'https://api.ebay.com/sell/marketing/v1/ad_campaign/{campaign_id}/ad',
                                headers=headers,
                                json={'listingId': lid, 'bidPercentage': str(group['rate'])})
                            if ad_resp.status_code in (200, 201):
                                applied += 1
                            elif '35036' in ad_resp.text:
                                # Listing already in a campaign — skip (already promoted)
                                applied += 1  # Count as success — it's already promoted
                            else:
                                failed += 1
                                errors.append(f'{lid}: {ad_resp.text[:80]}')
                        except Exception as e:
                            failed += 1
                            errors.append(f'{lid}: {str(e)}')
            else:
                failed += len(group['listings'])
                errors.append(f'Campaign creation failed for {group["rate"]}%: {resp.text[:100]}')
        except Exception as e:
            failed += len(group['listings'])
            errors.append(f'Campaign error: {str(e)}')

    # Invalidate cache
    global _promotions_cache
    _promotions_cache = None

    return jsonify({
        'success': True,
        'applied': applied,
        'failed': failed,
        'campaigns_created': campaigns_created,
        'errors': errors[:10],
    })


@app.route('/api/promotions/detail')
def get_promotion_detail():
    """Granular per-product promo breakdown with every cost type and analytics"""
    promo_data = fetch_all_promotions()
    listings = ebay.get_all_listings()
    per_listing_promos = promo_data.get('per_listing', {})
    item_promos = promo_data.get('item_promotions', [])
    campaigns = promo_data.get('campaigns', [])

    # Build campaign lookup
    campaign_map = {}
    for c in campaigns:
        cid = c.get('campaignId', '')
        fund = c.get('fundingStrategy', {})
        campaign_map[cid] = {
            'name': c.get('campaignName', ''),
            'status': c.get('campaignStatus', ''),
            'model': fund.get('fundingModel', ''),
            'strategy': fund.get('adRateStrategy', 'FIXED'),
            'start': c.get('startDate', ''),
        }

    # Build promo type for each listing
    products = []
    total_cps = 0
    total_cpc = 0
    total_markdown = 0

    for listing in listings:
        lid = listing['id']
        price = listing['price']
        title = listing['title']
        category = categorize_for_market(title) or 'Other'

        promo = per_listing_promos.get(lid, {})
        campaign_id = promo.get('campaign_id', '')
        campaign = campaign_map.get(campaign_id, {})

        ad_rate = promo.get('ad_rate', 0)
        model = campaign.get('model', promo.get('funding_model', ''))
        strategy = campaign.get('strategy', '')
        is_dynamic = strategy == 'DYNAMIC' or 'dynamic' in (campaign.get('name', '') or '').lower()

        # Cost breakdown by type
        cps_cost = price * (ad_rate / 100) if model == 'COST_PER_SALE' else 0
        cpc_cost = 0.30 if model == 'COST_PER_CLICK' else 0  # Estimate avg CPC
        markdown_pct = 0

        # Check if this listing is in a markdown sale
        in_markdown = False
        in_coupon = False
        markdown_name = ''
        coupon_name = ''
        for ip in item_promos:
            ptype = ip.get('promotionType', '')
            pstatus = ip.get('promotionStatus', '')
            if pstatus != 'RUNNING':
                continue
            if ptype == 'MARKDOWN_SALE':
                in_markdown = True
                markdown_name = ip.get('name', '')
                markdown_pct = 15  # Typical markdown
            elif ptype in ('CODED_COUPON', 'ORDER_DISCOUNT'):
                in_coupon = True
                coupon_name = ip.get('name', '')

        markdown_cost = price * (markdown_pct / 100) if in_markdown else 0
        coupon_cost = price * 0.15 if in_coupon else 0  # Estimate avg coupon value

        total_cost = cps_cost + cpc_cost + markdown_cost + coupon_cost
        total_cps += cps_cost
        total_cpc += cpc_cost
        total_markdown += markdown_cost

        # Determine if this product's promos are "working"
        # Working = has promo + price competitive with market
        status = 'none'
        if cps_cost > 0 or cpc_cost > 0:
            if ad_rate > 8:
                status = 'overspending'
            elif ad_rate > 0:
                status = 'active'
        if in_markdown:
            status = 'markdown'
        if in_coupon:
            status = 'coupon'

        products.append({
            'listing_id': lid,
            'title': title[:80],
            'category': category,
            'price': price,
            'campaign': campaign.get('name', ''),
            'campaign_status': campaign.get('status', ''),
            'campaign_start': campaign.get('start', '')[:10] if campaign.get('start') else '',
            'ad_type': 'Dynamic CPS' if is_dynamic and model == 'COST_PER_SALE' else 'Fixed CPS' if model == 'COST_PER_SALE' else 'CPC' if model == 'COST_PER_CLICK' else 'None',
            'ad_rate': ad_rate,
            'cps_cost': round(cps_cost, 2),
            'cpc_cost': round(cpc_cost, 2),
            'markdown_active': in_markdown,
            'markdown_name': markdown_name,
            'markdown_cost': round(markdown_cost, 2),
            'coupon_active': in_coupon,
            'coupon_name': coupon_name,
            'coupon_cost': round(coupon_cost, 2),
            'total_cost': round(total_cost, 2),
            'promo_status': status,
        })

    products.sort(key=lambda x: x['total_cost'], reverse=True)

    # Analytics: what's working
    active_products = [p for p in products if p['promo_status'] != 'none']
    high_spend = [p for p in products if p['total_cost'] > 20]
    overspending = [p for p in products if p['promo_status'] == 'overspending']

    return jsonify({
        'products': products,
        'total_products': len(products),
        'total_promoted': len(active_products),
        'analytics': {
            'total_cps_spend': round(total_cps, 2),
            'total_cpc_spend': round(total_cpc, 2),
            'total_markdown_cost': round(total_markdown, 2),
            'total_all_spend': round(total_cps + total_cpc + total_markdown, 2),
            'avg_ad_rate': round(sum(p['ad_rate'] for p in active_products) / len(active_products), 1) if active_products else 0,
            'high_spend_count': len(high_spend),
            'overspending_count': len(overspending),
            'by_type': {
                'dynamic_cps': len([p for p in products if p['ad_type'] == 'Dynamic CPS']),
                'fixed_cps': len([p for p in products if p['ad_type'] == 'Fixed CPS']),
                'cpc': len([p for p in products if p['ad_type'] == 'CPC']),
                'markdown': len([p for p in products if p['markdown_active']]),
                'coupon': len([p for p in products if p['coupon_active']]),
                'none': len([p for p in products if p['promo_status'] == 'none']),
            }
        },
        'campaigns': [{'name': c.get('campaignName', ''), 'status': c.get('campaignStatus', ''), 'ads': c.get('ad_count', 0)} for c in campaigns if c.get('campaignStatus') == 'RUNNING'],
        'active_promos': [{'name': p.get('name', ''), 'type': p.get('promotionType', ''), 'status': p.get('promotionStatus', '')} for p in item_promos if p.get('promotionStatus') == 'RUNNING'],
    })


@app.route('/api/promotions/recommendations')
def get_promo_recommendations():
    """Get AI-driven promotion optimization recommendations"""
    promo_data = fetch_all_promotions()
    listings = ebay.get_all_listings()
    recs = generate_promo_recommendations(promo_data, listings)
    return jsonify(recs)


@app.route('/api/promotions/create-campaign', methods=['POST'])
def create_campaign():
    """Create a new Promoted Listings Standard campaign"""
    data = request.get_json()
    campaign_name = data.get('name', f'DATARADAR Campaign {datetime.now().strftime("%Y-%m-%d")}')
    ad_rate = data.get('ad_rate', 2.0)
    listing_ids = data.get('listing_ids', [])

    headers = get_marketing_headers()
    if not headers:
        return jsonify({'error': 'eBay authentication failed'}), 401

    # Create campaign
    campaign_body = {
        'campaignName': campaign_name,
        'marketplaceId': 'EBAY_US',
        'startDate': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        'fundingStrategy': {
            'fundingModel': 'COST_PER_SALE',
            'bidPercentage': str(ad_rate)
        }
    }

    try:
        resp = requests.post(
            'https://api.ebay.com/sell/marketing/v1/ad_campaign',
            headers=headers,
            json=campaign_body
        )

        if resp.status_code not in (200, 201):
            return jsonify({'error': f'Campaign creation failed: {resp.text[:300]}'}), resp.status_code

        # Extract campaign ID from Location header
        campaign_url = resp.headers.get('Location', '')
        campaign_id = campaign_url.split('/')[-1] if campaign_url else ''

        # Add listings to campaign
        added = 0
        if campaign_id and listing_ids:
            for lid in listing_ids:
                ad_body = {
                    'listingId': lid,
                    'bidPercentage': str(ad_rate)
                }
                ad_resp = requests.post(
                    f'https://api.ebay.com/sell/marketing/v1/ad_campaign/{campaign_id}/ad',
                    headers=headers,
                    json=ad_body
                )
                if ad_resp.status_code in (200, 201):
                    added += 1

        # Invalidate cache
        global _promotions_cache
        _promotions_cache = None

        return jsonify({
            'success': True,
            'campaign_id': campaign_id,
            'campaign_name': campaign_name,
            'ads_added': added
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/promotions/update-ad-rate', methods=['POST'])
def update_ad_rate():
    """Update ad rate for a specific listing in a campaign"""
    data = request.get_json()
    campaign_id = data.get('campaign_id')
    ad_id = data.get('ad_id')
    new_rate = data.get('ad_rate')

    if not all([campaign_id, ad_id, new_rate]):
        return jsonify({'error': 'Missing campaign_id, ad_id, or ad_rate'}), 400

    headers = get_marketing_headers()
    if not headers:
        return jsonify({'error': 'eBay authentication failed'}), 401

    try:
        resp = requests.post(
            f'https://api.ebay.com/sell/marketing/v1/ad_campaign/{campaign_id}/ad/{ad_id}/update_bid',
            headers=headers,
            json={'bidPercentage': str(new_rate)}
        )

        if resp.status_code in (200, 204):
            global _promotions_cache
            _promotions_cache = None
            return jsonify({'success': True})

        return jsonify({'error': f'Update failed: {resp.text[:200]}'}), resp.status_code

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Feature: Real Sold Data Pipeline (#1)
# =============================================================================

@app.route('/api/sold/history')
def get_sold_history():
    """Get real sold items with revenue, velocity, DOM"""
    sold = ebay.get_sold_items(days_back=90)

    total_revenue = sum(s.get('price', 0) * s.get('quantity_sold', 1) for s in sold)
    avg_dom = 0
    dom_values = [s['days_on_market'] for s in sold if s.get('days_on_market')]
    if dom_values:
        avg_dom = round(sum(dom_values) / len(dom_values))

    # Group by title for velocity
    by_title = {}
    for s in sold:
        key = s.get('title', '')[:50]
        if key not in by_title:
            by_title[key] = {'count': 0, 'revenue': 0, 'prices': [], 'dom': []}
        by_title[key]['count'] += s.get('quantity_sold', 1)
        by_title[key]['revenue'] += s.get('price', 0) * s.get('quantity_sold', 1)
        by_title[key]['prices'].append(s.get('price', 0))
        if s.get('days_on_market'):
            by_title[key]['dom'].append(s['days_on_market'])

    top_sellers = sorted(by_title.items(), key=lambda x: x[1]['count'], reverse=True)[:20]

    return jsonify({
        'sold_items': sold,
        'total_sold': len(sold),
        'total_revenue': round(total_revenue, 2),
        'avg_days_on_market': avg_dom,
        'top_sellers': [{'title': k, **v, 'avg_price': round(sum(v['prices'])/len(v['prices']),2), 'avg_dom': round(sum(v['dom'])/len(v['dom'])) if v['dom'] else None} for k, v in top_sellers],
    })


# =============================================================================
# Feature: Live Comps from eBay Sold Items (#2)
# =============================================================================

@app.route('/api/comps/live')
def get_live_comps():
    """Get live sold comps for a specific item from eBay"""
    title = request.args.get('title', '')
    if not title:
        return jsonify({'error': 'Missing title'}), 400

    # Extract key search terms
    stop_words = {'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of', 'is', 'by', 'with', 'new', 'lot', 'rare', 'free', 'shipping'}
    words = [w for w in re.findall(r'\w+', title.lower()) if w not in stop_words and len(w) > 2]
    query = ' '.join(words[:6])

    # Search eBay for active comps (sold items require different API)
    comps = search_ebay(query, 10000, 0, limit=20)
    item_attrs = extract_item_attributes(title)

    scored = []
    for c in comps:
        comp_attrs = extract_item_attributes(c.get('title', ''))
        attr_score = attribute_match_score(item_attrs, comp_attrs)
        fake, _ = is_likely_fake(c.get('title', ''), c.get('price', 0), '')
        if not fake and attr_score >= 0:
            scored.append({**c, 'attr_score': attr_score})

    scored.sort(key=lambda x: x['attr_score'], reverse=True)

    prices = [c['price'] for c in scored if c['price'] > 0]
    stats = {}
    if prices:
        stats = {
            'count': len(prices),
            'min': min(prices),
            'max': max(prices),
            'avg': round(sum(prices) / len(prices), 2),
            'median': sorted(prices)[len(prices) // 2],
        }

    return jsonify({'comps': scored[:15], 'stats': stats, 'query': query})


# =============================================================================
# Feature: Auto-Repricing Engine (#3)
# =============================================================================

AUTOPRICING_FILE = os.path.join(DATA_DIR, 'autopricing_rules.json')


def load_autopricing_rules():
    if os.path.exists(AUTOPRICING_FILE):
        try:
            with open(AUTOPRICING_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {'enabled': False, 'rules': [], 'log': []}


@app.route('/api/autopricing', methods=['GET', 'POST'])
def manage_autopricing():
    """Get or update auto-repricing rules"""
    if request.method == 'POST':
        data = request.get_json()
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(AUTOPRICING_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        return jsonify({'success': True})

    return jsonify(load_autopricing_rules())


@app.route('/api/autopricing/run', methods=['POST'])
def run_autopricing():
    """Execute auto-repricing based on calendar + rules"""
    rules_data = load_autopricing_rules()
    if not rules_data.get('enabled'):
        return jsonify({'message': 'Auto-repricing is disabled', 'applied': 0})

    listings = ebay.get_all_listings()
    pricing_rules = load_pricing_rules()
    now = datetime.now()
    mmdd = now.strftime('%m-%d')

    applied = 0
    log = []

    for listing in listings:
        title_lower = listing['title'].lower()
        price = listing['price']

        for rule in pricing_rules:
            keywords = rule.get('keywords', [])
            if not any(kw.lower() in title_lower for kw in keywords):
                continue

            start = rule.get('start_date', '')
            end = rule.get('end_date', '')
            tier = rule.get('tier', 'MINOR')
            boost_pct = rule.get('increase_percent', 0)

            if start <= mmdd <= end:
                # Event active — boost price
                new_price = round(price * (1 + boost_pct / 100), 2)
                if ebay.update_price(listing['id'], new_price):
                    applied += 1
                    log.append(f"Boosted {listing['title'][:40]} ${price} -> ${new_price} ({rule['name']} +{boost_pct}%)")

    rules_data['log'] = log[-50:]
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(AUTOPRICING_FILE, 'w') as f:
        json.dump(rules_data, f, indent=2)

    return jsonify({'applied': applied, 'log': log})


# =============================================================================
# Feature: Competitor Alerts (#4)
# =============================================================================

@app.route('/api/competitors/check')
def check_competitors():
    """Check for competitor undercuts on your listings"""
    listings = ebay.get_all_listings()
    alerts = []

    # Sample check — for top 20 highest-value items, search for competitors
    top_items = sorted(listings, key=lambda x: x['price'], reverse=True)[:20]

    for listing in top_items:
        stop_words = {'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of', 'is', 'by', 'with', 'new', 'lot', 'rare', 'free', 'shipping'}
        words = [w for w in re.findall(r'\w+', listing['title'].lower()) if w not in stop_words and len(w) > 2]
        query = ' '.join(words[:5])
        if not query:
            continue

        try:
            comps = search_ebay(query, listing['price'] * 1.5, listing['price'] * 0.3, limit=5)
            undercuts = [c for c in comps if c['price'] < listing['price'] * 0.9 and c.get('id') != listing['id']]

            if undercuts:
                cheapest = min(undercuts, key=lambda x: x['price'])
                diff = listing['price'] - cheapest['price']
                alerts.append({
                    'your_listing': listing['title'][:60],
                    'your_price': listing['price'],
                    'your_id': listing['id'],
                    'competitor_title': cheapest['title'][:60],
                    'competitor_price': cheapest['price'],
                    'competitor_url': cheapest.get('url', ''),
                    'undercut_amount': round(diff, 2),
                    'undercut_pct': round((diff / listing['price']) * 100, 1),
                })
        except Exception:
            continue

    alerts.sort(key=lambda x: x['undercut_pct'], reverse=True)
    return jsonify({'alerts': alerts, 'checked': len(top_items)})


# =============================================================================
# Feature: P&L Profit Dashboard (#5)
# =============================================================================

COST_BASIS_FILE = os.path.join(DATA_DIR, 'cost_basis.json')


def load_cost_basis():
    if os.path.exists(COST_BASIS_FILE):
        try:
            with open(COST_BASIS_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


@app.route('/api/pnl')
def get_pnl():
    """Real P&L per item — revenue - costs - fees - ads - shipping"""
    listings = ebay.get_all_listings()
    sold = ebay.get_sold_items(days_back=90)
    cost_basis = load_cost_basis()
    promo_data = fetch_all_promotions()
    per_listing_promos = promo_data.get('per_listing', {})

    pnl_items = []
    total_revenue = 0
    total_cost = 0
    total_fees = 0
    total_ad = 0
    total_profit = 0

    EBAY_FEE = 0.1312
    SHIPPING_EST = 8.0

    for s in sold:
        lid = s.get('id', '')
        price = s.get('price', 0)
        qty = s.get('quantity_sold', 1)
        revenue = price * qty
        cost = cost_basis.get(lid, {}).get('cost', price * 0.4)  # Default 40% if unknown
        fees = revenue * EBAY_FEE
        promo = per_listing_promos.get(lid, {})
        ad_rate = promo.get('ad_rate', 0)
        ad_cost = revenue * (ad_rate / 100)
        shipping = SHIPPING_EST * qty
        profit = revenue - cost - fees - ad_cost - shipping

        total_revenue += revenue
        total_cost += cost
        total_fees += fees
        total_ad += ad_cost
        total_profit += profit

        pnl_items.append({
            'listing_id': lid,
            'title': s.get('title', '')[:60],
            'sold_price': price,
            'quantity': qty,
            'revenue': round(revenue, 2),
            'cost_basis': round(cost, 2),
            'ebay_fees': round(fees, 2),
            'ad_cost': round(ad_cost, 2),
            'shipping': round(shipping, 2),
            'net_profit': round(profit, 2),
            'margin_pct': round((profit / revenue) * 100, 1) if revenue else 0,
            'days_on_market': s.get('days_on_market'),
            'date_sold': s.get('end_time', '')[:10] if s.get('end_time') else '',
        })

    pnl_items.sort(key=lambda x: x['net_profit'], reverse=True)

    return jsonify({
        'items': pnl_items,
        'summary': {
            'total_sold': len(pnl_items),
            'total_revenue': round(total_revenue, 2),
            'total_cost': round(total_cost, 2),
            'total_fees': round(total_fees, 2),
            'total_ad_spend': round(total_ad, 2),
            'total_net_profit': round(total_profit, 2),
            'avg_margin': round((total_profit / total_revenue) * 100, 1) if total_revenue else 0,
        }
    })


@app.route('/api/pnl/cost-basis', methods=['POST'])
def update_cost_basis():
    """Set cost basis for a listing"""
    data = request.get_json()
    lid = data.get('listing_id', '')
    cost = data.get('cost', 0)

    cb = load_cost_basis()
    cb[lid] = {'cost': cost, 'updated': datetime.now().isoformat()}

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(COST_BASIS_FILE, 'w') as f:
        json.dump(cb, f, indent=2)

    return jsonify({'success': True})


# =============================================================================
# Feature: Listing Quality Scorer (#7)
# =============================================================================

def score_listing_quality(listing):
    """Score a listing's SEO quality 0-100"""
    title = listing.get('title', '')
    price = listing.get('price', 0)
    image = listing.get('image', '')

    score = 0
    tips = []

    # Title length (eBay max 80 chars, sweet spot 60-80)
    tlen = len(title)
    if tlen >= 60:
        score += 20
    elif tlen >= 40:
        score += 10
        tips.append(f'Title is {tlen} chars — aim for 60-80 for better SEO')
    else:
        tips.append(f'Title too short ({tlen} chars) — add descriptive keywords')

    # Key attributes in title
    attrs = extract_item_attributes(title)
    if attrs['signed']:
        score += 15
    else:
        tips.append('Add "Signed" to title if applicable')
    if attrs['numbered']:
        score += 10
    if attrs['coa']:
        score += 10
    else:
        tips.append('Add authentication (JSA/PSA/COA) to title if applicable')

    # Artist/brand name
    t_lower = title.lower()
    has_artist = any(a in t_lower for a in ['shepard fairey', 'obey', 'banksy', 'kaws', 'brainwash', 'death nyc', 'bearbrick'])
    if has_artist:
        score += 10
    else:
        tips.append('Include artist/brand name prominently in title')

    # Edition info
    if any(w in t_lower for w in ['/50', '/100', '/150', '/200', '/300', '/500', 'edition', 'limited']):
        score += 10
    else:
        tips.append('Add edition size if applicable (e.g., "Edition of 200")')

    # Condition/rarity keywords
    if any(w in t_lower for w in ['rare', 'mint', 'new', 'sealed', 'pristine', 'excellent']):
        score += 5

    # Has image
    if image:
        score += 10
    else:
        tips.append('Add gallery image — listings with photos get 3x more views')

    # Price sanity
    if price > 0:
        score += 10

    score = min(100, score)
    grade = 'A' if score >= 80 else 'B' if score >= 60 else 'C' if score >= 40 else 'D'

    # Generate improved title suggestion
    suggested_title = title
    if not attrs['signed'] and 'signed' not in t_lower:
        pass  # Don't add signed if we don't know
    if tlen < 50 and has_artist:
        suggested_title = title + ' | Limited Edition Art Print'

    return {
        'score': score,
        'grade': grade,
        'tips': tips,
        'suggested_title': suggested_title if suggested_title != title else None,
    }


@app.route('/api/listings/quality')
def get_listing_quality():
    """Score all listings on SEO quality"""
    listings = ebay.get_all_listings()
    results = []
    for l in listings:
        quality = score_listing_quality(l)
        results.append({
            'listing_id': l['id'],
            'title': l['title'][:80],
            'price': l['price'],
            **quality,
        })
    results.sort(key=lambda x: x['score'])

    avg_score = round(sum(r['score'] for r in results) / len(results)) if results else 0
    grades = {'A': 0, 'B': 0, 'C': 0, 'D': 0}
    for r in results:
        grades[r['grade']] = grades.get(r['grade'], 0) + 1

    return jsonify({
        'listings': results,
        'avg_score': avg_score,
        'grade_distribution': grades,
        'total': len(results),
    })


# =============================================================================
# Feature: Batch Operations (#8)
# =============================================================================

@app.route('/api/batch/ad-rate', methods=['POST'])
def batch_set_ad_rate():
    """Set ad rate for multiple listings at once"""
    data = request.get_json()
    listing_ids = data.get('listing_ids', [])
    ad_rate = data.get('ad_rate', 0)
    ad_type = data.get('ad_type', 'standard_cps')

    if not listing_ids:
        return jsonify({'error': 'No listings selected'}), 400

    headers = get_marketing_headers()
    if not headers:
        return jsonify({'error': 'eBay auth failed'}), 401

    # Create one campaign for the batch
    campaign_body = {
        'campaignName': f'DATARADAR Batch {datetime.now().strftime("%m/%d %H:%M")}',
        'marketplaceId': 'EBAY_US',
        'startDate': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        'fundingStrategy': {
            'fundingModel': 'COST_PER_SALE',
            'bidPercentage': str(ad_rate),
        }
    }
    if ad_type == 'dynamic_cps':
        campaign_body['fundingStrategy']['adRateStrategy'] = 'DYNAMIC'

    try:
        resp = requests.post('https://api.ebay.com/sell/marketing/v1/ad_campaign', headers=headers, json=campaign_body)
        if resp.status_code not in (200, 201):
            return jsonify({'error': f'Campaign creation failed: {resp.text[:200]}'}), 500

        campaign_url = resp.headers.get('Location', '')
        campaign_id = campaign_url.split('/')[-1] if campaign_url else ''

        added = 0
        failed = 0
        for lid in listing_ids:
            try:
                r = requests.post(
                    f'https://api.ebay.com/sell/marketing/v1/ad_campaign/{campaign_id}/ad',
                    headers=headers, json={'listingId': lid, 'bidPercentage': str(ad_rate)})
                if r.status_code in (200, 201):
                    added += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

        global _promotions_cache
        _promotions_cache = None

        return jsonify({'success': True, 'campaign_id': campaign_id, 'added': added, 'failed': failed})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/batch/price', methods=['POST'])
def batch_update_price():
    """Update prices for multiple listings"""
    data = request.get_json()
    updates = data.get('updates', [])  # [{listing_id, new_price}]

    applied = 0
    failed = 0
    for u in updates:
        if ebay.update_price(u['listing_id'], float(u['new_price'])):
            applied += 1
        else:
            failed += 1

    return jsonify({'success': True, 'applied': applied, 'failed': failed})


# =============================================================================
# Feature: Notification System (#9)
# =============================================================================

NOTIFICATIONS_FILE = os.path.join(DATA_DIR, 'notifications.json')


def load_notifications():
    if os.path.exists(NOTIFICATIONS_FILE):
        try:
            with open(NOTIFICATIONS_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def add_notification(ntype, title, message, severity='info', data=None):
    """Add a notification"""
    notifs = load_notifications()
    notifs.insert(0, {
        'id': f"n-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        'type': ntype,
        'title': title,
        'message': message,
        'severity': severity,
        'data': data or {},
        'read': False,
        'created': datetime.now().isoformat(),
    })
    # Keep last 100
    notifs = notifs[:100]
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(NOTIFICATIONS_FILE, 'w') as f:
        json.dump(notifs, f, indent=2)
    return notifs


@app.route('/api/notifications')
def get_notifications():
    """Get all notifications"""
    notifs = load_notifications()
    unread = len([n for n in notifs if not n.get('read')])
    return jsonify({'notifications': notifs[:50], 'unread': unread})


@app.route('/api/notifications/read', methods=['POST'])
def mark_notifications_read():
    """Mark all notifications as read"""
    notifs = load_notifications()
    for n in notifs:
        n['read'] = True
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(NOTIFICATIONS_FILE, 'w') as f:
        json.dump(notifs, f, indent=2)
    return jsonify({'success': True})


@app.route('/api/notifications/generate', methods=['POST'])
def generate_notifications():
    """Scan for conditions and generate notifications"""
    listings = ebay.get_all_listings()
    rules = load_pricing_rules()
    now = datetime.now()
    mmdd = now.strftime('%m-%d')

    generated = 0

    # Upcoming event alerts
    for rule in rules:
        start = rule.get('start_date', '')
        try:
            event_date = datetime.strptime(f"{now.year}-{start}", '%Y-%m-%d')
            if event_date < now:
                event_date = datetime.strptime(f"{now.year + 1}-{start}", '%Y-%m-%d')
            delta = (event_date - now).days
            if delta == 7:
                add_notification('event', f"{rule['name']} in 7 days",
                    f"Consider boosting ad rates for {', '.join(rule.get('keywords', [])[:3])} items. Tier: {rule['tier']}",
                    severity='warning')
                generated += 1
            elif delta == 1:
                add_notification('event', f"{rule['name']} TOMORROW",
                    f"Activate promotions NOW for {rule['tier']} event. +{rule.get('increase_percent', 0)}% price boost recommended.",
                    severity='urgent')
                generated += 1
        except ValueError:
            continue

    # High-value items without promotion
    promo_data = fetch_all_promotions()
    per_listing = promo_data.get('per_listing', {})
    for l in listings:
        if l['price'] > 300 and l['id'] not in per_listing:
            add_notification('promo', f"High-value item not promoted",
                f"{l['title'][:50]} (${l['price']:.0f}) has no active promotion.",
                severity='info', data={'listing_id': l['id']})
            generated += 1

    return jsonify({'generated': generated})


# =============================================================================
# Feature: My Sales vs Market Comparison
# =============================================================================

@app.route('/api/reports/vs-market')
def report_vs_market():
    """Compare your sales performance against market category comps"""
    sold = ebay.get_sold_items(days_back=60)
    listings = ebay.get_all_listings()
    index = load_market_index()
    promo_data = fetch_all_promotions()
    per_listing = promo_data.get('per_listing', {})
    market_cats = index.get('categories', {}) if index else {}

    def detect_cat(title):
        t = title.lower()
        if 'shepard fairey' in t or 'obey' in t: return 'Shepard Fairey'
        elif 'death nyc' in t: return 'Death NYC'
        elif 'banksy' in t: return 'Banksy'
        elif 'kaws' in t: return 'KAWS'
        elif 'bearbrick' in t: return 'Bearbrick'
        elif 'brainwash' in t: return 'Mr. Brainwash'
        elif 'apollo' in t or 'nasa' in t or 'astronaut' in t: return 'Space/NASA'
        elif ('vinyl' in t or 'record' in t or 'album' in t) and 'signed' in t: return 'Signed Music'
        elif 'pickguard' in t: return 'Pickguard'
        return 'Other'

    # Aggregate my data
    my_data = {}
    for s in sold:
        if s['price'] < 25: continue
        cat = detect_cat(s.get('title', ''))
        if cat not in my_data:
            my_data[cat] = {'sold': 0, 'revenue': 0, 'prices': [], 'doms': [], 'active': 0, 'active_value': 0, 'promoted': 0, 'ad_spend': 0, 'ad_rates': []}
        my_data[cat]['sold'] += 1
        my_data[cat]['revenue'] += s['price']
        my_data[cat]['prices'].append(s['price'])
        if s.get('days_on_market') is not None:
            my_data[cat]['doms'].append(s['days_on_market'])

    for l in listings:
        cat = detect_cat(l['title'])
        if cat not in my_data:
            my_data[cat] = {'sold': 0, 'revenue': 0, 'prices': [], 'doms': [], 'active': 0, 'active_value': 0, 'promoted': 0, 'ad_spend': 0, 'ad_rates': []}
        my_data[cat]['active'] += 1
        my_data[cat]['active_value'] += l['price']
        p = per_listing.get(l['id'], {})
        if p.get('ad_rate', 0) > 0:
            my_data[cat]['promoted'] += 1
            my_data[cat]['ad_spend'] += l['price'] * (p['ad_rate'] / 100)
            my_data[cat]['ad_rates'].append(p['ad_rate'])

    # Find market comps for each category
    comparisons = []
    for cat, my in my_data.items():
        # Find best market match
        mkt = None
        for mk, mv in market_cats.items():
            if cat.lower().split()[0] in mk.lower():
                if not mkt or mv.get('sold_count', 0) > mkt.get('sold_count', 0):
                    mkt = mv
                    mkt['_name'] = mk

        my_avg_price = round(sum(my['prices']) / len(my['prices'])) if my['prices'] else 0
        my_avg_dom = round(sum(my['doms']) / len(my['doms'])) if my['doms'] else 0
        my_sell_rate = round(my['sold'] / max(my['active'], 1) * 100, 1) if my['active'] else 0
        my_avg_ad = round(sum(my['ad_rates']) / len(my['ad_rates']), 1) if my['ad_rates'] else 0
        my_promo_pct = round(my['promoted'] / max(my['active'], 1) * 100) if my['active'] else 0

        mkt_avg = round(mkt.get('sold_avg', 0)) if mkt else 0
        mkt_median = round(mkt.get('sold_median', 0)) if mkt else 0
        mkt_count = mkt.get('sold_count', 0) if mkt else 0
        mkt_active = mkt.get('count', 0) if mkt else 0

        # Price vs market
        price_diff = 0
        price_signal = 'at_market'
        if mkt_median and my_avg_price:
            price_diff = round(((my_avg_price / mkt_median) - 1) * 100)
            if price_diff < -20:
                price_signal = 'underpriced'
            elif price_diff > 30:
                price_signal = 'premium'
            else:
                price_signal = 'at_market'

        # Commentary
        commentary = []
        if price_signal == 'underpriced':
            commentary.append(f'Selling {abs(price_diff)}% below market median — raise prices or hold for better offers.')
        elif price_signal == 'premium':
            commentary.append(f'Selling {price_diff}% above median — premium pricing working, maintain quality positioning.')

        if my_avg_dom <= 7:
            commentary.append(f'Fast seller ({my_avg_dom}d avg) — can afford to price higher.')
        elif my_avg_dom > 21:
            commentary.append(f'Slow mover ({my_avg_dom}d avg) — try promotions, markdowns, or lower entry prices.')

        if my_promo_pct < 50 and my['active'] > 5:
            commentary.append(f'Only {my_promo_pct}% promoted — add more items to campaigns.')

        if my['ad_spend'] > my['revenue'] * 0.15 and my['revenue'] > 0:
            commentary.append(f'Ad spend is {round(my["ad_spend"]/my["revenue"]*100)}% of revenue — too high, reduce rates.')

        if my_sell_rate > 30:
            commentary.append(f'{my_sell_rate}% sell-through rate is strong.')
        elif my_sell_rate < 10 and my['active'] > 5:
            commentary.append(f'Low sell-through ({my_sell_rate}%) — review pricing and promotion strategy.')

        comparisons.append({
            'category': cat,
            'my_sold': my['sold'],
            'my_revenue': round(my['revenue']),
            'my_avg_price': my_avg_price,
            'my_avg_dom': my_avg_dom,
            'my_active': my['active'],
            'my_active_value': round(my['active_value']),
            'my_promoted': my['promoted'],
            'my_promo_pct': my_promo_pct,
            'my_avg_ad_rate': my_avg_ad,
            'my_ad_spend': round(my['ad_spend']),
            'my_sell_rate': my_sell_rate,
            'mkt_name': mkt.get('_name', '') if mkt else '',
            'mkt_avg_price': mkt_avg,
            'mkt_median_price': mkt_median,
            'mkt_sold_count': mkt_count,
            'mkt_active_count': mkt_active,
            'price_diff_pct': price_diff,
            'price_signal': price_signal,
            'commentary': commentary,
        })

    comparisons.sort(key=lambda x: x['my_revenue'], reverse=True)

    # Totals
    total_rev = sum(c['my_revenue'] for c in comparisons)
    total_active = sum(c['my_active'] for c in comparisons)
    total_sold = sum(c['my_sold'] for c in comparisons)
    total_ad = sum(c['my_ad_spend'] for c in comparisons)

    return jsonify({
        'comparisons': comparisons,
        'totals': {
            'revenue': total_rev,
            'active': total_active,
            'sold': total_sold,
            'ad_spend': total_ad,
            'sell_through': round(total_sold / max(total_active, 1) * 100, 1),
        }
    })


@app.route('/reports/vs-market')
def vs_market_page():
    """Standalone HTML comparison page"""
    data = report_vs_market()
    d = json.loads(data.data)
    comps = d['comparisons']
    totals = d['totals']

    rows = ''
    for c in comps:
        sig_color = '#30d158' if c['price_signal'] == 'premium' else '#ff453a' if c['price_signal'] == 'underpriced' else '#86868b'
        sig_icon = '▲' if c['price_signal'] == 'premium' else '▼' if c['price_signal'] == 'underpriced' else '—'
        commentary_html = '<br>'.join(c['commentary']) if c['commentary'] else 'On track'

        rows += f'''<tr>
            <td style="font-weight:600;">{c["category"]}</td>
            <td class="num">{c["my_sold"]}</td>
            <td class="num green">${c["my_revenue"]:,}</td>
            <td class="num">${c["my_avg_price"]}</td>
            <td class="num dim">${c["mkt_median_price"]}</td>
            <td class="num" style="color:{sig_color};font-weight:700;">{sig_icon} {c["price_diff_pct"]:+}%</td>
            <td class="num">{c["my_avg_dom"]}d</td>
            <td class="num">{c["my_active"]}</td>
            <td class="num">${c["my_active_value"]:,}</td>
            <td class="num">{c["my_promo_pct"]}%</td>
            <td class="num">{c["my_avg_ad_rate"]}%</td>
            <td class="num orange">${c["my_ad_spend"]}</td>
            <td class="num">{c["my_sell_rate"]}%</td>
            <td style="font-size:11px;color:#86868b;line-height:1.4;">{commentary_html}</td>
        </tr>'''

    html = f'''<!DOCTYPE html>
<html><head><title>My Sales vs Market</title>
<style>
body {{ font-family: -apple-system, sans-serif; background: #000; color: #f5f5f7; padding: 40px; max-width: 1400px; margin: 0 auto; }}
h1 {{ font-size: 28px; letter-spacing: -0.5px; }}
h2 {{ font-size: 18px; margin-top: 24px; color: #86868b; }}
.stat {{ display: inline-block; background: #1c1c1e; border-radius: 12px; padding: 16px 24px; margin: 4px; text-align: center; }}
.stat .val {{ font-size: 24px; font-weight: 700; }}
.stat .lbl {{ font-size: 11px; color: #86868b; text-transform: uppercase; letter-spacing: 0.5px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 16px; }}
th {{ text-align: left; padding: 10px 8px; color: #86868b; font-size: 9px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #38383a; }}
td {{ padding: 8px; border-bottom: 1px solid rgba(255,255,255,0.04); }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.green {{ color: #30d158; }} .red {{ color: #ff453a; }} .orange {{ color: #ff9f0a; }} .dim {{ color: #86868b; }}
.insight {{ background: #1c1c1e; border-radius: 12px; padding: 16px; margin: 8px 0; border-left: 3px solid #0a84ff; }}
</style></head><body>
<h1>My Sales vs. Market</h1>
<p class="dim">60-day comparison — How you stack up against category benchmarks</p>

<div>
<div class="stat"><div class="val green">${totals["revenue"]:,}</div><div class="lbl">My Revenue</div></div>
<div class="stat"><div class="val">{totals["sold"]}</div><div class="lbl">Items Sold</div></div>
<div class="stat"><div class="val">{totals["active"]}</div><div class="lbl">Active</div></div>
<div class="stat"><div class="val orange">${totals["ad_spend"]:,}</div><div class="lbl">Ad Spend</div></div>
<div class="stat"><div class="val">{totals["sell_through"]}%</div><div class="lbl">Sell-Through</div></div>
</div>

<h2>Category Comparison</h2>
<table>
<tr>
<th>Category</th><th class="num">Sold</th><th class="num">Revenue</th>
<th class="num">My Avg $</th><th class="num">Mkt Median</th><th class="num">vs Mkt</th>
<th class="num">Avg DOM</th><th class="num">Active</th><th class="num">Value</th>
<th class="num">Promo%</th><th class="num">Avg Rate</th><th class="num">Ad Spend</th>
<th class="num">Sell Rate</th><th>Commentary</th>
</tr>
{rows}
</table>

<h2>Key Insights</h2>
{''.join('<div class="insight">' + '<br>'.join(c["commentary"]) + '</div>' for c in comps if c["commentary"])}

</body></html>'''

    return html, 200, {'Content-Type': 'text/html'}


# =============================================================================
# Feature: Executive Dashboard, Forecast, Quick Wins, Competitor Map
# =============================================================================

@app.route('/api/executive')
def get_executive_dashboard():
    """One-screen executive summary with health score, forecast, quick wins"""
    listings = ebay.get_all_listings()
    sold = [s for s in ebay.get_sold_items(days_back=60) if s['price'] >= 25]
    promo = fetch_all_promotions()
    per_listing = promo.get('per_listing', {})
    enriched = load_personal_inventory()
    rules = load_pricing_rules()
    now = datetime.now()

    # Revenue trend by week
    from collections import defaultdict
    weekly = defaultdict(lambda: {'count': 0, 'revenue': 0})
    for s in sold:
        d = s.get('end_time', '')[:10]
        if d:
            # ISO week
            try:
                dt = datetime.fromisoformat(d)
                wk = dt.strftime('%Y-W%U')
                weekly[wk]['count'] += 1
                weekly[wk]['revenue'] += s['price']
            except Exception:
                pass

    # Health score (0-100)
    total = max(len(listings), 1)
    with_watchers = len([l for l in listings if l.get('watchers', 0) > 0])
    promoted = len(per_listing)

    health_promoted = min(promoted / total, 1) * 30  # 30 pts for promotion coverage
    health_watchers = min(with_watchers / total, 1) * 25  # 25 pts for watcher coverage
    health_velocity = min(len(sold) / (total * 0.5), 1) * 25  # 25 pts for sell-through
    health_margin = 20  # Assume healthy unless proven otherwise
    health_score = round(health_promoted + health_watchers + health_velocity + health_margin)

    # Forecast
    days = 60
    sold_per_day = len(sold) / days
    avg_price = sum(s['price'] for s in sold) / max(len(sold), 1)
    forecast_30d_items = round(sold_per_day * 30)
    forecast_30d_rev = round(sold_per_day * 30 * avg_price)

    # Speed High forecast (3x velocity based on your historical data)
    speed_high_multiplier = 2.5  # Conservative — you did 3-4/day before
    forecast_high_items = round(sold_per_day * speed_high_multiplier * 30)
    forecast_high_rev = round(sold_per_day * speed_high_multiplier * 30 * avg_price)

    # Quick wins — top 5 most actionable items
    quick_wins = []

    # 1. High-watcher items not at optimal price
    for l in sorted(listings, key=lambda x: x.get('watchers', 0), reverse=True)[:5]:
        if l.get('watchers', 0) >= 3:
            quick_wins.append({
                'title': l['title'][:50],
                'action': f'{l["watchers"]} watchers — likely to sell soon. Ensure promoted.',
                'type': 'hot_watcher',
                'id': l['id'],
                'url': l.get('url', ''),
                'price': l['price'],
            })

    # 2. Sell-now enriched items
    for item in enriched:
        rec = item.get('ebay_supply', {}).get('recommendation', '')
        if rec == 'SELL NOW' and len(quick_wins) < 8:
            quick_wins.append({
                'title': item['name'][:50],
                'action': f'Low supply — price at ${item.get("suggested_price",0):.0f}',
                'type': 'sell_now',
                'price': item.get('suggested_price', 0),
            })

    # 3. Upcoming event items
    for rule in rules:
        try:
            start = rule['start_date']
            ed = datetime.strptime(f"{now.year}-{start}", '%Y-%m-%d')
            if ed < now: ed = datetime.strptime(f"{now.year + 1}-{start}", '%Y-%m-%d')
            delta = (ed - now).days
            if 0 < delta <= 7:
                for l in listings:
                    if any(kw.lower() in l['title'].lower() for kw in rule.get('keywords', [])):
                        if len(quick_wins) < 10:
                            quick_wins.append({
                                'title': l['title'][:50],
                                'action': f'{rule["name"]} in {delta}d — boost ad rate',
                                'type': 'event',
                                'id': l['id'],
                                'url': l.get('url', ''),
                                'price': l['price'],
                            })
                        break
        except ValueError:
            pass

    # Top 3 actions
    top_actions = []
    if promoted < total * 0.8:
        top_actions.append(f'Promote {total - promoted} more items — only {round(promoted/total*100)}% covered')
    if len([l for l in listings if l.get('watchers', 0) >= 3]) > 0:
        hot_count = len([l for l in listings if l.get('watchers', 0) >= 3])
        top_actions.append(f'{hot_count} items have 3+ watchers — these are hot, ensure they sell')
    upcoming_events = [r for r in rules if 0 < (datetime.strptime(f"{now.year}-{r['start_date']}", '%Y-%m-%d') - now).days <= 14]
    if upcoming_events:
        top_actions.append(f'{len(upcoming_events)} events in next 14 days — boost matching items NOW')
    if not top_actions:
        top_actions.append('All systems go — monitor watchers and keep promoting')

    return jsonify({
        'health_score': health_score,
        'health_breakdown': {
            'promotion': round(health_promoted),
            'watchers': round(health_watchers),
            'velocity': round(health_velocity),
            'margin': health_margin,
        },
        'revenue_trend': dict(sorted(weekly.items())),
        'forecast': {
            'current_pace': {'items': forecast_30d_items, 'revenue': forecast_30d_rev},
            'speed_high': {'items': forecast_high_items, 'revenue': forecast_high_rev},
            'velocity': round(sold_per_day, 2),
            'avg_price': round(avg_price),
        },
        'quick_wins': quick_wins[:8],
        'top_actions': top_actions,
        'stats': {
            'total_listings': total,
            'with_watchers': with_watchers,
            'promoted': promoted,
            'total_sold_60d': len(sold),
            'total_revenue_60d': round(sum(s['price'] for s in sold)),
        },
    })


@app.route('/api/ai/strategy')
def ai_strategy_recommendations():
    """AI-powered strategic recommendations from all inventory, sales, scoring, and capital data"""
    # Gather all data
    listings = ebay.get_all_listings()
    sold = [s for s in ebay.get_sold_items(days_back=60) if s['price'] >= 25]
    promo = fetch_all_promotions()
    per_listing = promo.get('per_listing', {})

    # Get full analytics (uses cache if recent)
    try:
        with app.test_request_context():
            inv_resp = get_full_inventory_analytics()
            inv_data = inv_resp.get_json()
    except Exception:
        inv_data = {'items': [], 'action_summary': {}, 'capital_efficiency': {}}

    items = inv_data.get('items', [])
    acts = inv_data.get('action_summary', {})
    cap = inv_data.get('capital_efficiency', {})

    # Build data summary for AI
    total = len(items)
    total_value = sum(i.get('suggested_price', 0) or 0 for i in items)
    total_revenue_60d = sum(s['price'] for s in sold)
    avg_sale = round(total_revenue_60d / max(len(sold), 1))
    velocity = round(len(sold) / 60, 2)

    # Category breakdown
    cat_stats = {}
    for i in items:
        c = i.get('artist', 'Other')
        if c not in cat_stats:
            cat_stats[c] = {'count': 0, 'value': 0, 'promote': 0, 'discount': 0, 'relist': 0, 'hold': 0, 'avg_margin': []}
        cat_stats[c]['count'] += 1
        cat_stats[c]['value'] += i.get('suggested_price', 0) or 0
        cat_stats[c][i.get('action', 'hold').lower()] = cat_stats[c].get(i.get('action', 'hold').lower(), 0) + 1
        if i.get('margin_pct'):
            cat_stats[c]['avg_margin'].append(i['margin_pct'])

    cat_summary = []
    for c, s in sorted(cat_stats.items(), key=lambda x: -x[1]['count']):
        avg_m = round(sum(s['avg_margin']) / max(len(s['avg_margin']), 1), 1) if s['avg_margin'] else 0
        cat_summary.append(f"  {c}: {s['count']} items, ${round(s['value'])} value, {avg_m}% margin, {s['promote']}P/{s['discount']}D/{s['relist']}R/{s['hold']}H")

    # Top discount candidates
    discount_items = sorted([i for i in items if i.get('action') == 'DISCOUNT'], key=lambda x: -(x.get('action_score', 0)))[:5]
    discount_text = '\n'.join([f"  ${i['suggested_price']:.0f} {i['name'][:45]} — {i.get('action_reason','')}" for i in discount_items])

    # Top promote candidates
    promote_items = sorted([i for i in items if i.get('action') == 'PROMOTE'], key=lambda x: -(x.get('action_score', 0)))[:5]
    promote_text = '\n'.join([f"  ${i['suggested_price']:.0f} {i['name'][:45]} — {i.get('action_reason','')}" for i in promote_items])

    # Stale items
    stale = sorted([i for i in items if (i.get('days_listed', 0) or 0) > 30], key=lambda x: -(x.get('days_listed', 0) or 0))[:5]
    stale_text = '\n'.join([f"  {i.get('days_listed',0)}d ${i['suggested_price']:.0f} {i['name'][:45]}" for i in stale])

    # Top performers (recent sold)
    top_sold = sorted(sold, key=lambda x: -x['price'])[:5]
    sold_text = '\n'.join([f"  ${s['price']:.0f} {s.get('title','')[:45]} ({s.get('days_on_market','?')}d DOM)" for s in top_sold])

    # Margin warnings
    margin_warn = [i for i in items if i.get('ad_eats_margin')]
    margin_text = f"{len(margin_warn)} items where ad cost exceeds 50% of gross profit"

    prompt = f"""You are a data-driven eBay selling strategist analyzing a real art/collectibles store. Give specific, actionable recommendations based on this data.

STORE SNAPSHOT:
- {total} active listings, ${round(total_value)} total inventory value
- {len(sold)} sold in 60 days, ${round(total_revenue_60d)} revenue, {velocity} items/day
- Average sale: ${avg_sale}
- Capital locked: ${round(cap.get('total_locked', 0))}
- Inventory turnover: {cap.get('inventory_turnover', 0)}x/year
- Avg days listed: {cap.get('avg_days_listed', 0)}

ACTION SCORING:
- PROMOTE: {acts.get('PROMOTE', 0)} items (need ads to convert)
- DISCOUNT: {acts.get('DISCOUNT', 0)} items (overpriced vs market)
- RELIST: {acts.get('RELIST', 0)} items (no visibility)
- HOLD: {acts.get('HOLD', 0)} items (performing well)

CATEGORIES:
{chr(10).join(cat_summary)}

DISCOUNT CANDIDATES:
{discount_text or '  None flagged'}

PROMOTE CANDIDATES:
{promote_text or '  None flagged'}

STALE ITEMS (30+ days):
{stale_text or '  None'}

RECENT TOP SALES:
{sold_text or '  None in 60 days'}

MARGIN WARNINGS:
{margin_text}

Give me exactly 7 specific recommendations. For each:
1. One-line action (what to do right now)
2. Expected impact ($ or %)
3. Priority (1-3, 1=do today)

Format each as: **[PRIORITY] ACTION** — IMPACT

Be specific — use actual item names, dollar amounts, percentages. No generic advice."""

    claude_key = ENV.get('CLAUDE_API_KEY', '')
    recommendations = []

    if claude_key:
        try:
            resp = requests.post('https://api.anthropic.com/v1/messages',
                headers={'x-api-key': claude_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                json={'model': 'claude-sonnet-4-5-20241022', 'max_tokens': 800, 'messages': [{'role': 'user', 'content': prompt}]},
                timeout=30)
            if resp.status_code == 200:
                text = resp.json().get('content', [{}])[0].get('text', '')
                # Parse into structured recs
                for line in text.split('\n'):
                    line = line.strip()
                    if line and (line.startswith('**') or line.startswith('1.') or line.startswith('2.') or line.startswith('3.') or line.startswith('4.') or line.startswith('5.') or line.startswith('6.') or line.startswith('7.')):
                        recommendations.append(line)
                if not recommendations:
                    recommendations = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 20]
        except Exception as e:
            print(f"AI strategy error: {e}")

    if not recommendations:
        # Fallback: rule-based recommendations
        if acts.get('DISCOUNT', 0) > 0:
            recommendations.append(f"**[1] Discount {acts['DISCOUNT']} overpriced items** — Align with market to unlock ${acts['DISCOUNT'] * avg_sale * 0.2:.0f}+ in stale capital")
        if acts.get('PROMOTE', 0) > 0:
            recommendations.append(f"**[1] Promote {acts['PROMOTE']} high-impression items** — Convert existing traffic into sales")
        if acts.get('RELIST', 0) > 0:
            recommendations.append(f"**[2] Relist {acts['RELIST']} zero-visibility items** — Fresh listing = fresh search placement")
        if len(margin_warn) > 0:
            recommendations.append(f"**[2] Fix {len(margin_warn)} items where ads eat >50% profit** — Lower ad rate or raise price")
        if velocity < 1:
            recommendations.append(f"**[1] Velocity at {velocity}/day is low** — Apply Speed High promo strategy across all categories")
        recommendations.append(f"**[3] Capital: ${round(cap.get('total_locked', 0))} locked at {cap.get('inventory_turnover', 0)}x turnover** — Target 4x+ by moving stale items")
        if len(stale) > 0:
            recommendations.append(f"**[2] {len(stale)} items listed 30+ days** — Discount 10-15% or relist with new photos/titles")

    return jsonify({
        'recommendations': recommendations[:7],
        'data_summary': {
            'total_items': total,
            'total_value': round(total_value),
            'revenue_60d': round(total_revenue_60d),
            'velocity': velocity,
            'capital_locked': round(cap.get('total_locked', 0)),
            'turnover': cap.get('inventory_turnover', 0),
            'actions': acts,
        },
    })


@app.route('/api/competitor-map/<listing_id>')
def get_competitor_map(listing_id):
    """Visual price position map — where you sit vs competitors"""
    listings = ebay.get_all_listings()
    listing = None
    for l in listings:
        if l['id'] == listing_id:
            listing = l
            break

    if not listing:
        return jsonify({'error': 'Not found'}), 404

    # Search for competitors
    stop_words = {'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of', 'is', 'by', 'with', 'new', 'lot', 'rare', 'free', 'shipping'}
    words = [w for w in re.findall(r'\w+', listing['title'].lower()) if w not in stop_words and len(w) > 2]
    query = ' '.join(words[:5])
    comps = search_ebay(query, listing['price'] * 3, max(listing['price'] * 0.2, 10), limit=15) if query else []

    prices = [{'price': c['price'], 'title': c.get('title', '')[:40], 'yours': False, 'url': c.get('url', '')} for c in comps]
    prices.append({'price': listing['price'], 'title': 'YOUR LISTING', 'yours': True, 'url': listing.get('url', '')})
    prices.sort(key=lambda x: x['price'])

    your_rank = next((i for i, p in enumerate(prices) if p['yours']), 0)
    your_position = round((your_rank / max(len(prices) - 1, 1)) * 100)  # 0=cheapest, 100=most expensive

    return jsonify({
        'listing_id': listing_id,
        'title': listing['title'][:60],
        'your_price': listing['price'],
        'competitors': prices,
        'total_competitors': len(prices) - 1,
        'your_rank': your_rank + 1,
        'your_position_pct': your_position,
        'cheapest': prices[0]['price'] if prices else 0,
        'most_expensive': prices[-1]['price'] if prices else 0,
    })


# =============================================================================
# Feature: Universal Drill-Down API
# =============================================================================

@app.route('/api/drill/<drill_type>')
def drill_down(drill_type):
    """Universal drill-down — returns detailed data for any clickable element"""

    if drill_type == 'sell_now':
        """Items flagged SELL NOW with full detail"""
        inventory = load_personal_inventory()
        items = []
        for item in inventory:
            rec = item.get('ebay_supply', {}).get('recommendation', '')
            if rec == 'SELL NOW':
                supply = item.get('ebay_supply', {})
                items.append({
                    'name': item['name'],
                    'suggested_price': item.get('suggested_price', 0),
                    'price_range': item.get('price_range', ''),
                    'reason': supply.get('reason', ''),
                    'ebay_count': supply.get('ebay_count', 0),
                    'competing': supply.get('competing_listings', [])[:5],
                })
        return jsonify({'items': items, 'total': len(items), 'title': 'SELL NOW Items'})

    elif drill_type == 'sold_history':
        """Full sold history with details"""
        cat = request.args.get('category', '')
        sold = ebay.get_sold_items(days_back=60)
        items = []
        for s in sold:
            if s['price'] < 25: continue
            if cat:
                t = s.get('title', '').lower()
                item_cat = 'Shepard Fairey' if ('shepard fairey' in t or 'obey' in t) else 'Death NYC' if 'death nyc' in t else 'Banksy' if 'banksy' in t else 'Other'
                if cat.lower() not in item_cat.lower(): continue
            items.append({
                'title': s.get('title', '')[:60],
                'price': s.get('price', 0),
                'dom': s.get('days_on_market'),
                'listed': s.get('start_time', '')[:10],
                'sold_date': s.get('end_time', '')[:10],
            })
        return jsonify({'items': items, 'total': len(items), 'title': f'Sold Items{" — " + cat if cat else ""}'})

    elif drill_type == 'competing':
        """Competing eBay listings for a specific item"""
        listing_id = request.args.get('id', '')
        title = request.args.get('title', '')
        if not title:
            # Try to find from listings
            for l in ebay.get_all_listings():
                if l['id'] == listing_id:
                    title = l['title']
                    break

        # Search eBay for competitors
        stop_words = {'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of', 'is', 'by', 'with', 'new', 'lot', 'rare', 'free', 'shipping'}
        words = [w for w in re.findall(r'\w+', title.lower()) if w not in stop_words and len(w) > 2]
        query = ' '.join(words[:5])
        comps = search_ebay(query, 10000, 0, limit=20) if query else []

        # Also get enriched competing listings
        enriched = load_personal_inventory()
        enriched_comps = []
        for item in enriched:
            lw = set(re.findall(r'\w+', item['name'].lower()))
            tw = set(re.findall(r'\w+', title.lower()))
            if len(lw & tw) >= 2:
                enriched_comps = item.get('ebay_supply', {}).get('competing_listings', [])
                break

        all_comps = []
        seen = set()
        for c in enriched_comps + comps:
            t = c.get('title', '')[:40]
            if t not in seen:
                seen.add(t)
                all_comps.append({
                    'title': c.get('title', ''),
                    'price': c.get('price', 0),
                    'url': c.get('url', ''),
                    'seller': c.get('seller', ''),
                    'condition': c.get('condition', ''),
                })

        all_comps.sort(key=lambda x: x['price'])
        return jsonify({'items': all_comps, 'total': len(all_comps), 'title': f'Competing Listings — {title[:40]}'})

    elif drill_type == 'category':
        """All items in a category"""
        cat = request.args.get('name', '')
        listings = ebay.get_all_listings()
        promo = fetch_all_promotions()
        per_listing = promo.get('per_listing', {})

        items = []
        for l in listings:
            t = l['title'].lower()
            if cat == 'Shepard Fairey' and ('shepard fairey' in t or 'obey' in t): pass
            elif cat == 'Death NYC' and 'death nyc' in t: pass
            elif cat == 'Banksy' and 'banksy' in t: pass
            elif cat == 'KAWS' and 'kaws' in t: pass
            elif cat == 'Space/NASA' and ('apollo' in t or 'nasa' in t or 'astronaut' in t): pass
            elif cat == 'Signed Music' and (('vinyl' in t or 'record' in t) and 'signed' in t): pass
            elif cat == 'Other': pass
            else: continue

            p = per_listing.get(l['id'], {})
            items.append({
                'title': l['title'][:60],
                'price': l['price'],
                'watchers': l.get('watchers', 0),
                'ad_rate': p.get('ad_rate', 0),
                'url': l.get('url', ''),
                'id': l['id'],
            })

        items.sort(key=lambda x: x['price'], reverse=True)
        return jsonify({'items': items, 'total': len(items), 'title': f'{cat} — {len(items)} items'})

    elif drill_type == 'hot_deals':
        """Hot deals with detail"""
        deals = load_art_deals()
        hot = [d for d in deals if d.get('discount_pct', 0) >= 50 and 'ebay.com' in (d.get('url') or '')]
        items = [{'title': d['title'][:60], 'price': d.get('price', 0), 'median': d.get('median', 0), 'profit': d.get('profit', 0), 'discount': d.get('discount_pct', 0), 'url': d.get('url', '')} for d in hot[:20]]
        return jsonify({'items': items, 'total': len(items), 'title': 'Hot Deals (50%+ Below Market)'})

    elif drill_type == 'event_items':
        """Items affected by a specific event"""
        event_name = request.args.get('event', '')
        rules = load_pricing_rules()
        listings = ebay.get_all_listings()

        rule = None
        for r in rules:
            if r['name'] == event_name:
                rule = r
                break

        if not rule:
            return jsonify({'items': [], 'total': 0, 'title': 'Event not found'})

        items = []
        for l in listings:
            if any(kw.lower() in l['title'].lower() for kw in rule.get('keywords', [])):
                items.append({
                    'title': l['title'][:60],
                    'price': l['price'],
                    'url': l.get('url', ''),
                    'id': l['id'],
                })

        return jsonify({
            'items': items,
            'total': len(items),
            'title': f'{event_name} — {len(items)} matching items',
            'event': {'name': rule['name'], 'tier': rule['tier'], 'boost': rule.get('increase_percent', 0)},
        })

    elif drill_type == 'watchers':
        """Items with specific watcher count"""
        min_w = int(request.args.get('min', 0))
        max_w = int(request.args.get('max', 999))
        listings = ebay.get_all_listings()
        items = [{'title': l['title'][:60], 'price': l['price'], 'watchers': l.get('watchers', 0), 'url': l.get('url', ''), 'id': l['id']}
                 for l in listings if min_w <= l.get('watchers', 0) <= max_w]
        items.sort(key=lambda x: x['watchers'], reverse=True)
        label = f'Watchers {min_w}-{max_w}' if max_w < 999 else f'Watchers {min_w}+'
        return jsonify({'items': items, 'total': len(items), 'title': label})

    return jsonify({'error': 'Unknown drill type'}), 400


# =============================================================================
# Feature: Deep Analytics — 12 Insights
# =============================================================================

@app.route('/api/analytics/deep')
def get_deep_analytics():
    """All 12 deep analytics in one call"""
    sold = [s for s in ebay.get_sold_items(days_back=60) if s['price'] >= 25]
    listings = ebay.get_all_listings()
    promo = fetch_all_promotions()
    per_listing = promo.get('per_listing', {})
    now = datetime.now()

    def detect_cat(title):
        t = title.lower()
        if 'shepard fairey' in t or 'obey' in t: return 'Shepard Fairey'
        elif 'death nyc' in t: return 'Death NYC'
        elif 'banksy' in t: return 'Banksy'
        elif 'kaws' in t: return 'KAWS'
        elif ('vinyl' in t or 'record' in t) and 'signed' in t: return 'Signed Music'
        elif 'apollo' in t or 'nasa' in t or 'astronaut' in t: return 'Space/NASA'
        return 'Other'

    # ── 1. Day-of-week analysis ──
    from collections import defaultdict
    dow = defaultdict(lambda: {'count': 0, 'revenue': 0, 'avg_price': 0})
    for s in sold:
        d = s.get('end_time', '')[:19]
        if d:
            try:
                dt = datetime.fromisoformat(d)
                day = dt.strftime('%A')
                dow[day]['count'] += 1
                dow[day]['revenue'] += s['price']
            except Exception:
                pass
    for day in dow:
        dow[day]['avg_price'] = round(dow[day]['revenue'] / max(dow[day]['count'], 1))
    best_day = max(dow.items(), key=lambda x: x[1]['revenue'])[0] if dow else 'Unknown'

    # ── 2. Price elasticity ──
    price_vs_dom = []
    for s in sold:
        if s.get('days_on_market') is not None:
            price_vs_dom.append({'price': s['price'], 'dom': s['days_on_market'], 'category': detect_cat(s.get('title', ''))})

    # By price bucket
    elasticity = {}
    for bucket_name, lo, hi in [('<$75', 0, 75), ('$75-150', 75, 150), ('$150-300', 150, 300), ('$300-500', 300, 500), ('$500+', 500, 99999)]:
        items = [p for p in price_vs_dom if lo <= p['price'] < hi]
        if items:
            avg_dom = round(sum(i['dom'] for i in items) / len(items))
            elasticity[bucket_name] = {'count': len(items), 'avg_dom': avg_dom, 'avg_price': round(sum(i['price'] for i in items) / len(items))}

    # ── 3. Repeat buyers ──
    # (Would need buyer IDs from sold data — not available in current API response)

    # ── 4. Dead stock ──
    dead_stock = []
    for l in listings:
        watchers = l.get('watchers', 0)
        start = l.get('start_time', '')
        if start and watchers == 0:
            try:
                listed = datetime.fromisoformat(start[:19])
                age = (now - listed).days
                if age > 30:
                    dead_stock.append({
                        'title': l['title'][:50], 'price': l['price'], 'days': age,
                        'holding_cost': round(l['price'] * 0.001 * age, 2),  # ~0.1%/day opportunity cost
                        'url': l.get('url', ''), 'id': l['id'],
                    })
            except Exception:
                pass
    dead_stock.sort(key=lambda x: x['days'], reverse=True)

    # ── 5. Restock signals ──
    cat_sold = defaultdict(lambda: {'sold': 0, 'revenue': 0, 'avg_dom': 0, 'doms': []})
    cat_active = defaultdict(int)
    for s in sold:
        cat = detect_cat(s.get('title', ''))
        cat_sold[cat]['sold'] += 1
        cat_sold[cat]['revenue'] += s['price']
        if s.get('days_on_market') is not None:
            cat_sold[cat]['doms'].append(s['days_on_market'])
    for l in listings:
        cat_active[detect_cat(l['title'])] += 1

    restock = []
    for cat, data in cat_sold.items():
        active = cat_active.get(cat, 0)
        avg_dom = round(sum(data['doms']) / len(data['doms'])) if data['doms'] else 30
        sell_rate = data['sold'] / 60  # per day
        days_of_stock = round(active / max(sell_rate, 0.01))
        if days_of_stock < 30 and data['sold'] >= 3:
            restock.append({
                'category': cat, 'active': active, 'sold_60d': data['sold'],
                'avg_dom': avg_dom, 'days_of_stock': days_of_stock,
                'urgency': 'critical' if days_of_stock < 14 else 'soon',
                'message': f'{cat}: {active} left, selling {sell_rate:.1f}/day. {days_of_stock} days of stock.',
            })
    restock.sort(key=lambda x: x['days_of_stock'])

    # ── 7. Ad rate sweet spot ──
    rate_vs_conv = defaultdict(lambda: {'total': 0, 'sold': 0})
    sold_titles = set(s.get('title', '').lower()[:40] for s in sold)
    for l in listings:
        p = per_listing.get(l['id'], {})
        rate = p.get('ad_rate', 0)
        bucket = '0%' if rate == 0 else f'{int(rate)}%'
        rate_vs_conv[bucket]['total'] += 1
        if l['title'].lower()[:40] in sold_titles:
            rate_vs_conv[bucket]['sold'] += 1

    ad_sweet_spot = {}
    for bucket, data in sorted(rate_vs_conv.items()):
        conv = round(data['sold'] / max(data['total'], 1) * 100, 1)
        ad_sweet_spot[bucket] = {'items': data['total'], 'sold': data['sold'], 'conversion': conv}

    # ── 9. Watcher-to-sale ──
    watcher_conv = {'high_watch': 0, 'high_watch_sold': 0, 'low_watch': 0, 'low_watch_sold': 0}
    for l in listings:
        w = l.get('watchers', 0)
        was_sold = l['title'].lower()[:40] in sold_titles
        if w >= 3:
            watcher_conv['high_watch'] += 1
            if was_sold: watcher_conv['high_watch_sold'] += 1
        else:
            watcher_conv['low_watch'] += 1
            if was_sold: watcher_conv['low_watch_sold'] += 1

    # ── 11. Seasonal price premium ──
    # Check if items matching event keywords sold for more during event windows
    rules = load_pricing_rules()
    seasonal_premium = []
    for rule in rules[:5]:  # Top 5 events
        kws = rule.get('keywords', [])
        start_mmdd = rule.get('start_date', '')
        end_mmdd = rule.get('end_date', '')
        event_sales = []
        non_event_sales = []
        for s in sold:
            if any(kw.lower() in s.get('title', '').lower() for kw in kws):
                sale_mmdd = s.get('end_time', '')[5:10]
                if start_mmdd <= sale_mmdd <= end_mmdd:
                    event_sales.append(s['price'])
                else:
                    non_event_sales.append(s['price'])

        if event_sales and non_event_sales:
            event_avg = sum(event_sales) / len(event_sales)
            non_avg = sum(non_event_sales) / len(non_event_sales)
            premium = round(((event_avg / non_avg) - 1) * 100)
            seasonal_premium.append({
                'event': rule['name'], 'event_avg': round(event_avg),
                'non_event_avg': round(non_avg), 'premium_pct': premium,
            })

    # ── Summary insights ──
    insights = []
    insights.append(f'Best selling day: {best_day} (${dow.get(best_day, {}).get("revenue", 0):,.0f} revenue)')
    if dead_stock:
        total_dead_value = sum(d['price'] for d in dead_stock)
        insights.append(f'{len(dead_stock)} dead stock items worth ${total_dead_value:,.0f} with 0 watchers >30 days')
    if restock:
        insights.append(f'Restock alert: {restock[0]["message"]}')
    fastest_bucket = min(elasticity.items(), key=lambda x: x[1]['avg_dom'])[0] if elasticity else '?'
    insights.append(f'Fastest selling price range: {fastest_bucket}')

    return jsonify({
        'day_of_week': dict(dow),
        'best_day': best_day,
        'price_elasticity': elasticity,
        'price_vs_dom': price_vs_dom[:50],
        'dead_stock': dead_stock[:20],
        'dead_stock_total': len(dead_stock),
        'dead_stock_value': round(sum(d['price'] for d in dead_stock)),
        'restock_signals': restock,
        'ad_sweet_spot': ad_sweet_spot,
        'watcher_conversion': watcher_conv,
        'seasonal_premium': seasonal_premium,
        'insights': insights,
        'total_sold': len(sold),
        'total_active': len(listings),
    })


# =============================================================================
# Feature: LLM-Validated Comps
# =============================================================================

@app.route('/api/comps/smart')
def get_smart_comps():
    """LLM-first comp matching — AI picks the search query AND validates results"""
    title = request.args.get('title', '')
    artist = request.args.get('artist', '')
    price = float(request.args.get('price', 0))

    if not title:
        return jsonify({'error': 'Missing title'}), 400

    claude_key = ENV.get('CLAUDE_API_KEY', '')
    if not claude_key:
        return jsonify({'error': 'No Claude API key'}), 500

    # Step 1: Build smart search queries programmatically (no LLM — faster, deterministic)
    noise = {'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of', 'is', 'by', 'with',
             'new', 'lot', 'rare', 'free', 'shipping', 'print', 'signed', 'numbered', 'hand',
             'screen', 'edition', 'limited', 'art', 'original', 'artist', 'proof', 'framed',
             'obey', 'giant', 'authentic', 'vinyl', 'figure', 'open'}

    # Extract meaningful words (the title of the work)
    artist_first = artist.lower().split()[0] if artist else ''
    words = [w for w in re.findall(r'\w+', title) if w.lower() not in noise and len(w) > 2 and w.lower() != artist_first]

    # Build 3 queries
    queries = []
    if artist:
        # Query 1: Artist + first 2 meaningful words
        q1_words = words[:2]
        if q1_words:
            queries.append(f"{artist} {' '.join(q1_words)}")

        # Query 2: Artist + next 2 words
        q2_words = words[1:3] if len(words) > 2 else words[:2]
        if q2_words:
            queries.append(f"{artist} {' '.join(q2_words)}")

        # Query 3: Just artist + first word (broader)
        if words:
            queries.append(f"{artist} {words[0]}")
    else:
        queries = [' '.join(words[:4])]

    # Category-specific minimum prices
    min_prices = {'KAWS': 200, 'Banksy': 50, 'Shepard Fairey': 40, 'Death NYC': 20, 'Mr. Brainwash': 50}
    min_price = min_prices.get(artist, 20)

    if not queries:
        queries = [title[:40]]

    # Step 2: Search eBay with queries
    all_results = []
    for q in queries[:3]:
        try:
            results = search_ebay(q, max(price * 3, 500), min_price, limit=15)
            all_results.extend(results)
        except Exception:
            pass

    # Deduplicate
    seen = set()
    unique = []
    for r in all_results:
        key = r.get('title', '')[:30]
        if key not in seen:
            seen.add(key)
            unique.append(r)

    if not unique:
        return jsonify({'comps': [], 'queries': queries, 'validated': 0})

    # Step 2.5: PRE-FILTER — word overlap + quality gate + learned rejections
    title_word_set = set(w.lower() for w in words if len(w) > 2)
    rejections_data = load_comp_rejections()
    pre_filtered = []
    pre_removed = []
    for r in unique:
        comp_title = r.get('title', '')
        comp_words = set(w.lower() for w in re.findall(r'\w+', comp_title) if len(w) > 2)
        overlap = title_word_set & comp_words
        comp_title_lower = comp_title.lower()
        is_wrong_type = any(x in comp_title_lower for x in ['sticker', 'pin', 't-shirt', 'tshirt', 'tee ', 'book', 'magazine', 'postcard', 'magnet', 'keychain', 'patch', 'button'])
        passes_gate = passes_artist_quality_gate(comp_title, artist)
        # Check learned rejections
        is_learned_reject, reject_reason = comp_matches_learned_rejection(comp_title, rejections_data)
        if len(overlap) >= 1 and not is_wrong_type and passes_gate and not is_learned_reject:
            pre_filtered.append(r)
        else:
            pre_removed.append(r)

    if not pre_filtered:
        return jsonify({'comps': [], 'queries': queries, 'validated': 0, 'pre_filtered': len(unique), 'reason': 'No results matched title words'})

    # Step 3: Ask Claude to validate which pre-filtered results are real comps
    comp_text = '\n'.join([f"{i+1}. ${r['price']:.0f} — {r.get('title', '')[:70]}" for i, r in enumerate(pre_filtered[:15])])

    validate_prompt = f"""I need to find comparable sales for this SPECIFIC item: "{title}" by {artist}.

CRITICAL RULES — be STRICT:
1. The comp must be the EXACT same work or very close variant (same print name, same figure model)
2. "{title}" is the item — if a comp is a DIFFERENT work by the same artist, REJECT it
3. Same artist is REQUIRED but NOT SUFFICIENT — "Shepard Fairey Peace Goddess" is NOT a comp for "Shepard Fairey Fragile Peace"
4. Reject different product types (stickers, shirts, books, postcards if item is a print/figure)
5. Reject obvious fakes/reproductions

Listings to evaluate:
{comp_text}

Which numbers are VALID comps for "{title}"? Be strict — when in doubt, REJECT.
Respond ONLY with JSON: {{"valid": [1, 3], "reason": "brief explanation"}}
If NONE are valid, respond: {{"valid": [], "reason": "none match"}}"""

    valid_indices = set(range(len(pre_filtered)))
    reason = ''

    try:
        resp = requests.post('https://api.anthropic.com/v1/messages',
            headers={'x-api-key': claude_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json={'model': 'claude-sonnet-4-5-20241022', 'max_tokens': 200, 'messages': [{'role': 'user', 'content': validate_prompt}]},
            timeout=15)
        if resp.status_code == 200:
            text = resp.json().get('content', [{}])[0].get('text', '')
            match = re.search(r'\{[^}]+\}', text, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                nums = parsed.get('valid', [])
                if isinstance(nums, list):
                    valid_indices = set(n - 1 for n in nums if 1 <= n <= len(pre_filtered))
                reason = parsed.get('reason', '')
    except Exception:
        reason = 'Validation failed — showing pre-filtered only'

    validated = [pre_filtered[i] for i in sorted(valid_indices) if i < len(pre_filtered)]
    removed = pre_removed + [pre_filtered[i] for i in range(len(pre_filtered)) if i not in valid_indices]

    # IQR outlier removal — remove top and bottom outliers
    v_prices = [c['price'] for c in validated if c.get('price', 0) > 0]
    outlier_removed = []
    if len(v_prices) >= 4:
        sp = sorted(v_prices)
        q1, q3 = sp[len(sp)//4], sp[3*len(sp)//4]
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        new_validated = []
        for c in validated:
            cp = c.get('price', 0)
            if cp > 0 and lo <= cp <= hi:
                new_validated.append(c)
            else:
                outlier_removed.append(c)
        validated = new_validated
        v_prices = [c['price'] for c in validated if c.get('price', 0) > 0]

    stats = {}
    if v_prices:
        sp = sorted(v_prices)
        stats = {'count': len(sp), 'min': sp[0], 'max': sp[-1], 'median': sp[len(sp)//2], 'avg': round(sum(sp)/len(sp))}

    return jsonify({
        'comps': [{'title': c.get('title', '')[:60], 'price': c['price'], 'url': c.get('url', ''), 'condition': c.get('condition', '')} for c in validated],
        'removed': [{'title': r.get('title', '')[:40], 'price': r['price']} for r in removed],
        'outliers_removed': [{'title': o.get('title', '')[:40], 'price': o['price']} for o in outlier_removed] if outlier_removed else [],
        'stats': stats,
        'queries': queries,
        'min_price': min_price,
        'validated': len(validated),
        'raw': len(unique),
        'reason': reason if 'reason' in dir() else '',
    })


# =============================================================================
# Comp Rejection Learning System
# =============================================================================
COMP_REJECTIONS_FILE = os.path.join(DATA_DIR, 'comp_rejections.json')


def load_comp_rejections():
    if os.path.exists(COMP_REJECTIONS_FILE):
        try:
            with open(COMP_REJECTIONS_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {'rejected_titles': [], 'rejected_patterns': [], 'learned_rules': [], 'stats': {'total_rejected': 0, 'total_learned': 0}}


def save_comp_rejections(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(COMP_REJECTIONS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def learn_from_rejections(rejections_data):
    """Analyze rejected comps and extract reusable rejection patterns.
    If the same word appears in 3+ rejected titles AND is NOT in the item titles, it becomes a learned rule."""
    from collections import Counter
    noise = {'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of', 'is', 'by', 'with',
             'new', 'lot', 'rare', 'free', 'shipping', 'print', 'signed', 'numbered', 'hand',
             'obey', 'giant', 'screen', 'edition', 'limited', 'art', 'original', 'artist',
             'shepard', 'fairey', 'kaws', 'banksy', 'death', 'nyc'}

    word_counts = Counter()
    # Collect words from the items being comped — these should NOT become rejection patterns
    item_words = set()
    for rej in rejections_data.get('rejected_titles', []):
        item_title = rej.get('item_title', '').lower()
        item_words.update(w for w in re.findall(r'\w+', item_title) if len(w) > 2)
        title = rej.get('comp_title', '').lower()
        words = set(w for w in re.findall(r'\w+', title) if w not in noise and len(w) > 2)
        for w in words:
            word_counts[w] += 1

    # Words appearing in 3+ rejections AND not in any item title
    learned = [{'word': w, 'count': c, 'type': 'word_pattern'} for w, c in word_counts.items() if c >= 3 and w not in item_words]
    rejections_data['learned_rules'] = learned
    rejections_data['stats']['total_learned'] = len(learned)
    return rejections_data


def comp_matches_learned_rejection(comp_title, rejections_data):
    """Check if a comp matches any learned rejection pattern"""
    t = comp_title.lower()

    # Check exact rejected titles (normalized)
    for rej in rejections_data.get('rejected_titles', []):
        rej_title = rej.get('comp_title', '').lower()[:30]
        if rej_title and rej_title in t[:30]:
            return True, f'Previously rejected: {rej_title[:25]}'

    # Check learned word patterns
    for rule in rejections_data.get('learned_rules', []):
        word = rule.get('word', '')
        if word and word in t and rule.get('count', 0) >= 3:
            return True, f'Learned: "{word}" appears in {rule["count"]} rejected comps'

    return False, ''


@app.route('/api/comps/reject', methods=['POST'])
def reject_comp():
    """User rejects a comp — save it and update learning model"""
    data = request.get_json()
    comp_title = data.get('comp_title', '')
    comp_price = data.get('comp_price', 0)
    item_title = data.get('item_title', '')
    artist = data.get('artist', '')
    reason = data.get('reason', 'user_rejected')

    if not comp_title:
        return jsonify({'error': 'Missing comp_title'}), 400

    rejections = load_comp_rejections()
    rejections['rejected_titles'].append({
        'comp_title': comp_title[:80],
        'comp_price': comp_price,
        'item_title': item_title[:80],
        'artist': artist,
        'reason': reason,
        'date': datetime.now().isoformat(),
    })
    rejections['stats']['total_rejected'] = len(rejections['rejected_titles'])

    # Re-learn patterns
    rejections = learn_from_rejections(rejections)
    save_comp_rejections(rejections)

    return jsonify({
        'success': True,
        'total_rejected': rejections['stats']['total_rejected'],
        'learned_rules': len(rejections.get('learned_rules', [])),
    })


@app.route('/api/comps/rejections')
def get_comp_rejections():
    """Get all rejection data and learned rules"""
    return jsonify(load_comp_rejections())


@app.route('/api/comps/rejections/clear', methods=['POST'])
def clear_comp_rejections():
    """Clear all rejections and learned rules"""
    save_comp_rejections({'rejected_titles': [], 'rejected_patterns': [], 'learned_rules': [], 'stats': {'total_rejected': 0, 'total_learned': 0}})
    return jsonify({'success': True})


@app.route('/api/comps/validated')
def get_validated_comps():
    """Get comps validated by LLM — removes wrong products, fakes, outliers"""
    title = request.args.get('title', '')
    artist = request.args.get('artist', '')
    price = float(request.args.get('price', 0))

    if not title:
        return jsonify({'error': 'Missing title'}), 400

    # Get raw comps from all sources
    raw_comps = lookup_historical_prices(title, artist, 20)

    # Also get live eBay comps
    stop_words = {'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of', 'is', 'by', 'with', 'new', 'lot', 'rare', 'free', 'shipping'}
    words = [w for w in re.findall(r'\w+', title.lower()) if w not in stop_words and len(w) > 2]
    query = ' '.join(words[:5])
    if query:
        live = search_ebay(query, max(price * 3, 500), max(price * 0.2, 10), limit=10)
        for l in live:
            raw_comps.append({'name': l.get('title', ''), 'price': l.get('price', 0), 'date': 'Active', 'source': 'eBay Active', 'url': l.get('url', '')})

    if not raw_comps:
        return jsonify({'comps': [], 'validated': 0, 'raw': 0})

    # Build comp text for LLM
    comp_text = '\n'.join([f"  {i+1}. ${c.get('price',0):.0f} — {c.get('name','')[:60]} ({c.get('source','')}, {c.get('date','')})" for i, c in enumerate(raw_comps[:20])])

    prompt = f"""You are a collectibles expert. I need you to validate which of these comparable sales are ACTUALLY the same product or a legitimate comp for this item.

ITEM: {title}
ARTIST: {artist}
CURRENT PRICE: ${price:.0f}

RAW COMPS:
{comp_text}

For EACH comp, respond with ONLY a JSON array of the comp numbers (1-based) that are VALID comps. A valid comp:
- Must be the SAME artist (Shepard Fairey comps must be Shepard Fairey items, not Banksy or random art)
- Must be a similar product type (if item is a signed print, comp should be a signed print, not a sticker or book)
- Must NOT be a knockoff/fake (KAWS $14 figures are fakes — real ones are $200+)
- Remove obvious outliers that are a different product

Respond ONLY with JSON: {{"valid": [1, 3, 5], "removed": [2, 4], "reason": "brief explanation"}}"""

    valid_indices = set(range(len(raw_comps)))  # Default: all valid
    llm_reason = ''

    # Ask Claude
    claude_key = ENV.get('CLAUDE_API_KEY', '')
    if claude_key:
        try:
            resp = requests.post('https://api.anthropic.com/v1/messages',
                headers={'x-api-key': claude_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                json={'model': 'claude-sonnet-4-5-20241022', 'max_tokens': 200, 'messages': [{'role': 'user', 'content': prompt}]},
                timeout=20)
            if resp.status_code == 200:
                text = resp.json().get('content', [{}])[0].get('text', '')
                match = re.search(r'\{[^}]+\}', text, re.DOTALL)
                if match:
                    result = json.loads(match.group())
                    valid_nums = result.get('valid', [])
                    valid_indices = set(v - 1 for v in valid_nums if 1 <= v <= len(raw_comps))
                    llm_reason = result.get('reason', '')
        except Exception as e:
            print(f"LLM comp validation error: {e}")

    # Also ask GPT for second opinion
    openai_key = ENV.get('OPENAI_API_KEY', '')
    if openai_key:
        try:
            resp = requests.post('https://api.openai.com/v1/chat/completions',
                headers={'Authorization': f'Bearer {openai_key}', 'Content-Type': 'application/json'},
                json={'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 200},
                timeout=20)
            if resp.status_code == 200:
                text = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
                match = re.search(r'\{[^}]+\}', text, re.DOTALL)
                if match:
                    result = json.loads(match.group())
                    gpt_valid = set(v - 1 for v in result.get('valid', []) if 1 <= v <= len(raw_comps))
                    # Consensus: keep only comps both LLMs agree on
                    if gpt_valid:
                        valid_indices = valid_indices & gpt_valid if valid_indices != set(range(len(raw_comps))) else gpt_valid
                    llm_reason += ' | GPT: ' + result.get('reason', '')
        except Exception as e:
            print(f"GPT comp validation error: {e}")

    # Filter comps
    validated = [raw_comps[i] for i in sorted(valid_indices) if i < len(raw_comps)]
    removed = [raw_comps[i] for i in range(len(raw_comps)) if i not in valid_indices]

    # Calculate validated stats
    v_prices = [c['price'] for c in validated if c.get('price', 0) > 0]
    stats = {}
    if v_prices:
        sorted_p = sorted(v_prices)
        stats = {
            'count': len(v_prices),
            'min': min(v_prices),
            'max': max(v_prices),
            'median': sorted_p[len(sorted_p) // 2],
            'avg': round(sum(v_prices) / len(v_prices), 2),
        }

    return jsonify({
        'comps': validated,
        'removed': [{'name': r.get('name', '')[:50], 'price': r.get('price', 0)} for r in removed],
        'stats': stats,
        'validated': len(validated),
        'raw': len(raw_comps),
        'llm_reason': llm_reason,
    })


# =============================================================================
# Feature: Per-Listing Price History Chart Data
# =============================================================================

@app.route('/api/listing/chart/<listing_id>')
def get_listing_chart(listing_id):
    """Get chart-ready price history for a specific listing — WorthPoint + eBay active comps"""
    listings = ebay.get_all_listings()
    listing = None
    for l in listings:
        if l['id'] == listing_id:
            listing = l
            break

    if not listing:
        return jsonify({'error': 'Listing not found'}), 404

    title = listing['title']
    price = listing['price']

    # Detect artist
    t = title.lower()
    if 'shepard fairey' in t or 'obey' in t:
        artist = 'Shepard Fairey'
    elif 'death nyc' in t:
        artist = 'Death NYC'
    elif 'kaws' in t:
        artist = 'KAWS'
    elif 'banksy' in t:
        artist = 'Banksy'
    else:
        artist = ''

    # Get historical comps
    historical = lookup_historical_prices(title, artist, 30)

    # Get current eBay active comps
    stop_words = {'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of', 'is', 'by', 'with', 'new', 'lot', 'rare', 'free', 'shipping'}
    words = [w for w in re.findall(r'\w+', title.lower()) if w not in stop_words and len(w) > 2]
    query = ' '.join(words[:5])
    active_comps = search_ebay(query, price * 3, max(price * 0.2, 10), limit=10) if query else []

    # Build chart data — sorted by date
    chart_points = []

    for h in historical:
        d = h.get('date', '')
        p = h.get('price', 0)
        if p and p > 0:
            chart_points.append({
                'date': d[:10] if d else 'Unknown',
                'price': p,
                'source': h.get('source', 'Historical'),
                'name': h.get('name', '')[:50],
                'type': 'sold',
            })

    # Add active comps as "current" points
    today = datetime.now().strftime('%Y-%m-%d')
    for c in active_comps:
        chart_points.append({
            'date': today,
            'price': c['price'],
            'source': 'eBay Active',
            'name': c.get('title', '')[:50],
            'type': 'active',
        })

    # Add your listing
    chart_points.append({
        'date': today,
        'price': price,
        'source': 'Your Listing',
        'name': title[:50],
        'type': 'yours',
    })

    # Sort by date
    chart_points.sort(key=lambda x: x['date'])

    # Stats
    hist_prices = [p['price'] for p in chart_points if p['type'] == 'sold']
    active_prices = [p['price'] for p in chart_points if p['type'] == 'active']

    return jsonify({
        'listing_id': listing_id,
        'title': title[:80],
        'your_price': price,
        'chart_points': chart_points,
        'stats': {
            'historical_count': len(hist_prices),
            'active_count': len(active_prices),
            'hist_median': sorted(hist_prices)[len(hist_prices)//2] if hist_prices else 0,
            'hist_avg': round(sum(hist_prices)/len(hist_prices), 2) if hist_prices else 0,
            'active_median': sorted(active_prices)[len(active_prices)//2] if active_prices else 0,
        }
    })


@app.route('/api/listings/all-charts')
def get_all_listing_charts():
    """Get mini chart data for all listings — for inline sparklines"""
    listings = ebay.get_all_listings()
    enriched = load_personal_inventory()

    # Build enrichment lookup
    enriched_map = {}
    for item in enriched:
        words = set(re.findall(r'\w+', item['name'].lower()))
        words -= {'the', 'a', 'and', 'of', 'in', 'print', 'signed', 'obey', 'giant', 'shepard', 'fairey'}
        enriched_map[frozenset(list(words)[:6])] = item

    results = {}
    for l in listings:
        # Check enrichment for price history
        lw = set(re.findall(r'\w+', l['title'].lower()))
        lw -= {'the', 'a', 'and', 'of', 'in', 'print', 'signed', 'obey', 'giant', 'shepard', 'fairey', 'new', 'rare', 'limited'}
        best = None
        for key, item in enriched_map.items():
            if len(lw & key) >= 2:
                best = item
                break

        if best and best.get('market_data', {}).get('price_history'):
            history = best['market_data']['price_history']
            prices = [h['price'] for h in history if h.get('price') and h['price'] > 0]
            if prices:
                results[l['id']] = {
                    'prices': prices[-10:],  # Last 10 data points for sparkline
                    'current': l['price'],
                    'trend': 'up' if len(prices) >= 2 and prices[-1] > prices[0] else 'down' if len(prices) >= 2 and prices[-1] < prices[0] else 'flat',
                }

    return jsonify(results)


# =============================================================================
# Feature: Automation Engine — auto-apply strategies
# =============================================================================

AUTOMATION_FILE = os.path.join(DATA_DIR, 'automation_config.json')


def load_automation_config():
    if os.path.exists(AUTOMATION_FILE):
        try:
            with open(AUTOMATION_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        'enabled': False,
        'auto_event_boost': True,       # 1. Auto-boost when event <7 days
        'auto_event_pullback': True,     # 2. Auto-reduce after events end
        'auto_markdown_stale': True,     # 3. Auto-markdown 0-watcher items after 30d
        'daily_brief_enabled': True,     # 4. Daily brief generation
        'event_boost_strategy': 'speed_medium',
        'default_strategy': 'steady',
        'stale_days': 30,
        'stale_markdown_pct': 10,
        'log': [],
    }


@app.route('/api/automation', methods=['GET', 'POST'])
def manage_automation():
    """Get or update automation config"""
    if request.method == 'POST':
        data = request.get_json()
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(AUTOMATION_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        return jsonify({'success': True})
    return jsonify(load_automation_config())


@app.route('/api/automation/run', methods=['POST'])
def run_automation():
    """Execute all automation rules"""
    config = load_automation_config()
    if not config.get('enabled'):
        return jsonify({'message': 'Automation disabled', 'actions': 0})

    listings = ebay.get_all_listings()
    rules = load_pricing_rules()
    promo = fetch_all_promotions()
    per_listing = promo.get('per_listing', {})
    now = datetime.now()
    mmdd = now.strftime('%m-%d')
    actions = []

    # 1. Auto-boost for events <7 days away
    if config.get('auto_event_boost'):
        strategy = AD_STRATEGY_PRESETS.get(config.get('event_boost_strategy', 'speed_medium'), AD_STRATEGY_PRESETS['speed_medium'])
        for listing in listings:
            title_lower = listing['title'].lower()
            for rule in rules:
                keywords = rule.get('keywords', [])
                if any(kw.lower() in title_lower for kw in keywords):
                    start = rule.get('start_date', '')
                    try:
                        ed = datetime.strptime(f"{now.year}-{start}", '%Y-%m-%d')
                        if ed < now:
                            ed = datetime.strptime(f"{now.year + 1}-{start}", '%Y-%m-%d')
                        delta = (ed - now).days
                        if 0 <= delta <= 7:
                            actions.append({
                                'type': 'event_boost',
                                'listing': listing['title'][:50],
                                'event': rule['name'],
                                'days': delta,
                                'action': f'Boost ad rate for {rule["name"]} ({rule["tier"]})',
                            })
                    except ValueError:
                        pass
                    break

    # 2. Auto-pullback after events end
    if config.get('auto_event_pullback'):
        for rule in rules:
            end = rule.get('end_date', '')
            if end < mmdd:
                # Event ended — flag items for rate reduction
                for listing in listings:
                    if any(kw.lower() in listing['title'].lower() for kw in rule.get('keywords', [])):
                        p = per_listing.get(listing['id'], {})
                        if p.get('ad_rate', 0) > 8:
                            actions.append({
                                'type': 'event_pullback',
                                'listing': listing['title'][:50],
                                'event': rule['name'],
                                'action': f'Reduce rate from {p["ad_rate"]}% — event ended',
                            })

    # 3. Auto-markdown stale items (0 watchers, listed >30 days)
    if config.get('auto_markdown_stale'):
        stale_days = config.get('stale_days', 30)
        for listing in listings:
            watchers = listing.get('watchers', 0)
            start = listing.get('start_time', '')
            if watchers == 0 and start:
                try:
                    listed = datetime.fromisoformat(start[:19])
                    age = (now - listed).days
                    if age > stale_days:
                        actions.append({
                            'type': 'stale_markdown',
                            'listing': listing['title'][:50],
                            'days_listed': age,
                            'action': f'Mark down {config.get("stale_markdown_pct", 10)}% — {age}d, 0 watchers',
                        })
                except Exception:
                    pass

    # Save log
    config['log'] = [{'time': now.isoformat(), 'actions': len(actions)}] + config.get('log', [])[:20]
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(AUTOMATION_FILE, 'w') as f:
        json.dump(config, f, indent=2)

    return jsonify({
        'actions': actions,
        'total': len(actions),
        'by_type': {
            'event_boost': len([a for a in actions if a['type'] == 'event_boost']),
            'event_pullback': len([a for a in actions if a['type'] == 'event_pullback']),
            'stale_markdown': len([a for a in actions if a['type'] == 'stale_markdown']),
        }
    })


# =============================================================================
# Feature: Watchers Analysis
# =============================================================================

@app.route('/api/analytics/watchers')
def get_watchers_analysis():
    """Watcher analysis by product and category"""
    listings = ebay.get_all_listings()
    promo = fetch_all_promotions()
    per_listing = promo.get('per_listing', {})

    def detect_cat(title):
        t = title.lower()
        if 'shepard fairey' in t or 'obey' in t: return 'Shepard Fairey'
        elif 'death nyc' in t: return 'Death NYC'
        elif 'banksy' in t: return 'Banksy'
        elif 'kaws' in t: return 'KAWS'
        elif 'bearbrick' in t: return 'Bearbrick'
        elif 'brainwash' in t: return 'Mr. Brainwash'
        elif 'apollo' in t or 'nasa' in t or 'astronaut' in t: return 'Space/NASA'
        elif ('vinyl' in t or 'record' in t or 'album' in t) and 'signed' in t: return 'Signed Music'
        elif 'pickguard' in t: return 'Pickguard'
        return 'Other'

    items = []
    cat_data = {}
    total_watchers = 0

    for l in listings:
        cat = detect_cat(l['title'])
        watchers = l.get('watchers', 0)
        price = l['price']
        p = per_listing.get(l['id'], {})
        ad_rate = p.get('ad_rate', 0)
        total_watchers += watchers

        items.append({
            'title': l['title'][:60],
            'category': cat,
            'price': price,
            'watchers': watchers,
            'ad_rate': ad_rate,
            'promoted': ad_rate > 0,
            'listing_id': l['id'],
        })

        if cat not in cat_data:
            cat_data[cat] = {'items': 0, 'watchers': 0, 'value': 0, 'promoted': 0}
        cat_data[cat]['items'] += 1
        cat_data[cat]['watchers'] += watchers
        cat_data[cat]['value'] += price
        if ad_rate > 0:
            cat_data[cat]['promoted'] += 1

    # Sort items by watchers
    items.sort(key=lambda x: x['watchers'], reverse=True)

    # Category averages
    for cat in cat_data:
        d = cat_data[cat]
        d['avg_watchers'] = round(d['watchers'] / d['items'], 1) if d['items'] else 0

    # Hot items (high watchers = likely to sell soon)
    hot = [i for i in items if i['watchers'] >= 3]

    # Cold items (0 watchers, been listed a while)
    cold = [i for i in items if i['watchers'] == 0]

    return jsonify({
        'items': items,
        'categories': cat_data,
        'total_watchers': total_watchers,
        'avg_watchers': round(total_watchers / len(items), 1) if items else 0,
        'hot_items': hot[:20],
        'cold_items': cold[:20],
        'total': len(items),
    })


# =============================================================================
# Feature: Seasonality Analysis
# =============================================================================

@app.route('/api/analytics/seasonality')
def get_seasonality_analysis():
    """Analyze seasonal patterns from historical sales and calendar events"""
    sold = ebay.get_sold_items(days_back=60)
    rules = load_pricing_rules()
    now = datetime.now()

    def detect_cat(title):
        t = title.lower()
        if 'shepard fairey' in t or 'obey' in t: return 'Shepard Fairey'
        elif 'death nyc' in t: return 'Death NYC'
        elif 'banksy' in t: return 'Banksy'
        elif 'kaws' in t: return 'KAWS'
        else: return 'Other'

    # Monthly sales pattern
    monthly = {}
    for s in sold:
        if s['price'] < 25: continue
        d = s.get('end_time', '')[:7]
        if not d: continue
        if d not in monthly:
            monthly[d] = {'count': 0, 'revenue': 0}
        monthly[d]['count'] += 1
        monthly[d]['revenue'] += s['price']

    # Build full year event calendar with revenue impact
    events_by_month = {}
    for rule in rules:
        month_num = int(rule['start_date'][:2])
        month_name = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][month_num]
        if month_name not in events_by_month:
            events_by_month[month_name] = []
        events_by_month[month_name].append({
            'name': rule['name'],
            'tier': rule['tier'],
            'boost': rule.get('increase_percent', 0),
            'start': rule['start_date'],
            'end': rule['end_date'],
            'keywords': rule.get('keywords', [])[:3],
        })

    # Upcoming opportunities (next 90 days)
    upcoming = []
    for rule in rules:
        try:
            start = rule['start_date']
            ed = datetime.strptime(f"{now.year}-{start}", '%Y-%m-%d')
            if ed < now:
                ed = datetime.strptime(f"{now.year + 1}-{start}", '%Y-%m-%d')
            delta = (ed - now).days
            if 0 < delta <= 90:
                upcoming.append({
                    'name': rule['name'],
                    'tier': rule['tier'],
                    'days_away': delta,
                    'boost': rule.get('increase_percent', 0),
                    'keywords': rule.get('keywords', []),
                    'action': f'Boost {", ".join(rule.get("keywords", [])[:3])} items by +{rule.get("increase_percent", 0)}%' if delta <= 14 else f'Prepare — {delta}d away',
                })
        except ValueError:
            continue

    upcoming.sort(key=lambda x: x['days_away'])

    # Seasonal strategy recommendations
    recs = []
    # Current quarter
    q = (now.month - 1) // 3 + 1
    if q == 4:  # Q4 Oct-Dec
        recs.append({'strategy': 'Q4 Holiday Push', 'description': 'Peak selling season. Maximize promotion on all inventory. Run markdowns on stale items. KAWS, Bearbrick, art prints are hot gift items.', 'priority': 'high'})
    elif q == 1:  # Q1 Jan-Mar
        recs.append({'strategy': 'Q1 New Year Reset', 'description': 'Post-holiday slowdown. Focus on Shepard Fairey (birthday Feb 13), David Bowie (Jan 8), Beatles memorabilia. Lower rates on non-seasonal items.', 'priority': 'medium'})
    elif q == 2:  # Q2 Apr-Jun
        recs.append({'strategy': 'Q2 Spring Sales', 'description': 'Beatles Breakup (Apr 8), Record Store Day (Apr 19), Music Memorabilia Season (June). Push signed vinyl, pickguards, and music items.', 'priority': 'medium'})
    elif q == 3:  # Q3 Jul-Sep
        recs.append({'strategy': 'Q3 Summer Slow + Apollo', 'description': 'Summer slowdown except Apollo 11 Anniversary (Jul 16-24). Push Space/NASA items hard in July. Art season starts Aug-Sep.', 'priority': 'medium'})

    return jsonify({
        'monthly_sales': monthly,
        'events_by_month': events_by_month,
        'upcoming': upcoming,
        'recommendations': recs,
        'total_events': len(rules),
        'current_quarter': f'Q{q}',
    })


# =============================================================================
# Feature: Pricing Rationale — AI Strategy Summary
# =============================================================================

@app.route('/api/pricing/rationale')
def get_pricing_rationale():
    """Generate qualitative pricing rationale per item with AI"""
    listings = ebay.get_all_listings()
    sold = ebay.get_sold_items(days_back=60)
    enriched = load_personal_inventory()
    promo_data = fetch_all_promotions()
    per_listing = promo_data.get('per_listing', {})
    rules = load_pricing_rules()
    now = datetime.now()
    mmdd = now.strftime('%m-%d')

    # Build enrichment lookup
    enriched_by_words = {}
    for item in enriched:
        name_words = set(re.findall(r'\w+', item['name'].lower()))
        name_words -= {'the', 'a', 'and', 'of', 'in', 'print', 'signed', 'obey', 'giant', 'shepard', 'fairey'}
        if len(name_words) >= 2:
            enriched_by_words[frozenset(list(name_words)[:6])] = item

    def find_enrichment(title):
        title_words = set(re.findall(r'\w+', title.lower()))
        title_words -= {'the', 'a', 'and', 'of', 'in', 'print', 'signed', 'obey', 'giant', 'shepard', 'fairey', 'new', 'rare', 'limited'}
        best, best_o = None, 0
        for key, item in enriched_by_words.items():
            o = len(title_words & key)
            if o >= 2 and o > best_o:
                best_o = o
                best = item
        return best

    # Sold analysis
    sold_by_month = {}
    for s in sold:
        m = s.get('end_time', '')[:7]
        if m:
            if m not in sold_by_month:
                sold_by_month[m] = {'count': 0, 'revenue': 0}
            sold_by_month[m]['count'] += 1
            sold_by_month[m]['revenue'] += s.get('price', 0)

    items = []
    for listing in listings:
        lid = listing['id']
        price = listing['price']
        title = listing['title']
        title_lower = title.lower()
        promo = per_listing.get(lid, {})
        ad_rate = promo.get('ad_rate', 0)

        # Category
        if 'shepard fairey' in title_lower or 'obey' in title_lower:
            cat = 'Shepard Fairey'
        elif 'death nyc' in title_lower:
            cat = 'Death NYC'
        elif 'kaws' in title_lower:
            cat = 'KAWS'
        elif 'banksy' in title_lower:
            cat = 'Banksy'
        else:
            cat = 'Other'

        # Enrichment
        en = find_enrichment(title)
        market_median = en.get('market_data', {}).get('median', 0) if en else 0
        supply_count = en.get('ebay_supply', {}).get('ebay_count', 0) if en else 0
        rec = en.get('ebay_supply', {}).get('recommendation', '') if en else ''

        # Calendar events
        active_event = ''
        upcoming_event = ''
        for rule in rules:
            keywords = rule.get('keywords', [])
            if any(kw.lower() in title_lower for kw in keywords):
                start = rule.get('start_date', '')
                end = rule.get('end_date', '')
                if start <= mmdd <= end:
                    active_event = f"{rule['name']} (+{rule.get('increase_percent', 0)}%)"
                else:
                    try:
                        ed = datetime.strptime(f"{now.year}-{start}", '%Y-%m-%d')
                        if ed < now:
                            ed = datetime.strptime(f"{now.year + 1}-{start}", '%Y-%m-%d')
                        delta = (ed - now).days
                        if 0 < delta <= 30:
                            upcoming_event = f"{rule['name']} in {delta}d"
                    except ValueError:
                        pass

        # Build rationale
        factors = []
        action = 'HOLD'
        change = 0

        if market_median > 0 and price < market_median * 0.75:
            change = round(market_median * 0.9 - price, 2)
            factors.append(f'Underpriced — ${price:.0f} vs median ${market_median:.0f}. Raise to capture value.')
            action = 'RAISE'
        elif market_median > 0 and price > market_median * 1.4:
            change = round(market_median * 1.1 - price, 2)
            factors.append(f'Above market — ${price:.0f} vs median ${market_median:.0f}. Reduce to improve sell-through.')
            action = 'REDUCE'

        if supply_count <= 2:
            factors.append(f'Low supply ({supply_count} on eBay) — scarcity supports premium pricing.')
            if action == 'HOLD':
                action = 'RAISE'
                change = round(price * 0.05, 2)
        elif supply_count >= 15:
            factors.append(f'High supply ({supply_count} competing) — consider competitive pricing or wait.')

        if active_event:
            factors.append(f'EVENT ACTIVE: {active_event} — demand elevated, hold or raise.')
            if action == 'HOLD':
                action = 'RAISE'
        elif upcoming_event:
            factors.append(f'UPCOMING: {upcoming_event} — start promoting now, hold price for event premium.')

        if ad_rate > 10:
            factors.append(f'High ad rate ({ad_rate}%) — eating margin. Consider reducing to 4-6% or switch to dynamic.')
        elif ad_rate == 0:
            factors.append('Not promoted — adding 3-5% CPS could increase visibility.')

        if rec == 'SELL NOW':
            factors.append('Market signal: SELL NOW — low supply, act fast.')
        elif rec == 'HOLD':
            factors.append('Market signal: HOLD — wait for better conditions.')

        if not factors:
            factors.append('Stable — no significant price change warranted at this time.')

        items.append({
            'listing_id': lid,
            'title': title[:80],
            'category': cat,
            'current_price': price,
            'market_median': market_median,
            'action': action,
            'suggested_change': change,
            'new_price': round(price + change, 2),
            'ad_rate': ad_rate,
            'supply': supply_count,
            'event': active_event or upcoming_event or '',
            'rationale': factors,
            'signal': rec,
        })

    # Sort: RAISE first, then REDUCE, then HOLD
    order = {'RAISE': 0, 'REDUCE': 1, 'HOLD': 2}
    items.sort(key=lambda x: order.get(x['action'], 2))

    # Summary
    raise_count = len([i for i in items if i['action'] == 'RAISE'])
    reduce_count = len([i for i in items if i['action'] == 'REDUCE'])
    hold_count = len([i for i in items if i['action'] == 'HOLD'])
    total_change = sum(i['suggested_change'] for i in items)

    return jsonify({
        'items': items,
        'total': len(items),
        'summary': {
            'raise': raise_count,
            'reduce': reduce_count,
            'hold': hold_count,
            'total_value_change': round(total_change, 2),
            'sold_trend': sold_by_month,
        }
    })


# =============================================================================
# Feature 1: Sell This Week — AI picks + optimized titles
# =============================================================================

@app.route('/api/sell-this-week')
def sell_this_week():
    """Pick 7 items most likely to sell this week with AI-optimized titles"""
    listings = ebay.get_all_listings()
    sold = ebay.get_sold_items(days_back=60)
    promo = fetch_all_promotions()
    per_listing = promo.get('per_listing', {})
    enriched = load_personal_inventory()
    rules = load_pricing_rules()
    now = datetime.now()
    mmdd = now.strftime('%m-%d')

    # Build enrichment lookup
    enriched_map = {}
    for item in enriched:
        words = set(re.findall(r'\w+', item['name'].lower()))
        words -= {'the', 'a', 'and', 'of', 'in', 'print', 'signed', 'obey', 'giant', 'shepard', 'fairey'}
        for l in listings:
            lw = set(re.findall(r'\w+', l['title'].lower()))
            lw -= {'the', 'a', 'and', 'of', 'in', 'print', 'signed', 'obey', 'giant', 'shepard', 'fairey', 'new', 'rare', 'limited'}
            if len(words & lw) >= 2:
                enriched_map[l['id']] = item
                break

    # Sold titles for velocity check
    sold_titles = set(s.get('title', '').lower()[:30] for s in sold)

    # Score each listing
    scored = []
    for l in listings:
        score = 0
        reasons = []
        watchers = l.get('watchers', 0)
        price = l['price']
        lid = l['id']
        title = l['title']
        t_lower = title.lower()

        # Watchers (strongest signal)
        if watchers >= 5:
            score += 40
            reasons.append(f'{watchers} watchers — high interest')
        elif watchers >= 3:
            score += 25
            reasons.append(f'{watchers} watchers')
        elif watchers >= 1:
            score += 10

        # Price sweet spot ($50-200 sells fastest)
        if 50 <= price <= 200:
            score += 15
            reasons.append('In $50-200 sweet spot')
        elif 200 < price <= 500:
            score += 10

        # Enrichment signal
        en = enriched_map.get(lid)
        if en:
            rec = en.get('ebay_supply', {}).get('recommendation', '')
            if rec == 'SELL NOW':
                score += 30
                reasons.append('SELL NOW — low supply')
            elif rec == 'GOOD TO SELL':
                score += 20
                reasons.append('Good market conditions')

        # Calendar event boost
        for rule in rules:
            if any(kw.lower() in t_lower for kw in rule.get('keywords', [])):
                try:
                    ed = datetime.strptime(f"{now.year}-{rule['start_date']}", '%Y-%m-%d')
                    if ed < now: ed = datetime.strptime(f"{now.year+1}-{rule['start_date']}", '%Y-%m-%d')
                    delta = (ed - now).days
                    if 0 <= delta <= 14:
                        score += 20
                        reasons.append(f'{rule["name"]} in {delta}d')
                except ValueError:
                    pass
                break

        # Promoted
        if lid in per_listing:
            score += 5
        else:
            reasons.append('Not promoted — add to campaign')

        # Generate optimized title
        attrs = extract_item_attributes(title)
        opt_title = title
        if len(title) < 60:
            additions = []
            if attrs['signed'] and 'signed' not in t_lower: additions.append('Signed')
            if attrs['numbered'] and 'numbered' not in t_lower: additions.append('Numbered')
            if 'obey' not in t_lower and 'shepard fairey' in t_lower: additions.append('Obey Giant')
            if additions:
                opt_title = title + ' ' + ' '.join(additions)

        promo_info = per_listing.get(lid, {})
        suggested_rate = promo_info.get('ad_rate', 0) or 8  # Default 8% if not promoted

        scored.append({
            'listing_id': lid,
            'title': title[:70],
            'optimized_title': opt_title[:80],
            'price': price,
            'watchers': watchers,
            'score': score,
            'reasons': reasons,
            'suggested_rate': suggested_rate,
            'url': l.get('url', ''),
        })

    scored.sort(key=lambda x: x['score'], reverse=True)

    return jsonify({
        'picks': scored[:7],
        'total_scored': len(scored),
    })


# =============================================================================
# Feature 2: Price Sniper — competitor monitoring + auto-compete
# =============================================================================

@app.route('/api/price-sniper')
def price_sniper():
    """Monitor competitors and suggest price adjustments"""
    listings = ebay.get_all_listings()
    results = []

    # Check top 15 highest-value items
    top = sorted(listings, key=lambda x: x['price'], reverse=True)[:15]

    for l in top:
        stop_words = {'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of', 'is', 'by', 'with', 'new', 'lot', 'rare', 'free', 'shipping'}
        words = [w for w in re.findall(r'\w+', l['title'].lower()) if w not in stop_words and len(w) > 2]
        query = ' '.join(words[:5])
        if not query:
            continue

        try:
            comps = search_ebay(query, l['price'] * 2, l['price'] * 0.3, limit=10)
            cheaper = [c for c in comps if c['price'] < l['price'] * 0.95]

            if cheaper:
                cheapest = min(cheaper, key=lambda x: x['price'])
                undercut = l['price'] - cheapest['price']

                # Suggest matching or undercutting
                match_price = round(cheapest['price'] - 1, 2)  # Undercut by $1
                floor = l['price'] * 0.7  # Never go below 70% of current

                results.append({
                    'listing_id': l['id'],
                    'title': l['title'][:55],
                    'your_price': l['price'],
                    'cheapest_comp': cheapest['price'],
                    'comp_title': cheapest.get('title', '')[:50],
                    'comp_url': cheapest.get('url', ''),
                    'undercut_by': round(undercut, 2),
                    'suggested_price': max(match_price, floor),
                    'action': 'lower' if match_price > floor else 'hold_floor',
                    'url': l.get('url', ''),
                })
        except Exception:
            continue

    results.sort(key=lambda x: x['undercut_by'], reverse=True)

    return jsonify({
        'undercuts': results,
        'checked': len(top),
        'undercut_count': len(results),
    })


# =============================================================================
# Feature 3: Buyer Outreach — offer to watchers
# =============================================================================

@app.route('/api/buyer-outreach')
def buyer_outreach():
    """Find items with watchers sitting 14+ days — suggest offers"""
    listings = ebay.get_all_listings()
    now = datetime.now()
    candidates = []

    for l in listings:
        watchers = l.get('watchers', 0)
        if watchers == 0:
            continue

        start = l.get('start_time', '')
        days_listed = 0
        if start:
            try:
                listed = datetime.fromisoformat(start[:19])
                days_listed = (now - listed).days
            except Exception:
                pass

        # Watchers + sitting = offer opportunity
        if watchers >= 1 and days_listed >= 14:
            discount = 10 if days_listed < 30 else 15 if days_listed < 60 else 20
            offer_price = round(l['price'] * (1 - discount / 100), 2)

            candidates.append({
                'listing_id': l['id'],
                'title': l['title'][:55],
                'price': l['price'],
                'watchers': watchers,
                'days_listed': days_listed,
                'discount_pct': discount,
                'offer_price': offer_price,
                'url': l.get('url', ''),
            })

    candidates.sort(key=lambda x: (-x['watchers'], -x['days_listed']))

    return jsonify({
        'candidates': candidates,
        'total': len(candidates),
    })


# =============================================================================
# Feature 4: Auction Alerts — price signals from major houses
# =============================================================================

@app.route('/api/auction-alerts')
def auction_alerts():
    """Check Google Calendar for auction events and compare to inventory"""
    gcal_events = get_google_calendar_events(
        (datetime.utcnow() - timedelta(days=30)).isoformat() + 'Z',
        (datetime.utcnow() + timedelta(days=60)).isoformat() + 'Z',
        100
    )

    auctions = []
    for e in gcal_events:
        title = e.get('title', '').lower()
        if any(kw in title for kw in ['auction', 'christie', 'sotheby', 'heritage', 'julien', 'bonham']):
            is_past = e.get('start', '') < datetime.now().strftime('%Y-%m-%d')
            auctions.append({
                'title': e.get('title', ''),
                'date': e.get('start', '')[:10],
                'past': is_past,
                'type': 'result' if is_past else 'upcoming',
                'action': 'Check hammer prices — compare to your inventory' if is_past else 'Monitor — results may affect your pricing',
            })

    auctions.sort(key=lambda x: x['date'])

    return jsonify({
        'auctions': auctions,
        'past': len([a for a in auctions if a['past']]),
        'upcoming': len([a for a in auctions if not a['past']]),
    })


# =============================================================================
# Feature 5: Cross-Platform Arbitrage
# =============================================================================

@app.route('/api/arbitrage')
def cross_platform_arbitrage():
    """Flag items that could sell for more on premium platforms"""
    listings = ebay.get_all_listings()
    enriched = load_personal_inventory()

    # Platform premium estimates by category
    platform_premiums = {
        'Shepard Fairey': {'1stDibs': 1.8, 'Artsy': 1.5, 'Chairish': 1.3},
        'Banksy': {'1stDibs': 2.0, 'Artsy': 1.7, 'Chairish': 1.4},
        'KAWS': {'1stDibs': 1.6, 'Artsy': 1.5, 'Chairish': 1.2},
        'Mr. Brainwash': {'1stDibs': 1.5, 'Artsy': 1.3, 'Chairish': 1.2},
        'Death NYC': {'1stDibs': 1.0, 'Artsy': 1.0, 'Chairish': 1.0},  # Not premium enough
    }

    # Platform fees
    platform_fees = {'eBay': 0.1312, '1stDibs': 0.20, 'Artsy': 0.15, 'Chairish': 0.30}

    opportunities = []
    for l in listings:
        t = l['title'].lower()
        price = l['price']

        if 'shepard fairey' in t or 'obey' in t: cat = 'Shepard Fairey'
        elif 'banksy' in t: cat = 'Banksy'
        elif 'kaws' in t: cat = 'KAWS'
        elif 'brainwash' in t: cat = 'Mr. Brainwash'
        else: continue  # Skip categories without premium platform potential

        premiums = platform_premiums.get(cat, {})
        best_platform = None
        best_net = price * (1 - platform_fees['eBay'])  # eBay net

        for platform, multiplier in premiums.items():
            if multiplier <= 1.0:
                continue
            est_price = round(price * multiplier)
            net = est_price * (1 - platform_fees.get(platform, 0.15))
            if net > best_net * 1.2:  # At least 20% better than eBay
                if not best_platform or net > best_net:
                    best_platform = platform
                    best_net = net

        if best_platform:
            ebay_net = round(price * (1 - platform_fees['eBay']))
            premium_price = round(price * premiums[best_platform])
            premium_net = round(premium_price * (1 - platform_fees[best_platform]))
            opportunities.append({
                'title': l['title'][:55],
                'category': cat,
                'ebay_price': price,
                'ebay_net': ebay_net,
                'platform': best_platform,
                'est_price': premium_price,
                'est_net': premium_net,
                'extra_profit': premium_net - ebay_net,
                'url': l.get('url', ''),
            })

    opportunities.sort(key=lambda x: x['extra_profit'], reverse=True)

    return jsonify({
        'opportunities': opportunities[:20],
        'total': len(opportunities),
        'total_extra_profit': sum(o['extra_profit'] for o in opportunities),
    })


# =============================================================================
# Feature 6: Trend Detection — price appreciation/decline
# =============================================================================

@app.route('/api/trends')
def trend_detection():
    """Analyze historical data for price trends per print title"""
    historical = load_historical_prices()
    kaws = load_kaws_data()

    # Group by title and calculate trend
    title_data = {}

    for rec in historical[:10000]:  # Sample for speed
        name = rec.get('name', '')[:40]
        price = rec.get('price', 0)
        date = rec.get('date', '')
        if not name or not price or price <= 0:
            continue

        if name not in title_data:
            title_data[name] = {'prices': [], 'dates': [], 'artist': 'Shepard Fairey'}
        title_data[name]['prices'].append(price)
        title_data[name]['dates'].append(date)

    for rec in kaws[:5000]:
        name = rec.get('name', '')[:40]
        price = rec.get('price', 0)
        date = rec.get('date', '')
        if not name or not price or price <= 0:
            continue

        if name not in title_data:
            title_data[name] = {'prices': [], 'dates': [], 'artist': 'KAWS'}
        title_data[name]['prices'].append(price)
        title_data[name]['dates'].append(date)

    # Calculate trends for titles with 3+ data points
    trends = []
    for name, data in title_data.items():
        if len(data['prices']) < 3:
            continue

        prices = data['prices']
        dates = sorted(data['dates'])

        # Simple trend: compare first half avg to second half avg
        mid = len(prices) // 2
        first_half = sum(prices[:mid]) / mid if mid > 0 else 0
        second_half = sum(prices[mid:]) / (len(prices) - mid) if len(prices) > mid else 0

        if first_half > 0:
            change_pct = round(((second_half / first_half) - 1) * 100)
        else:
            change_pct = 0

        direction = 'appreciating' if change_pct > 15 else 'declining' if change_pct < -15 else 'stable'

        if abs(change_pct) > 10:  # Only show meaningful trends
            trends.append({
                'title': name,
                'artist': data['artist'],
                'data_points': len(prices),
                'earliest': dates[0] if dates else '',
                'latest': dates[-1] if dates else '',
                'avg_early': round(first_half),
                'avg_recent': round(second_half),
                'change_pct': change_pct,
                'direction': direction,
                'current_median': round(sorted(prices)[len(prices)//2]),
            })

    appreciating = sorted([t for t in trends if t['direction'] == 'appreciating'], key=lambda x: x['change_pct'], reverse=True)
    declining = sorted([t for t in trends if t['direction'] == 'declining'], key=lambda x: x['change_pct'])

    return jsonify({
        'appreciating': appreciating[:15],
        'declining': declining[:15],
        'total_tracked': len(title_data),
        'total_trending': len(trends),
    })


# =============================================================================
# Feature: Seller Reports — Text Summary + HTML Analysis
# =============================================================================

@app.route('/api/reports/active-listings')
def report_active_listings():
    """Generate seller summary of active listings with hot items and events"""
    listings = ebay.get_all_listings()
    enriched = load_personal_inventory()
    promo_data = fetch_all_promotions()
    per_listing = promo_data.get('per_listing', {})
    rules = load_pricing_rules()
    now = datetime.now()
    mmdd = now.strftime('%m-%d')

    # Build enrichment lookup
    enriched_map = {}
    for item in enriched:
        words = set(re.findall(r'\w+', item['name'].lower()))
        words -= {'the', 'a', 'and', 'of', 'in', 'print', 'signed', 'obey', 'giant', 'shepard', 'fairey'}
        for l in listings:
            lw = set(re.findall(r'\w+', l['title'].lower()))
            lw -= {'the', 'a', 'and', 'of', 'in', 'print', 'signed', 'obey', 'giant', 'shepard', 'fairey', 'new', 'rare', 'limited'}
            if len(words & lw) >= 2:
                enriched_map[l['id']] = item
                break

    # Categorize
    cats = {}
    hot_items = []
    event_items = []
    unpromoted_high = []

    for l in listings:
        lid = l['id']
        title = l['title']
        price = l['price']
        title_lower = title.lower()

        if 'shepard fairey' in title_lower or 'obey' in title_lower: cat = 'Shepard Fairey'
        elif 'death nyc' in title_lower: cat = 'Death NYC'
        elif 'kaws' in title_lower: cat = 'KAWS'
        elif 'banksy' in title_lower: cat = 'Banksy'
        else: cat = 'Other'

        if cat not in cats:
            cats[cat] = {'count': 0, 'value': 0, 'promoted': 0}
        cats[cat]['count'] += 1
        cats[cat]['value'] += price
        if lid in per_listing:
            cats[cat]['promoted'] += 1

        # Hot items — enriched with SELL NOW signal
        en = enriched_map.get(lid)
        if en:
            rec = en.get('ebay_supply', {}).get('recommendation', '')
            if rec in ('SELL NOW', 'GOOD TO SELL'):
                hot_items.append({'title': title[:60], 'price': price, 'signal': rec, 'reason': en.get('ebay_supply', {}).get('reason', '')})

        # Event items
        for rule in rules:
            if any(kw.lower() in title_lower for kw in rule.get('keywords', [])):
                start = rule.get('start_date', '')
                try:
                    ed = datetime.strptime(f"{now.year}-{start}", '%Y-%m-%d')
                    if ed < now: ed = datetime.strptime(f"{now.year + 1}-{start}", '%Y-%m-%d')
                    delta = (ed - now).days
                    if 0 <= delta <= 30:
                        event_items.append({'title': title[:60], 'price': price, 'event': rule['name'], 'days': delta, 'tier': rule['tier']})
                except ValueError:
                    pass
                break

        # Unpromoted high value
        if lid not in per_listing and price >= 100:
            unpromoted_high.append({'title': title[:60], 'price': price})

    # Build text report
    total_value = sum(l['price'] for l in listings)
    promoted_count = len(per_listing)

    lines = []
    lines.append(f"DATARADAR ACTIVE LISTINGS REPORT — {now.strftime('%B %d, %Y')}")
    lines.append("=" * 60)
    lines.append(f"Total Active: {len(listings)} items | Value: ${total_value:,.0f}")
    lines.append(f"Promoted: {promoted_count}/{len(listings)} ({round(promoted_count/max(len(listings),1)*100)}%)")
    lines.append("")

    lines.append("CATEGORY BREAKDOWN:")
    for c, d in sorted(cats.items(), key=lambda x: -x[1]['value']):
        lines.append(f"  {c}: {d['count']} items, ${d['value']:,.0f}, {d['promoted']}/{d['count']} promoted")
    lines.append("")

    if hot_items:
        lines.append(f"🔥 HOT ITEMS — SELL NOW ({len(hot_items)}):")
        for h in sorted(hot_items, key=lambda x: x['price'], reverse=True)[:10]:
            lines.append(f"  ${h['price']:>8.0f}  {h['title']}  [{h['signal']}] {h['reason']}")
        lines.append("")

    if event_items:
        lines.append(f"📅 CALENDAR-BOOSTED ITEMS ({len(event_items)}):")
        for e in sorted(event_items, key=lambda x: x['days']):
            lines.append(f"  ${e['price']:>8.0f}  {e['title']}  → {e['event']} in {e['days']}d ({e['tier']})")
        lines.append("")

    if unpromoted_high:
        lines.append(f"⚠️  HIGH-VALUE UNPROMOTED ({len(unpromoted_high)}):")
        for u in sorted(unpromoted_high, key=lambda x: x['price'], reverse=True)[:10]:
            lines.append(f"  ${u['price']:>8.0f}  {u['title']}")
        lines.append("")

    lines.append("ACTION ITEMS:")
    lines.append(f"  1. {len(hot_items)} items ready to sell — list aggressively")
    lines.append(f"  2. {len(event_items)} items have events in <30 days — boost ad rates")
    lines.append(f"  3. {len(unpromoted_high)} high-value items not promoted — add to campaigns")
    if promoted_count < len(listings) * 0.8:
        lines.append(f"  4. Only {round(promoted_count/len(listings)*100)}% promoted — target 80%+")

    report_text = '\n'.join(lines)

    return jsonify({
        'text': report_text,
        'summary': {
            'total': len(listings), 'value': round(total_value, 2),
            'promoted': promoted_count, 'hot': len(hot_items),
            'event_boosted': len(event_items), 'unpromoted_high': len(unpromoted_high),
        },
        'hot_items': hot_items[:15],
        'event_items': event_items[:20],
        'unpromoted_high': unpromoted_high[:15],
        'categories': cats,
    })


@app.route('/api/reports/sales-analysis')
def report_sales_analysis():
    """Historical sales with full expense breakdown and HTML summary"""
    sold = ebay.get_sold_items(days_back=60)
    promo_data = fetch_all_promotions()
    per_listing = promo_data.get('per_listing', {})
    cost_basis = load_cost_basis()

    EBAY_FVF = 0.1312
    SHIPPING_EST = 8.0
    MIN_SALE_PRICE = 25  # Exclude sub-$25 sales — not real inventory, skews metrics

    items = []
    total_rev = 0
    total_cost = 0
    total_fees = 0
    total_ad = 0
    total_ship = 0
    total_profit = 0
    fast_count = 0
    promoted_sold = 0

    for s in sold:
        lid = s.get('id', '')
        price = s.get('price', 0)
        if price < MIN_SALE_PRICE:
            continue  # Skip sub-$25 sales — posters, stickers, not real inventory
        qty = s.get('quantity_sold', 1)
        dom = s.get('days_on_market')
        title = s.get('title', '')

        revenue = price * qty
        cost = cost_basis.get(lid, {}).get('cost', price * 0.4)
        fees = revenue * EBAY_FVF
        promo = per_listing.get(lid, {})
        ad_rate = promo.get('ad_rate', 0)
        ad_cost = revenue * (ad_rate / 100)
        funding = promo.get('funding_model', 'None')
        campaign = promo.get('campaign_name', '')
        shipping = SHIPPING_EST * qty
        profit = revenue - cost - fees - ad_cost - shipping
        margin = (profit / revenue * 100) if revenue else 0

        total_rev += revenue
        total_cost += cost
        total_fees += fees
        total_ad += ad_cost
        total_ship += shipping
        total_profit += profit
        if dom is not None and dom <= 7:
            fast_count += 1
        if ad_rate > 0:
            promoted_sold += 1

        items.append({
            'title': title[:60],
            'price': price,
            'revenue': round(revenue, 2),
            'cost_basis': round(cost, 2),
            'ebay_fees': round(fees, 2),
            'ad_rate': ad_rate,
            'ad_type': funding,
            'ad_cost': round(ad_cost, 2),
            'campaign': campaign,
            'shipping': round(shipping, 2),
            'profit': round(profit, 2),
            'margin': round(margin, 1),
            'dom': dom,
            'listed': s.get('start_time', '')[:10],
            'sold_date': s.get('end_time', '')[:10],
        })

    items.sort(key=lambda x: x['profit'], reverse=True)

    # What worked / what to change
    good = []
    change = []

    avg_dom = round(sum(i['dom'] for i in items if i['dom'] is not None) / len([i for i in items if i['dom'] is not None])) if items else 0
    avg_margin = round(total_profit / total_rev * 100, 1) if total_rev else 0

    if fast_count > len(items) * 0.3:
        good.append(f"{fast_count} items ({round(fast_count/len(items)*100)}%) sold in under 7 days — fast turnover")
    if avg_margin > 30:
        good.append(f"Average margin {avg_margin}% is healthy")
    if promoted_sold > len(items) * 0.5:
        good.append(f"{promoted_sold}/{len(items)} sold items were promoted — ads are driving sales")

    top_profit = items[0] if items else None
    if top_profit:
        good.append(f"Best sale: {top_profit['title']} — ${top_profit['profit']:.0f} profit ({top_profit['margin']:.0f}% margin)")

    # Category-specific analysis
    cat_analysis = {}
    for i in items:
        t = i['title'].lower()
        if 'shepard fairey' in t or 'obey' in t: cat = 'Shepard Fairey'
        elif 'death nyc' in t: cat = 'Death NYC'
        elif 'banksy' in t: cat = 'Banksy'
        elif 'kaws' in t: cat = 'KAWS'
        elif 'vinyl' in t or 'record' in t or 'album' in t: cat = 'Signed Music'
        elif 'apollo' in t or 'nasa' in t or 'astronaut' in t: cat = 'Space/NASA'
        else: cat = 'Other'
        if cat not in cat_analysis:
            cat_analysis[cat] = {'count': 0, 'revenue': 0, 'profit': 0, 'doms': [], 'prices': []}
        cat_analysis[cat]['count'] += 1
        cat_analysis[cat]['revenue'] += i['revenue']
        cat_analysis[cat]['profit'] += i['profit']
        cat_analysis[cat]['prices'].append(i['price'])
        if i['dom'] is not None:
            cat_analysis[cat]['doms'].append(i['dom'])

    # Category velocity benchmarks (from actual data)
    cat_benchmarks = {'Shepard Fairey': 16, 'Death NYC': 15, 'Banksy': 4, 'Signed Music': 12, 'Space/NASA': 22, 'Other': 21}

    for cat, d in cat_analysis.items():
        cat_avg_dom = round(sum(d['doms'])/len(d['doms'])) if d['doms'] else 0
        benchmark = cat_benchmarks.get(cat, 21)
        cat_avg_price = round(sum(d['prices'])/len(d['prices'])) if d['prices'] else 0
        cat_margin = round(d['profit']/d['revenue']*100) if d['revenue'] else 0

        if cat_avg_dom <= benchmark:
            good.append(f"{cat}: {d['count']} sold, avg {cat_avg_dom}d (at/below {benchmark}d benchmark), ${d['revenue']:,.0f} rev, {cat_margin}% margin")
        else:
            slow_in_cat = [dom for dom in d['doms'] if dom > benchmark * 1.5]
            change.append(f"{cat}: avg {cat_avg_dom}d to sell (benchmark {benchmark}d). {len(slow_in_cat)} outlier slow items. Avg price ${cat_avg_price}.")

    if total_ad > total_rev * 0.10:
        change.append(f"Promo fees are {round(total_ad/total_rev*100, 1)}% of revenue — target under 10%.")

    negative_profit = [i for i in items if i['profit'] < 0]
    if negative_profit:
        change.append(f"{len(negative_profit)} items sold at a loss.")

    if promoted_sold < len(items) * 0.3:
        change.append("Most sales are organic. Your old 15% promo rate strategy sold 3-4/day. Consider Speed-Medium or Speed-High strategy.")

    # Build HTML summary
    good_html = ''.join('<div class="good">✓ ' + g + '</div>' for g in good)
    change_html = ''.join('<div class="bad">✗ ' + c + '</div>' for c in change)

    rows_html = ''
    for i in items:
        ad_cls = 'red' if i['ad_cost'] > 20 else 'dim'
        prof_cls = 'green' if i['profit'] > 0 else 'red'
        marg_cls = 'green' if i['margin'] > 30 else ('red' if i['margin'] < 10 else '')
        dom_str = str(i['dom']) + 'd' if i['dom'] is not None else '--'
        rows_html += f'<tr><td>{i["title"]}</td><td class="green">${i["revenue"]:.0f}</td>'
        rows_html += f'<td class="dim">${i["cost_basis"]:.0f}</td><td class="dim">${i["ebay_fees"]:.0f}</td>'
        rows_html += f'<td class="dim">{i["ad_type"]}</td><td>{i["ad_rate"]}%</td>'
        rows_html += f'<td class="{ad_cls}">${i["ad_cost"]:.0f}</td><td class="dim">${i["shipping"]:.0f}</td>'
        rows_html += f'<td class="{prof_cls}" style="font-weight:700;">${i["profit"]:.0f}</td>'
        rows_html += f'<td class="{marg_cls}">{i["margin"]}%</td><td>{dom_str}</td><td class="dim">{i["sold_date"]}</td></tr>'

    generated = datetime.now().strftime('%B %d, %Y')
    html = f"""<!DOCTYPE html>
<html><head><title>DATARADAR Sales Analysis</title>
<style>
body {{ font-family: -apple-system, sans-serif; background: #000; color: #f5f5f7; padding: 40px; max-width: 1200px; margin: 0 auto; }}
h1 {{ font-size: 28px; letter-spacing: -0.5px; }}
h2 {{ font-size: 20px; margin-top: 32px; color: #86868b; }}
.stat {{ display: inline-block; background: #1c1c1e; border-radius: 12px; padding: 16px 24px; margin: 4px; text-align: center; }}
.stat .val {{ font-size: 24px; font-weight: 700; }}
.stat .lbl {{ font-size: 11px; color: #86868b; text-transform: uppercase; letter-spacing: 0.5px; }}
.good {{ color: #30d158; padding: 8px 0; border-bottom: 1px solid #38383a; font-size: 14px; }}
.bad {{ color: #ff453a; padding: 8px 0; border-bottom: 1px solid #38383a; font-size: 14px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 16px; }}
th {{ text-align: left; padding: 10px 8px; color: #86868b; font-size: 10px; text-transform: uppercase; border-bottom: 1px solid #38383a; }}
td {{ padding: 8px; border-bottom: 1px solid rgba(255,255,255,0.04); }}
.green {{ color: #30d158; }} .red {{ color: #ff453a; }} .dim {{ color: #86868b; }}
</style></head><body>
<h1>Sales Analysis Report</h1>
<p class="dim">Last 60 days — {len(items)} items sold — Generated {generated}</p>
<div>
<div class="stat"><div class="val green">${total_rev:,.0f}</div><div class="lbl">Revenue</div></div>
<div class="stat"><div class="val">${total_profit:,.0f}</div><div class="lbl">Net Profit</div></div>
<div class="stat"><div class="val">{avg_margin}%</div><div class="lbl">Margin</div></div>
<div class="stat"><div class="val">{len(items)}</div><div class="lbl">Items Sold</div></div>
<div class="stat"><div class="val">{avg_dom}d</div><div class="lbl">Avg Days to Sell</div></div>
<div class="stat"><div class="val red">${total_ad:,.0f}</div><div class="lbl">Promo Fees</div></div>
</div>
<h2>What's Working</h2>{good_html}
<h2>What to Change</h2>{change_html}
<h2>Full Sales Breakdown</h2>
<table><tr><th>Item</th><th>Sold $</th><th>Cost</th><th>eBay Fee</th><th>Promo Type</th><th>Promo %</th><th>Promo Fee</th><th>Ship</th><th>Profit</th><th>Margin</th><th>DOM</th><th>Date</th></tr>
{rows_html}</table></body></html>"""

    return jsonify({
        'items': items,
        'summary': {
            'total_sold': len(items), 'total_revenue': round(total_rev, 2),
            'total_cost': round(total_cost, 2), 'total_fees': round(total_fees, 2),
            'total_ad_spend': round(total_ad, 2), 'total_shipping': round(total_ship, 2),
            'total_profit': round(total_profit, 2), 'avg_margin': avg_margin,
            'avg_dom': avg_dom, 'fast_sales': fast_count, 'promoted_sold': promoted_sold,
        },
        'good': good,
        'change': change,
        'html': html,
    })


@app.route('/api/historical-analysis')
def historical_analysis():
    """Deep analysis of 132k+ Shepard Fairey + 44k KAWS historical records"""
    historical = load_historical_prices()
    kaws = load_kaws_data()

    from collections import defaultdict

    # Process all records
    by_title = defaultdict(lambda: {'prices': [], 'dates': [], 'signed': 0, 'unsigned': 0, 'medium': ''})
    by_theme = defaultdict(lambda: {'count': 0, 'prices': [], 'dates': []})
    by_year = defaultdict(lambda: {'count': 0, 'total': 0, 'prices': []})
    by_medium = defaultdict(lambda: {'count': 0, 'prices': []})
    by_signed = {'signed': {'count': 0, 'prices': []}, 'unsigned': {'count': 0, 'prices': []}}
    all_prices = []
    all_items = []

    def detect_theme(name):
        n = name.lower()
        if 'peace' in n or 'dove' in n: return 'Peace'
        if 'hope' in n: return 'Hope'
        if 'flower' in n or 'floral' in n or 'lotus' in n or 'rose' in n: return 'Floral'
        if 'mandala' in n: return 'Mandala'
        if 'andre' in n or 'giant' in n or 'obey icon' in n: return 'Andre/Giant'
        if 'revolution' in n: return 'Revolution'
        if 'war' in n or 'soldier' in n or 'military' in n: return 'War/Military'
        if 'music' in n or 'record' in n or 'guitar' in n: return 'Music'
        if 'flag' in n or 'america' in n or 'liberty' in n: return 'Americana'
        if 'woman' in n or 'girl' in n or 'goddess' in n: return 'Women/Portraits'
        if 'skull' in n or 'death' in n: return 'Dark/Skull'
        if 'mlk' in n or 'king' in n or 'obama' in n: return 'Political'
        return 'Other'

    for rec in historical:
        name = rec.get('name', '')[:50]
        price = rec.get('price', 0)
        date = rec.get('date', '')
        signed = rec.get('signed', False)
        medium = rec.get('medium', 'Unknown')

        if not price or price <= 0 or price > 50000:
            continue

        all_prices.append(price)
        all_items.append({'name': name, 'price': price, 'date': date, 'signed': signed, 'medium': medium, 'artist': 'Shepard Fairey'})

        by_title[name]['prices'].append(price)
        by_title[name]['dates'].append(date)
        if signed:
            by_title[name]['signed'] += 1
            by_signed['signed']['count'] += 1
            by_signed['signed']['prices'].append(price)
        else:
            by_title[name]['unsigned'] += 1
            by_signed['unsigned']['count'] += 1
            by_signed['unsigned']['prices'].append(price)
        by_title[name]['medium'] = medium

        theme = detect_theme(name)
        by_theme[theme]['count'] += 1
        by_theme[theme]['prices'].append(price)
        by_theme[theme]['dates'].append(date)

        if date and len(date) >= 4:
            year = date[:4]
            by_year[year]['count'] += 1
            by_year[year]['total'] += price
            by_year[year]['prices'].append(price)

        by_medium[medium]['count'] += 1
        by_medium[medium]['prices'].append(price)

    # Add KAWS
    for rec in kaws:
        price = rec.get('price', 0)
        if not price or price <= 0 or price > 50000:
            continue
        all_items.append({'name': rec.get('name', '')[:50], 'price': price, 'date': rec.get('date', ''), 'artist': 'KAWS'})

    # Top performing titles (by median price, min 3 sales)
    top_titles = []
    for title, data in by_title.items():
        if len(data['prices']) >= 3:
            p = sorted(data['prices'])
            top_titles.append({
                'title': title,
                'sales': len(p),
                'median': p[len(p)//2],
                'min': p[0],
                'max': p[-1],
                'signed_pct': round(data['signed'] / (data['signed'] + data['unsigned']) * 100) if (data['signed'] + data['unsigned']) > 0 else 0,
                'dates': sorted(data['dates'])[-3:],
            })

    # Theme analysis
    theme_analysis = {}
    for theme, data in by_theme.items():
        if data['count'] >= 5:
            p = sorted(data['prices'])
            theme_analysis[theme] = {
                'count': data['count'],
                'median': p[len(p)//2],
                'avg': round(sum(p)/len(p)),
                'min': p[0],
                'max': p[-1],
            }

    # Year trends
    year_trends = {}
    for year, data in sorted(by_year.items()):
        p = data['prices']
        year_trends[year] = {
            'count': data['count'],
            'avg': round(sum(p)/len(p)),
            'median': sorted(p)[len(p)//2],
            'total': round(data['total']),
        }

    # Medium analysis
    medium_analysis = {}
    for medium, data in by_medium.items():
        if data['count'] >= 5:
            p = sorted(data['prices'])
            medium_analysis[medium] = {
                'count': data['count'],
                'median': p[len(p)//2],
                'avg': round(sum(p)/len(p)),
            }

    # Signed premium
    signed_med = sorted(by_signed['signed']['prices'])[len(by_signed['signed']['prices'])//2] if by_signed['signed']['prices'] else 0
    unsigned_med = sorted(by_signed['unsigned']['prices'])[len(by_signed['unsigned']['prices'])//2] if by_signed['unsigned']['prices'] else 0
    signed_premium = round(((signed_med / max(unsigned_med, 1)) - 1) * 100) if unsigned_med else 0

    sorted_prices = sorted(all_prices)

    return jsonify({
        'total_records': len(all_items),
        'sf_records': len(historical),
        'kaws_records': len(kaws),
        'price_stats': {
            'min': sorted_prices[0] if sorted_prices else 0,
            'max': sorted_prices[-1] if sorted_prices else 0,
            'median': sorted_prices[len(sorted_prices)//2] if sorted_prices else 0,
            'avg': round(sum(all_prices)/len(all_prices)) if all_prices else 0,
        },
        'top_titles': sorted(top_titles, key=lambda x: x['median'], reverse=True)[:30],
        'themes': dict(sorted(theme_analysis.items(), key=lambda x: -x[1]['median'])),
        'year_trends': year_trends,
        'mediums': dict(sorted(medium_analysis.items(), key=lambda x: -x[1]['count'])),
        'signed_premium': signed_premium,
        'signed_median': signed_med,
        'unsigned_median': unsigned_med,
    })


@app.route('/api/historical-item')
def historical_item_detail():
    """Get all sales for a specific title — for drill-down charts"""
    title = request.args.get('title', '')
    if not title:
        return jsonify({'error': 'Missing title'}), 400

    historical = load_historical_prices()
    matches = []
    title_lower = title.lower()

    for rec in historical:
        if title_lower in rec.get('name', '').lower():
            if rec.get('price', 0) > 0:
                matches.append({
                    'name': rec.get('name', ''),
                    'price': rec['price'],
                    'date': rec.get('date', ''),
                    'signed': rec.get('signed', False),
                    'medium': rec.get('medium', ''),
                    'source': rec.get('source', ''),
                })

    matches.sort(key=lambda x: x.get('date', ''))

    return jsonify({
        'title': title,
        'sales': matches,
        'total': len(matches),
    })


# =============================================================================
# Feature: Auto-Feedback — LLM-generated, multi-model reviewed
# =============================================================================

# =============================================================================
# Fix 1: Manual Comp Mapping for Top Items
# =============================================================================

COMP_MAP_FILE = os.path.join(DATA_DIR, 'comp_mappings.json')


def load_comp_mappings():
    if os.path.exists(COMP_MAP_FILE):
        try:
            with open(COMP_MAP_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


@app.route('/api/comp-map', methods=['GET', 'POST'])
def manage_comp_map():
    """Get or set manual comp title mappings for top items"""
    if request.method == 'POST':
        data = request.get_json()
        mappings = load_comp_mappings()
        mappings[data['listing_id']] = {
            'comp_title': data.get('comp_title', ''),
            'artist': data.get('artist', ''),
            'min_price': data.get('min_price', 0),
            'updated': datetime.now().isoformat(),
        }
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(COMP_MAP_FILE, 'w') as f:
            json.dump(mappings, f, indent=2)
        return jsonify({'success': True})

    return jsonify(load_comp_mappings())


# =============================================================================
# Fix 3: Aggressive Caching Layer
# =============================================================================

_cache = {}
_cache_times = {}
CACHE_TTL = 300  # 5 minutes


def cached_get(key, fetch_fn, ttl=CACHE_TTL):
    """Cache any API call result for TTL seconds"""
    now = datetime.now()
    if key in _cache and key in _cache_times:
        age = (now - _cache_times[key]).total_seconds()
        if age < ttl:
            return _cache[key]
    result = fetch_fn()
    _cache[key] = result
    _cache_times[key] = now
    return result


@app.route('/api/cache/clear', methods=['POST'])
def clear_cache():
    """Clear all caches"""
    global _cache, _cache_times, _promotions_cache, _live_deals_cache
    _cache = {}
    _cache_times = {}
    _promotions_cache = None
    _live_deals_cache = None
    return jsonify({'success': True})


# =============================================================================
# Fix 5: Cost Basis Bulk Input
# =============================================================================

@app.route('/api/cost-basis/bulk', methods=['POST'])
def bulk_cost_basis():
    """Set cost basis for multiple items at once — by category or individually"""
    data = request.get_json()
    cb = load_cost_basis()

    if data.get('category_cost'):
        # Set all items in a category to a cost
        listings = ebay.get_all_listings()
        cat = data['category']
        cost = float(data['category_cost'])
        count = 0
        for l in listings:
            t = l['title'].lower()
            match = False
            if cat == 'Shepard Fairey' and ('shepard fairey' in t or 'obey' in t): match = True
            elif cat == 'Death NYC' and 'death nyc' in t: match = True
            elif cat == 'Banksy' and 'banksy' in t: match = True
            elif cat == 'KAWS' and 'kaws' in t: match = True
            elif cat == 'Space/NASA' and ('apollo' in t or 'nasa' in t or 'astronaut' in t): match = True
            if match:
                cb[l['id']] = {'cost': cost, 'updated': datetime.now().isoformat()}
                count += 1

        os.makedirs(DATA_DIR, exist_ok=True)
        with open(COST_BASIS_FILE, 'w') as f:
            json.dump(cb, f, indent=2)
        return jsonify({'success': True, 'updated': count})

    elif data.get('items'):
        # Individual items
        for item in data['items']:
            cb[item['listing_id']] = {'cost': float(item['cost']), 'updated': datetime.now().isoformat()}

        os.makedirs(DATA_DIR, exist_ok=True)
        with open(COST_BASIS_FILE, 'w') as f:
            json.dump(cb, f, indent=2)
        return jsonify({'success': True, 'updated': len(data['items'])})

    return jsonify({'error': 'Missing category_cost or items'}), 400


# =============================================================================
# Fix 6: Natural Language Query on Historical Data
# =============================================================================

@app.route('/api/query', methods=['POST'])
def natural_query():
    """Ask a question about historical data — LLM translates to data query"""
    data = request.get_json()
    question = data.get('question', '')
    if not question:
        return jsonify({'error': 'Missing question'}), 400

    # Build context from historical data
    historical = load_historical_prices()
    total = len(historical)

    # Sample data for LLM context
    sample = historical[:100]
    prices = [r['price'] for r in historical if r.get('price') and r['price'] > 0]
    signed_prices = [r['price'] for r in historical if r.get('signed') and r.get('price') and r['price'] > 0]

    context = f"""You have access to {total:,} Shepard Fairey historical sales records.
Fields: name, price, date, medium, signed (bool), source.
Price range: ${min(prices):.0f} — ${max(prices):.0f}, median ${sorted(prices)[len(prices)//2]:.0f}.
{len(signed_prices):,} are signed (median ${sorted(signed_prices)[len(signed_prices)//2]:.0f} vs unsigned).

Sample titles: {', '.join(set(r['name'][:30] for r in sample[:20]))}

User question: {question}

Answer the question using the data. Be specific with numbers. If the question asks for items matching criteria, list up to 10 with prices and dates. Keep response under 200 words."""

    claude_key = ENV.get('CLAUDE_API_KEY', '')
    answer = ''
    if claude_key:
        try:
            resp = requests.post('https://api.anthropic.com/v1/messages',
                headers={'x-api-key': claude_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                json={'model': 'claude-sonnet-4-5-20241022', 'max_tokens': 300, 'messages': [{'role': 'user', 'content': context}]},
                timeout=20)
            if resp.status_code == 200:
                answer = resp.json().get('content', [{}])[0].get('text', '')
        except Exception as e:
            answer = f'Query failed: {str(e)[:50]}'

    return jsonify({'question': question, 'answer': answer, 'records_searched': total})


@app.route('/api/feedback/overview')
def feedback_overview():
    """Get full feedback overview — pending for buyers AND sellers"""
    token = ebay.get_access_token()
    if not token:
        return jsonify({'error': 'Auth failed'}), 401

    import xml.etree.ElementTree as ET

    headers = {
        'X-EBAY-API-SITEID': '0',
        'X-EBAY-API-COMPATIBILITY-LEVEL': '967',
        'X-EBAY-API-IAF-TOKEN': token,
        'Content-Type': 'text/xml'
    }

    # Get sold items (feedback to leave FOR buyers)
    headers['X-EBAY-API-CALL-NAME'] = 'GetMyeBaySelling'
    xml_sold = '''<?xml version="1.0" encoding="utf-8"?>
    <GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
        <SoldList><Include>true</Include><DurationInDays>60</DurationInDays>
        <Pagination><EntriesPerPage>50</EntriesPerPage><PageNumber>1</PageNumber></Pagination>
        </SoldList></GetMyeBaySellingRequest>'''
    r_sold = requests.post('https://api.ebay.com/ws/api.dll', headers=headers, data=xml_sold)

    ns = {'e': 'urn:ebay:apis:eBLBaseComponents'}
    sold_items = []
    try:
        root = ET.fromstring(r_sold.text)
        for ot in root.findall('.//e:SoldList//e:OrderTransaction', ns):
            txn = ot.find('e:Transaction', ns) or ot.find('e:Order', ns)
            if txn is None: continue
            item = txn.find('e:Item', ns) or txn.find('.//e:Item', ns)
            buyer = txn.find('e:Buyer', ns)
            if item is None: continue

            buyer_id = ''
            if buyer is not None:
                uid = buyer.find('e:UserID', ns)
                if uid is not None: buyer_id = uid.text

            iid = item.find('e:ItemID', ns)
            title = item.find('e:Title', ns)
            price_el = txn.find('.//e:TransactionPrice', ns) or item.find('.//e:BuyItNowPrice', ns)
            txn_id_el = txn.find('e:TransactionID', ns)

            sold_items.append({
                'item_id': iid.text if iid is not None else '',
                'title': (title.text if title is not None else '')[:60],
                'price': float(price_el.text) if price_el is not None and price_el.text else 0,
                'buyer': buyer_id,
                'transaction_id': txn_id_el.text if txn_id_el is not None else '',
                'role': 'seller',
            })
    except Exception as e:
        print(f"Parse sold error: {e}")

    # Get purchased items (feedback to leave FOR sellers)
    headers['X-EBAY-API-CALL-NAME'] = 'GetMyeBayBuying'
    xml_bought = '''<?xml version="1.0" encoding="utf-8"?>
    <GetMyeBayBuyingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
        <WonList><Include>true</Include><DurationInDays>60</DurationInDays>
        <Pagination><EntriesPerPage>50</EntriesPerPage><PageNumber>1</PageNumber></Pagination>
        </WonList></GetMyeBayBuyingRequest>'''
    r_bought = requests.post('https://api.ebay.com/ws/api.dll', headers=headers, data=xml_bought)

    bought_items = []
    try:
        root = ET.fromstring(r_bought.text)
        for ot in root.findall('.//e:WonList//e:OrderTransaction', ns):
            txn = ot.find('e:Transaction', ns) or ot.find('e:Order', ns)
            if txn is None: continue
            item = txn.find('e:Item', ns) or txn.find('.//e:Item', ns)
            if item is None: continue

            seller_el = item.find('.//e:Seller/e:UserID', ns)
            seller_id = seller_el.text if seller_el is not None else ''

            iid = item.find('e:ItemID', ns)
            title = item.find('e:Title', ns)
            price_el = txn.find('.//e:TransactionPrice', ns) or item.find('.//e:BuyItNowPrice', ns) or item.find('.//e:CurrentPrice', ns)
            txn_id_el = txn.find('e:TransactionID', ns)

            bought_items.append({
                'item_id': iid.text if iid is not None else '',
                'title': (title.text if title is not None else '')[:60],
                'price': float(price_el.text) if price_el is not None and price_el.text else 0,
                'seller': seller_id,
                'transaction_id': txn_id_el.text if txn_id_el is not None else '',
                'role': 'buyer',
            })
    except Exception as e:
        print(f"Parse bought error: {e}")

    return jsonify({
        'for_buyers': sold_items,
        'for_sellers': bought_items,
        'total_buyer_feedback': len(sold_items),
        'total_seller_feedback': len(bought_items),
        'total': len(sold_items) + len(bought_items),
    })


@app.route('/api/feedback/pending')
def get_pending_feedback():
    """Get sold items that may need feedback"""
    sold = ebay.get_sold_items(days_back=30)
    items = []
    for s in sold:
        items.append({
            'item_id': s.get('id', ''),
            'title': s.get('title', '')[:60],
            'price': s.get('price', 0),
            'sold_date': s.get('end_time', '')[:10],
        })
    return jsonify({'items': items, 'total': len(items)})


@app.route('/api/feedback/generate', methods=['POST'])
def generate_feedback():
    """Generate feedback using Claude (write) + GPT (edit) for a batch of items"""
    data = request.get_json()
    items = data.get('items', [])

    claude_key = ENV.get('CLAUDE_API_KEY', '')
    openai_key = ENV.get('OPENAI_API_KEY', '')

    results = []

    for item in items[:20]:  # Max 20 at a time
        title = item.get('title', '')
        price = item.get('price', 0)
        buyer = item.get('buyer', '')
        role = item.get('role', 'seller')  # seller leaving for buyer, or buyer leaving for seller

        if role == 'seller':
            prompt = f"""Write a warm, PERSONAL eBay feedback comment (max 80 chars) as a SELLER thanking a buyer for purchasing this specific item: "{title}".

Be specific about the item — reference it by name. Make it feel like a real person wrote it, not a template.
Examples of the tone I want:
- "Thanks so much for buying the Peace Goddess! Hope you love it as much as we do"
- "So glad this Basquiat found a great home. Enjoy it!"
- "Appreciate you grabbing the signed print — it's a beauty. Enjoy!"

Do NOT use generic phrases like "Great buyer!" or "A++ transaction". Reference the actual item.
Return ONLY the feedback text, no quotes."""
        else:
            prompt = f"Write a short, genuine eBay feedback comment (max 80 chars) as a BUYER thanking the seller for this item: {title}. Be specific about the item. No generic phrases."

        # Step 1: Claude writes the draft
        draft = ''
        if claude_key:
            try:
                resp = requests.post('https://api.anthropic.com/v1/messages',
                    headers={'x-api-key': claude_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                    json={'model': 'claude-sonnet-4-5-20241022', 'max_tokens': 60, 'messages': [{'role': 'user', 'content': prompt}]},
                    timeout=10)
                if resp.status_code == 200:
                    draft = resp.json().get('content', [{}])[0].get('text', '').strip().strip('"')
            except Exception:
                pass

        if not draft:
            # Personal fallback that references the item
            short_title = re.sub(r'\b(signed|numbered|limited|edition|print|screen|obey|giant|art|framed)\b', '', title, flags=re.IGNORECASE).strip()
            short_title = re.sub(r'\s+', ' ', short_title).strip()[:30]
            draft = f"Thanks for the {short_title}! Hope you love it"

        # Step 2: GPT edits/polishes
        final = draft
        if openai_key and draft:
            try:
                edit_prompt = f"Edit this eBay feedback to be more natural and under 80 characters. Remove quotes. Just return the text, nothing else:\n\n{draft}"
                resp = requests.post('https://api.openai.com/v1/chat/completions',
                    headers={'Authorization': f'Bearer {openai_key}', 'Content-Type': 'application/json'},
                    json={'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': edit_prompt}], 'max_tokens': 40},
                    timeout=10)
                if resp.status_code == 200:
                    edited = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '').strip().strip('"')
                    if edited and len(edited) <= 80:
                        final = edited
            except Exception:
                pass

        # Ensure under 80 chars
        if len(final) > 80:
            final = final[:77] + '...'

        results.append({
            **item,
            'draft': draft,
            'final': final,
            'char_count': len(final),
        })

    return jsonify({'feedback': results, 'total': len(results)})


@app.route('/api/feedback/submit', methods=['POST'])
def submit_feedback():
    """Submit feedback to eBay for multiple items"""
    data = request.get_json()
    feedbacks = data.get('feedbacks', [])

    token = ebay.get_access_token()
    if not token:
        return jsonify({'error': 'eBay auth failed'}), 401

    headers = {
        'X-EBAY-API-SITEID': '0',
        'X-EBAY-API-COMPATIBILITY-LEVEL': '967',
        'X-EBAY-API-CALL-NAME': 'LeaveFeedback',
        'X-EBAY-API-IAF-TOKEN': token,
        'Content-Type': 'text/xml'
    }

    submitted = 0
    failed = 0
    errors = []

    for fb in feedbacks:
        item_id = fb.get('item_id', '')
        # target_user is who we're leaving feedback FOR — buyer OR seller
        target_user = fb.get('buyer', '') or fb.get('seller', '') or fb.get('target_user', '')
        comment = fb.get('comment', '')
        txn_id = fb.get('transaction_id', '')

        if not item_id or not target_user or not comment:
            failed += 1
            errors.append(f'{item_id}: missing item_id/target_user/comment')
            continue

        # Escape XML special chars in comment
        safe_comment = comment[:80].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        xml = f'''<?xml version="1.0" encoding="utf-8"?>
        <LeaveFeedbackRequest xmlns="urn:ebay:apis:eBLBaseComponents">
            <ItemID>{item_id}</ItemID>
            <CommentText>{safe_comment}</CommentText>
            <CommentType>Positive</CommentType>
            <TargetUser>{target_user}</TargetUser>
            {f'<TransactionID>{txn_id}</TransactionID>' if txn_id else ''}
        </LeaveFeedbackRequest>'''

        try:
            resp = requests.post('https://api.ebay.com/ws/api.dll', headers=headers, data=xml)
            if 'Success' in resp.text:
                submitted += 1
            else:
                failed += 1
                import re as _re
                err = _re.findall(r'<LongMessage>(.*?)</LongMessage>', resp.text)
                errors.append(f'{item_id}: {err[0][:60] if err else "unknown"}')
        except Exception as e:
            failed += 1
            errors.append(f'{item_id}: {str(e)[:40]}')

    return jsonify({'submitted': submitted, 'failed': failed, 'errors': errors[:5]})


@app.route('/reports/levers')
def levers_report():
    """What you can CONTROL — analysis of every variable that drives sales"""
    sold = [s for s in ebay.get_sold_items(days_back=60) if s['price'] >= 25]
    listings = ebay.get_all_listings()
    promo = fetch_all_promotions()
    per_listing = promo.get('per_listing', {})
    cost_basis = load_cost_basis()

    from collections import defaultdict
    import statistics

    EBAY_FEE = 0.1312
    SHIP = 8

    def detect_cat(t):
        t = t.lower()
        if 'shepard fairey' in t or 'obey' in t: return 'Shepard Fairey'
        elif 'death nyc' in t: return 'Death NYC'
        elif 'banksy' in t: return 'Banksy'
        elif 'kaws' in t: return 'KAWS'
        elif 'apollo' in t or 'nasa' in t or 'astronaut' in t: return 'Space/NASA'
        return 'Other'

    # ── LEVER 1: PRICE — what price points sell fastest? ──
    price_analysis = defaultdict(lambda: {'count': 0, 'doms': [], 'rev': 0, 'profit': 0})
    for s in sold:
        p = s['price']
        dom = s.get('days_on_market')
        cost = cost_basis.get(s['id'], {}).get('cost', p * 0.4)
        profit = p - cost - (p * EBAY_FEE) - SHIP

        if p < 75: bucket = 'Under $75'
        elif p < 150: bucket = '$75-150'
        elif p < 300: bucket = '$150-300'
        elif p < 500: bucket = '$300-500'
        else: bucket = '$500+'

        price_analysis[bucket]['count'] += 1
        price_analysis[bucket]['rev'] += p
        price_analysis[bucket]['profit'] += profit
        if dom is not None: price_analysis[bucket]['doms'].append(dom)

    # ── LEVER 2: AD RATE — what rate drives most sales? ──
    # Check current listings' ad rates vs which sold
    sold_titles = set(s['title'].lower()[:30] for s in sold)
    rate_analysis = defaultdict(lambda: {'total': 0, 'sold': 0, 'revenue': 0})
    for l in listings:
        p = per_listing.get(l['id'], {})
        rate = p.get('ad_rate', 0)
        was_sold = l['title'].lower()[:30] in sold_titles

        if rate == 0: bucket = '0% (none)'
        elif rate <= 3: bucket = '1-3%'
        elif rate <= 6: bucket = '4-6%'
        elif rate <= 10: bucket = '7-10%'
        else: bucket = '10%+'

        rate_analysis[bucket]['total'] += 1
        if was_sold:
            rate_analysis[bucket]['sold'] += 1
            rate_analysis[bucket]['revenue'] += l['price']

    # ── LEVER 3: DAY OF WEEK — when to list/end? ──
    dow_analysis = defaultdict(lambda: {'count': 0, 'rev': 0, 'avg_price': 0, 'doms': []})
    for s in sold:
        d = s.get('end_time', '')[:19]
        if d:
            try:
                dt = datetime.fromisoformat(d)
                day = dt.strftime('%A')
                dow_analysis[day]['count'] += 1
                dow_analysis[day]['rev'] += s['price']
                if s.get('days_on_market') is not None:
                    dow_analysis[day]['doms'].append(s['days_on_market'])
            except Exception:
                pass

    # ── LEVER 4: CATEGORY — which categories to invest in? ──
    cat_analysis = defaultdict(lambda: {'sold': 0, 'active': 0, 'rev': 0, 'profit': 0, 'doms': [], 'prices': []})
    for s in sold:
        cat = detect_cat(s.get('title', ''))
        cost = cost_basis.get(s['id'], {}).get('cost', s['price'] * 0.4)
        profit = s['price'] - cost - (s['price'] * EBAY_FEE) - SHIP
        cat_analysis[cat]['sold'] += 1
        cat_analysis[cat]['rev'] += s['price']
        cat_analysis[cat]['profit'] += profit
        cat_analysis[cat]['prices'].append(s['price'])
        if s.get('days_on_market') is not None:
            cat_analysis[cat]['doms'].append(s['days_on_market'])
    for l in listings:
        cat = detect_cat(l['title'])
        cat_analysis[cat]['active'] += 1

    # ── LEVER 5: TITLE LENGTH — does SEO matter? ──
    title_analysis = defaultdict(lambda: {'count': 0, 'doms': [], 'rev': 0})
    for s in sold:
        tlen = len(s.get('title', ''))
        if tlen < 40: bucket = 'Short (<40)'
        elif tlen < 60: bucket = 'Medium (40-60)'
        else: bucket = 'Long (60+)'
        title_analysis[bucket]['count'] += 1
        title_analysis[bucket]['rev'] += s['price']
        if s.get('days_on_market') is not None:
            title_analysis[bucket]['doms'].append(s['days_on_market'])

    # ── BUILD RECOMMENDATIONS ──
    recs = []

    # Price rec
    fastest_bucket = min(price_analysis.items(), key=lambda x: (sum(x[1]['doms'])/max(len(x[1]['doms']),1)) if x[1]['doms'] else 999)
    most_profit_bucket = max(price_analysis.items(), key=lambda x: x[1]['profit']/max(x[1]['count'],1))
    recs.append(f"PRICE: {fastest_bucket[0]} sells fastest ({round(sum(fastest_bucket[1]['doms'])/max(len(fastest_bucket[1]['doms']),1))}d avg). {most_profit_bucket[0]} is most profitable (${round(most_profit_bucket[1]['profit']/max(most_profit_bucket[1]['count'],1))}/item).")

    # Rate rec
    best_rate = max(rate_analysis.items(), key=lambda x: x[1]['sold']/max(x[1]['total'],1))
    recs.append(f"AD RATE: {best_rate[0]} has highest conversion ({best_rate[1]['sold']}/{best_rate[1]['total']} = {round(best_rate[1]['sold']/max(best_rate[1]['total'],1)*100)}%). Your old 15% strategy worked — the data proves it.")

    # Day rec
    best_day = max(dow_analysis.items(), key=lambda x: x[1]['rev'])
    worst_day = min(dow_analysis.items(), key=lambda x: x[1]['count'] if x[1]['count'] > 0 else 999)
    recs.append(f"TIMING: {best_day[0]} is your best day (${best_day[1]['rev']:,.0f} revenue). {worst_day[0]} is slowest. Schedule listings to end on {best_day[0]}.")

    # Category rec
    best_cat_roi = max(cat_analysis.items(), key=lambda x: x[1]['profit']/max(x[1]['rev'],1))
    fastest_cat = min(cat_analysis.items(), key=lambda x: (sum(x[1]['doms'])/max(len(x[1]['doms']),1)) if x[1]['doms'] else 999)
    recs.append(f"CATEGORY: {fastest_cat[0]} sells fastest ({round(sum(fastest_cat[1]['doms'])/max(len(fastest_cat[1]['doms']),1))}d). {best_cat_roi[0]} has best ROI ({round(best_cat_roi[1]['profit']/max(best_cat_roi[1]['rev'],1)*100)}% margin). Stock more of both.")

    # Build HTML
    def table_section(title, headers, rows):
        h = ''.join(f'<th class="{("r" if i > 0 else "")}">{h}</th>' for i, h in enumerate(headers))
        r = ''.join(f'<tr>{"".join(f"<td class=r>{c}</td>" if i > 0 else f"<td><b>{c}</b></td>" for i, c in enumerate(row))}</tr>' for row in rows)
        return f'<h2>{title}</h2><div style="overflow-x:auto;border-radius:12px;background:#1c1c1e;"><table><thead><tr>{h}</tr></thead><tbody>{r}</tbody></table></div>'

    # Price table
    price_rows = []
    for bucket in ['Under $75', '$75-150', '$150-300', '$300-500', '$500+']:
        d = price_analysis.get(bucket, {})
        avg_dom = round(sum(d.get('doms', []))/max(len(d.get('doms', [])), 1)) if d.get('doms') else '--'
        avg_profit = round(d.get('profit', 0)/max(d.get('count', 1), 1))
        price_rows.append([bucket, str(d.get('count', 0)), f"${d.get('rev', 0):,.0f}", f"{avg_dom}d" if avg_dom != '--' else '--', f"${avg_profit}"])

    # Rate table
    rate_rows = []
    for bucket in ['0% (none)', '1-3%', '4-6%', '7-10%', '10%+']:
        d = rate_analysis.get(bucket, {})
        conv = round(d.get('sold', 0)/max(d.get('total', 1), 1)*100, 1)
        rate_rows.append([bucket, str(d.get('total', 0)), str(d.get('sold', 0)), f"{conv}%", f"${d.get('revenue', 0):,.0f}"])

    # DOW table
    dow_rows = []
    for day in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']:
        d = dow_analysis.get(day, {})
        avg_dom = round(sum(d.get('doms', []))/max(len(d.get('doms', [])), 1)) if d.get('doms') else '--'
        dow_rows.append([day[:3], str(d.get('count', 0)), f"${d.get('rev', 0):,.0f}", f"{avg_dom}d" if avg_dom != '--' else '--'])

    # Category table
    cat_rows = []
    for cat, d in sorted(cat_analysis.items(), key=lambda x: -x[1]['rev']):
        avg_dom = round(sum(d['doms'])/max(len(d['doms']), 1)) if d['doms'] else '--'
        margin = round(d['profit']/max(d['rev'], 1)*100)
        sellthru = round(d['sold']/max(d['active'], 1)*100)
        cat_rows.append([cat, str(d['sold']), str(d['active']), f"${d['rev']:,.0f}", f"${round(d['profit']):,.0f}", f"{margin}%", f"{avg_dom}d" if avg_dom != '--' else '--', f"{sellthru}%"])

    # Title table
    title_rows = []
    for bucket in ['Short (<40)', 'Medium (40-60)', 'Long (60+)']:
        d = title_analysis.get(bucket, {})
        avg_dom = round(sum(d.get('doms', []))/max(len(d.get('doms', [])), 1)) if d.get('doms') else '--'
        title_rows.append([bucket, str(d.get('count', 0)), f"${d.get('rev', 0):,.0f}", f"{avg_dom}d" if avg_dom != '--' else '--'])

    recs_html = ''.join(f'<div style="background:#1c1c1e;border-radius:10px;padding:14px;margin-bottom:8px;border-left:3px solid #0a84ff;font-size:14px;line-height:1.6;">{r}</div>' for r in recs)

    html = f'''<!DOCTYPE html>
<html><head><title>Levers — What You Can Control</title>
<style>
body {{ font-family:-apple-system,sans-serif; background:#000; color:#f5f5f7; padding:30px; max-width:1200px; margin:0 auto; }}
h1 {{ font-size:28px; letter-spacing:-0.5px; }}
h2 {{ font-size:18px; margin-top:28px; margin-bottom:12px; color:#86868b; }}
.sub {{ font-size:13px; color:#86868b; margin-bottom:20px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ text-align:left; padding:10px 8px; color:#86868b; font-size:10px; text-transform:uppercase; border-bottom:1px solid rgba(255,255,255,0.08); }}
td {{ padding:9px 8px; border-bottom:1px solid rgba(255,255,255,0.03); }}
.r {{ text-align:right; font-variant-numeric:tabular-nums; }}
</style></head><body>
<h1>🎛 Levers — What You Can Control</h1>
<div class="sub">These are the variables that directly impact your sales. Adjust them to sell faster.</div>

<h2>🎯 Top Recommendations</h2>
{recs_html}

{table_section("💰 LEVER 1: Price — What Price Sells Fastest?", ["Price Range", "Sold", "Revenue", "Avg DOM", "Avg Profit"], price_rows)}

{table_section("📢 LEVER 2: Ad Rate — What Promotion Rate Converts?", ["Rate", "Listed", "Sold", "Conversion", "Revenue"], rate_rows)}

{table_section("📅 LEVER 3: Timing — Best Day to Sell?", ["Day", "Sold", "Revenue", "Avg DOM"], dow_rows)}

{table_section("📦 LEVER 4: Category — Where to Invest?", ["Category", "Sold", "Active", "Revenue", "Profit", "Margin", "Avg DOM", "Sell-Thru"], cat_rows)}

{table_section("✏️ LEVER 5: Title Length — Does SEO Matter?", ["Length", "Sold", "Revenue", "Avg DOM"], title_rows)}

</body></html>'''

    return html, 200, {'Content-Type': 'text/html'}


@app.route('/reports/what-worked')
def what_worked_report():
    """Line-by-line analysis of every sale — what worked, what didn't, by category"""
    sold = [s for s in ebay.get_sold_items(days_back=60) if s['price'] >= 25]
    promo = fetch_all_promotions()
    per_listing = promo.get('per_listing', {})
    cost_basis = load_cost_basis()

    from collections import defaultdict

    EBAY_FEE = 0.1312
    SHIP = 8

    def detect_cat(title):
        t = title.lower()
        if 'shepard fairey' in t or 'obey' in t: return 'Shepard Fairey'
        elif 'death nyc' in t: return 'Death NYC'
        elif 'banksy' in t: return 'Banksy'
        elif 'kaws' in t: return 'KAWS'
        elif 'apollo' in t or 'nasa' in t or 'astronaut' in t: return 'Space/NASA'
        elif ('vinyl' in t or 'record' in t) and 'signed' in t: return 'Signed Music'
        return 'Other'

    # Build items with full P&L
    items = []
    cats = defaultdict(lambda: {'items': [], 'rev': 0, 'profit': 0, 'fast': 0, 'slow': 0, 'doms': [], 'prices': []})

    for s in sold:
        cat = detect_cat(s.get('title', ''))
        price = s['price']
        dom = s.get('days_on_market')
        cost = cost_basis.get(s['id'], {}).get('cost', price * 0.4)
        fees = price * EBAY_FEE
        p = per_listing.get(s['id'], {})
        ad_rate = p.get('ad_rate', 0)
        ad_cost = price * (ad_rate / 100)
        profit = price - cost - fees - ad_cost - SHIP
        margin = round((profit / price) * 100, 1) if price else 0

        # What worked / didn't for this item
        verdict = ''
        if dom is not None and dom <= 7 and margin > 30:
            verdict = 'WINNER — fast sale, good margin'
        elif dom is not None and dom <= 7:
            verdict = 'FAST — sold quick but check margin'
        elif dom is not None and dom > 30:
            verdict = 'SLOW — sat too long, consider lower price or more promo'
        elif margin > 40:
            verdict = 'PROFITABLE — strong margin'
        elif margin < 10:
            verdict = 'LOW MARGIN — fees eating profit'
        elif profit < 0:
            verdict = 'LOSS — sold below cost'
        else:
            verdict = 'OK'

        item = {
            'title': s.get('title', '')[:55],
            'cat': cat,
            'price': price,
            'cost': round(cost),
            'fees': round(fees),
            'ad_rate': ad_rate,
            'ad_cost': round(ad_cost),
            'ship': SHIP,
            'profit': round(profit),
            'margin': margin,
            'dom': dom,
            'listed': s.get('start_time', '')[:10],
            'sold_date': s.get('end_time', '')[:10],
            'verdict': verdict,
        }
        items.append(item)

        cats[cat]['items'].append(item)
        cats[cat]['rev'] += price
        cats[cat]['profit'] += profit
        cats[cat]['prices'].append(price)
        if dom is not None:
            cats[cat]['doms'].append(dom)
            if dom <= 7: cats[cat]['fast'] += 1
            if dom > 21: cats[cat]['slow'] += 1

    # Sort items by date
    items.sort(key=lambda x: x['sold_date'], reverse=True)

    # Category verdicts
    cat_html = ''
    for cat, d in sorted(cats.items(), key=lambda x: -x[1]['rev']):
        n = len(d['items'])
        avg_p = round(sum(d['prices']) / n) if n else 0
        avg_dom = round(sum(d['doms']) / len(d['doms'])) if d['doms'] else 0
        margin = round((d['profit'] / d['rev']) * 100) if d['rev'] else 0
        winners = len([i for i in d['items'] if 'WINNER' in i['verdict']])
        losses = len([i for i in d['items'] if 'LOSS' in i['verdict'] or 'LOW' in i['verdict']])

        # Category verdict
        if d['fast'] > n * 0.5 and margin > 30:
            cat_verdict = '🟢 STRONG — fast sales, good margins. Keep stocking.'
        elif d['fast'] > n * 0.3:
            cat_verdict = '🟡 GOOD — decent velocity. Optimize pricing on slow items.'
        elif d['slow'] > n * 0.5:
            cat_verdict = '🔴 SLOW — most items sitting. Increase promo rates or lower prices.'
        elif margin < 20:
            cat_verdict = '🟠 LOW MARGIN — selling but not profitably. Raise prices or cut costs.'
        else:
            cat_verdict = '⚪ MODERATE — room for improvement.'

        cat_html += f'''<div style="background:#1c1c1e;border-radius:14px;padding:20px;margin-bottom:12px;border-left:4px solid {'#30d158' if '🟢' in cat_verdict else '#ff9f0a' if '🟡' in cat_verdict else '#ff453a' if '🔴' in cat_verdict else '#86868b'};">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <div style="font-size:18px;font-weight:700;">{cat}</div>
                <div style="font-size:22px;font-weight:800;color:#30d158;">${d["rev"]:,.0f}</div>
            </div>
            <div style="display:flex;gap:20px;margin:10px 0;font-size:13px;color:#86868b;">
                <span>{n} sold</span>
                <span>avg ${avg_p}</span>
                <span>{avg_dom}d avg</span>
                <span style="color:#30d158;">{d["fast"]} fast</span>
                <span style="color:#ff453a;">{d["slow"]} slow</span>
                <span>{margin}% margin</span>
                <span style="color:#30d158;">{winners} winners</span>
                <span style="color:#ff453a;">{losses} losses</span>
            </div>
            <div style="font-size:14px;margin-top:8px;">{cat_verdict}</div>
        </div>'''

    # All items table
    rows = ''
    for i in items:
        mc = '#30d158' if i['margin'] > 30 else '#ff9f0a' if i['margin'] > 10 else '#ff453a'
        dc = '#30d158' if i['dom'] is not None and i['dom'] <= 7 else '#ff453a' if i['dom'] is not None and i['dom'] > 21 else '#86868b'
        vc = '#30d158' if 'WINNER' in i['verdict'] else '#ff453a' if 'LOSS' in i['verdict'] or 'SLOW' in i['verdict'] else '#ff9f0a' if 'LOW' in i['verdict'] else '#86868b'

        rows += f'''<tr>
            <td>{i["sold_date"]}</td>
            <td style="font-size:11px;color:#0a84ff;">{i["cat"]}</td>
            <td title="{i['title']}">{i["title"]}</td>
            <td class="r" style="color:#30d158;font-weight:700;">${i["price"]:.0f}</td>
            <td class="r" style="color:#86868b;">${i["cost"]}</td>
            <td class="r" style="color:#86868b;">${i["fees"]}</td>
            <td class="r">{i["ad_rate"]}%</td>
            <td class="r" style="color:#ff9f0a;">${i["ad_cost"]}</td>
            <td class="r" style="color:#86868b;">${i["ship"]}</td>
            <td class="r" style="color:{mc};font-weight:700;">${i["profit"]}</td>
            <td class="r" style="color:{mc};">{i["margin"]}%</td>
            <td class="r" style="color:{dc};font-weight:600;">{str(i["dom"])+"d" if i["dom"] is not None else "--"}</td>
            <td style="font-size:11px;color:{vc};">{i["verdict"]}</td>
        </tr>'''

    total_rev = sum(i['price'] for i in items)
    total_profit = sum(i['profit'] for i in items)
    total_margin = round((total_profit / total_rev) * 100, 1) if total_rev else 0
    winners = len([i for i in items if 'WINNER' in i['verdict']])
    losses = len([i for i in items if 'LOSS' in i['verdict']])

    html = f'''<!DOCTYPE html>
<html><head><title>What Worked — Sales Analysis</title>
<style>
body {{ font-family:-apple-system,sans-serif; background:#000; color:#f5f5f7; padding:30px; max-width:1400px; margin:0 auto; }}
h1 {{ font-size:28px; letter-spacing:-0.5px; }}
.sub {{ font-size:13px; color:#86868b; margin-bottom:20px; }}
.stats {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:20px; }}
.stat {{ background:#1c1c1e; border-radius:12px; padding:14px 20px; text-align:center; flex:1; min-width:100px; }}
.stat .v {{ font-size:22px; font-weight:700; }}
.stat .l {{ font-size:10px; color:#86868b; text-transform:uppercase; letter-spacing:0.5px; margin-top:3px; }}
h2 {{ font-size:18px; margin-top:28px; margin-bottom:12px; color:#86868b; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th {{ text-align:left; padding:8px 6px; color:#86868b; font-size:9px; text-transform:uppercase; border-bottom:1px solid rgba(255,255,255,0.08); position:sticky; top:0; background:#000; }}
td {{ padding:7px 6px; border-bottom:1px solid rgba(255,255,255,0.03); }}
.r {{ text-align:right; font-variant-numeric:tabular-nums; }}
</style></head><body>
<h1>What Worked — Every Sale Analyzed</h1>
<div class="sub">{len(items)} sales · ${total_rev:,.0f} revenue · ${total_profit:,.0f} profit · {total_margin}% margin · Last 60 days</div>

<div class="stats">
    <div class="stat"><div class="v" style="color:#30d158;">${total_rev:,.0f}</div><div class="l">Revenue</div></div>
    <div class="stat"><div class="v">${total_profit:,.0f}</div><div class="l">Net Profit</div></div>
    <div class="stat"><div class="v">{total_margin}%</div><div class="l">Margin</div></div>
    <div class="stat"><div class="v" style="color:#30d158;">{winners}</div><div class="l">Winners</div></div>
    <div class="stat"><div class="v" style="color:#ff453a;">{losses}</div><div class="l">Losses</div></div>
    <div class="stat"><div class="v">{len(items)}</div><div class="l">Total Sold</div></div>
</div>

<h2>Category Verdicts</h2>
{cat_html}

<h2>Every Sale — Line by Line</h2>
<div style="overflow-x:auto;border-radius:12px;background:#1c1c1e;">
<table>
<thead><tr>
    <th>Date</th><th>Category</th><th>Item</th><th class="r">Sold $</th><th class="r">Cost</th>
    <th class="r">eBay Fee</th><th class="r">Ad%</th><th class="r">Ad Cost</th><th class="r">Ship</th>
    <th class="r">Profit</th><th class="r">Margin</th><th class="r">DOM</th><th>Verdict</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>
</div>
</body></html>'''

    return html, 200, {'Content-Type': 'text/html'}


@app.route('/reports/performance')
def performance_report():
    """Full visual performance analysis — what worked, what didn't"""
    sold = [s for s in ebay.get_sold_items(days_back=60) if s['price'] >= 25]
    promo = fetch_all_promotions()
    per_listing = promo.get('per_listing', {})

    def detect_cat(title):
        t = title.lower()
        if 'shepard fairey' in t or 'obey' in t: return 'Shepard Fairey'
        elif 'death nyc' in t: return 'Death NYC'
        elif 'banksy' in t: return 'Banksy'
        elif 'kaws' in t: return 'KAWS'
        elif 'apollo' in t or 'nasa' in t or 'astronaut' in t: return 'Space/NASA'
        elif ('vinyl' in t or 'record' in t) and 'signed' in t: return 'Signed Music'
        return 'Other'

    from collections import defaultdict
    import statistics

    # Build category data
    cats = defaultdict(lambda: {'prices': [], 'doms': [], 'items': []})
    dow_data = defaultdict(lambda: {'count': 0, 'revenue': 0})
    weekly_data = defaultdict(lambda: {'count': 0, 'revenue': 0})
    price_buckets = defaultdict(lambda: {'count': 0, 'doms': [], 'revenue': 0})
    dom_buckets = defaultdict(lambda: {'count': 0, 'revenue': 0, 'prices': []})

    for s in sold:
        cat = detect_cat(s.get('title', ''))
        price = s['price']
        dom = s.get('days_on_market')
        title = s.get('title', '')[:50]
        d = s.get('end_time', '')[:10]

        cats[cat]['prices'].append(price)
        cats[cat]['items'].append({'title': title, 'price': price, 'dom': dom, 'date': d})
        if dom is not None:
            cats[cat]['doms'].append(dom)

        # Day of week
        if d:
            try:
                dt = datetime.fromisoformat(d)
                day = dt.strftime('%A')
                dow_data[day]['count'] += 1
                dow_data[day]['revenue'] += price
                wk = d[:7]
                weekly_data[wk]['count'] += 1
                weekly_data[wk]['revenue'] += price
            except Exception:
                pass

        # Price buckets
        if price < 75: pb = '<$75'
        elif price < 150: pb = '$75-150'
        elif price < 300: pb = '$150-300'
        elif price < 500: pb = '$300-500'
        else: pb = '$500+'
        price_buckets[pb]['count'] += 1
        price_buckets[pb]['revenue'] += price
        if dom is not None:
            price_buckets[pb]['doms'].append(dom)

        # DOM buckets
        if dom is not None:
            if dom <= 3: db = '0-3 days'
            elif dom <= 7: db = '4-7 days'
            elif dom <= 14: db = '8-14 days'
            elif dom <= 30: db = '15-30 days'
            else: db = '30+ days'
            dom_buckets[db]['count'] += 1
            dom_buckets[db]['revenue'] += price
            dom_buckets[db]['prices'].append(price)

    total_rev = sum(s['price'] for s in sold)
    total_items = len(sold)

    # Build category comparison JSON for Chart.js
    cat_chart = {}
    for cat, d in cats.items():
        p = sorted(d['prices'])
        cat_chart[cat] = {
            'count': len(p),
            'min': min(p), 'max': max(p),
            'avg': round(sum(p)/len(p)),
            'median': p[len(p)//2],
            'avg_dom': round(sum(d['doms'])/len(d['doms'])) if d['doms'] else 0,
            'revenue': round(sum(p)),
            'fast': len([x for x in d['doms'] if x <= 7]),
            'slow': len([x for x in d['doms'] if x > 21]),
            'top_item': max(d['items'], key=lambda x: x['price']),
            'fastest': min(d['items'], key=lambda x: x['dom'] if x['dom'] is not None else 999),
        }

    # Build HTML
    # Category bars data
    cat_names = list(cat_chart.keys())
    cat_json = json.dumps(cat_chart)
    dow_json = json.dumps(dict(dow_data))
    weekly_json = json.dumps(dict(sorted(weekly_data.items())))
    pb_json = json.dumps(dict(price_buckets))
    db_json = json.dumps(dict(dom_buckets))

    # Top 10 and bottom 10 sales
    sorted_sales = sorted(sold, key=lambda x: x['price'], reverse=True)
    top10 = sorted_sales[:10]
    fast10 = sorted([s for s in sold if s.get('days_on_market') is not None], key=lambda x: x['days_on_market'])[:10]
    slow10 = sorted([s for s in sold if s.get('days_on_market') is not None], key=lambda x: x['days_on_market'], reverse=True)[:10]

    def item_rows(items):
        return ''.join(f'<tr><td>{s["title"][:45]}</td><td class="r g b">${s["price"]:.0f}</td><td class="r">{s.get("days_on_market","--")}d</td><td class="d">{s.get("end_time","")[:10]}</td></tr>' for s in items)

    html = f'''<!DOCTYPE html>
<html><head><title>Performance Analysis</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
body {{ font-family:-apple-system,sans-serif; background:#000; color:#f5f5f7; padding:30px; max-width:1400px; margin:0 auto; }}
h1 {{ font-size:28px; letter-spacing:-0.5px; margin-bottom:4px; }}
h2 {{ font-size:18px; margin-top:32px; color:#86868b; margin-bottom:12px; }}
.sub {{ font-size:13px; color:#86868b; margin-bottom:24px; }}
.stats {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:24px; }}
.stat {{ background:#1c1c1e; border-radius:12px; padding:16px 20px; text-align:center; flex:1; min-width:120px; }}
.stat .v {{ font-size:24px; font-weight:700; }}
.stat .l {{ font-size:10px; color:#86868b; text-transform:uppercase; letter-spacing:0.8px; margin-top:4px; }}
.charts {{ display:flex; gap:16px; flex-wrap:wrap; margin-bottom:24px; }}
.chart-box {{ flex:1; min-width:300px; background:#1c1c1e; border-radius:14px; padding:20px; }}
.chart-title {{ font-size:11px; color:#86868b; text-transform:uppercase; letter-spacing:0.8px; font-weight:600; margin-bottom:10px; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; margin-top:8px; }}
th {{ text-align:left; padding:8px; color:#86868b; font-size:9px; text-transform:uppercase; border-bottom:1px solid rgba(255,255,255,0.08); }}
td {{ padding:8px; border-bottom:1px solid rgba(255,255,255,0.03); }}
.r {{ text-align:right; font-variant-numeric:tabular-nums; }}
.g {{ color:#30d158; }} .o {{ color:#ff9f0a; }} .rd {{ color:#ff453a; }} .d {{ color:#86868b; }} .b {{ font-weight:700; }}
.insight {{ background:#1c1c1e; border-radius:10px; padding:14px; margin-bottom:8px; border-left:3px solid #0a84ff; font-size:13px; line-height:1.5; }}
.cat-card {{ background:#1c1c1e; border-radius:12px; padding:16px; margin-bottom:10px; }}
</style></head><body>
<h1>Performance Analysis</h1>
<div class="sub">{total_items} items sold · ${total_rev:,.0f} revenue · Last 60 days · Generated {datetime.now().strftime("%B %d, %Y")}</div>

<div class="stats">
    <div class="stat"><div class="v g">${total_rev:,.0f}</div><div class="l">Revenue</div></div>
    <div class="stat"><div class="v">{total_items}</div><div class="l">Items Sold</div></div>
    <div class="stat"><div class="v">${round(total_rev/max(total_items,1))}</div><div class="l">Avg Sale</div></div>
    <div class="stat"><div class="v">{len(cats)}</div><div class="l">Categories</div></div>
</div>

<h2>Category Comparison — Price Ranges</h2>
<div class="charts">
    <div class="chart-box"><div class="chart-title">Price Range by Category</div><canvas id="cat-range-chart" height="250"></canvas></div>
    <div class="chart-box"><div class="chart-title">Revenue by Category</div><canvas id="cat-rev-chart" height="250"></canvas></div>
</div>

<h2>Category Deep Dive</h2>
{''.join(f"""<div class="cat-card">
    <div style="display:flex;justify-content:space-between;align-items:center;">
        <div style="font-size:16px;font-weight:700;">{cat}</div>
        <div style="font-size:22px;font-weight:800;color:#30d158;">${d['revenue']:,}</div>
    </div>
    <div style="display:flex;gap:16px;margin-top:8px;font-size:13px;color:#86868b;">
        <span>{d['count']} sold</span>
        <span>${d['min']}—${d['max']}</span>
        <span>avg ${d['avg']}</span>
        <span>median ${d['median']}</span>
        <span>{d['avg_dom']}d avg</span>
        <span style="color:#30d158;">{d['fast']} fast</span>
        <span style="color:#ff453a;">{d['slow']} slow</span>
    </div>
    <div style="margin-top:6px;font-size:12px;">Top: {d['top_item']['title']} (${d['top_item']['price']:.0f}) · Fastest: {d['fastest']['title']} ({d['fastest']['dom']}d)</div>
</div>""" for cat, d in sorted(cat_chart.items(), key=lambda x: -x[1]['revenue']))}

<h2>When Things Sell</h2>
<div class="charts">
    <div class="chart-box"><div class="chart-title">Sales by Day of Week</div><canvas id="dow-chart" height="200"></canvas></div>
    <div class="chart-box"><div class="chart-title">Weekly Revenue Trend</div><canvas id="weekly-chart" height="200"></canvas></div>
</div>

<h2>What Price Sells Fastest?</h2>
<div class="charts">
    <div class="chart-box"><div class="chart-title">Speed by Price Range</div><canvas id="price-speed-chart" height="200"></canvas></div>
    <div class="chart-box"><div class="chart-title">Revenue by Days on Market</div><canvas id="dom-rev-chart" height="200"></canvas></div>
</div>

<h2>Top 10 Sales</h2>
<table><tr><th>Item</th><th class="r">Price</th><th class="r">DOM</th><th>Date</th></tr>{item_rows(top10)}</table>

<h2>10 Fastest Sales</h2>
<table><tr><th>Item</th><th class="r">Price</th><th class="r">DOM</th><th>Date</th></tr>{item_rows(fast10)}</table>

<h2>10 Slowest Sales</h2>
<table><tr><th>Item</th><th class="r">Price</th><th class="r">DOM</th><th>Date</th></tr>{item_rows(slow10)}</table>

<script>
const catData = {cat_json};
const dowData = {dow_json};
const weeklyData = {weekly_json};
const pbData = {pb_json};
const dbData = {db_json};

// Category range chart
const catNames = Object.keys(catData).sort((a,b) => catData[b].revenue - catData[a].revenue);
new Chart(document.getElementById('cat-range-chart'), {{
    type: 'bar',
    data: {{
        labels: catNames,
        datasets: [
            {{label:'Min', data:catNames.map(c=>catData[c].min), backgroundColor:'rgba(134,134,139,0.3)', borderRadius:4}},
            {{label:'Avg', data:catNames.map(c=>catData[c].avg), backgroundColor:'#0a84ff', borderRadius:4}},
            {{label:'Max', data:catNames.map(c=>catData[c].max), backgroundColor:'rgba(48,209,88,0.5)', borderRadius:4}},
        ]
    }},
    options: {{responsive:true, plugins:{{legend:{{labels:{{color:'#86868b'}}}}}}, scales:{{x:{{ticks:{{color:'#86868b'}},grid:{{display:false}}}}, y:{{ticks:{{color:'#86868b',callback:v=>'$'+v}},grid:{{color:'rgba(255,255,255,0.04)'}}}}}}}}
}});

// Revenue by category
new Chart(document.getElementById('cat-rev-chart'), {{
    type: 'doughnut',
    data: {{
        labels: catNames.map(c => c + ' $' + catData[c].revenue.toLocaleString()),
        datasets: [{{data:catNames.map(c=>catData[c].revenue), backgroundColor:['#0a84ff','#30d158','#ff9f0a','#ff453a','#bf5af2','#ffd60a','#86868b'], borderWidth:0}}]
    }},
    options: {{responsive:true, cutout:'55%', plugins:{{legend:{{position:'right',labels:{{color:'#86868b',font:{{size:10}},padding:6}}}}}}}}
}});

// Day of week
const days = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
new Chart(document.getElementById('dow-chart'), {{
    type: 'bar',
    data: {{
        labels: days.map(d=>d.substring(0,3)),
        datasets: [
            {{label:'Items', data:days.map(d=>(dowData[d]||{{}}).count||0), backgroundColor:'#0a84ff', borderRadius:4, yAxisID:'y'}},
            {{label:'Revenue', data:days.map(d=>(dowData[d]||{{}}).revenue||0), type:'line', borderColor:'#30d158', pointRadius:4, yAxisID:'y1'}}
        ]
    }},
    options: {{responsive:true, plugins:{{legend:{{labels:{{color:'#86868b'}}}}}},
        scales:{{x:{{ticks:{{color:'#86868b'}},grid:{{display:false}}}},
            y:{{ticks:{{color:'#86868b'}},grid:{{color:'rgba(255,255,255,0.04)'}}}},
            y1:{{position:'right',ticks:{{color:'#86868b',callback:v=>'$'+(v/1000).toFixed(0)+'k'}},grid:{{display:false}}}}}}}}
}});

// Weekly trend
const weeks = Object.keys(weeklyData);
new Chart(document.getElementById('weekly-chart'), {{
    type: 'line',
    data: {{labels:weeks, datasets:[{{data:weeks.map(w=>weeklyData[w].revenue), borderColor:'#30d158', backgroundColor:'rgba(48,209,88,0.1)', fill:true, tension:0.4, pointRadius:3}}]}},
    options: {{responsive:true, plugins:{{legend:{{display:false}}}}, scales:{{x:{{ticks:{{color:'#86868b'}},grid:{{display:false}}}}, y:{{ticks:{{color:'#86868b',callback:v=>'$'+v}},grid:{{color:'rgba(255,255,255,0.04)'}}}}}}}}
}});

// Price vs speed
const pbs = ['<$75','$75-150','$150-300','$300-500','$500+'];
new Chart(document.getElementById('price-speed-chart'), {{
    type: 'bar',
    data: {{
        labels: pbs,
        datasets: [{{label:'Avg Days', data:pbs.map(p=>{{const d=pbData[p]; return d&&d.doms&&d.doms.length ? Math.round(d.doms.reduce((a,b)=>a+b,0)/d.doms.length) : 0;}}), backgroundColor:pbs.map((p,i)=>['#30d158','#30d158','#0a84ff','#ff9f0a','#ff453a'][i]), borderRadius:4}}]
    }},
    options: {{responsive:true, plugins:{{legend:{{display:false}}}}, scales:{{x:{{ticks:{{color:'#86868b'}},grid:{{display:false}}}}, y:{{ticks:{{color:'#86868b',callback:v=>v+'d'}},grid:{{color:'rgba(255,255,255,0.04)'}}}}}}}}
}});

// DOM vs revenue
const dbs = ['0-3 days','4-7 days','8-14 days','15-30 days','30+ days'];
new Chart(document.getElementById('dom-rev-chart'), {{
    type: 'bar',
    data: {{
        labels: dbs,
        datasets: [{{label:'Revenue', data:dbs.map(d=>(dbData[d]||{{}}).revenue||0), backgroundColor:'#0a84ff', borderRadius:4}},
                   {{label:'Items', data:dbs.map(d=>(dbData[d]||{{}}).count||0), type:'line', borderColor:'#ff9f0a', pointRadius:4, yAxisID:'y1'}}]
    }},
    options: {{responsive:true, plugins:{{legend:{{labels:{{color:'#86868b'}}}}}},
        scales:{{x:{{ticks:{{color:'#86868b'}},grid:{{display:false}}}},
            y:{{ticks:{{color:'#86868b',callback:v=>'$'+v}},grid:{{color:'rgba(255,255,255,0.04)'}}}},
            y1:{{position:'right',ticks:{{color:'#86868b'}},grid:{{display:false}}}}}}}}
}});
</script>
</body></html>'''

    return html, 200, {'Content-Type': 'text/html'}


@app.route('/reports/sales')
def sales_report_page():
    """Serve the HTML sales report directly"""
    data = report_sales_analysis()
    html = json.loads(data.data).get('html', '<h1>No data</h1>')
    return html, 200, {'Content-Type': 'text/html'}


# =============================================================================
# Feature: Unified Calendar — eBay Listings + Google Calendar + Pricing Events
# =============================================================================

def get_google_calendar_events(time_min=None, time_max=None, max_results=100):
    """Pull events from Google Calendar"""
    try:
        import pickle
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        token_path = os.path.join(os.path.dirname(__file__), 'token.pickle')
        if not os.path.exists(token_path):
            return []

        with open(token_path, 'rb') as f:
            creds = pickle.load(f)

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, 'wb') as f:
                pickle.dump(creds, f)

        service = build('calendar', 'v3', credentials=creds)

        if not time_min:
            time_min = datetime.utcnow().replace(day=1).isoformat() + 'Z'
        if not time_max:
            # End of next month
            now = datetime.utcnow()
            if now.month == 12:
                time_max = datetime(now.year + 1, 2, 1).isoformat() + 'Z'
            else:
                time_max = datetime(now.year, now.month + 2, 1).isoformat() + 'Z'

        result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = []
        for e in result.get('items', []):
            start = e.get('start', {}).get('dateTime', e.get('start', {}).get('date', ''))
            end = e.get('end', {}).get('dateTime', e.get('end', {}).get('date', ''))
            summary = e.get('summary', '')

            # Categorize
            if summary.startswith('List:') or '📦' in summary:
                event_type = 'listing'
            elif 'AUCTION' in summary.upper() or 'DEADLINE' in summary.upper():
                event_type = 'auction'
            elif 'MONTH' in summary.upper():
                event_type = 'reminder'
            else:
                event_type = 'personal'

            events.append({
                'title': summary,
                'start': start[:16] if start else '',
                'end': end[:16] if end else '',
                'type': event_type,
                'source': 'google',
            })

        return events
    except Exception as e:
        print(f"Google Calendar error: {e}")
        return []


@app.route('/api/calendar/unified')
def get_unified_calendar():
    """All calendar sources merged — Google Calendar + eBay listings + pricing events"""
    year = int(request.args.get('year', datetime.now().year))
    month = request.args.get('month')

    # Time range
    if month:
        m = int(month)
        time_min = datetime(year, m, 1).isoformat() + 'Z'
        if m == 12:
            time_max = datetime(year + 1, 1, 1).isoformat() + 'Z'
        else:
            time_max = datetime(year, m + 1, 1).isoformat() + 'Z'
    else:
        time_min = datetime(year, 1, 1).isoformat() + 'Z'
        time_max = datetime(year, 12, 31).isoformat() + 'Z'

    all_events = []

    # 1. Google Calendar events
    gcal_events = get_google_calendar_events(time_min, time_max, 200)
    all_events.extend(gcal_events)

    # 2. Pricing events
    rules = load_pricing_rules()
    for rule in rules:
        start_mmdd = rule.get('start_date', '')
        end_mmdd = rule.get('end_date', '')
        all_events.append({
            'title': f"🔥 {rule['name']} (+{rule.get('increase_percent', 0)}%)",
            'start': f"{year}-{start_mmdd}",
            'end': f"{year}-{end_mmdd}",
            'type': 'pricing',
            'tier': rule.get('tier', 'MINOR'),
            'source': 'pricing',
            'keywords': rule.get('keywords', []),
        })

    # 3. eBay listing dates (when items were listed)
    listings = ebay.get_all_listings()
    for l in listings:
        if l.get('end_time'):
            all_events.append({
                'title': f"📦 {l['title'][:40]}",
                'start': l['end_time'][:10],
                'end': l['end_time'][:10],
                'type': 'listing_end',
                'source': 'ebay',
            })

    # Sort by start date
    all_events.sort(key=lambda x: x.get('start', ''))

    # Summary
    types = {}
    for e in all_events:
        t = e.get('type', 'other')
        types[t] = types.get(t, 0) + 1

    return jsonify({
        'events': all_events,
        'total': len(all_events),
        'types': types,
        'year': year,
    })


# =============================================================================
# Feature: Search & Analyze — Multi-AI Print Lookup
# =============================================================================

@app.route('/api/analyze', methods=['POST'])
def analyze_item():
    """Multi-AI analysis of a print/item — prices, velocity, buy/pass"""
    data = request.get_json()
    query = data.get('query', '')
    min_price = data.get('min_price', 0)
    do_enrich = data.get('enrich', False)
    if not query:
        return jsonify({'error': 'Missing query'}), 400

    # 1. Search eBay for active listings — apply min price floor
    active = search_ebay(query, 50000, min_price, limit=50)
    active = [a for a in active if not is_likely_fake(a.get('title',''), a.get('price',0), '')[0]]
    if min_price > 0:
        active = [a for a in active if a['price'] >= min_price]
    active_prices = [a['price'] for a in active if a['price'] > 0]

    # 2. Search historical data
    historical = lookup_historical_prices(query, limit=30)
    # Apply min price floor to historical too
    if min_price > 0:
        historical = [h for h in historical if h.get('price', 0) >= min_price]
    hist_prices = [h['price'] for h in historical if h.get('price') and h['price'] > 0]

    # 3. Search KAWS data if relevant
    if 'kaws' in query.lower():
        kaws = load_kaws_data()
        kaws_matches = []
        query_words = set(re.findall(r'\w+', query.lower()))
        for k in kaws:
            if min_price > 0 and k.get('price', 0) < min_price:
                continue
            name_words = set(re.findall(r'\w+', k.get('name', '').lower()))
            if len(query_words & name_words) >= 2:
                kaws_matches.append(k)
                if len(kaws_matches) >= 30:
                    break
        hist_prices += [k['price'] for k in kaws_matches if k.get('price')]
        historical += [{'name': k['name'], 'price': k['price'], 'date': k.get('date',''), 'source': 'WorthPoint'} for k in kaws_matches]

    # 4. Compute stats
    all_prices = active_prices + hist_prices
    stats = {}
    if all_prices:
        sorted_p = sorted(all_prices)
        stats = {
            'count': len(all_prices),
            'min': min(all_prices),
            'max': max(all_prices),
            'avg': round(sum(all_prices) / len(all_prices), 2),
            'median': sorted_p[len(sorted_p) // 2],
            'active_count': len(active_prices),
            'historical_count': len(hist_prices),
        }

    # 5. Velocity estimate
    velocity = 'Unknown'
    if len(active) > 10:
        velocity = 'High Supply — may be slow'
    elif len(active) > 5:
        velocity = 'Medium Supply'
    elif len(active) > 0:
        velocity = 'Low Supply — likely sells fast'
    else:
        velocity = 'Rare — no active listings found'

    # 6. Liquidity
    liquidity = 'High' if len(hist_prices) >= 10 else 'Medium' if len(hist_prices) >= 3 else 'Low'

    # 7. Last sale
    last_sale = None
    for h in sorted(historical, key=lambda x: x.get('date', ''), reverse=True):
        if h.get('price') and h.get('date'):
            last_sale = {'price': h['price'], 'date': h['date'], 'source': h.get('source', '')}
            break

    # 8. Buy/Pass recommendation
    recommendation = 'PASS'
    rec_reason = ''
    if stats:
        median = stats['median']
        if active_prices:
            cheapest = min(active_prices)
            if cheapest < median * 0.7:
                recommendation = 'BUY'
                rec_reason = f'Active listing at ${cheapest:.0f} is {round((1-cheapest/median)*100)}% below median ${median:.0f}'
            elif cheapest < median * 0.9:
                recommendation = 'CONSIDER'
                rec_reason = f'Active at ${cheapest:.0f}, median ${median:.0f} — decent deal if condition is good'
            else:
                recommendation = 'PASS'
                rec_reason = f'Active at ${cheapest:.0f}, median ${median:.0f} — no discount'

    # 9. Get AI commentary + enrichment
    ai_commentary = ''
    llm_enrichment = {}
    claude_key = ENV.get('CLAUDE_API_KEY', '')
    openai_key = ENV.get('OPENAI_API_KEY', '')

    if (claude_key or openai_key) and (stats or do_enrich):
        active_text = ', '.join(f'${int(p)}' for p in sorted(active_prices)[:8])
        hist_text = ', '.join(f'${int(p)}' for p in sorted(hist_prices)[:8])

        prompt = f"""You are an art/collectibles market expert. Analyze this item:

Item: {query}
Min price filter: ${min_price}
Active eBay listings: {len(active_prices)} (prices: {active_text})
Historical sales: {len(hist_prices)} (prices: {hist_text})
Last sale: {f"${last_sale['price']:.0f} on {last_sale['date']}" if last_sale else 'Unknown'}

TASKS:
1. Remove any prices that are clearly for a DIFFERENT product (fakes, knockoffs, stickers vs prints, wrong artist)
2. Give fair market value RANGE (low, mid, high)
3. Is this a BUY, PASS, or CONSIDER at each price point?
4. Note any data quality issues

Respond in JSON:
{{"fair_low": <num>, "fair_mid": <num>, "fair_high": <num>, "outlier_prices": [<prices to ignore>], "commentary": "<3 sentences>", "buy_below": <max buy price>, "data_issues": "<any problems with the comps>"}}"""

        # Claude
        if claude_key:
            try:
                resp = requests.post('https://api.anthropic.com/v1/messages',
                    headers={'x-api-key': claude_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                    json={'model': 'claude-sonnet-4-5-20241022', 'max_tokens': 300, 'messages': [{'role': 'user', 'content': prompt}]},
                    timeout=20)
                if resp.status_code == 200:
                    text = resp.json().get('content', [{}])[0].get('text', '')
                    match = re.search(r'\{[^}]+\}', text, re.DOTALL)
                    if match:
                        claude_result = json.loads(match.group())
                        llm_enrichment['claude'] = claude_result
                        ai_commentary = claude_result.get('commentary', '')
            except Exception as e:
                ai_commentary = f'Claude error: {str(e)[:50]}'

        # GPT
        if openai_key:
            try:
                resp = requests.post('https://api.openai.com/v1/chat/completions',
                    headers={'Authorization': f'Bearer {openai_key}', 'Content-Type': 'application/json'},
                    json={'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 300},
                    timeout=20)
                if resp.status_code == 200:
                    text = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
                    match = re.search(r'\{[^}]+\}', text, re.DOTALL)
                    if match:
                        gpt_result = json.loads(match.group())
                        llm_enrichment['gpt'] = gpt_result
                        if not ai_commentary:
                            ai_commentary = gpt_result.get('commentary', '')
            except Exception:
                pass

        # Consensus from both LLMs
        if llm_enrichment:
            models = list(llm_enrichment.values())
            llm_enrichment['consensus'] = {
                'fair_low': round(sum(m.get('fair_low', 0) for m in models) / len(models)),
                'fair_mid': round(sum(m.get('fair_mid', 0) for m in models) / len(models)),
                'fair_high': round(sum(m.get('fair_high', 0) for m in models) / len(models)),
                'buy_below': round(sum(m.get('buy_below', 0) for m in models) / len(models)),
            }

            # Combine data issues
            issues = [m.get('data_issues', '') for m in models if m.get('data_issues')]
            if issues:
                llm_enrichment['data_issues'] = ' | '.join(issues)

    return jsonify({
        'query': query,
        'stats': stats,
        'velocity': velocity,
        'liquidity': liquidity,
        'last_sale': last_sale,
        'recommendation': recommendation,
        'rec_reason': rec_reason,
        'ai_commentary': ai_commentary,
        'llm_enrichment': llm_enrichment,
        'min_price_applied': min_price,
        'active_listings': [{'title': a['title'][:60], 'price': a['price'], 'url': a.get('url','')} for a in active[:10]],
        'historical_sales': [{'name': h.get('name','')[:60], 'price': h.get('price',0), 'date': h.get('date',''), 'source': h.get('source','')} for h in historical[:15]],
        'price_distribution': sorted(all_prices),
    })


# =============================================================================
# Feature: Sales History Analysis — Rate Impact
# =============================================================================

@app.route('/api/analytics/sales-history')
def get_sales_history():
    """Analyze sold items — posting date, time to sale, promo type, rate impact"""
    sold = ebay.get_sold_items(days_back=60)
    promo_data = fetch_all_promotions()
    per_listing = promo_data.get('per_listing', {})

    items = []
    for s in sold:
        lid = s.get('id', '')
        promo = per_listing.get(lid, {})
        ad_rate = promo.get('ad_rate', 0)
        funding = promo.get('funding_model', 'Unknown')
        campaign = promo.get('campaign_name', '')
        is_promoted = ad_rate > 0

        items.append({
            'title': s.get('title', '')[:60],
            'price': s.get('price', 0),
            'listed': s.get('start_time', '')[:10],
            'sold': s.get('end_time', '')[:10],
            'days_to_sell': s.get('days_on_market'),
            'promoted': is_promoted,
            'ad_rate': ad_rate,
            'ad_type': funding,
            'campaign': campaign,
            'ad_cost': round(s.get('price', 0) * (ad_rate / 100), 2),
        })

    # Analysis
    promoted_sales = [i for i in items if i['promoted']]
    organic_sales = [i for i in items if not i['promoted']]
    avg_dom_promoted = round(sum(i['days_to_sell'] or 0 for i in promoted_sales) / len(promoted_sales)) if promoted_sales else 0
    avg_dom_organic = round(sum(i['days_to_sell'] or 0 for i in organic_sales) / len(organic_sales)) if organic_sales else 0

    return jsonify({
        'items': items,
        'total_sold': len(items),
        'total_revenue': round(sum(i['price'] for i in items), 2),
        'promoted_sold': len(promoted_sales),
        'organic_sold': len(organic_sales),
        'avg_dom_promoted': avg_dom_promoted,
        'avg_dom_organic': avg_dom_organic,
        'total_ad_cost': round(sum(i['ad_cost'] for i in items), 2),
    })


# =============================================================================
# Feature: Ad Rate Strategy Presets (Low/Medium/High)
# =============================================================================

# eBay suggested rates by category (what eBay recommends)
EBAY_SUGGESTED_RATES = {
    'Shepard Fairey': 12.0, 'Death NYC': 10.0, 'KAWS': 14.0,
    'Banksy': 14.0, 'Bearbrick': 11.0, 'Mr. Brainwash': 12.0,
    'Space/NASA': 8.0, 'Signed Music': 7.0, 'Pickguard': 7.0, 'Other': 8.0,
}

AD_STRATEGY_PRESETS = {
    'profit': {
        'name': 'Maximize Profit',
        'description': 'Keep margins 40%+. Low ad rates, promote only high-margin items. Organic-first.',
        'default_rate': 3.0,
        'category_rates': {
            'Shepard Fairey': 3.0, 'Death NYC': 2.0, 'KAWS': 4.0,
            'Banksy': 4.0, 'Bearbrick': 3.0, 'Mr. Brainwash': 3.0,
            'Space/NASA': 2.0, 'Signed Music': 2.0, 'Other': 2.0,
        },
        'promote_pct': 40,
        'margin_target': 40,
    },
    'steady': {
        'name': 'Few Per Week',
        'description': 'Sell 3-5 items/week steadily. Moderate rates, target profitable categories.',
        'default_rate': 6.0,
        'category_rates': {
            'Shepard Fairey': 6.0, 'Death NYC': 4.0, 'KAWS': 8.0,
            'Banksy': 5.0, 'Bearbrick': 6.0, 'Mr. Brainwash': 6.0,
            'Space/NASA': 5.0, 'Signed Music': 5.0, 'Other': 4.0,
        },
        'promote_pct': 60,
        'margin_target': 35,
    },
    'speed_low': {
        'name': 'Speed — Low',
        'description': 'Move inventory faster. eBay suggested rates, 70% promoted. Keep 35%+ margin.',
        'default_rate': 8.0,
        'category_rates': {
            'Shepard Fairey': 8.0, 'Death NYC': 6.0, 'KAWS': 10.0,
            'Banksy': 10.0, 'Bearbrick': 8.0, 'Mr. Brainwash': 8.0,
            'Space/NASA': 6.0, 'Signed Music': 5.0, 'Other': 5.0,
        },
        'promote_pct': 70,
        'margin_target': 35,
    },
    'speed_medium': {
        'name': 'Speed — Medium',
        'description': 'eBay suggested rates by category. 80% promoted. Your old pace of ~1/day.',
        'default_rate': 12.0,
        'category_rates': {k: v for k, v in EBAY_SUGGESTED_RATES.items()},
        'promote_pct': 80,
        'margin_target': 30,
    },
    'speed_high': {
        'name': 'Speed — High',
        'description': 'eBay suggested + 3-5% boost on key items. 90%+ promoted. Goal: 3-4 sales/day. Your old winning strategy.',
        'default_rate': 15.0,
        'category_rates': {k: v + 3 for k, v in EBAY_SUGGESTED_RATES.items()},
        'promote_pct': 90,
        'margin_target': 25,
    },
}


CATEGORY_STRATEGY_FILE = os.path.join(DATA_DIR, 'category_strategies.json')


@app.route('/api/promotions/strategy')
def get_ad_strategies():
    """Get strategy presets + per-category overrides"""
    # Load saved category strategies
    cat_strats = {}
    if os.path.exists(CATEGORY_STRATEGY_FILE):
        try:
            with open(CATEGORY_STRATEGY_FILE, 'r') as f:
                cat_strats = json.load(f)
        except Exception:
            pass

    return jsonify({
        'presets': AD_STRATEGY_PRESETS,
        'category_strategies': cat_strats,
        'ebay_suggested': EBAY_SUGGESTED_RATES,
    })


@app.route('/api/promotions/category-strategy', methods=['POST'])
def set_category_strategy():
    """Set strategy per category — e.g., Banksy=speed_high, Death NYC=profit"""
    data = request.get_json()
    # data = {category: strategy_level, ...}

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CATEGORY_STRATEGY_FILE, 'w') as f:
        json.dump(data, f, indent=2)

    return jsonify({'success': True})


@app.route('/api/promotions/apply-category-strategies', methods=['POST'])
def apply_category_strategies():
    """Apply per-category strategies to all listings and push to eBay"""
    cat_strats = {}
    if os.path.exists(CATEGORY_STRATEGY_FILE):
        try:
            with open(CATEGORY_STRATEGY_FILE, 'r') as f:
                cat_strats = json.load(f)
        except Exception:
            pass

    listings = ebay.get_all_listings()
    headers = get_marketing_headers()
    if not headers:
        return jsonify({'error': 'eBay auth failed'}), 401

    # Group listings by strategy
    groups = {}  # strategy_key -> {rate, listings}
    for l in listings:
        t = l['title'].lower()
        if 'shepard fairey' in t or 'obey' in t: cat = 'Shepard Fairey'
        elif 'death nyc' in t: cat = 'Death NYC'
        elif 'banksy' in t: cat = 'Banksy'
        elif 'kaws' in t: cat = 'KAWS'
        elif 'bearbrick' in t: cat = 'Bearbrick'
        elif 'brainwash' in t: cat = 'Mr. Brainwash'
        elif 'apollo' in t or 'nasa' in t or 'astronaut' in t: cat = 'Space/NASA'
        elif ('vinyl' in t or 'record' in t or 'album' in t) and 'signed' in t: cat = 'Signed Music'
        elif 'pickguard' in t: cat = 'Pickguard'
        else: cat = 'Other'

        strat_level = cat_strats.get(cat, 'steady')
        strat = AD_STRATEGY_PRESETS.get(strat_level, AD_STRATEGY_PRESETS['steady'])
        rate = strat['category_rates'].get(cat, strat['default_rate'])

        # Ensure minimum 2%
        rate = max(rate, 2.0)

        key = f"{strat_level}_{rate}"
        if key not in groups:
            groups[key] = {'rate': rate, 'level': strat_level, 'listings': []}
        groups[key]['listings'].append(l['id'])

    # Create campaigns per group
    applied = 0
    failed = 0
    campaigns = 0

    for key, group in groups.items():
        try:
            campaign_body = {
                'campaignName': f'DR {group["level"]} {group["rate"]}% {datetime.now().strftime("%m/%d")}',
                'marketplaceId': 'EBAY_US',
                'startDate': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                'fundingStrategy': {
                    'fundingModel': 'COST_PER_SALE',
                    'bidPercentage': str(group['rate']),
                }
            }

            resp = requests.post('https://api.ebay.com/sell/marketing/v1/ad_campaign', headers=headers, json=campaign_body)
            if resp.status_code in (200, 201):
                cid = resp.headers.get('Location', '').split('/')[-1]
                campaigns += 1
                for lid in group['listings']:
                    try:
                        r = requests.post(f'https://api.ebay.com/sell/marketing/v1/ad_campaign/{cid}/ad',
                            headers=headers, json={'listingId': lid, 'bidPercentage': str(group['rate'])})
                        if r.status_code in (200, 201) or '35036' in r.text:
                            applied += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1
            else:
                failed += len(group['listings'])
        except Exception:
            failed += len(group['listings'])

    global _promotions_cache
    _promotions_cache = None

    return jsonify({'success': True, 'applied': applied, 'failed': failed, 'campaigns': campaigns})


@app.route('/api/analytics/insights')
def get_data_insights():
    """Mine historical data for actionable insights"""
    sold = ebay.get_sold_items(days_back=60)
    listings = ebay.get_all_listings()

    def detect_cat(title):
        t = title.lower()
        if 'shepard fairey' in t or 'obey' in t: return 'Shepard Fairey'
        elif 'death nyc' in t: return 'Death NYC'
        elif 'banksy' in t: return 'Banksy'
        elif 'kaws' in t: return 'KAWS'
        elif ('vinyl' in t or 'record' in t or 'album' in t) and 'signed' in t: return 'Signed Music'
        elif 'apollo' in t or 'nasa' in t or 'astronaut' in t: return 'Space/NASA'
        elif 'pickguard' in t: return 'Pickguard'
        return 'Other'

    # Filter
    sold_clean = [s for s in sold if s['price'] >= 25]

    # 1. Price sweet spots
    price_buckets = {'Under $50': [], '$50-100': [], '$100-200': [], '$200-500': [], '$500+': []}
    for s in sold_clean:
        p = s['price']
        if p < 50: price_buckets['Under $50'].append(s)
        elif p < 100: price_buckets['$50-100'].append(s)
        elif p < 200: price_buckets['$100-200'].append(s)
        elif p < 500: price_buckets['$200-500'].append(s)
        else: price_buckets['$500+'].append(s)

    sweet_spots = {}
    for name, items in price_buckets.items():
        if items:
            doms = [i['days_on_market'] for i in items if i.get('days_on_market') is not None]
            sweet_spots[name] = {
                'count': len(items),
                'avg_dom': round(sum(doms)/len(doms)) if doms else 0,
                'revenue': round(sum(i['price'] for i in items)),
            }

    # 2. Category velocity
    cat_velocity = {}
    for s in sold_clean:
        cat = detect_cat(s.get('title', ''))
        if cat not in cat_velocity:
            cat_velocity[cat] = {'fast': 0, 'slow': 0, 'total': 0, 'revenue': 0, 'fast_prices': [], 'slow_prices': []}
        cat_velocity[cat]['total'] += 1
        cat_velocity[cat]['revenue'] += s['price']
        dom = s.get('days_on_market')
        if dom is not None:
            if dom <= 7:
                cat_velocity[cat]['fast'] += 1
                cat_velocity[cat]['fast_prices'].append(s['price'])
            elif dom > 21:
                cat_velocity[cat]['slow'] += 1
                cat_velocity[cat]['slow_prices'].append(s['price'])

    # 3. Fastest sellers
    fastest = sorted([s for s in sold_clean if s.get('days_on_market') is not None],
                     key=lambda x: x['days_on_market'])[:10]

    # 4. Insights
    insights = []

    # Fast sellers are cheaper
    for cat, d in cat_velocity.items():
        if d['fast_prices'] and d['slow_prices']:
            avg_fast = round(sum(d['fast_prices'])/len(d['fast_prices']))
            avg_slow = round(sum(d['slow_prices'])/len(d['slow_prices']))
            if avg_fast < avg_slow:
                insights.append({
                    'type': 'pricing',
                    'title': f'{cat}: lower-priced items sell faster',
                    'detail': f'Fast sales avg ${avg_fast} vs slow sales avg ${avg_slow}. Consider pricing more items in the ${avg_fast} range for faster turnover.',
                    'category': cat,
                })
            else:
                insights.append({
                    'type': 'pricing',
                    'title': f'{cat}: higher-priced items sell just as fast',
                    'detail': f'Fast: ${avg_fast} avg vs slow: ${avg_slow} avg. Price is not the bottleneck — promote harder.',
                    'category': cat,
                })

    # Banksy sells fast
    if cat_velocity.get('Banksy', {}).get('fast', 0) > cat_velocity.get('Banksy', {}).get('total', 1) * 0.7:
        insights.append({
            'type': 'opportunity',
            'title': 'Banksy items sell extremely fast — stock more',
            'detail': f'{cat_velocity["Banksy"]["fast"]}/{cat_velocity["Banksy"]["total"]} sold in under 7 days. Source more Banksy items.',
        })

    # $50-200 is the sweet spot
    mid_range = sweet_spots.get('$50-100', {}).get('count', 0) + sweet_spots.get('$100-200', {}).get('count', 0)
    total_sold = len(sold_clean)
    if mid_range > total_sold * 0.5:
        insights.append({
            'type': 'pricing',
            'title': '$50-$200 is your sweet spot',
            'detail': f'{mid_range}/{total_sold} ({round(mid_range/total_sold*100)}%) of sales are in $50-$200 range. Focus inventory here for fastest turnover.',
        })

    # Space/NASA is high value but slow
    if cat_velocity.get('Space/NASA', {}).get('revenue', 0) > 3000:
        insights.append({
            'type': 'category',
            'title': 'Space/NASA: high revenue but slow — promote aggressively',
            'detail': f'${cat_velocity["Space/NASA"]["revenue"]:,.0f} revenue but avg 22d to sell. These need Speed High promotion.',
        })

    # Stale active inventory
    active_count = len(listings)
    if active_count > total_sold * 2:
        insights.append({
            'type': 'inventory',
            'title': f'Inventory turnover is low — {active_count} active vs {total_sold} sold in 60d',
            'detail': 'At current pace, it would take 6+ months to sell through. Consider markdown events or higher promotion rates.',
        })

    return jsonify({
        'sweet_spots': sweet_spots,
        'category_velocity': {k: {kk: vv for kk, vv in v.items() if kk not in ('fast_prices', 'slow_prices')} for k, v in cat_velocity.items()},
        'fastest_sellers': [{'title': s['title'][:50], 'price': s['price'], 'dom': s.get('days_on_market', 0)} for s in fastest],
        'insights': insights,
        'total_sold': total_sold,
        'total_active': active_count,
    })


@app.route('/api/promotions/apply-strategy', methods=['POST'])
def apply_ad_strategy():
    """Apply a strategy preset to all suggestions"""
    data = request.get_json()
    level = data.get('level', 'medium')

    strategy = AD_STRATEGY_PRESETS.get(level, AD_STRATEGY_PRESETS['steady'])
    listings = ebay.get_all_listings()

    suggestions = []
    for listing in listings:
        title_lower = listing['title'].lower()
        # Detect category
        if 'shepard fairey' in title_lower or 'obey' in title_lower:
            cat = 'Shepard Fairey'
        elif 'death nyc' in title_lower:
            cat = 'Death NYC'
        elif 'kaws' in title_lower:
            cat = 'KAWS'
        elif 'banksy' in title_lower:
            cat = 'Banksy'
        elif 'bearbrick' in title_lower:
            cat = 'Bearbrick'
        elif 'brainwash' in title_lower:
            cat = 'Mr. Brainwash'
        elif 'apollo' in title_lower or 'nasa' in title_lower or 'astronaut' in title_lower:
            cat = 'Space/NASA'
        elif ('vinyl' in title_lower or 'record' in title_lower) and 'signed' in title_lower:
            cat = 'Signed Music'
        else:
            cat = 'Other'

        rate = strategy['category_rates'].get(cat, strategy['default_rate'])

        # Calendar boost (additive, on top of strategy)
        rules = load_pricing_rules()
        now = datetime.now()
        for rule in rules:
            keywords = rule.get('keywords', [])
            if any(kw.lower() in title_lower for kw in keywords):
                start = rule.get('start_date', '')
                try:
                    event_date = datetime.strptime(f"{now.year}-{start}", '%Y-%m-%d')
                    if event_date < now:
                        event_date = datetime.strptime(f"{now.year + 1}-{start}", '%Y-%m-%d')
                    delta = (event_date - now).days
                    if 0 <= delta <= 14:
                        tier_boost = {'MINOR': 1, 'MEDIUM': 2, 'MAJOR': 3, 'PEAK': 5}
                        rate += tier_boost.get(rule.get('tier', 'MINOR'), 1)
                        break
                except ValueError:
                    pass

        suggestions.append({
            'listing_id': listing['id'],
            'title': listing['title'][:80],
            'category': cat,
            'price': listing['price'],
            'suggested_rate': round(rate, 1),
            'estimated_fee': round(listing['price'] * rate / 100, 2),
        })

    return jsonify({
        'strategy': strategy,
        'suggestions': suggestions,
        'total': len(suggestions),
        'total_estimated_cost': round(sum(s['estimated_fee'] for s in suggestions), 2),
    })


# =============================================================================
# Feature: Category Price Adjustment with AI Feedback
# =============================================================================

@app.route('/api/pricing/by-category')
def get_pricing_by_category():
    """Get all listings grouped by category with AI price suggestions"""
    listings = ebay.get_all_listings()
    enriched = load_personal_inventory()
    rules = load_pricing_rules()
    now = datetime.now()
    mmdd = now.strftime('%m-%d')

    # Build enrichment lookup
    enriched_by_words = {}
    for item in enriched:
        name_words = set(re.findall(r'\w+', item['name'].lower()))
        name_words -= {'the', 'a', 'and', 'of', 'in', 'print', 'signed', 'obey', 'giant', 'shepard', 'fairey'}
        if len(name_words) >= 2:
            enriched_by_words[frozenset(list(name_words)[:6])] = item

    def find_enrichment(title):
        title_words = set(re.findall(r'\w+', title.lower()))
        title_words -= {'the', 'a', 'and', 'of', 'in', 'print', 'signed', 'obey', 'giant', 'shepard', 'fairey', 'new', 'rare', 'limited'}
        best, best_overlap = None, 0
        for key, item in enriched_by_words.items():
            overlap = len(title_words & key)
            if overlap >= 2 and overlap > best_overlap:
                best_overlap = overlap
                best = item
        return best

    categories = {}
    items = []

    for listing in listings:
        title_lower = listing['title'].lower()
        if 'shepard fairey' in title_lower or 'obey' in title_lower:
            cat = 'Shepard Fairey'
        elif 'death nyc' in title_lower:
            cat = 'Death NYC'
        elif 'bearbrick' in title_lower or 'be@rbrick' in title_lower:
            cat = 'Bearbrick'
        elif 'kaws' in title_lower:
            cat = 'KAWS'
        elif 'banksy' in title_lower:
            cat = 'Banksy'
        elif 'brainwash' in title_lower or 'mbw' in title_lower:
            cat = 'Mr. Brainwash'
        elif 'apollo' in title_lower or 'nasa' in title_lower or 'astronaut' in title_lower:
            cat = 'Space/NASA'
        elif 'pickguard' in title_lower:
            cat = 'Pickguard'
        elif ('vinyl' in title_lower or 'record' in title_lower or 'album' in title_lower) and 'signed' in title_lower:
            cat = 'Signed Music'
        else:
            cat = 'Other'

        # Get enrichment for AI suggestion
        enriched_item = find_enrichment(listing['title'])
        market_median = 0
        suggested = listing['price']
        ai_change = 0
        ai_rationale = ''

        if enriched_item:
            md = enriched_item.get('market_data', {})
            market_median = md.get('median', 0)
            suggested = enriched_item.get('suggested_price', 0) or listing['price']

            if market_median and listing['price'] < market_median * 0.8:
                ai_change = round(market_median * 0.9 - listing['price'], 2)
                ai_rationale = f'Below market median ${market_median:.0f} — raise to ${listing["price"] + ai_change:.0f}'
            elif market_median and listing['price'] > market_median * 1.3:
                ai_change = round(market_median * 1.1 - listing['price'], 2)
                ai_rationale = f'Above market ${market_median:.0f} — consider reducing to ${listing["price"] + ai_change:.0f}'

        # Calendar boost check
        for rule in rules:
            keywords = rule.get('keywords', [])
            if any(kw.lower() in title_lower for kw in keywords):
                start = rule.get('start_date', '')
                end = rule.get('end_date', '')
                try:
                    event_date = datetime.strptime(f"{now.year}-{start}", '%Y-%m-%d')
                    if event_date < now:
                        event_date = datetime.strptime(f"{now.year + 1}-{start}", '%Y-%m-%d')
                    delta = (event_date - now).days
                    if 0 <= delta <= 14:
                        boost_pct = rule.get('increase_percent', 0)
                        event_boost = round(listing['price'] * boost_pct / 100, 2)
                        if event_boost > abs(ai_change):
                            ai_change = event_boost
                            ai_rationale = f'{rule["name"]} in {delta}d — boost +{boost_pct}% to ${listing["price"] + event_boost:.0f}'
                except ValueError:
                    pass

        item = {
            'listing_id': listing['id'],
            'title': listing['title'][:80],
            'category': cat,
            'current_price': listing['price'],
            'market_median': market_median,
            'suggested_price': round(suggested, 2),
            'ai_change': ai_change,
            'ai_rationale': ai_rationale,
        }
        items.append(item)

        if cat not in categories:
            categories[cat] = {'count': 0, 'total_value': 0, 'avg_price': 0}
        categories[cat]['count'] += 1
        categories[cat]['total_value'] += listing['price']

    for cat in categories:
        categories[cat]['avg_price'] = round(categories[cat]['total_value'] / categories[cat]['count'], 2)

    return jsonify({
        'items': items,
        'categories': categories,
        'total': len(items),
    })


@app.route('/api/pricing/apply', methods=['POST'])
def apply_price_changes():
    """Apply price changes to eBay listings"""
    data = request.get_json()
    changes = data.get('changes', [])  # [{listing_id, new_price}]

    applied = 0
    failed = 0
    errors = []

    for change in changes:
        lid = change.get('listing_id')
        new_price = change.get('new_price', 0)
        if lid and new_price > 0:
            if ebay.update_price(lid, float(new_price)):
                applied += 1
            else:
                failed += 1
                errors.append(f'{lid}: update failed')

    return jsonify({'success': True, 'applied': applied, 'failed': failed, 'errors': errors[:10]})


# =============================================================================
# Feature: Ad Spend Analytics — Views, CTR, Sales Correlation
# =============================================================================

@app.route('/api/analytics/ad-performance')
def get_ad_performance():
    """Ad spend analytics — correlate spend with views, CTR, sales"""
    listings = ebay.get_all_listings()
    sold_items = ebay.get_sold_items(days_back=60)
    promo_data = fetch_all_promotions()
    per_listing = promo_data.get('per_listing', {})

    # Build sold lookup by title (IDs change after sale)
    sold_by_title = {}
    for s in sold_items:
        key = s.get('title', '').lower()[:40]
        sold_by_title[key] = s
    total_sold_rev = sum(s.get('price', 0) for s in sold_items)

    # Group by ad rate bucket
    buckets = {
        '0% (none)': {'items': 0, 'value': 0, 'sold': 0, 'revenue': 0, 'ad_cost': 0},
        '1-2%': {'items': 0, 'value': 0, 'sold': 0, 'revenue': 0, 'ad_cost': 0},
        '3-5%': {'items': 0, 'value': 0, 'sold': 0, 'revenue': 0, 'ad_cost': 0},
        '6-10%': {'items': 0, 'value': 0, 'sold': 0, 'revenue': 0, 'ad_cost': 0},
        '10%+': {'items': 0, 'value': 0, 'sold': 0, 'revenue': 0, 'ad_cost': 0},
    }

    # Per-item performance
    item_perf = []

    for listing in listings:
        lid = listing['id']
        price = listing['price']
        promo = per_listing.get(lid, {})
        ad_rate = promo.get('ad_rate', 0)
        ad_cost = price * (ad_rate / 100)
        title_key = listing['title'].lower()[:40]
        was_sold = title_key in sold_by_title

        # Bucket
        if ad_rate == 0:
            bucket = '0% (none)'
        elif ad_rate <= 2:
            bucket = '1-2%'
        elif ad_rate <= 5:
            bucket = '3-5%'
        elif ad_rate <= 10:
            bucket = '6-10%'
        else:
            bucket = '10%+'

        buckets[bucket]['items'] += 1
        buckets[bucket]['value'] += price
        buckets[bucket]['ad_cost'] += ad_cost
        if was_sold:
            buckets[bucket]['sold'] += 1
            buckets[bucket]['revenue'] += price

        item_perf.append({
            'listing_id': lid,
            'title': listing['title'][:60],
            'price': price,
            'ad_rate': ad_rate,
            'ad_cost': round(ad_cost, 2),
            'sold': was_sold,
            'category': categorize_for_market(listing['title']) or 'Other',
        })

    # Calculate conversion rates per bucket
    for b in buckets.values():
        b['conversion_rate'] = round((b['sold'] / b['items'] * 100) if b['items'] else 0, 1)
        b['roi'] = round(((b['revenue'] - b['ad_cost']) / b['ad_cost'] * 100) if b['ad_cost'] > 0 else 0, 1)
        b['avg_price'] = round(b['value'] / b['items']) if b['items'] else 0
        b['value'] = round(b['value'], 2)
        b['ad_cost'] = round(b['ad_cost'], 2)
        b['revenue'] = round(b['revenue'], 2)

    # Category performance
    cat_perf = {}
    for item in item_perf:
        cat = item['category']
        if cat not in cat_perf:
            cat_perf[cat] = {'items': 0, 'promoted': 0, 'sold': 0, 'total_ad_cost': 0, 'total_value': 0, 'total_revenue': 0}
        cat_perf[cat]['items'] += 1
        if item['ad_rate'] > 0:
            cat_perf[cat]['promoted'] += 1
        cat_perf[cat]['total_ad_cost'] += item['ad_cost']
        cat_perf[cat]['total_value'] += item['price']
        if item['sold']:
            cat_perf[cat]['sold'] += 1
            cat_perf[cat]['total_revenue'] += item['price']

    for cat in cat_perf:
        c = cat_perf[cat]
        c['conversion_rate'] = round((c['sold'] / c['items'] * 100) if c['items'] else 0, 1)
        c['promo_rate'] = round((c['promoted'] / c['items'] * 100) if c['items'] else 0, 1)

    # Top performers (sold items with ad spend)
    top_performers = sorted(
        [i for i in item_perf if i['sold'] and i['ad_rate'] > 0],
        key=lambda x: x['price'], reverse=True
    )[:10]

    # Worst performers (high ad rate, not sold)
    worst = sorted(
        [i for i in item_perf if not i['sold'] and i['ad_rate'] > 5],
        key=lambda x: x['ad_cost'], reverse=True
    )[:10]

    return jsonify({
        'rate_buckets': buckets,
        'category_performance': cat_perf,
        'top_performers': top_performers,
        'worst_performers': worst,
        'total_items': len(item_perf),
        'total_promoted': len([i for i in item_perf if i['ad_rate'] > 0]),
        'total_sold': len([i for i in item_perf if i['sold']]),
        'total_ad_spend': round(sum(i['ad_cost'] for i in item_perf), 2),
        'total_revenue': round(sum(i['price'] for i in item_perf if i['sold']), 2),
    })


# =============================================================================
# Feature: Command Center / AI Daily Brief
# =============================================================================

@app.route('/api/dashboard')
def get_dashboard():
    """Command center data — everything you need in one call"""
    listings = ebay.get_all_listings()
    sold = ebay.get_sold_items(days_back=30)
    promo_data = fetch_all_promotions()
    per_listing = promo_data.get('per_listing', {})
    rules = load_pricing_rules()
    now = datetime.now()
    mmdd = now.strftime('%m-%d')
    inventory = load_personal_inventory()
    alerts_data = check_alerts(load_art_deals(), inventory)

    # Revenue last 30 days
    revenue_30d = sum(s.get('price', 0) * s.get('quantity_sold', 1) for s in sold)
    items_sold_30d = len(sold)

    # Active inventory value
    total_value = sum(l['price'] for l in listings)
    promoted = len(per_listing)
    total_ad_spend = sum(l['price'] * (per_listing.get(l['id'], {}).get('ad_rate', 0) / 100) for l in listings)

    # Next event
    next_event = None
    min_days = 999
    for rule in rules:
        start = rule.get('start_date', '')
        try:
            event_date = datetime.strptime(f"{now.year}-{start}", '%Y-%m-%d')
            if event_date < now:
                event_date = datetime.strptime(f"{now.year + 1}-{start}", '%Y-%m-%d')
            delta = (event_date - now).days
            if 0 < delta < min_days:
                min_days = delta
                next_event = {'name': rule['name'], 'tier': rule['tier'], 'days': delta, 'keywords': rule.get('keywords', [])}
        except ValueError:
            continue

    # Sell signals
    sell_now = [i for i in inventory if i.get('ebay_supply', {}).get('recommendation') == 'SELL NOW']

    # AI daily brief
    brief = []
    if items_sold_30d > 0:
        brief.append(f"Sold {items_sold_30d} items for ${revenue_30d:,.0f} in the last 30 days.")
    if next_event:
        brief.append(f"{next_event['name']} in {next_event['days']} days — consider boosting {', '.join(next_event['keywords'][:3])} items.")
    if sell_now:
        brief.append(f"{len(sell_now)} items flagged SELL NOW — low supply, act fast.")
    if promoted < len(listings) * 0.5:
        brief.append(f"Only {promoted}/{len(listings)} items promoted. Consider adding more to campaigns.")
    high_alerts = [a for a in alerts_data if a.get('severity') == 'high']
    if high_alerts:
        brief.append(f"{len(high_alerts)} high-priority alerts need attention.")
    if not brief:
        brief.append("All systems running smoothly. Keep monitoring for opportunities.")

    return jsonify({
        'listings': len(listings),
        'total_value': round(total_value, 2),
        'revenue_30d': round(revenue_30d, 2),
        'items_sold_30d': items_sold_30d,
        'promoted': promoted,
        'total_ad_spend': round(total_ad_spend, 2),
        'inventory_count': len(inventory),
        'sell_now_count': len(sell_now),
        'alerts': len(alerts_data),
        'high_alerts': len(high_alerts),
        'next_event': next_event,
        'brief': brief,
        'sell_now_items': [{'name': i['name'][:50], 'price': i.get('suggested_price', 0)} for i in sell_now[:5]],
    })


# =============================================================================
# Feature: Purchase Tracker
# =============================================================================

PURCHASES_FILE = os.path.join(DATA_DIR, 'purchases.json')


def load_purchases():
    if os.path.exists(PURCHASES_FILE):
        try:
            with open(PURCHASES_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return []


@app.route('/api/purchases', methods=['GET', 'POST'])
def manage_purchases():
    """Track purchases — what you bought, cost, date"""
    if request.method == 'POST':
        data = request.get_json()
        purchases = load_purchases()
        purchase = {
            'id': f"p-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'title': data.get('title', ''),
            'cost': float(data.get('cost', 0)),
            'date': data.get('date', datetime.now().strftime('%Y-%m-%d')),
            'source': data.get('source', ''),
            'category': data.get('category', ''),
            'notes': data.get('notes', ''),
            'listing_id': data.get('listing_id', ''),
            'created': datetime.now().isoformat(),
        }
        purchases.insert(0, purchase)

        # Also update cost basis
        if purchase['listing_id'] and purchase['cost']:
            cb = load_cost_basis()
            cb[purchase['listing_id']] = {'cost': purchase['cost'], 'updated': datetime.now().isoformat()}
            with open(COST_BASIS_FILE, 'w') as f:
                json.dump(cb, f, indent=2)

        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PURCHASES_FILE, 'w') as f:
            json.dump(purchases, f, indent=2)
        return jsonify({'success': True, 'purchase': purchase})

    purchases = load_purchases()
    total_spent = sum(p.get('cost', 0) for p in purchases)
    return jsonify({
        'purchases': purchases,
        'total': len(purchases),
        'total_spent': round(total_spent, 2),
    })


# =============================================================================
# Feature: Background Enrichment
# =============================================================================

_enrichment_progress = {'running': False, 'done': 0, 'total': 0, 'status': ''}


@app.route('/api/enrichment/start', methods=['POST'])
def start_enrichment():
    """Start background enrichment of eBay listings"""
    import threading

    if _enrichment_progress['running']:
        return jsonify({'message': 'Already running', **_enrichment_progress})

    def run_enrichment():
        _enrichment_progress['running'] = True
        _enrichment_progress['done'] = 0
        _enrichment_progress['status'] = 'Starting...'

        listings = ebay.get_all_listings()
        enriched = load_personal_inventory()
        enriched_titles = set(i['name'].lower()[:40] for i in enriched)

        to_enrich = [l for l in listings if l['title'].lower()[:40] not in enriched_titles]
        _enrichment_progress['total'] = len(to_enrich)

        ENRICHMENT_FILE = os.path.join(DATA_DIR, 'auto_enrichment.json')
        cache = {}
        if os.path.exists(ENRICHMENT_FILE):
            try:
                with open(ENRICHMENT_FILE, 'r') as f:
                    cache = json.load(f)
            except Exception:
                pass

        for i, listing in enumerate(to_enrich):
            if listing['id'] in cache:
                _enrichment_progress['done'] = i + 1
                continue

            _enrichment_progress['status'] = f'Enriching: {listing["title"][:40]}...'
            stop_words = {'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of', 'is', 'by', 'with', 'new', 'lot', 'rare', 'free', 'shipping'}
            words = [w for w in re.findall(r'\w+', listing['title'].lower()) if w not in stop_words and len(w) > 2]
            query = ' '.join(words[:5])

            if query:
                try:
                    comps = search_ebay(query, listing['price'] * 3, max(listing['price'] * 0.2, 10), limit=10)
                    if comps:
                        cp = [c['price'] for c in comps if c['price'] > 0]
                        if cp:
                            median = sorted(cp)[len(cp) // 2]
                            avg_p = sum(cp) / len(cp)
                            ec = len(comps)
                            rec = 'GOOD TO SELL' if ec <= 3 else 'HOLD' if ec >= 8 else 'GOOD TO SELL'
                            reason = f'{ec} comps, median ${median:.0f}'

                            cache[listing['id']] = {
                                'market_data': {'count': len(cp), 'min': min(cp), 'max': max(cp), 'avg': round(avg_p, 2), 'median': median, 'suggested_price': round(median, 2)},
                                'ebay_supply': {'ebay_count': ec, 'ebay_avg_price': round(avg_p, 2), 'recommendation': rec, 'reason': reason,
                                    'competing_listings': [{'title': c['title'], 'price': c['price'], 'url': c.get('url', '')} for c in comps[:5]]},
                                'recommendation': rec, 'recommendation_reason': reason,
                            }
                except Exception:
                    pass

            _enrichment_progress['done'] = i + 1

            # Save periodically
            if (i + 1) % 10 == 0:
                try:
                    with open(ENRICHMENT_FILE, 'w') as f:
                        json.dump(cache, f)
                except Exception:
                    pass

        # Final save
        try:
            with open(ENRICHMENT_FILE, 'w') as f:
                json.dump(cache, f)
        except Exception:
            pass

        _enrichment_progress['running'] = False
        _enrichment_progress['status'] = f'Done — enriched {len(cache)} items'

    threading.Thread(target=run_enrichment, daemon=True).start()
    return jsonify({'message': 'Enrichment started', 'total': _enrichment_progress.get('total', 0)})


@app.route('/api/enrichment/progress')
def get_enrichment_progress():
    """Get background enrichment progress"""
    return jsonify(_enrichment_progress)


# =============================================================================
# Feature: Cost Basis Management
# =============================================================================

@app.route('/api/cost-basis', methods=['GET', 'POST'])
def manage_cost_basis():
    """Get or set cost basis for items"""
    if request.method == 'POST':
        data = request.get_json()
        cb = load_cost_basis()
        cb[data['listing_id']] = {'cost': float(data['cost']), 'updated': datetime.now().isoformat()}
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(COST_BASIS_FILE, 'w') as f:
            json.dump(cb, f, indent=2)
        return jsonify({'success': True})

    return jsonify(load_cost_basis())


# =============================================================================
# Consolidated detect_cat — single source of truth for category detection
# =============================================================================

def detect_category(title):
    """Detect artist/category from listing title — single source of truth"""
    t = title.lower()
    if 'shepard fairey' in t or 'obey' in t: return 'Shepard Fairey'
    elif 'death nyc' in t: return 'Death NYC'
    elif 'banksy' in t or 'd*face' in t: return 'Banksy'
    elif 'kaws' in t: return 'KAWS'
    elif 'bearbrick' in t or 'be@rbrick' in t: return 'Bearbrick'
    elif 'brainwash' in t or 'mbw' in t: return 'Mr. Brainwash'
    elif 'apollo' in t or 'nasa' in t or 'astronaut' in t or 'aldrin' in t or 'armstrong' in t: return 'Space/NASA'
    elif 'pickguard' in t: return 'Pickguard'
    elif ('vinyl' in t or 'record' in t or 'album' in t or 'guitar' in t) and 'signed' in t: return 'Signed Music'
    return 'Other'


# =============================================================================
# Feature: Sold Comps — Market completed/sold listing prices via Finding API
# =============================================================================
SOLD_COMPS_CACHE_FILE = os.path.join(DATA_DIR, 'sold_comps_cache.json')


def search_sold_comps(query, min_price=0, max_price=9999, days_back=90):
    """Search eBay Finding API for completed+sold items — actual sold prices, not asking"""
    client_id = ENV.get('EBAY_CLIENT_ID', '')
    if not client_id:
        return []

    params = {
        'OPERATION-NAME': 'findCompletedItems',
        'SERVICE-VERSION': '1.0.0',
        'SECURITY-APPNAME': client_id,
        'RESPONSE-DATA-FORMAT': 'JSON',
        'REST-PAYLOAD': '',
        'keywords': query,
        'itemFilter(0).name': 'SoldItemsOnly',
        'itemFilter(0).value(0)': 'true',
        'itemFilter(1).name': 'MinPrice',
        'itemFilter(1).value(0)': str(min_price),
        'itemFilter(2).name': 'MaxPrice',
        'itemFilter(2).value(0)': str(max_price),
        'paginationInput.entriesPerPage': '100',
        'sortOrder': 'EndTimeSoonest',
    }

    try:
        resp = requests.get('https://svcs.ebay.com/services/search/FindingService/v1', params=params, timeout=15)
        if resp.status_code != 200:
            return []

        data = resp.json()
        results = data.get('findCompletedItemsResponse', [{}])[0]
        items = results.get('searchResult', [{}])[0].get('item', [])

        comps = []
        for item in items:
            price = float(item.get('sellingStatus', [{}])[0].get('currentPrice', [{}])[0].get('__value__', 0))
            sold_date = item.get('listingInfo', [{}])[0].get('endTime', [''])[0][:10]
            title = item.get('title', [''])[0] if isinstance(item.get('title'), list) else item.get('title', '')
            url = item.get('viewItemURL', [''])[0] if isinstance(item.get('viewItemURL'), list) else item.get('viewItemURL', '')
            condition = item.get('condition', [{}])[0].get('conditionDisplayName', [''])[0] if item.get('condition') else ''

            if price > 0:
                comps.append({
                    'title': title[:80],
                    'price': price,
                    'sold_date': sold_date,
                    'url': url,
                    'condition': condition,
                    'source': 'eBay Sold',
                })

        return comps

    except Exception as e:
        print(f"Finding API error: {e}")
        return []


@app.route('/api/comps/sold')
def get_sold_comps():
    """Get actual sold prices from eBay completed listings"""
    title = request.args.get('title', '')
    artist = request.args.get('artist', '')
    price = float(request.args.get('price', 0))

    if not title:
        return jsonify({'error': 'Missing title'}), 400

    # Build search query — artist + key title words
    noise = {'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of', 'is', 'by', 'with',
             'new', 'lot', 'rare', 'free', 'shipping', 'print', 'signed', 'numbered', 'hand',
             'screen', 'edition', 'limited', 'art', 'original', 'artist', 'proof', 'framed',
             'obey', 'giant', 'authentic', 'vinyl', 'figure', 'open'}
    words = [w for w in re.findall(r'\w+', title) if w.lower() not in noise and len(w) > 2]

    min_price_floor = FAKE_PRICE_THRESHOLDS.get(artist, 15)
    max_price_ceil = max(price * 4, 500)

    # Run 2 queries for better coverage
    all_comps = []
    q1 = f"{artist} {' '.join(words[:3])}" if artist else ' '.join(words[:4])
    q2 = f"{artist} {' '.join(words[:2])}" if artist and len(words) >= 2 else ''

    for q in [q1, q2]:
        if q.strip():
            results = search_sold_comps(q, min_price_floor, max_price_ceil)
            all_comps.extend(results)

    # Deduplicate
    seen = set()
    unique = []
    for c in all_comps:
        key = c['title'][:30].lower()
        if key not in seen:
            seen.add(key)
            unique.append(c)

    # Quality gate + rejection filter
    rejections_data = load_comp_rejections()
    filtered = []
    for c in unique:
        if not passes_artist_quality_gate(c['title'], artist):
            continue
        is_rej, _ = comp_matches_learned_rejection(c['title'], rejections_data)
        if is_rej:
            continue
        filtered.append(c)

    # IQR outlier removal
    prices = [c['price'] for c in filtered if c['price'] > 0]
    if len(prices) >= 4:
        sp = sorted(prices)
        q1_val, q3_val = sp[len(sp)//4], sp[3*len(sp)//4]
        iqr = q3_val - q1_val
        lo, hi = q1_val - 1.5 * iqr, q3_val + 1.5 * iqr
        filtered = [c for c in filtered if lo <= c['price'] <= hi]
        prices = [c['price'] for c in filtered]

    stats = {}
    if prices:
        sp = sorted(prices)
        stats = {'count': len(sp), 'min': sp[0], 'max': sp[-1], 'median': sp[len(sp)//2], 'avg': round(sum(sp)/len(sp))}

    return jsonify({
        'sold_comps': filtered[:20],
        'stats': stats,
        'queries': [q1, q2],
        'total': len(filtered),
    })


# =============================================================================
# Feature: Auto-Reprice Engine — Weekly stale item markdown with floor
# =============================================================================
REPRICE_CONFIG_FILE = os.path.join(DATA_DIR, 'reprice_config.json')


def load_reprice_config():
    if os.path.exists(REPRICE_CONFIG_FILE):
        try:
            with open(REPRICE_CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        'enabled': False,
        'stale_threshold_days': 21,
        'category_rules': {
            'Shepard Fairey': {'markdown_pct': 5, 'max_total_pct': 25},
            'KAWS': {'markdown_pct': 5, 'max_total_pct': 20},
            'Death NYC': {'markdown_pct': 8, 'max_total_pct': 30},
            'Banksy': {'markdown_pct': 3, 'max_total_pct': 15},
            'Bearbrick': {'markdown_pct': 5, 'max_total_pct': 25},
            'Mr. Brainwash': {'markdown_pct': 7, 'max_total_pct': 30},
            'Space/NASA': {'markdown_pct': 7, 'max_total_pct': 30},
            'Signed Music': {'markdown_pct': 5, 'max_total_pct': 25},
            'Other': {'markdown_pct': 10, 'max_total_pct': 35},
        },
        'original_prices': {},
        'history': [],
    }


def save_reprice_config(config):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(REPRICE_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


@app.route('/api/reprice/config', methods=['GET', 'POST'])
def manage_reprice_config():
    if request.method == 'POST':
        data = request.get_json()
        config = load_reprice_config()
        config.update(data)
        save_reprice_config(config)
        return jsonify({'success': True})
    return jsonify(load_reprice_config())


@app.route('/api/reprice/preview')
def reprice_preview():
    """Dry run — show what would change without applying"""
    config = load_reprice_config()
    listings = ebay.get_all_listings()
    sold_data = fetch_and_cache_sold()
    sold_items = sold_data.get('items', [])

    # Build sold price lookup
    sold_by_title = {}
    for s in sold_items:
        key = (s.get('title', '') or '').lower()[:40]
        if key not in sold_by_title or s['price'] > sold_by_title[key]:
            sold_by_title[key] = s['price']

    threshold = config.get('stale_threshold_days', 21)
    changes = []

    for listing in listings:
        # Calculate age
        start_time = listing.get('start_time', '') or ''
        days_listed = 0
        if start_time:
            try:
                st = datetime.strptime(start_time[:19], '%Y-%m-%dT%H:%M:%S')
                days_listed = (datetime.now() - st).days
            except Exception:
                pass

        if days_listed < threshold:
            continue

        cat = detect_category(listing['title'])
        rule = config['category_rules'].get(cat, config['category_rules'].get('Other', {'markdown_pct': 10, 'max_total_pct': 35}))

        current = listing['price']
        original = config.get('original_prices', {}).get(listing['id'], current)

        # Floor: never below last sale price
        title_key = listing['title'].lower()[:40]
        floor = sold_by_title.get(title_key, current * 0.5)

        # Calculate markdown
        markdown = current * (1 - rule['markdown_pct'] / 100)
        max_markdown = original * (1 - rule['max_total_pct'] / 100)
        new_price = round(max(markdown, max_markdown, floor), 2)

        if new_price < current:
            pct_off = round((1 - new_price / original) * 100, 1) if original else 0
            changes.append({
                'id': listing['id'],
                'title': listing['title'][:60],
                'category': cat,
                'current_price': current,
                'new_price': new_price,
                'floor': round(floor, 2),
                'original': original,
                'days_listed': days_listed,
                'total_markdown_pct': pct_off,
                'this_markdown_pct': rule['markdown_pct'],
                'url': listing.get('url', ''),
            })

    changes.sort(key=lambda x: -x['days_listed'])
    return jsonify({'changes': changes, 'total': len(changes), 'threshold': threshold})


@app.route('/api/reprice/run', methods=['POST'])
def run_reprice_engine():
    """Execute auto-reprice on stale items"""
    config = load_reprice_config()
    listings = ebay.get_all_listings()
    sold_data = fetch_and_cache_sold()
    sold_items = sold_data.get('items', [])

    sold_by_title = {}
    for s in sold_items:
        key = (s.get('title', '') or '').lower()[:40]
        if key not in sold_by_title or s['price'] > sold_by_title[key]:
            sold_by_title[key] = s['price']

    threshold = config.get('stale_threshold_days', 21)
    applied = 0
    failed = 0
    log = []

    for listing in listings:
        start_time = listing.get('start_time', '') or ''
        days_listed = 0
        if start_time:
            try:
                st = datetime.strptime(start_time[:19], '%Y-%m-%dT%H:%M:%S')
                days_listed = (datetime.now() - st).days
            except Exception:
                pass

        if days_listed < threshold:
            continue

        cat = detect_category(listing['title'])
        rule = config['category_rules'].get(cat, config['category_rules'].get('Other', {'markdown_pct': 10, 'max_total_pct': 35}))

        current = listing['price']
        original = config.get('original_prices', {}).get(listing['id'], current)

        title_key = listing['title'].lower()[:40]
        floor = sold_by_title.get(title_key, current * 0.5)

        markdown = current * (1 - rule['markdown_pct'] / 100)
        max_markdown = original * (1 - rule['max_total_pct'] / 100)
        new_price = round(max(markdown, max_markdown, floor), 2)

        if new_price < current:
            if ebay.update_price(listing['id'], new_price):
                applied += 1
                if listing['id'] not in config.get('original_prices', {}):
                    config.setdefault('original_prices', {})[listing['id']] = current
                log.append(f"${current:.0f} → ${new_price:.0f} ({cat}) {listing['title'][:40]}")
            else:
                failed += 1

    config['history'] = (config.get('history', []) + log)[-100:]
    config['last_run'] = datetime.now().isoformat()
    save_reprice_config(config)

    return jsonify({'applied': applied, 'failed': failed, 'log': log[:20]})


# =============================================================================
# Feature: Auto-Offer to Watchers — Negotiation API with AI messages
# =============================================================================
WATCHER_OFFERS_FILE = os.path.join(DATA_DIR, 'watcher_offers_config.json')


def load_watcher_offers_config():
    if os.path.exists(WATCHER_OFFERS_FILE):
        try:
            with open(WATCHER_OFFERS_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        'enabled': False,
        'category_discounts': {
            'Shepard Fairey': {'min_days': 14, 'discount_pct': 10, 'max_discount_pct': 20},
            'KAWS': {'min_days': 14, 'discount_pct': 12, 'max_discount_pct': 25},
            'Death NYC': {'min_days': 14, 'discount_pct': 15, 'max_discount_pct': 25},
            'Banksy': {'min_days': 21, 'discount_pct': 8, 'max_discount_pct': 15},
            'Bearbrick': {'min_days': 14, 'discount_pct': 10, 'max_discount_pct': 20},
            'Mr. Brainwash': {'min_days': 14, 'discount_pct': 12, 'max_discount_pct': 20},
            'Space/NASA': {'min_days': 14, 'discount_pct': 12, 'max_discount_pct': 20},
            'Signed Music': {'min_days': 14, 'discount_pct': 10, 'max_discount_pct': 20},
            'Other': {'min_days': 21, 'discount_pct': 15, 'max_discount_pct': 30},
        },
        'offer_duration_days': 2,
        'allow_counter': True,
        'cooldown_days': 7,
        'sent_offers': {},
    }


def save_watcher_offers_config(config):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(WATCHER_OFFERS_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def generate_offer_message(title, category, discount_pct, price, offer_price):
    """Generate a unique, personal offer message using Claude"""
    claude_key = ENV.get('CLAUDE_API_KEY', '')

    prompt = f"""Write a 1-2 sentence personalized eBay offer message for a buyer watching this item. Be conversational, mention the artwork/item specifically, create gentle urgency. No generic messages.

Item: {title}
Category: {category}
Original price: ${price:.0f}
Offer price: ${offer_price:.0f} ({discount_pct}% off)

Examples of the tone:
- "Hey! I noticed you're watching the Peace Goddess print. I'd love for it to find a great home — here's {discount_pct}% off, just for you."
- "This {category} piece has been getting a lot of attention. Wanted to offer you first dibs at a special price before it's gone."
- "Thanks for checking out the {title[:20]}! Sending you an exclusive offer — this one won't last long."

Return ONLY the message text, no quotes, under 500 characters."""

    if claude_key:
        try:
            resp = requests.post('https://api.anthropic.com/v1/messages',
                headers={'x-api-key': claude_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                json={'model': 'claude-sonnet-4-5-20241022', 'max_tokens': 80, 'messages': [{'role': 'user', 'content': prompt}]},
                timeout=10)
            if resp.status_code == 200:
                msg = resp.json().get('content', [{}])[0].get('text', '').strip().strip('"')
                if msg and len(msg) < 500:
                    return msg
        except Exception:
            pass

    # Fallback — still personal
    short_title = re.sub(r'\b(signed|numbered|limited|edition|print|screen|obey|giant|art|framed)\b', '', title, flags=re.IGNORECASE).strip()
    short_title = re.sub(r'\s+', ' ', short_title).strip()[:30]
    return f"Hi! I noticed you're watching the {short_title}. Here's an exclusive {discount_pct}% off just for you — this piece won't last long!"


@app.route('/api/watcher-offers/config', methods=['GET', 'POST'])
def manage_watcher_offers():
    if request.method == 'POST':
        data = request.get_json()
        config = load_watcher_offers_config()
        config.update(data)
        save_watcher_offers_config(config)
        return jsonify({'success': True})
    return jsonify(load_watcher_offers_config())


@app.route('/api/watcher-offers/preview')
def preview_watcher_offers():
    """Show which items would get offers — dry run with AI messages"""
    config = load_watcher_offers_config()
    listings = ebay.get_all_listings()
    now = datetime.now()
    sent = config.get('sent_offers', {})
    cooldown = config.get('cooldown_days', 7)

    candidates = []
    for listing in listings:
        if listing.get('watchers', 0) < 1:
            continue

        # Calculate age
        start_time = listing.get('start_time', '') or ''
        days_listed = 0
        if start_time:
            try:
                st = datetime.strptime(start_time[:19], '%Y-%m-%dT%H:%M:%S')
                days_listed = (now - st).days
            except Exception:
                pass

        cat = detect_category(listing['title'])
        cat_config = config['category_discounts'].get(cat, config['category_discounts'].get('Other', {'min_days': 21, 'discount_pct': 15, 'max_discount_pct': 30}))

        if days_listed < cat_config['min_days']:
            continue

        # Check cooldown
        last_sent = sent.get(listing['id'], '')
        if last_sent:
            try:
                last_dt = datetime.fromisoformat(last_sent)
                if (now - last_dt).days < cooldown:
                    continue
            except Exception:
                pass

        # Tiered discount: more stale = higher discount
        base_discount = cat_config['discount_pct']
        max_discount = cat_config['max_discount_pct']
        if days_listed > 45:
            discount = max_discount
        elif days_listed > 30:
            discount = min(base_discount + 5, max_discount)
        else:
            discount = base_discount

        offer_price = round(listing['price'] * (1 - discount / 100), 2)

        # Generate AI message
        message = generate_offer_message(listing['title'], cat, discount, listing['price'], offer_price)

        candidates.append({
            'id': listing['id'],
            'title': listing['title'][:60],
            'category': cat,
            'price': listing['price'],
            'offer_price': offer_price,
            'discount_pct': discount,
            'watchers': listing.get('watchers', 0),
            'days_listed': days_listed,
            'message': message,
            'url': listing.get('url', ''),
        })

    candidates.sort(key=lambda x: (-x['watchers'], -x['days_listed']))
    return jsonify({'candidates': candidates, 'total': len(candidates)})


@app.route('/api/watcher-offers/send', methods=['POST'])
def send_watcher_offers():
    """Send offers to watchers via eBay Negotiation API"""
    data = request.get_json()
    offers = data.get('offers', [])  # [{id, offer_price, message}]

    if not offers:
        # Auto-generate from preview
        with app.test_request_context():
            preview_resp = preview_watcher_offers()
            preview_data = preview_resp.get_json()
            offers = preview_data.get('candidates', [])

    token = ebay.get_access_token()
    if not token:
        return jsonify({'error': 'eBay auth failed'}), 401

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US',
    }

    config = load_watcher_offers_config()
    sent = 0
    failed = 0
    errors = []

    for offer in offers:
        item_id = offer.get('id', '')
        offer_price = offer.get('offer_price', 0)
        message = offer.get('message', '')

        if not item_id or not offer_price:
            failed += 1
            continue

        body = {
            'offeredItems': [{'listingId': f'v1|{item_id}|0'}],
            'allowCounterOffer': config.get('allow_counter', True),
            'message': message[:500] if message else '',
            'offerDuration': {'unit': 'DAY', 'value': config.get('offer_duration_days', 2)},
            'offeredPrice': {'currency': 'USD', 'value': str(offer_price)},
        }

        try:
            resp = requests.post(
                'https://api.ebay.com/sell/negotiation/v1/send_offer_to_interested_buyers',
                headers=headers, json=body, timeout=15)
            if resp.status_code in (200, 201):
                sent += 1
                config.setdefault('sent_offers', {})[item_id] = datetime.now().isoformat()
            else:
                failed += 1
                err_text = resp.text[:100]
                errors.append(f'{item_id}: {err_text}')
        except Exception as e:
            failed += 1
            errors.append(f'{item_id}: {str(e)[:50]}')

    save_watcher_offers_config(config)
    return jsonify({'sent': sent, 'failed': failed, 'errors': errors[:10]})


# =============================================================================
# Cron Scheduler — Background automation engine
# =============================================================================
SCHEDULER_CONFIG_FILE = os.path.join(DATA_DIR, 'scheduler_config.json')


def load_scheduler_config():
    if os.path.exists(SCHEDULER_CONFIG_FILE):
        try:
            with open(SCHEDULER_CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        'enabled': False,
        'tasks': {
            'scrape': {'enabled': True, 'interval_hours': 4, 'last_run': None},
            'reprice': {'enabled': True, 'day': 'monday', 'hour': 9, 'last_run': None},
            'offers': {'enabled': True, 'day': 'tuesday', 'hour': 10, 'last_run': None},
            'deal_alerts': {'enabled': True, 'interval_hours': 4, 'last_run': None},
        },
        'log': [],
    }


def save_scheduler_config(config):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SCHEDULER_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def check_deal_alerts():
    """Check scraped deals for hot ones and create notifications"""
    try:
        if not os.path.exists(LIVE_DEALS_FILE):
            return 0

        with open(LIVE_DEALS_FILE, 'r') as f:
            cache = json.load(f)

        deals = cache.get('deals', [])
        alerts = 0

        for d in deals:
            price = d.get('price', 0)
            title = d.get('title', '')
            cat = d.get('category', '')

            # Alert on extreme discounts (items way below typical prices)
            threshold = FAKE_PRICE_THRESHOLDS.get(cat, 50)
            if price > threshold and price < threshold * 3:
                # Cheap but not fake — potential deal
                add_notification('deal_alert',
                    f'Deal: ${price:.0f} {title[:40]}',
                    f'{cat} item at ${price:.0f} — check if legitimate',
                    severity='high',
                    data={'price': price, 'title': title, 'url': d.get('url', ''), 'category': cat})
                alerts += 1
                if alerts >= 5:
                    break  # Cap at 5 alerts per check

        return alerts
    except Exception as e:
        print(f"Deal alert error: {e}")
        return 0


def scheduler_loop():
    """Background scheduler — checks every 60 seconds if any task is due"""
    import time
    import threading

    while True:
        try:
            config = load_scheduler_config()
            if not config.get('enabled'):
                time.sleep(60)
                continue

            now = datetime.now()
            tasks = config.get('tasks', {})
            ran_something = False

            # Scrape — every N hours
            scrape_task = tasks.get('scrape', {})
            if scrape_task.get('enabled'):
                interval = scrape_task.get('interval_hours', 4)
                last = scrape_task.get('last_run')
                due = not last or (now - datetime.fromisoformat(last)).total_seconds() > interval * 3600
                if due and not _scrape_running:
                    print(f"[Scheduler] Starting scrape at {now.isoformat()}")
                    thread = threading.Thread(target=run_background_scrape, daemon=True)
                    thread.start()
                    tasks['scrape']['last_run'] = now.isoformat()
                    config['log'] = ([f"{now.strftime('%m/%d %H:%M')} Scrape started"] + config.get('log', []))[:50]
                    ran_something = True

            # Reprice — specific day of week
            reprice_task = tasks.get('reprice', {})
            if reprice_task.get('enabled'):
                target_day = reprice_task.get('day', 'monday').lower()
                target_hour = reprice_task.get('hour', 9)
                current_day = now.strftime('%A').lower()
                last = reprice_task.get('last_run')
                today_str = now.strftime('%Y-%m-%d')
                already_ran = last and last[:10] == today_str

                if current_day == target_day and now.hour >= target_hour and not already_ran:
                    print(f"[Scheduler] Running reprice at {now.isoformat()}")
                    with app.test_request_context():
                        result = run_reprice_engine()
                        data = result.get_json()
                    tasks['reprice']['last_run'] = now.isoformat()
                    config['log'] = ([f"{now.strftime('%m/%d %H:%M')} Reprice: {data.get('applied', 0)} items"] + config.get('log', []))[:50]
                    add_notification('reprice', f"Auto-reprice: {data.get('applied', 0)} items marked down",
                        f"{data.get('applied', 0)} stale items repriced, {data.get('failed', 0)} failed", severity='info')
                    ran_something = True

            # Offers — specific day of week
            offers_task = tasks.get('offers', {})
            if offers_task.get('enabled'):
                target_day = offers_task.get('day', 'tuesday').lower()
                target_hour = offers_task.get('hour', 10)
                current_day = now.strftime('%A').lower()
                last = offers_task.get('last_run')
                today_str = now.strftime('%Y-%m-%d')
                already_ran = last and last[:10] == today_str

                if current_day == target_day and now.hour >= target_hour and not already_ran:
                    print(f"[Scheduler] Sending offers at {now.isoformat()}")
                    with app.test_request_context():
                        result = send_watcher_offers()
                        data = result.get_json()
                    tasks['offers']['last_run'] = now.isoformat()
                    config['log'] = ([f"{now.strftime('%m/%d %H:%M')} Offers: {data.get('sent', 0)} sent"] + config.get('log', []))[:50]
                    add_notification('offers', f"Auto-offers: {data.get('sent', 0)} sent to watchers",
                        f"{data.get('sent', 0)} personalized offers sent, {data.get('failed', 0)} failed", severity='info')
                    ran_something = True

            # Deal alerts — every N hours (after scrape)
            alert_task = tasks.get('deal_alerts', {})
            if alert_task.get('enabled'):
                interval = alert_task.get('interval_hours', 4)
                last = alert_task.get('last_run')
                due = not last or (now - datetime.fromisoformat(last)).total_seconds() > interval * 3600
                if due:
                    alerts = check_deal_alerts()
                    tasks['deal_alerts']['last_run'] = now.isoformat()
                    if alerts:
                        config['log'] = ([f"{now.strftime('%m/%d %H:%M')} Deal alerts: {alerts}"] + config.get('log', []))[:50]
                    ran_something = True

            if ran_something:
                save_scheduler_config(config)

        except Exception as e:
            print(f"[Scheduler] Error: {e}")

        time.sleep(60)


@app.route('/api/scheduler/config', methods=['GET', 'POST'])
def manage_scheduler():
    if request.method == 'POST':
        data = request.get_json()
        config = load_scheduler_config()
        # Merge — don't replace entirely
        if 'enabled' in data:
            config['enabled'] = data['enabled']
        if 'tasks' in data:
            for task_name, task_data in data['tasks'].items():
                if task_name in config['tasks']:
                    config['tasks'][task_name].update(task_data)
        save_scheduler_config(config)
        return jsonify({'success': True})
    return jsonify(load_scheduler_config())


@app.route('/api/scheduler/start', methods=['POST'])
def start_scheduler():
    """Start the background scheduler thread"""
    import threading
    config = load_scheduler_config()
    config['enabled'] = True
    save_scheduler_config(config)

    # Check if already running
    for t in threading.enumerate():
        if t.name == 'dataradar-scheduler':
            return jsonify({'status': 'already_running'})

    thread = threading.Thread(target=scheduler_loop, name='dataradar-scheduler', daemon=True)
    thread.start()
    return jsonify({'status': 'started'})


@app.route('/api/scheduler/stop', methods=['POST'])
def stop_scheduler():
    """Stop the scheduler (sets enabled=False, loop exits on next check)"""
    config = load_scheduler_config()
    config['enabled'] = False
    save_scheduler_config(config)
    return jsonify({'status': 'stopped'})


# =============================================================================
# In-App Notification Bell + Browser Push Notifications
# =============================================================================

@app.route('/api/notifications/subscribe', methods=['POST'])
def subscribe_push():
    """Store push subscription for browser notifications (Web Push API)"""
    # For now, we use in-app polling. Full Web Push requires VAPID keys + pywebpush.
    # This endpoint stores the subscription for future use.
    data = request.get_json()
    sub_file = os.path.join(DATA_DIR, 'push_subscriptions.json')
    subs = []
    if os.path.exists(sub_file):
        try:
            with open(sub_file, 'r') as f:
                subs = json.load(f)
        except Exception:
            pass
    subs.append({'subscription': data, 'created': datetime.now().isoformat()})
    subs = subs[-10:]  # Keep last 10
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(sub_file, 'w') as f:
        json.dump(subs, f, indent=2)
    return jsonify({'success': True})


@app.route('/api/notifications/poll')
def poll_notifications():
    """Poll for new notifications — lightweight endpoint for frequent checks"""
    notifs = load_notifications()
    unread = [n for n in notifs if not n.get('read')]
    return jsonify({
        'unread': len(unread),
        'latest': unread[:5] if unread else [],
        'has_alerts': any(n.get('severity') == 'high' for n in unread),
    })


# =============================================================================
# Main
# =============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    debug = os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'

    # Auto-start scheduler if enabled
    import threading
    config = load_scheduler_config()
    if config.get('enabled'):
        thread = threading.Thread(target=scheduler_loop, name='dataradar-scheduler', daemon=True)
        thread.start()
        print("[Scheduler] Auto-started from config")

    app.run(debug=debug, host='0.0.0.0', port=port)
