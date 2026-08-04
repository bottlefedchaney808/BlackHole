# Archived: 2026-07-29 Phase 1-5 session docs

These 16 files were produced in a single session on 2026-07-29, following a CARL
adversarial review that found the dashboard had no authentication. They describe
a "Phase 1-5" push toward a multi-user, cloud-deployed, production-hardened
version of this project.

They are archived, not deleted, because the underlying *code* several of them
describe (`shared/connection_pool.py`, `shared/query_monitor.py`,
`shared/data_source.py`, `shared/identifiers.py`, `migrations/`) is still live
and current. Only the docs and their framing are stale:

- **Auth**: `PHASE_1_QUICK_START.md`'s headline fix ("Dashboard Auth: Open to
  anyone → Requires API key") describes a posture that was later *deliberately
  reversed* (commit `88a7912`, 2026-08-03) — this is a single-user, localhost-only
  tool, and the API-key gate was removed on that basis. See `dashboard/auth.py`.
- **Deployment**: `PHASE_3_PLANNING.md`'s Heroku/k8s roadmap and
  `PRODUCTION_READY_HANDOFF.md`'s "production-ready, enterprise-scalable"
  framing contradict `CLAUDE.md`'s current, explicit statement that there is no
  cloud deployment path in active use.

None of these files are referenced from `CLAUDE.md`. Moved here per
`docs/PROJECT_AUDIT_AND_SPEC.md`'s recommendation, with explicit user sign-off,
on 2026-08-04.
