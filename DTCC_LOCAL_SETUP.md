# DTCC Swap Data — Automatic Ingestion

There is no manual download step. The old premise here — "the dashboard is
JavaScript-rendered, so we can't scrape it" — was based on never having found
the real API underneath the dashboard. It exists: `pddata.dtcc.com/ppd/api/...`
is a public, unauthenticated JSON API that the dashboard itself calls, and it's
what this pipeline uses directly.

## One-time setup

```bash
python setup_db.py
python backfill.py
```

`backfill.py` pulls the full available history (about a year of daily data)
for SEC and CFTC Equities swaps straight from DTCC's public cumulative-report
API and loads it into `swaps.db`. No downloads, no manual clicking through
the dashboard.

## Ongoing (automatic)

```bash
python scheduled_ingest.py --start-scheduler
```

Leave this running. It polls DTCC's live slice feed every 5 minutes and
loads anything new. Set `POLL_INTERVAL_MINUTES` in `.env` to change the
interval.

Other useful commands:

```bash
python scheduled_ingest.py --run-now     # one poll pass, then exit
python scheduled_ingest.py --backfill    # re-run the full backfill
python swaps_query.py                    # check what's in the database
```

## Data source

- Live feed: `GET https://pddata.dtcc.com/ppd/api/slice/{SEC|CFTC}/EQ`
- Historical: `GET https://pddata.dtcc.com/ppd/api/cumulative/{SEC|CFTC}/EQ`

Both return public S3 file links, no authentication required.
