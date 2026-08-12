# Two-Vanna Divergence — Evidence Trail (2026-08-09)

User report: "My quant terminal is still showing two different vanna values after running vol suite... We are using only one dealer positioning model now so name on the outputs still say vol replication 2.1. Who knows what model is running you guys have fucked this up 3 times in a row so."

## The run

- Run dir: `orchestrator_output/9b868325b0c2487db238b088ec0fa670/` (UROY, 2026-08-09 05:38 CT)
- Bridge log: `orchestrator_output/bridge_jobs/9b868325b0c2487db238b088ec0fa670.log`
- Command: `volatility_suite.py --pack ... --chain-scan`

## The two numbers (both in ONE run's output)

| Path | Sign convention | Value |
|---|---|---|
| `dealer_positioning.py` comparison chart vanna panel | canonical per-strike signs (accumulated replication + SABR deviation) | ≈ **−450** sh/pp IV net (per chart read) |
| `options_chain_scanner.py` text, log line 801 | **hardcoded flat** `call=+1 / put=−1` | **+5,359** sh/pp IV net |

Reproduction of the scanner number (exact match to log):
```python
df = pd.read_csv('<run>/*chain_scan*.csv')
df['v'] = df['vanna'] * df['oi'] * 100 * 0.01 * (df['right'].map({'C':1.0,'P':-1.0}))
print(df['v'].sum())   # → 5359.1 == log line "+5,359"
```
The direction-model variants: bias=0 → 0.0; bias=+1 → −5359.1; bias=−1 → +5359.1 (== flat). So the flat path and bias=−1 are numerically identical — another reason a "direction" default can silently equal the flat heuristic.

## Root cause chain

1. `options_chain_scanner.py compute_vanna_positioning()` (L384-426) has **no sign_model parameter** and hardcodes `np.where(right=='C', 1.0, -1.0)`.
2. `dealer_positioning.py _resolve_sign()` resolves per-strike signs via the canonical direction/SABR path.
3. `volatility_suite.py` headless default is still `os.environ.get("DEALER_SIGN_MODEL", "vol_surface_replication")` → the run label the user sees says the OLD model name.
4. Both `_SIGN_MODEL_LABELS` dicts in `dealer_positioning.py` (L1299-1305, L1599-1605) still map 5 models including "Vol-Surface + Replication (v2.1)" → stale labels.

## Concurrent-writer incident (critical debugging lesson)

During the investigation the repo state changed under the agent:

- First `git diff` (14:31) showed working tree `CANONICAL_SIGN_MODEL = 'vol_surface_replication'` (index `3eef575`).
- Later reads (14:39-14:40) showed `CANONICAL_SIGN_MODEL = 'direction'` (index `5cab44b`), mtime **14:39:51** — 30s after the 4 improve-analysis subagents finished (14:39:19).
- `git status` changed between checks: `options_chain_scanner.py`, `volatility_suite.py`, test files, and `archive/` renames vanished from the modified list; reflog showed `reset: moving to HEAD`.
- File size oscillated across reads: 92,490 → 83,655 → 83,198 bytes.
- `ps aux` showed `coder`, `personal-bot`, `research` Hermes profile gateways running since 14:25.

Lesson: when file mtimes, git status/diff, reflog, or read results disagree across consecutive calls, a concurrent writer (another Hermes profile agent, or the user) is editing the tree. Pin the state before patching; never "fix" a file whose content you can't guarantee.

## The memory flip-flop failure (self-inflicted)

Mid-session the agent "corrected" memory to say the canonical model was `vol_surface_replication`, based on:
- a misread of an archived report filename (`2026-08-09-sabr-deviation-default-evidence.md`),
- the session's "direction with min_score=1 was not the final model" message (which referred to a specific whale-only CONFIG, not the direction sign model itself).

The truth (verified against the live file + pre-session memory + the user's own words): **canonical = `direction` (V5) with per-expiry SABR deviation**, single model, all alternates deleted. The mid-session memory edit had to be reverted in a batch. Rule: before flipping any memory/doc about the canonical dealer model, verify against `CANONICAL_SIGN_MODEL` in the actual source and the authoritative session — archive filenames and single agent messages are not evidence.

## Stale artifacts found (from the improve 4-agent audit, all verified)

- `dealer_positioning.py` L1299-1305 and L1599-1605: `_SIGN_MODEL_LABELS` dicts with 5 retired models + dead `sign_model == 'replication'` color branch.
- `volatility_suite.py` L412-417: 5-model interactive prompt defaulting to `vol_surface_replication`; L1114 headless default `vol_surface_replication`.
- `dealer_positioning.py` L1721-1726: CLI prompt defaulting `oi_heuristic`.
- Docs: `README.md`, `PROJECT_SPEC.md` (4 spots), `dealer_positioning_brief.md`, `debate-evidence-kit-20260808.md` all name the old defaults/models.
- Refuted claims (verified=false): scanner has no `DEFAULT_SIGN_MODEL` constant, no `scanner_vanna_title()`, no `_fetch_direction_bias` import; no `archive/dealer-positioning-alternates/` dir on disk (retired in place, not moved).

## Proposed fix (presented to user for approval)

1. `options_chain_scanner.py`: `compute_vanna_positioning()` takes `sign_model` and resolves through dealer_positioning's canonical path (no flat heuristic).
2. Labels/defaults: single `CANONICAL_SIGN_MODEL`-derived label everywhere; drop 5-model menus; headless default = `direction`.
3. Tests: `test_vanna_sign_model_invariant.py` + `test_vanna_positioning_parity.py` (synthetic chain through both paths, NaN rows, flip-strike parity).
4. `.hermes.md`: canonical-model contract + "dealer↔scanner change in same commit" rule.
