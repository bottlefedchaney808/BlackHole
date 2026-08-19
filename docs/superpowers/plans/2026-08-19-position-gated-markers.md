# Position-Gated Direction Markers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chart markers mimic buying in and buying out: sell (0/5 ▼) and hold (3/5 ●) only draw after a long (4/5 ▲ or 5/5 ◆) has already fired in the visible series.

**Architecture:** Display-only state machine. Raw Direction scores stay untouched. A pure helper walks overlay entries in order with an imaginary `position` count and returns a marker kind per bar. The candlestick draw loop uses those kinds. The Direction tool (`signal_generator.generate` and the five modules) is byte-identical.

**Tech Stack:** Python 3.12 repo venv, pytest, existing `shared/candlestick_chart.py` overlay.

## Global Constraints

- Indicator/chart display only. Do NOT modify `Direction/signal_generator.py`, `elliott_wave.py`, `bollinger_analyzer.py`, `trend_engine.py`, `whale_scanner.py`, `liquidity_map.py`.
- Do NOT change `bar_eval` / replay score values. Scores stay raw.
- Windows git-bash. Clean launcher: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe`.
- ThetaData/PH v2 only. Never yfinance.
- Never fabricate history. Unmatched bars stay unmarked.
- Window start is FLAT (`position = 0`). No phantom long from before the lookback.
- Sell FLATTENS (`position = 0`), it does not decrement one lot.
- Marker glyphs unchanged: 0/5 ▼ orchid above, 3/5 ● gray below, 4/5 ▲ lime below, 5/5 ◆ gold below, 1-2/5 none — but 0 and 3 only after a long.
- Live stamp stays score-based (`LIVE: NONE (3/5)`), not a fill.
- Stage ONLY files the task lists. Pre-existing dirty files (`.superpowers/sdd/*`, `sentiment-scanner/...`, `trading_journal/...`) stay unstaged.
- TDD: failing test first, then implement, then commit with the stated message.

---

### Task 1

- **Task 1 — Pure position gate helper.** Add `apply_position_gate(entries) -> list[str]` in `shared/candlestick_chart.py`. Walk entries in list order (already time-sorted by the CLI). Each entry is a dict with `"score"` (int). Return one kind per entry: `"buy"` / `"add"` / `"hold"` / `"sell"` / `"none"`.

State machine (verbatim):

```python
position = 0
kinds = []
for entry in entries:
    score = int(entry.get("score", 0) or 0)
    if score == 4:
        position = max(position, 1)
        kinds.append("buy")
    elif score == 5:
        if position == 0:
            position = 1
        else:
            position += 1
        kinds.append("add")
    elif score == 3 and position > 0:
        kinds.append("hold")
    elif score == 0 and position > 0:
        position = 0
        kinds.append("sell")
    else:
        kinds.append("none")
return kinds
```

Required tests in `tests/test_candlestick_chart.py` (TDD, exact fixture):

```python
def test_position_gate_buy_in_hold_sell_out():
    from shared.candlestick_chart import apply_position_gate
    entries = [{"score": s} for s in [0, 3, 4, 3, 0, 0]]
    assert apply_position_gate(entries) == ["none", "none", "buy", "hold", "sell", "none"]

def test_position_gate_add_increments_then_flatten():
    from shared.candlestick_chart import apply_position_gate
    entries = [{"score": s} for s in [5, 5, 0]]
    assert apply_position_gate(entries) == ["add", "add", "sell"]

def test_position_gate_empty_and_missing_score():
    from shared.candlestick_chart import apply_position_gate
    assert apply_position_gate([]) == []
    assert apply_position_gate([{}]) == ["none"]
```

Export `apply_position_gate` from `shared/candlestick_chart.py` (`__all__` if present). Do not change the draw loop in this task.

- [ ] Write the three failing tests
- [ ] Confirm they fail (ImportError / missing name)
- [ ] Implement `apply_position_gate`
- [ ] Confirm the three tests pass
- [ ] Commit: `feat(chart): position-gate helper so sell/hold require a prior long`

**Files:** Modify `shared/candlestick_chart.py`, `tests/test_candlestick_chart.py`

---

### Task 2

- **Task 2 — Wire the gate into the draw loop.** In `shared/candlestick_chart.py` `render_candlestick`, replace the per-bar `_marker_for_score(entry["score"])` call with the gated kind for that bar.

Required behavior:
1. Build `kinds = apply_position_gate(direction_overlay)` once before the draw loop (overlay list order = time order the CLI already guarantees). If overlay is empty/None, skip as today.
2. Match each bar via existing `_match_overlay_entry(obs.timestamp, direction_overlay)`. If no entry, no marker.
3. Kind for a matched entry is `kinds[overlay.index(entry)]` — BETTER: zip overlay with kinds into a dict keyed the same way `_match_overlay_entry` keys (prefer building `id(entry) -> kind` or pairing by overlay index when matching). Cleanest: have `_match_overlay_entry` return `(entry, index)` OR build `ts -> kind` from overlay+kinds using the same `_normalize_ts` used for `"ts"` keys (and date fallback for `"date"` keys). Use `ts -> kind` (and date -> kind for legacy). First-wins if duplicate ts (matches existing matcher).
4. Draw using that kind against the existing `marker_style` dict. Glyphs/colors/placement UNCHANGED.
5. `_marker_for_score` STAYS (raw mapping still used by existing unit tests). Gated path does not replace it.

Required test: same-day bars with scores `[0, 3, 4, 3, 0]` → annotate glyphs in order `none, none, buy, hold, sell` (reuse the existing monkeypatched `Axes.annotate` style in `tests/test_candlestick_chart.py` if present; otherwise assert via a small helper that the renderer consults `apply_position_gate`). Update any test that assumed a lone score-0 overlay draws a sell.

- [ ] Write/adjust failing renderer test for the `[0,3,4,3,0]` sequence
- [ ] Confirm fail
- [ ] Wire the draw loop
- [ ] `pytest tests/test_candlestick_chart.py -q` all pass
- [ ] Commit: `feat(chart): draw sell/hold only while a simulated long is open`

**Files:** Modify `shared/candlestick_chart.py`, `tests/test_candlestick_chart.py`

---

### Task 3

- **Task 3 — CLI `markers:` line + skill + live PNG.** In `scripts/render_direction_chart.py`, after printing `scores: [...]`, also print `markers: [...]` from `apply_position_gate(overlay)` so the gate is visible in stdout. Do not change the `scores:` series (raw).

Live run (required):

```bash
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe scripts/render_direction_chart.py SPY --interval 15m --lookback 5d
```

Report: PNG path, bar count, `scores:` line, `markers:` line. Expect many leading `none` where scores were 0/3 before any 4/5. If this window still has no 4/5 (whale timeout), `markers:` may be all `none` — that is CORRECT, not a bug. Document it.

Update (outside git, no commit):
- `C:/Users/bottl/AppData/Local/hermes/skills/quantitative-finance/run-direction-chart/SKILL.md`
- `C:/Users/bottl/AppData/Local/hermes/skills/quantitative-finance/render-quant-chart/SKILL.md`

Document: sell/hold markers require a prior buy/add in the visible window; window starts flat; sell flattens; raw scores unchanged.

Regression: `pytest tests/test_candlestick_chart.py tests/test_render_direction_chart.py tests/test_chart_request.py -q`

- [ ] Add a CLI unit test that a fake overlay `[0,4,0]` prints `markers:` containing `none, buy, sell` (monkeypatch replay like existing CLI tests)
- [ ] Confirm fail
- [ ] Print `markers:` from `apply_position_gate`
- [ ] Live SPY run + skill updates
- [ ] Commit ONLY the script + test: `feat(chart): print gated marker series next to raw scores`

**Files:** Modify `scripts/render_direction_chart.py`, `tests/test_render_direction_chart.py`; skills outside git.
