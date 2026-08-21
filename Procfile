# Heroku process types for FinancialDevelopment.
#
#   web        -- FastAPI dashboard (dashboard/app.py exposes `app`)
#   scheduler  -- APScheduler DTCC ingestion loop (scheduled_ingest.py)
#   release    -- schema migration, runs before every new release goes live
#
# NOTE on `scheduler`: `python scheduled_ingest.py` with no flags only prints
# its argparse help and exits 0, which Heroku would treat as a crashed dyno and
# restart-loop forever. `--start-scheduler` is the flag that actually blocks on
# the APScheduler loop and honours SIGTERM via shutdown_signal.create_shutdown_manager.
#
# NOTE on persistence: Heroku dynos have an ephemeral filesystem. swaps.db is
# recreated empty on every dyno restart/deploy. See docs/guides/DEPLOY.md ("Heroku ->
# Persistence caveat") before running this anywhere that matters.

web: uvicorn dashboard.app:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 65 --log-level ${LOG_LEVEL:-info}
scheduler: python scheduled_ingest.py --start-scheduler
release: python setup_db.py --migrate
