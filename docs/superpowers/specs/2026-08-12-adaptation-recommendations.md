# Adaptation Recommendations From Migrated Tree

**Source compared**

- Current canonical repo: `C:\Users\bottl\FinancialDevelopment`
- Migrated tree: `C:\Users\bottl\Financial_Development_MIGRATED`

**Purpose**

This is the short, operator-facing version of the larger reconciliation spec. It answers the practical question: **what is worth adapting from the migrated tree, and what is not?**

## Executive take

Keep **`FinancialDevelopment`** as the base. It is ahead on platform, shared infrastructure, dashboard, tests, and operational wiring.

The migrated tree is still worth mining for **research/docs, specific scripts, targeted regression tests, one known sentiment fix, and a few optional suite-level experiments**. It is **not** worth using as a replacement base, and most generated artifacts should stay archived.

---

## Worth adapting now

### 1. Dealer-positioning and research docs

**Take: yes**

The migrated tree has higher-value research/history than the current repo in these areas:

- `docs/superpowers/specs/debate-20260811-v5-recommendations-spec.md`
- `docs/superpowers/specs/debate-20260811-v5-recommendations-verified-facts.md`
- `docs/superpowers/specs/debate-final-spec-20260808.md`
- `docs/superpowers/specs/per-expiry-direction-backtest-20260808.md`
- `DEALER_POSITIONING_V5_IMPROVEMENT_RECOMMENDATIONS_20260811.md`
- related battery/debate evidence docs under migrated `docs/superpowers/specs/`

**Why:** these are not throwaway notes; they capture reasoning and evidence that the current repo does not preserve as cleanly.

**How to adapt:** copy into `docs/archive/` or a dedicated `docs/research/` folder in the current repo, clearly labeled as historical analysis or evidence.

**Status:** archived in `docs/archive/2026-08-12-migrated-research/`

### 2. Workflow scripts

**Take: yes**

Highest-value migrated-only scripts:

- `scripts/burst_checkpoint.sh`
- `scripts/hooks/commit-msg`
- `scripts/verify_tradingview_submodule.sh`

**Why:** low-risk, practical workflow improvements.

**How to adapt:** port them as-is if compatible; otherwise lightly rewrite for current repo paths and conventions.

### 3. Options_Suite regression tests

**Take: yes**

Recommended ports:

- `Options_Suite/tests/test_active_imports.py`
- `Options_Suite/tests/test_gpu_parity.py`
- `Options_Suite/tests/test_heston_lsm_discount.py`

**Why:** these test exactly the sort of fragile pricing and import-regression behavior this repo is exposed to.

**How to adapt:** port tests, then adjust only imports/fixtures to current tree layout.

### 4. sentiment-scanner OI wiring

**Take: yes**

Compare migrated vs current:

- `sentiment-scanner/scanner/theta_integration.py`

**Why:** current docs already identified this as the strongest concrete candidate where migrated behavior is likely better.

**How to adapt:** do a direct file diff and port only the OI propagation logic, with regression tests.

---

## Worth reviewing carefully before adapting

### 5. VaR GPU/backend layer

**Take: maybe**

Migrated-only candidates:

- `VaR_Tools_Simulations/var_engine/backend.py`
- `VaR_Tools_Simulations/tests/test_gpu_parity.py`
- `VaR_Tools_Simulations/tests/test_greeks_exposure.py`
- `VaR_Tools_Simulations/tests/test_price_dist.py`

**Why:** could be useful if GPU support still matters, but this is optional complexity rather than obviously missing core behavior.

**Recommendation:** only adapt if you actively want GPU parity in VaR.

### 6. Options_Suite legacy quarantine pattern

**Take: maybe**

Migrated-only candidates:

- `Options_Suite/legacy/*`
- `Options_Suite/SPEC.md`
- `Options_Suite/PLAN_*`

**Why:** the pattern is useful, but current live wiring differs enough that copying blindly would likely confuse the active path.

**Recommendation:** borrow the **idea** of quarantine and import guards, not the exact structure.

### 7. Tool abstractions

**Take: maybe**

Migrated-only candidates:

- `Tools/cli.py`
- `Tools/tools/direction_common.py`

**Why:** may reduce duplication, but only if they fit the current `Tools/registry.py` shape cleanly.

**Recommendation:** inspect only if you plan to grow the current tool layer.

---

## Not worth adapting as live code

### 8. Dashboard

**Take: no**

Migrated-only dashboard items:

- `dashboard/templates/journal.html`
- `dashboard/templates/run_files.html`

**Why:** current dashboard is materially ahead: more modules, more tests, better output-run/quant surfaces, and stronger worker integration.

**Recommendation:** only borrow ideas if you miss those specific views.

### 9. Broad suite overwrites

**Take: no**

Do **not** wholesale replace current files in:

- `Direction/`
- `Vol_Suite/`
- `Options_Suite/`
- `VaR_Tools_Simulations/`

**Why:** both trees have drifted. Overwrites would almost certainly regress current work.

### 10. Generated artifacts

**Take: no**

Examples:

- `Vol_Suite/*.png`
- `Vol_Suite/outputs/*`
- `sentiment-scanner/data/exports/highlighted_ticker_packs/*`
- root logs, zip payloads, snapshot files

**Why:** evidence, not source.

**Recommendation:** archive only.

### 11. WSL control-plane files as active workflow

**Take: no**

Examples:

- `.hermes.md`
- `.hermes/`
- `findev.sh`
- `quant_workspace.sh`

**Why:** useful context, wrong operational base for the current Windows repo.

---

## Prioritized adaptation queue

If you want the next few changes in the best order, do them like this:

1. **Port migrated research/docs into curated current-repo docs**
2. **Diff and port `sentiment-scanner/scanner/theta_integration.py` OI logic**
3. **Port the three Options_Suite regression tests**
4. **Port `scripts/burst_checkpoint.sh` and `scripts/hooks/commit-msg`**
5. **Decide whether VaR GPU/backend is still worth pursuing**

---

## Bottom line

**Best things in migrated to adapt:**

- research/spec history,
- the targeted sentiment OI fix,
- a few strong Options/VaR tests,
- a few practical workflow scripts.

**Best things not to adapt:**

- broad dashboard replacement,
- broad suite overwrites,
- WSL operational scaffolding,
- generated outputs/logs.

**Rule of thumb:** adapt **evidence, tests, and narrow fixes**; avoid adapting **entire subsystems** unless there is a very specific missing capability.
