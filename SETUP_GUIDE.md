# Swaps Database Setup Guide - SQLite Edition

## Quick Start (2 minutes)

### 1. Install Dependencies

```bash
cd C:\Users\bottl\FinancialDevelopment
pip install -r requirements.txt
```

That's it. No database server needed.

### 2. Initialize Database

```bash
python setup_db.py
```

You should see:
```
Creating database at C:\Users\bottl\FinancialDevelopment\swaps.db...
Creating swaps table...
Creating indexes...
Creating scrape_log table...
✓ Database initialized at C:\Users\bottl\FinancialDevelopment\swaps.db
  Now run: python scheduled_ingest.py --run-now
```

A file called `swaps.db` will appear in your folder.

### 3. Test the Scraper

```bash
python scheduled_ingest.py --run-now
```

Expected output (first run):
```
============================================================
Starting DTCC swaps ingestion
============================================================
Fetching data from DTCC PPD...
...
✓ Ingestion complete
  Fetched: XXXX
  Inserted: XXXX
  Parse errors: X
  Duration: XX.Xs
============================================================
```

### 4. Query Your Database

```bash
python swaps_query.py
```

This shows:
- Total records in database
- Unique tickers
- Trading days
- Top 10 tickers by notional
- Sample ticker (AAPL)

---

## Production Setup (Scheduler)

Once testing is successful, start the scheduler to automatically pull data every business day at 5 PM ET:

```bash
python scheduled_ingest.py --start-scheduler
```

Keep this process running:
- **Local/Development:** Keep terminal window open
- **Production (Windows):** Use Windows Task Scheduler (see below)

### Windows Task Scheduler Setup

1. Open Task Scheduler
2. Create Basic Task → "DTCC Swaps Ingestion"
3. Trigger: Daily at 5:00 PM
4. Action: Start program
   - Program: `python` (or full path like `C:\Python311\python.exe`)
   - Arguments: `C:\Users\bottl\FinancialDevelopment\scheduled_ingest.py --start-scheduler`
   - Start in: `C:\Users\bottl\FinancialDevelopment`

---

## Using the Query API

### In Your Sentiment Scanner

```python
from swaps_query import SwapsQuery

# Initialize (automatically finds swaps.db in same folder)
q = SwapsQuery()

# Get ticker summary
summary = q.get_ticker_summary('AAPL', days_back=30)
print(summary)
# Output: {
#   'record_count': 150,
#   'total_notional': 5000000000,
#   'avg_price': 102.5,
#   'trading_days': 20,
#   'tenor_count': 5,
#   'max_daily_trades': 1250,
#   'first_date': '2026-06-28',
#   'last_date': '2026-07-28'
# }

# Get all data for ticker
df = q.query_by_ticker('AAPL', days_back=30)  # Returns pandas DataFrame
# Columns: ticker, date, tenor_years, notional_usd, mid_price, bid_ask_spread, volume_trades, counterparty_count

# Top tickers by notional
top = q.top_notional_tickers(limit=20)

# Export to CSV
q.export_to_csv('AAPL', 'aapl_swaps.csv', days_back=30)
```

### Integration Example

```python
from swaps_query import SwapsQuery
from scanner.narrative import score_messages

q = SwapsQuery()

def score_ticker_with_swaps(ticker: str, messages: list) -> dict:
    """Combine sentiment + swaps data."""
    # Get swaps summary
    swaps = q.get_ticker_summary(ticker, days_back=30)
    
    # Get sentiment scores
    sentiment = score_messages(messages)
    
    # Combine signals
    combined = {
        'ticker': ticker,
        'sentiment_score': sentiment['contested_narrative_score'],
        'swaps_notional': swaps.get('total_notional', 0),
        'swaps_trading_days': swaps.get('trading_days', 0),
        'swaps_tenor_count': swaps.get('tenor_count', 0),
    }
    
    return combined
```

---

## Monitoring & Maintenance

### Check Last Ingestion

```bash
python -c "from db_loader import SwapsLoader; l = SwapsLoader(); print(l.get_last_scrape_log())"
```

### View Scrape Log

```bash
python -c "
from swaps_query import SwapsQuery
import sqlite3
conn = sqlite3.connect('swaps.db')
cur = conn.cursor()
cur.execute('SELECT scrape_date, status, rows_fetched, rows_inserted, parse_errors, duration_seconds FROM scrape_log ORDER BY scrape_date DESC LIMIT 10')
for row in cur.fetchall():
    print(row)
conn.close()
"
```

### Database File Size

```bash
# On Windows
dir swaps.db

# The file will grow as you add data
```

### Backup Your Database

Just copy the `swaps.db` file:
```bash
copy swaps.db swaps_backup.db
```

---

## Troubleshooting

### "File is not a database" error

The `swaps.db` file is corrupted. Delete and reinitialize:
```bash
del swaps.db
python setup_db.py
python scheduled_ingest.py --run-now
```

### "DTCC fetch failed" — Network issue

Check:
1. Internet connection
2. DTCC URL: https://pddata.dtcc.com/ppd/cftcdashboard
3. Firewall/proxy blocking

Try manually downloading CSV from DTCC to verify it's available.

### Parse errors increasing

DTCC may have changed CSV format. Download CSV manually and check column headers against `dtcc_scraper.py` line ~50.

### Scheduler not running

Verify:
1. Terminal is still open (don't close it)
2. Check scrape_log table for errors
3. Make sure you ran: `python scheduled_ingest.py --start-scheduler`

---

## File Reference

| File | Purpose |
|------|---------|
| `swaps.db` | SQLite database (created automatically) |
| `setup_db.py` | Initialize database schema (run once) |
| `dtcc_scraper.py` | Fetch & parse DTCC CSV |
| `db_loader.py` | Load data into database |
| `scheduled_ingest.py` | Daily ingestion scheduler |
| `swaps_query.py` | Query interface for your code |
| `requirements.txt` | Python dependencies |

---

## Next Steps

1. ✅ Database initialized and populated
2. ⏳ Collect 2-4 weeks of data
3. ⏳ Phase 2: Backtest swaps-sentiment correlation
4. ⏳ Phase 3: Integrate into sentiment scanner

No PostgreSQL, no servers, no configuration. Just Python and SQLite.

