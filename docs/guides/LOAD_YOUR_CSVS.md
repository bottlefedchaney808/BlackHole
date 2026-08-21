# Loading DTCC Swap Data — 2 Commands, No Downloads

This used to describe a manual CSV-download workflow. It no longer applies —
the pipeline pulls data straight from DTCC's public API now.

```bash
python setup_db.py
python backfill.py
```

That loads the full available history (about a year) of SEC and CFTC
Equities swap data into `swaps.db`.

Then check what you got:

```bash
python swaps_query.py
```

## Keeping it current

```bash
python scheduled_ingest.py --start-scheduler
```

Leave this running and it keeps polling for new trades every 5 minutes —
nothing to repeat manually. See `DTCC_LOCAL_SETUP.md` for details.
