# Unit test suite

## Sub-features
- `pytest -m unit` across suites listed in `pyproject.toml` `testpaths`

## How to get to it (user POV)
Developer proof with no network or credentials.

## Driving it with pytest
From repo root, activated `.venv`:
```powershell
.\.venv\Scripts\python.exe -m pytest -m unit -q --tb=short | Tee-Object -FilePath .cursor\skills\verify-financial-development\evidence\pytest-unit.txt
```
Expect exit code 0 (or a known baseline failure list documented in the PR).

## Gotchas
- Do not require GPU / ThetaData / live market credentials for `-m unit`.
- Prefer this gate for shared/ and dashboard pure-logic changes.
