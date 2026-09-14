# Swaps dashboard (sibling app)

## Sub-features
- Standalone swap browser + JSON API on port **8788** (`swaps_dashboard`)

## How to get to it (user POV)
Separate process from the main Quant Console (see `swaps_dashboard.bat` / that
app's README). Main dashboard only reads a periodic JSON snapshot.

## Driving it with HTTP
Only when the change touches swaps_dashboard:
```powershell
curl -s -w "\n%{http_code}\n" http://127.0.0.1:8788/ -o ..\evidence\swaps-overview.html
```

## Gotchas
- Not required for every FinancialDevelopment verify pass.
- Do not confuse with main dashboard port 8000.
