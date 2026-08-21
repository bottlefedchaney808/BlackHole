# Equity Swaps Database - Implementation Spec

## Overview
Build a PostgreSQL database to store all DTCC Public Price Dissemination (PPD) equity swaps data. Automated daily ingestion via scheduled scraper. Query your own database instead of hitting DTCC API each time.

**Scope:** All equity swaps (thousands of names)  
**Update frequency:** Daily (after DTCC publishes, typically 4-6 PM ET)  
**Storage:** PostgreSQL (can migrate later if needed)  
**Timeline:** ~1 week to full production

---

## Architecture

```
DTCC PPD Dashboard
       ↓
   Scraper (Python)
       ↓
  CSV Parser
       ↓
  Validation
       ↓
PostgreSQL Database
       ↓
  Query Layer / Utilities
       ↓
Sentiment Scanner (downstream)
```

---

## Phase 1: Database Schema

### Table 1: `swaps` (Core fact table)
Stores daily swaps data by ticker and tenor.

```sql
CREATE TABLE swaps (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    date DATE NOT NULL,
    tenor_years DECIMAL(5, 2),  -- e.g., 1.0, 2.5, 5.0, 10.0
    notional_usd BIGINT,  -- in USD, e.g., 50000000 for $50M
    bid_price DECIMAL(10, 4),
    ask_price DECIMAL(10, 4),
    mid_price DECIMAL(10, 4),
    bid_ask_spread DECIMAL(10, 4),
    volume_trades INT,
    counterparty_count INT,
    credit_rating VARCHAR(10),  -- Moody's rating if available
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, date, tenor_years),
    INDEX idx_ticker_date (ticker, date DESC),
    INDEX idx_date (date DESC),
    INDEX idx_notional (notional_usd DESC)
);
```

### Table 2: `swaps_daily_aggregate` (Pre-aggregated for fast queries)
Summary stats by ticker/date (all tenors combined).

```sql
CREATE TABLE swaps_daily_aggregate (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    date DATE NOT NULL,
    total_notional_usd BIGINT,
    avg_mid_price DECIMAL(10, 4),
    tenor_count INT,
    volume_trades_total INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, date),
    INDEX idx_ticker_date (ticker, date DESC)
);
```

### Table 3: `scrape_log` (Operational tracking)
Track each ingestion run for debugging and monitoring.

```sql
CREATE TABLE scrape_log (
    id BIGSERIAL PRIMARY KEY,
    scrape_date DATE NOT NULL,
    rows_fetched INT,
    rows_inserted INT,
    rows_updated INT,
    parse_errors INT,
    status VARCHAR(20),  -- 'success', 'partial', 'failed'
    error_message TEXT,
    duration_seconds INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_date (scrape_date DESC)
);
```

---

## Phase 2: Data Ingestion Pipeline

### 2.1 Database Setup

**File:** `setup_db.py`

```python
import psycopg2
from psycopg2.extras import execute_values
import os

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 5432)),
    'database': os.getenv('DB_NAME', 'swaps'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'password'),
}

def init_database():
    """Create tables if they don't exist."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS swaps (
            id BIGSERIAL PRIMARY KEY,
            ticker VARCHAR(10) NOT NULL,
            date DATE NOT NULL,
            tenor_years DECIMAL(5, 2),
            notional_usd BIGINT,
            bid_price DECIMAL(10, 4),
            ask_price DECIMAL(10, 4),
            mid_price DECIMAL(10, 4),
            bid_ask_spread DECIMAL(10, 4),
            volume_trades INT,
            counterparty_count INT,
            credit_rating VARCHAR(10),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_swaps_unique 
        ON swaps(ticker, date, tenor_years);
    """)
    
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_ticker_date 
        ON swaps(ticker, date DESC);
    """)
    
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_date 
        ON swaps(date DESC);
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scrape_log (
            id BIGSERIAL PRIMARY KEY,
            scrape_date DATE NOT NULL,
            rows_fetched INT,
            rows_inserted INT,
            rows_updated INT,
            parse_errors INT,
            status VARCHAR(20),
            error_message TEXT,
            duration_seconds INT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    conn.commit()
    cur.close()
    conn.close()
    print("Database initialized.")

if __name__ == '__main__':
    init_database()
```

### 2.2 DTCC Scraper

**File:** `dtcc_scraper.py`

```python
import requests
import csv
import io
import logging
from datetime import datetime
from typing import List, Dict, Tuple
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DTCC_PPD_URL = "https://pddata.dtcc.com/ppd/cftcdashboard"
# Note: DTCC publishes CSV downloads. Adjust URL if format changes.

class DTCCSwapsScraper:
    def __init__(self, timeout: int = 30, retries: int = 3):
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def fetch_csv(self, url: str) -> str:
        """Download CSV from DTCC PPD dashboard."""
        for attempt in range(self.retries):
            try:
                logger.info(f"Fetching DTCC data (attempt {attempt + 1}/{self.retries})...")
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                return resp.text
            except requests.RequestException as e:
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt < self.retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                else:
                    raise
    
    def parse_csv(self, csv_content: str) -> Tuple[List[Dict], int]:
        """
        Parse DTCC CSV. Returns list of swaps records and count of parse errors.
        
        Expected CSV format (adjust if DTCC format differs):
        Ticker, Date, Tenor (Years), Notional (USD), Bid, Ask, Mid, Bid-Ask Spread, Trades, Counterparties, Credit Rating
        """
        records = []
        errors = 0
        
        reader = csv.DictReader(io.StringIO(csv_content))
        
        for row_num, row in enumerate(reader, start=2):  # Start at 2 (skip header)
            try:
                ticker = row.get('Ticker', '').strip().upper()
                if not ticker or len(ticker) > 10:
                    errors += 1
                    logger.warning(f"Row {row_num}: Invalid ticker '{ticker}'")
                    continue
                
                date_str = row.get('Date', '').strip()
                try:
                    date = datetime.strptime(date_str, '%Y-%m-%d').date()
                except ValueError:
                    errors += 1
                    logger.warning(f"Row {row_num}: Invalid date '{date_str}'")
                    continue
                
                tenor = float(row.get('Tenor (Years)', 0))
                notional = int(float(row.get('Notional (USD)', 0)))
                bid = float(row.get('Bid', 0))
                ask = float(row.get('Ask', 0))
                mid = float(row.get('Mid', 0))
                spread = float(row.get('Bid-Ask Spread', 0))
                trades = int(row.get('Trades', 0))
                counterparties = int(row.get('Counterparties', 0))
                rating = row.get('Credit Rating', '').strip() or None
                
                records.append({
                    'ticker': ticker,
                    'date': date,
                    'tenor_years': tenor,
                    'notional_usd': notional,
                    'bid_price': bid,
                    'ask_price': ask,
                    'mid_price': mid,
                    'bid_ask_spread': spread,
                    'volume_trades': trades,
                    'counterparty_count': counterparties,
                    'credit_rating': rating,
                })
            except Exception as e:
                errors += 1
                logger.error(f"Row {row_num} parse error: {e}")
        
        logger.info(f"Parsed {len(records)} records, {errors} errors.")
        return records, errors
    
    def scrape(self, url: str = DTCC_PPD_URL) -> Tuple[List[Dict], int]:
        """Fetch and parse DTCC swaps data."""
        csv_content = self.fetch_csv(url)
        records, errors = self.parse_csv(csv_content)
        return records, errors

if __name__ == '__main__':
    scraper = DTCCSwapsScraper()
    records, errors = scraper.scrape()
    print(f"Fetched {len(records)} records with {errors} parse errors.")
    if records:
        print(f"Sample: {records[0]}")
```

### 2.3 Database Loader

**File:** `db_loader.py`

```python
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime, date
import logging
from typing import List, Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 5432)),
    'database': os.getenv('DB_NAME', 'swaps'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'password'),
}

class SwapsLoader:
    def __init__(self, db_config: dict = None):
        self.db_config = db_config or DB_CONFIG
    
    def upsert_swaps(self, records: List[Dict]) -> Dict:
        """Upsert records into swaps table. Returns counts."""
        if not records:
            logger.info("No records to insert.")
            return {'inserted': 0, 'updated': 0}
        
        conn = psycopg2.connect(**self.db_config)
        cur = conn.cursor()
        
        inserted = 0
        updated = 0
        
        try:
            # Build upsert query
            query = """
                INSERT INTO swaps (
                    ticker, date, tenor_years, notional_usd, 
                    bid_price, ask_price, mid_price, bid_ask_spread,
                    volume_trades, counterparty_count, credit_rating
                ) VALUES %s
                ON CONFLICT (ticker, date, tenor_years) 
                DO UPDATE SET 
                    notional_usd = EXCLUDED.notional_usd,
                    bid_price = EXCLUDED.bid_price,
                    ask_price = EXCLUDED.ask_price,
                    mid_price = EXCLUDED.mid_price,
                    bid_ask_spread = EXCLUDED.bid_ask_spread,
                    volume_trades = EXCLUDED.volume_trades,
                    counterparty_count = EXCLUDED.counterparty_count,
                    credit_rating = EXCLUDED.credit_rating,
                    updated_at = CURRENT_TIMESTAMP;
            """
            
            values = [
                (
                    r['ticker'], r['date'], r['tenor_years'], r['notional_usd'],
                    r['bid_price'], r['ask_price'], r['mid_price'], r['bid_ask_spread'],
                    r['volume_trades'], r['counterparty_count'], r['credit_rating']
                )
                for r in records
            ]
            
            execute_values(cur, query, values, page_size=1000)
            conn.commit()
            
            result_count = cur.rowcount
            logger.info(f"Upserted {result_count} records.")
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Upsert failed: {e}")
            raise
        finally:
            cur.close()
            conn.close()
        
        return {'inserted': result_count, 'updated': 0}
    
    def log_scrape(self, scrape_date: date, fetched: int, inserted: int, 
                   updated: int, errors: int, status: str, error_msg: str = None, 
                   duration: int = 0):
        """Log scrape operation."""
        conn = psycopg2.connect(**self.db_config)
        cur = conn.cursor()
        
        try:
            cur.execute("""
                INSERT INTO scrape_log (
                    scrape_date, rows_fetched, rows_inserted, rows_updated, 
                    parse_errors, status, error_message, duration_seconds
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """, (scrape_date, fetched, inserted, updated, errors, status, error_msg, duration))
            
            conn.commit()
            logger.info(f"Logged scrape for {scrape_date}: status={status}")
        finally:
            cur.close()
            conn.close()

if __name__ == '__main__':
    loader = SwapsLoader()
    # Example: loader.upsert_swaps([...])
```

### 2.4 Scheduled Ingestion Job

**File:** `scheduled_ingest.py`

```python
import logging
import os
from datetime import datetime, date
from apscheduler.schedulers.background import BackgroundScheduler
from dtcc_scraper import DTCCSwapsScraper
from db_loader import SwapsLoader

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_ingestion_job():
    """Main job: fetch DTCC data, parse, and load into database."""
    logger.info("=== Starting DTCC swaps ingestion ===")
    start_time = datetime.now()
    
    loader = SwapsLoader()
    scraper = DTCCSwapsScraper()
    
    try:
        # Fetch and parse
        records, parse_errors = scraper.scrape()
        
        # Load into database
        result = loader.upsert_swaps(records)
        
        # Log success
        duration = (datetime.now() - start_time).total_seconds()
        loader.log_scrape(
            scrape_date=date.today(),
            fetched=len(records),
            inserted=result['inserted'],
            updated=result['updated'],
            errors=parse_errors,
            status='success',
            duration=int(duration)
        )
        
        logger.info(f"Ingestion complete. Fetched {len(records)}, inserted {result['inserted']}. Duration: {duration:.1f}s")
    
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        duration = (datetime.now() - start_time).total_seconds()
        loader.log_scrape(
            scrape_date=date.today(),
            fetched=0,
            inserted=0,
            updated=0,
            errors=0,
            status='failed',
            error_msg=str(e),
            duration=int(duration)
        )

def start_scheduler():
    """Start background scheduler for daily ingestion."""
    scheduler = BackgroundScheduler()
    
    # Schedule job for 5 PM ET every business day
    scheduler.add_job(
        run_ingestion_job,
        'cron',
        hour=17,
        minute=0,
        day_of_week='mon-fri',  # Business days only
        id='dtcc_daily_ingest',
        name='DTCC Daily Swaps Ingestion'
    )
    
    scheduler.start()
    logger.info("Scheduler started. Daily ingestion scheduled for 5 PM ET on business days.")
    
    return scheduler

if __name__ == '__main__':
    # Test run
    run_ingestion_job()
```

---

## Phase 3: Query Layer

**File:** `swaps_query.py`

```python
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, date
import pandas as pd
import os

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 5432)),
    'database': os.getenv('DB_NAME', 'swaps'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'password'),
}

class SwapsQuery:
    def __init__(self, db_config: dict = None):
        self.db_config = db_config or DB_CONFIG
    
    def get_connection(self):
        return psycopg2.connect(**self.db_config)
    
    def query_by_ticker(self, ticker: str, days_back: int = 30) -> pd.DataFrame:
        """Get all swaps data for a ticker over last N days."""
        conn = self.get_connection()
        query = """
            SELECT ticker, date, tenor_years, notional_usd, mid_price, 
                   bid_ask_spread, volume_trades, counterparty_count
            FROM swaps
            WHERE ticker = %s AND date >= CURRENT_DATE - %s::INTERVAL
            ORDER BY date DESC, tenor_years ASC;
        """
        df = pd.read_sql(query, conn, params=(ticker.upper(), f'{days_back} days'))
        conn.close()
        return df
    
    def query_by_date(self, query_date: date) -> pd.DataFrame:
        """Get all swaps data for a specific date."""
        conn = self.get_connection()
        query = """
            SELECT ticker, date, tenor_years, notional_usd, mid_price,
                   bid_ask_spread, volume_trades, counterparty_count
            FROM swaps
            WHERE date = %s
            ORDER BY notional_usd DESC;
        """
        df = pd.read_sql(query, conn, params=(query_date,))
        conn.close()
        return df
    
    def get_ticker_summary(self, ticker: str, days_back: int = 30) -> dict:
        """Get summary stats for a ticker."""
        conn = self.get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        query = """
            SELECT 
                COUNT(*) as record_count,
                SUM(notional_usd) as total_notional,
                AVG(mid_price) as avg_price,
                COUNT(DISTINCT date) as trading_days,
                COUNT(DISTINCT tenor_years) as tenor_count,
                MAX(volume_trades) as max_daily_trades
            FROM swaps
            WHERE ticker = %s AND date >= CURRENT_DATE - %s::INTERVAL;
        """
        cur.execute(query, (ticker.upper(), f'{days_back} days'))
        result = cur.fetchone()
        conn.close()
        return dict(result) if result else {}
    
    def top_notional_tickers(self, date_filter: date = None, limit: int = 50) -> pd.DataFrame:
        """Get top tickers by total notional."""
        conn = self.get_connection()
        if not date_filter:
            date_filter = date.today() - timedelta(days=1)
        
        query = """
            SELECT ticker, SUM(notional_usd) as total_notional, 
                   COUNT(*) as tenor_count
            FROM swaps
            WHERE date = %s
            GROUP BY ticker
            ORDER BY total_notional DESC
            LIMIT %s;
        """
        df = pd.read_sql(query, conn, params=(date_filter, limit))
        conn.close()
        return df
    
    def export_to_csv(self, ticker: str, output_file: str, days_back: int = 30):
        """Export ticker data to CSV."""
        df = self.query_by_ticker(ticker, days_back)
        df.to_csv(output_file, index=False)
        print(f"Exported {len(df)} rows to {output_file}")

if __name__ == '__main__':
    q = SwapsQuery()
    
    # Example queries
    print("=== Top 10 Notional Tickers (Today) ===")
    top = q.top_notional_tickers(limit=10)
    print(top)
    
    print("\n=== Sample Ticker: AAPL (Last 30 days) ===")
    aapl = q.query_by_ticker('AAPL', days_back=30)
    print(aapl.head())
    
    print("\n=== AAPL Summary ===")
    summary = q.get_ticker_summary('AAPL', days_back=30)
    print(summary)
```

---

## Phase 4: Deployment

### 4.1 Requirements

**File:** `requirements.txt`

```
psycopg2-binary==2.9.9
requests==2.31.0
pandas==2.0.3
APScheduler==3.10.4
python-dotenv==1.0.0
```

### 4.2 Environment Configuration

**File:** `.env`

```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=swaps
DB_USER=postgres
DB_PASSWORD=your_password
```

### 4.3 Setup & Run

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Initialize database
python setup_db.py

# 3. Run first ingestion (test)
python scheduled_ingest.py

# 4. Start scheduler (runs in background)
python -c "from scheduled_ingest import start_scheduler; import time; scheduler = start_scheduler(); time.sleep(86400)"

# Or in production, use supervisor/systemd to keep scheduler running
```

---

## Phase 5: Integration with Sentiment Scanner

Once database is populated, replace DTCC API calls with local queries:

**In your sentiment scanner:**

```python
from swaps_query import SwapsQuery

def get_ticker_swaps_summary(ticker: str):
    """Replace DTCC API call with local DB query."""
    query = SwapsQuery()
    return query.get_ticker_summary(ticker, days_back=30)

# Use in scanner:
swaps_data = get_ticker_swaps_summary('AAPL')
# >>> {'record_count': 150, 'total_notional': 5000000000, 'avg_price': 102.5, ...}
```

---

## Monitoring

### Key Metrics to Watch
- **Ingestion success rate:** Check `scrape_log.status`
- **Parse errors:** Monitor `scrape_log.parse_errors` trend
- **Latency:** Track ingestion duration; should be < 5 minutes
- **Database size:** `SELECT pg_size_pretty(pg_total_relation_size('swaps'));`

### Sample Monitoring Query

```sql
SELECT 
    scrape_date, 
    status, 
    rows_fetched, 
    rows_inserted,
    parse_errors,
    duration_seconds
FROM scrape_log
ORDER BY scrape_date DESC
LIMIT 30;
```

---

## Next Steps

1. **Week 1:** Database schema + scraper + first ingestion run
2. **Week 2:** Scheduler setup + monitoring dashboard
3. **Week 3:** Backfill historical data (if available) + query layer optimization
4. **Week 4+:** Pattern discovery + sentiment scanner integration (Option D)

