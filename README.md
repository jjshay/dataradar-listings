# DATARADAR Listings

> Intelligent eBay inventory management with automated key-date pricing

A Flask-based web application that helps eBay sellers maximize profits by automatically adjusting prices based on significant calendar events (birthdays, anniversaries, holidays) related to their inventory.

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-2.0+-green.svg)
![eBay API](https://img.shields.io/badge/eBay-Trading%20API-orange.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

## Features

- **Smart Price Boosting** - Automatically increase prices during relevant events
  - MINOR events: +5% (album releases, minor anniversaries)
  - MEDIUM events: +15% (significant birthdays, holidays)
  - MAJOR events: +25% (milestone anniversaries)
  - PEAK events: +35% (death anniversaries, major holidays)

- **Calendar Integration** - Visual calendar showing upcoming pricing events
- **Real-time eBay Sync** - Direct integration with eBay Trading API
- **Inventory Dashboard** - Search, filter, and manage all listings
- **Price Override** - Manual price adjustments pushed to eBay
- **Alert System** - Notifications for underpriced items and issues

## Tech Stack

- **Backend**: Python, Flask
- **Frontend**: HTML5, CSS3, JavaScript (vanilla)
- **APIs**: eBay Trading API, Google Sheets API
- **Auth**: OAuth 2.0 (eBay), Google OAuth
- **Storage**: JSON files, Google Sheets

## Screenshots

### Dashboard
```
┌─────────────────────────────────────┐
│  DATARADAR                          │
│  Manage your eBay inventory         │
├─────────────────────────────────────┤
│  283 Listings    7 Alerts    $45K   │
├─────────────────────────────────────┤
│  [Search...]                        │
│                                     │
│  ┌─────┐ Beatles Abbey Road  $125   │
│  │ img │ Vinyl Record              │
│  └─────┘ 2 days left               │
│                                     │
│  ┌─────┐ Signed Print MBW    $450   │
│  │ img │ Mr. Brainwash             │
│  └─────┘ Buy It Now                │
└─────────────────────────────────────┘
```

### Calendar View
```
┌─────────────────────────────────────┐
│  < January 2026 >                   │
├─────────────────────────────────────┤
│  8  Elvis Birthday (+25%)           │
│  9  Beatles Break Up Anniv (+15%)   │
│  27 Mozart Birthday (+15%)          │
└─────────────────────────────────────┘
```

## Installation

### Prerequisites
- Python 3.9+
- eBay Developer Account
- Google Cloud Project (for Sheets API)

### Setup

1. **Clone the repository**
```bash
git clone https://github.com/yourusername/dataradar-listings.git
cd dataradar-listings
```

2. **Create virtual environment**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Configure environment variables**
```bash
cp .env.example .env
# Edit .env with your API credentials
```

5. **Run the application**
```bash
python app.py
```

6. **Open in browser**
```
http://localhost:5050
```

## Configuration

### Environment Variables

```env
# eBay API Credentials
EBAY_CLIENT_ID=your_client_id
EBAY_CLIENT_SECRET=your_client_secret
EBAY_REFRESH_TOKEN=your_refresh_token
EBAY_DEV_ID=your_dev_id

# Google Sheets (optional)
DATARADAR_SHEET_ID=your_sheet_id
```

### Pricing Rules

Edit `pricing_rules.json` to customize events:

```json
{
  "name": "Elvis Birthday",
  "keywords": ["elvis", "presley"],
  "tier": "MAJOR",
  "increase_percent": 25,
  "start_date": "01-06",
  "end_date": "01-10"
}
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main dashboard |
| `/api/listings` | GET | Get all eBay listings |
| `/api/stats` | GET | Inventory statistics |
| `/api/calendar` | GET | Pricing calendar events |
| `/api/underpriced` | GET | Items below suggested price |
| `/api/alerts` | GET | System alerts |
| `/api/update-price` | POST | Update item price on eBay |

## Project Structure

```
dataradar-listings/
├── app.py                 # Main Flask application
├── pricing_engine.py      # Price calculation logic
├── requirements.txt       # Python dependencies
├── .env.example          # Environment template
├── pricing_rules.json    # Event configurations
├── templates/
│   └── index.html        # Dashboard UI
└── README.md
```

## Key Code Examples

### Price Calculation Engine
```python
def calculate_boosted_price(base_price, active_events):
    """Calculate price with event-based boost"""
    max_boost = 0
    for event in active_events:
        boost = TIER_BOOSTS.get(event['tier'], 0)
        max_boost = max(max_boost, boost)

    return base_price * (1 + max_boost / 100)
```

### eBay API Integration
```python
def update_ebay_price(item_id, new_price):
    """Update listing price via eBay Trading API"""
    api = Trading(config_file=None, **ebay_config)
    response = api.execute('ReviseItem', {
        'Item': {
            'ItemID': item_id,
            'StartPrice': new_price
        }
    })
    return response.reply.Ack == 'Success'
```

## Deployment

### PythonAnywhere

1. Upload files to `/home/username/dataradar-listings/`
2. Set up WSGI configuration
3. Add environment variables
4. Reload web app

See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed instructions.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

MIT License - see [LICENSE](LICENSE) for details.

## Author

**John Shay**
- GitHub: [@johnshay](https://github.com/johnshay)

## Acknowledgments

- eBay Developer Program for API access
- Flask community for excellent documentation
