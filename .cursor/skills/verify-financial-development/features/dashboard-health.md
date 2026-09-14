# Dashboard health + overview

## Sub-features
- Liveness via `/health` (or empty-state-safe `/`)
- Overview HTML: swap summary card + recent `orchestrator_runs` history

## How to get to it (user POV)
1. Start the dashboard from `dashboard/` with uvicorn on port 8000.
2. Open `http://127.0.0.1:8000/` in a browser, or hit `/health` with curl.

## Driving it with HTTP
```powershell
curl -s -w "\n%{http_code}\n" http://127.0.0.1:8000/health -o ..\evidence\health.json
curl -s -w "\n%{http_code}\n" http://127.0.0.1:8000/ -o ..\evidence\overview.html
```
Expect HTTP 200. Cold DB / empty history must still render (empty state, not 500).

## Gotchas
- Bind stays on localhost; do not use `--host 0.0.0.0` for verify.
- Skip `--reload` during verify.
- README may still mention `POST /run/...`; Phase 7 moved live orchestration to widgets — prefer catalog/widget routes for new work.
