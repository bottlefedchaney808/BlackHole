# Widget catalog

## Sub-features
- `GET /api/widgets/catalog` lists runnable widgets
- (Optional) `POST /api/widgets/{slug}/run` for a single safe slug

## How to get to it (user POV)
Quant Console widget picker / generic widget API (Phase 7). Catalog is the
read-only entry; runs are synchronous in-process.

## Driving it with HTTP
```powershell
curl -s http://127.0.0.1:8000/api/widgets/catalog -o ..\evidence\widgets-catalog.json
```
Expect HTTP 200 and a JSON list/object naming slugs. Only POST a run when the
change under test is a widget and the slug is unit-safe / non-billed.

## Gotchas
- Do not fire long suite / market-data widgets unless the user asked.
- Capture catalog JSON as the default proof for catalog-only changes.
