# Feature map — FinancialDevelopment

Index of user-facing surfaces to prove. Prefer HTTP against the live dashboard
on `127.0.0.1:8000`, plus `pytest -m unit` for no-network gates.

| Feature | File | Primary proof |
| --- | --- | --- |
| Dashboard health + overview | `dashboard-health.md` | `GET /health`, `GET /` |
| Widget catalog | `widget-catalog.md` | `GET /api/widgets/catalog` |
| Unit test suite | `unit-tests.md` | `pytest -m unit` |
| Swaps dashboard (sibling) | `swaps-dashboard.md` | port 8788 when that app is in scope |

Start with **dashboard-health** on every verify pass.
