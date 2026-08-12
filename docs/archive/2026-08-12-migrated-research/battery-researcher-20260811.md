# RESEARCHER — final pre-registered test battery (2026-08-11)

persona: RESEARCHER
overview:
- Falsification-first, cheapest-measurement-first: units gate, then the cheapest sign-content measurement, then the joint test with per-branch verdict map, then my two falsifiers (dual-inversion, competitive OOS vs locked V5).
- All tests ride ONE cached bulk payload (Jason's bulk-pull-save-reuse) — ~2.5-3 days total, zero capital; no number is read before the parity gate closes.
- Branch (b) Δvanna drift shares ZERO inputs with M2 — so no outcome can be dismissed as re-parameterization; that is what makes every verdict load-bearing.

tests:
  - id: T-RES-01
    hypothesis: "VANNA_PP_SCALE=0.01 is the right convention: ThetaData rec.vanna ≈ 0.01 × BS-vanna to factor-2, and put-call vanna parity ≈ 0 per strike."
    channel: One SPY expiry, bulk hist/all_greeks (shared/thetadata.py:509-536, vanna already in rows), cached to .shared_cache; BS-vanna same day/chain; VV via Options_Suite/VannaVolga.py:254 vv_all_greeks (RR/BF from :23 get_auto_rr_bf, one staged cached fetch).
    method: Per (strike,right)-day: s = median(rec.vanna / bs_vanna) pooled; per-strike parity |vanna_C(K)+vanna_P(K)| / mean|vanna| (never net-across-rights).
    thresholds: 0.005 ≤ s ≤ 0.02 (confirms 0.01, dealer_positioning.py:48-61); per-strike parity ≤ 1e-6; nothing read until both hold.
    verdict: pass -> units real, T-RES-02/03/04/05 read; fail -> convention wrong, freeze every accumulation number, escalate to Jason.
    cost: 0.5-1 day; 1 bulk fetch + 1 staged VV RR/BF fetch, both cached.
    kill: parity > 1e-6 OR s outside [0.005,0.02] -> battery dead until scale resolved.

  - id: T-RES-02
    hypothesis: "If flat-BS and VV smile-aware vanna agree on ≥95% of OTM (strike,right) cells, sign content is a flat-sigma artifact (BS sign ≡ +1 degenerate) and VV is a second desk quoting the same sign. If they disagree on ≥5-10% of cells, smile curvature carries sign content and VV earns the 4th column of the sign-agreement matrix."
    channel: Same cached payload as T-RES-01, same (strike,right)-day cells, T-floor 3-5d, 0DTE bucketed separately.
    method: Per-cell sign(VV-vanna) vs sign(BS-vanna) on the same run; build the 4-column sign-agreement matrix [mechanical sign(vanna) | SABR rich/cheap | ΔOI flow sign | VV-vanna sign] — disagreement cells = the information (volga-vs-delta split).
    thresholds: disagreement ≥10% -> VV column lives; 5-10% -> record, conditional; <5% -> VV retired. Sign source stays locked SABR regardless — VV is a measurement arm, never a sign source.
    verdict: ≥10% -> two-desk measurement; VV usable as OPPOSE-cell tiebreaker (real volga book vs marking noise); <5% -> BS-vanna + SABR sign sufficient, VV dies quietly.
    cost: 0 extra days, 0 extra fetches (rides T-RES-01 payload).
    kill: <5% kills VV's seat permanently — do not resurrect for the OPPOSE tiebreaker.

  - id: T-RES-03
    hypothesis: "Branch (b) Δvanna greek drift (SABR-signed) shares zero inputs with M2 delta-OI flow, so the joint test is decisive: collinear (≥80% 2×2 agreement or ρ≥0.7) = two independent channels agree -> M2 inversion ROBUST, not artifact; opposite (≤40%, ρ≤−0.3, clustered) = separate volga book, inversion real; grey = lead-lag decides."
    channel: backtest_stage3.py:656 _build_day_records — add book_b column (Δvanna drift, T-floor 3-5d, 0DTE bucketed separately, per (strike,right) cell, NEVER net-across-rights); M2 = _net_delta_oi (backtest_stage3.py:833); pooled_panel_backtest.py:90 _pooled_regression with book_b second regressor (block-shuffle perm p — M2's is 0.0245); known-answer fixture pins |book_c − (book_a + book_b)| ≤ 1e-9.
    method: (i) 2×2 per (strike,right)-day sign-agreement; (ii) Spearman of DAILY net changes (levels are integrated); (iii) strike-FE + day-FE regression; (iv) lead-lag k∈{0,1,2}; (v) clustered-disagreement (≥2/3 contiguous strikes); (vi) permutation-null power floor: ≥80% power to detect ρ=0.3 at realized N (block shuffle), else verdict downgraded to suggestive; (vii) verdict must survive the 5-perturbation battery (seed ±30d, seed-zero, 0DTE split, half-life 10/30, sign-source VV residual).
    thresholds: AGREE/robust ≥80% (or ρ≥0.7) + day controls + perturbation survival; OPPOSE/volga ≤40% (ρ≤−0.3) + clustered + survival; GREY 40-80% -> strong k=1 lead => half-size lead trade, else kill. Min N: ≥30 distinct trading days AND ≥200 pooled cells, else no verdict (SPY-30d lesson: 3 usable days was never decision-grade). Anti-fishing: re-pricing half ≥60% of variance -> flow half's verdict binds.
    verdict: AGREE -> M2 inversion is machinery-independent, escalate as market property, no new book; OPPOSE -> real volga book, fund next round ≤25% M2 notional-equivalent, top-3 days <50%, no stacking; GREY -> lead-lag decides, else no claim.
    cost: 1-1.5 days, zero new fetches (vanna in cached rows; ~5-10 lines to parse vanna_by_date beside replication_reference.py:540-544).
    kill: permutation power <80% at realized N -> no verdict; anti-fishing flow-verdict binds; parity failure upstream.

  - id: T-RES-04
    hypothesis: "Dual-inversion: if book_b ALSO predicts 5-10d forward realized skew/vol inverted vs its own rich=short theory while M2 stays inverted, the inversion is a market property — two independent channels cannot both invert by artifact; dealer vanna-hedging genuinely runs opposite the flow story."
    channel: Same panel as T-RES-03; forward helpers already exist (sentiment_backtest.py:85-121, 5d/10d); realized skew = IV-spread change, realized vol = ATM IV / return-vol change.
    method: Block-shuffle pooled regression of forward realized skew/vol on book_b, same grid; require BOTH channels inverted at perm p<0.05; power floor ≥80% both.
    thresholds: both inverted p<0.05 -> strongest 'real' verdict; book_b theory-direction + M2 inverted -> volga-vs-delta split (M2 inversion attributed to vanna offset); else no dual verdict, T-RES-03 mapping stands.
    verdict: dual-inversion -> market property, the only verdict that survives all five perturbations by construction; split -> separate books confirmed, attribution settled; no dual -> fall back.
    cost: 0 extra days (rides T-RES-03 run + forward helpers).
    kill: T-RES-03 grey-and-inconclusive -> dual test cannot bind.

  - id: T-RES-05
    hypothesis: "The vanna arm earns its keep ONLY if it beats locked V5 on 5-10d forward realized skew/vol AND decorrelates from M2 (day-FE'd) — otherwise it is re-parameterization of inputs V5 already contains (direction + sabr_deviation); null holds."
    channel: V5 leg = backtest_stage3.py:831-832 _net_gamma_v5_direction (LEVEL OI, no vanna — locked); book_b column; M2; forward 5-10d realized skew/vol from cached payload.
    method: Panel regression of forward realized skew/vol on {V5, book_b, M2}, block shuffle; book_b incremental t net of V5; partial ρ(book_b, M2) with day FE.
    thresholds: PASS = book_b t>2 with V5 in model AND day-FE'd |ρ(book_b,M2)| ≤ 0.3; REDUNDANT = t≤2 with high M2/V5 corr; DECORRELATED-DEAD = |ρ|≤0.3 but t≤2.
    verdict: PASS -> vanna channel funds as conviction overlay on M2 expression; DECORRELATED-DEAD -> separate channel, no predictive content -> kill arm, keep M2; REDUNDANT -> re-parameterization confirmed -> kill arm, M2 inversion stands as the only live anomaly.
    cost: 0.5-1 day, zero new fetches.
    kill: REDUNDANT or DECORRELATED-DEAD kills the vanna arm outright — no re-parameterization, no re-signed variant, no VV resurrection.

insight: "Whatever the joint test reads, the two legs shared zero inputs — the only open question is whether the M2 inversion is machinery or market, and this battery answers it with a decision rule, for ~3 days and zero dollars."
