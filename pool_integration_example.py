"""Example: Integrating connection pooling into your application.

This demonstrates:
  1. Initializing the connection pool during application startup
  2. Using pooled connections with SwapsLoader
  3. Using pooled connections with SwapsQuery
  4. Monitoring pool performance
  5. Gracefully shutting down the pool
"""
import logging
from datetime import datetime

from setup_db import migrate, initialize_connection_pool, get_schema_status
from shared.connection_pool import get_pool, close_pool
from db_loader import SwapsLoader
from swaps_query import SwapsQuery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    """Example application startup with connection pooling."""

    # Step 1: Ensure schema is initialized
    print("\n[1] Checking database schema...")
    schema_status = get_schema_status()
    if schema_status['pending']:
        print(f"  Pending migrations: {schema_status['pending']}")
        print("  Running migrations...")
        migrate()
    else:
        print("  Schema is up to date.")

    # Step 2: Initialize connection pool for high-throughput ingestion
    print("\n[2] Initializing connection pool...")
    pool_config = {
        'pool_size': 5,           # Initial connections
        'max_pool_size': 20,      # Maximum connections
        'stale_connection_timeout': 3600,  # Close idle connections after 1 hour
        'health_check_interval': 60,       # Health check every 60 seconds
    }
    pool_result = initialize_connection_pool(**pool_config)
    if pool_result['ok']:
        print(f"  Pool initialized successfully: {pool_result}")
    else:
        print(f"  Pool initialization failed: {pool_result['error']}")
        return 1

    # Step 3: Use SwapsLoader with pooled connections
    print("\n[3] Using SwapsLoader with connection pooling...")
    loader = SwapsLoader(use_pool=True)

    # Set ingestion state
    loader.set_state('SEC', 'EQ', last_cumulative_date='2024-01-01')
    state = loader.get_state('SEC', 'EQ')
    print(f"  Ingestion state: {state}")

    # Upsert some test records
    test_records = [
        {
            'dissemination_id': 'test_001',
            'regulator': 'SEC',
            'asset_class': 'EQ',
            'action_type': 'NEW',
            'event_type': 'TRADE',
            'event_timestamp': datetime.now().isoformat(),
            'execution_timestamp': datetime.now().isoformat(),
            'effective_date': '2024-01-01',
            'expiration_date': '2025-01-01',
            'cleared': 1,
            'notional_amount_leg1': 1000000.0,
            'notional_currency_leg1': 'USD',
            'notional_amount_leg2': 2000000.0,
            'notional_currency_leg2': 'EUR',
            'price': 1.5,
            'price_currency': 'USD',
            'price_unit_of_measure': 'bps',
            'underlier_id_leg1': 'MSFT',
            'underlier_id_source_leg1': 'CUSIP',
            'underlying_asset_name': 'Microsoft Corp',
            'upi': 'UPI_001',
            'upi_fisn': 'FISN_001',
            'upi_underlier_name': 'MSFT',
            'source_file': 'test.csv',
            'raw_json': '{}',
        }
    ]
    result = loader.upsert_trades(test_records, data_source='EXAMPLE')
    print(f"  Upserted records: {result}")

    # Step 4: Use SwapsQuery with pooled connections
    print("\n[4] Using SwapsQuery with connection pooling...")
    query = SwapsQuery(use_pool=True)

    # Get database statistics
    stats = query.get_database_stats()
    print(f"  Database statistics:")
    print(f"    Total records: {stats['total_records']}")
    print(f"    Unique UPIs: {stats['unique_upis']}")
    print(f"    Date range: {stats['earliest_date']} to {stats['latest_date']}")

    # Step 5: Monitor pool performance
    print("\n[5] Monitoring connection pool performance...")
    pool = get_pool()
    if pool:
        stats = pool.get_stats()
        print(f"  Pool statistics:")
        for key, value in stats.items():
            print(f"    {key}: {value}")

    # Step 6: Demonstrate concurrent usage
    print("\n[6] Demonstrating concurrent usage (in production)...")
    print("  With connection pooling enabled:")
    print("  - Multiple backfill.py instances can run concurrently")
    print("  - Dashboard queries don't block data ingestion")
    print("  - WAL mode ensures readers never block writers")
    print("  - Idle connections are automatically cleaned up")

    # Step 7: Graceful shutdown
    print("\n[7] Gracefully shutting down connection pool...")
    close_pool()
    print("  Pool closed successfully.")

    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
