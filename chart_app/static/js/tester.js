/* Live strategy tester: move a knob, watch the marks and the P&L move.

   Modelled on TradingView's Strategy Tester, with one difference that matters:
   it scores at NEXT-BAR-OPEN fills with costs charged (see
   `chart_app/backtest.py`), so the number it shows is one you could have
   taken. The marks it draws come from the live state machine, which records a
   decision on the bar that triggered it -- so the arrow sits on the signal bar
   while the P&L assumes the bar you could actually trade.

   It runs entirely against the bars already cached for the chart. Dragging a
   slider never calls a data provider and never costs anything.
*/

window.CTest = (function () {
  const { compact } = window.CT;

  // [key, label, min, max, step, default, target]
  // `target` says which engine the knob belongs to: the signal engine's
  // config, or ELMo's own defaults.
  /* Every level the state machine actually reads, in the order it reads them.
     `add_long` and `trim_long` were missing, so two of the four long levels
     were unreachable from the UI -- the panel implied entry/exit were the
     whole rule while a position could also scale in at +55 and scale out at
     +12 with no way to see or move either. The short pair appears for the
     same reason: `allow_short` was a checkbox that turned on two levels
     nobody could set.
     The 6th column is only a FALLBACK. Real values are seeded from
     `GET /api/indicator-defaults` (`signal`), so a slider can never sit at a
     number the engine does not use -- same rule the gears follow. */
  const KNOBS = [
    ["entry_long",      "entry",        0,  100,  1,   30,   "config"],
    ["add_long",        "add",          0,  100,  1,   55,   "config"],
    // "de-risk", not "trim": it sells one exit chunk when conviction WEAKENS
    // to this level. Profit-taking is the separate `take profit %` box.
    ["trim_long",       "de-risk",    -40,   80,  1,   12,   "config"],
    ["exit_long",       "exit",       -60,   20,  1,  -12,   "config"],
    ["entry_short",     "entry short", -100,  0,  1,  -35,   "config"],
    ["add_short",       "add short",   -100,  0,  1,  -55,   "config"],
    ["trim_short",      "de-risk short", -80, 40, 1,  -12,   "config"],
    ["exit_short",      "exit short",  -40,  40,  1,    8,   "config"],
    /* SIZING, as two gates plus an exit and a financing dial -- see
       `chart_app/sizing.py`. All percents here, fractions in the config, so a
       saved profile means the same thing on any pool size.
         invest %   gate 1: how much of the capital the full position is
         order %    gate 2: how much of THAT each entry/add order buys
                    (100 = all in at once)
         exit %     how much of the full position each trim / scale-out
                    order sells (12.5 = eighths)
         borrowed % share of each buy that is a margin loan. Same shares,
                    less cash, plus interest. 0 = cash; the server caps it
                    at 80% and at what the venue lends (Reg T 50%). */
    ["position_size",   "invest %",       1, 100,  1,  100,  "pct"],
    ["entry_slice",     "order %",        1, 100,  1,   33,  "pct"],
    ["exit_slice",      "exit %",         1, 100, 0.5,  33,  "pct"],
    ["margin_pct",      "borrowed %",     0,  80,  5,    0,  "pct"],
    ["atr_stop_mult",   "atr stop",     0.5, 12,  0.5,  4.0, "config"],
    // Bars between ANY two orders -- so with a scaled exit it is the spacing
    // of the sells. A stop, a margin call or an all-out exit ignores it.
    ["cooldown_bars",   "cooldown bars", 0,  30,  1,    3,   "config"],
    ["cost_bps",        "cost bps",     0,   50,  0.5,  2.0, "cost"],
  ];

  // Shipped values from the server, so this panel never hardcodes a default.
  let shipped = {};
  // The saved profile live on the chart, fed in by `app.js` from
  // `state.profile`. It OVERRIDES shipped for every key it carries.
  let profile = null;

  /* A knob's baseline: the saved profile's value, else shipped, else the
     table's fallback.

     The profile layer is new, and its absence was half of "my presets don't
     load". `server.post_backtest` has always merged the active profile under
     the request -- but this panel posts every knob it owns, so the merge only
     ever saw the shipped value this panel had put there. The chart drew the
     profile, the tester scored shipped, and the two disagreed silently. */
  function seed(key, fallback) {
    if (profile && profile.config && profile.config[key] != null) {
      return profile.config[key];
    }
    const v = shipped[key];
    return v == null ? fallback : v;
  }

  /* The sizing knobs in PERCENT, from whichever config is in force.

     A profile saved before the sizing model has no `position_size`; it has
     `max_units` x `unit_fraction`, and the engine resolves that to the same
     ladder (`sizing.resolve_sizing`). Showing it the same way here keeps a
     loaded old profile from silently re-scoring as the shipped 100/33/33. */
  function sizingPct() {
    const cfg = Object.assign({}, shipped, (profile && profile.config) || {});
    if (cfg.position_size != null) {
      return {
        position_size: 100 * cfg.position_size,
        entry_slice: cfg.entry_style === "all" ? 100 : 100 * (cfg.entry_slice || 1),
        exit_slice: 100 * (cfg.exit_slice || 1 / 3),
        margin_pct: 100 * (cfg.margin_pct || 0),
      };
    }
    const m = Math.max(1, parseInt(cfg.max_units || 3, 10));
    const uf = cfg.unit_fraction ? Number(cfg.unit_fraction) : 1 / m;
    const size = m * uf;
    return {
      position_size: 100 * size,
      entry_slice: 100 / m,
      exit_slice: 100 / m,
      margin_pct: size > 1 ? Math.min(80, 100 * (1 - 1 / size)) : 0,
    };
  }

  /* Gate 1's ceiling follows the borrowing dial: at 0% borrowed you can
     invest at most 100% of the capital; at 50% borrowed, 200%. */
  function boundInvest() {
    const inv = document.getElementById("t_position_size");
    const mar = document.getElementById("t_margin_pct");
    if (!inv || !mar) return;
    const m = Math.min(80, Math.max(0, parseFloat(mar.value) || 0));
    inv.max = String(Math.round(100 / (1 - m / 100)));
    if (parseFloat(inv.value) > parseFloat(inv.max)) inv.value = inv.max;
    const out = document.getElementById("v_position_size");
    if (out) out.textContent = inv.value;
  }

  function paintKnobs() {
    const pct = sizingPct();
    // The borrow dial first, so gate 1's ceiling is right before it is set.
    const order = KNOBS.slice().sort(function (a, b) {
      return (a[0] === "margin_pct" ? -1 : 0) - (b[0] === "margin_pct" ? -1 : 0);
    });
    order.forEach(function (k) {
      if (k[6] === "pct") {
        const el = document.getElementById("t_" + k[0]);
        const out = document.getElementById("v_" + k[0]);
        if (!el) return;
        if (k[0] === "position_size") boundInvest();
        el.value = Math.round(pct[k[0]] * 10) / 10;
        if (out) out.textContent = el.value;
        return;
      }
      const base = seed(k[0], null);
      if (base == null) return;
      const el = document.getElementById("t_" + k[0]);
      const out = document.getElementById("v_" + k[0]);
      if (!el) return;
      el.value = base;
      if (out) out.textContent = el.value;
    });
    // The non-slider controls carry profile values too, and they were the
    // easiest to miss: `allow_short` and `exit_style` are strategy, not
    // cosmetics.
    const shortEl = document.getElementById("t_allow_short");
    if (shortEl && profile && profile.config && profile.config.allow_short != null) {
      shortEl.checked = !!profile.config.allow_short;
    }
    const styleEl = document.getElementById("t_exit_style");
    if (styleEl && profile && profile.config && profile.config.exit_style) {
      // Old profiles say "full"; the model calls it "all".
      styleEl.value = profile.config.exit_style === "scale" ? "scale" : "all";
    }
    const fracEl = document.getElementById("t_fractional");
    if (fracEl) fracEl.checked = !!(profile && profile.config && profile.config.fractional);
    const tpEl = document.getElementById("t_take_profit");
    if (tpEl) {
      const tp = profile && profile.config && profile.config.take_profit;
      tpEl.value = Array.isArray(tp) ? tp.join(", ") : tp || "";
    }
    const capEl = document.getElementById("t_capital");
    if (capEl && profile && profile.capital != null) capEl.value = profile.capital;
  }

  /* Called by `app.js` on the first frame and on every symbol change. */
  function seedProfile(next) {
    profile = next || null;
    paintKnobs();
    if (enabled) schedule();   // the panel's numbers moved; rescore them
  }

  async function loadDefaults() {
    try {
      const res = await fetch("/api/indicator-defaults");
      const body = await res.json();
      shipped = Object.assign({}, body.signal || {});
    } catch (e) {
      // The fallbacks in KNOBS still stand; the panel must not fail to build
      // because a defaults fetch did.
    }
    paintKnobs();
  }

  /* INDICATOR periods are deliberately NOT here.
     ELMo's nine windows belong to the gears (`gears.js` TABLE.elmo), which is
     the one place indicators are configured. This panel used to duplicate four
     of them with different ranges, which meant the same parameter had two UIs
     that could disagree -- and the reading the backtest scored was then not
     the reading the chart was drawing.
     The split: indicators decide what is READ, this panel decides what is DONE
     with the read (levels, stop, cooldown, costs). The ELMo settings the run
     uses are inherited from the gears via `CGear.current()`. */

  let debounce = null;
  let lastResult = null;
  let onResult = function () {};
  let enabled = false;

  function values() {
    // `elmo` comes from the gears, never from this panel -- so the backtest
    // scores exactly the indicator reads the chart is drawing.
    const gears = window.CGear ? window.CGear.current() : { elmo: {} };
    const out = {
      config: {},
      elmo: gears.elmo || {},
      cost_bps: 2.0,
      capital: 1000000,
    };
    const capEl = document.getElementById("t_capital");
    if (capEl) {
      const cap = parseFloat(String(capEl.value).replace(/[^0-9.]/g, ""));
      if (!isNaN(cap) && cap > 0) out.capital = cap;
    }
    // Empty date = "whatever the cache holds on that side".
    const s = document.getElementById("t_start");
    const e = document.getElementById("t_end");
    if (s && s.value) out.start = s.value;
    if (e && e.value) out.end = e.value;
    KNOBS.forEach(function (k) {
      const el = document.getElementById("t_" + k[0]);
      if (!el) return;
      const v = parseFloat(el.value);
      if (isNaN(v)) return;
      if (k[6] === "config") out.config[k[0]] = v;
      else if (k[6] === "pct") out.config[k[0]] = v / 100;
      else if (k[6] === "elmo") out.elmo[k[0]] = v;
      else out.cost_bps = v;
    });
    const shortEl = document.getElementById("t_allow_short");
    if (shortEl) out.config.allow_short = !!shortEl.checked;

    const styleEl = document.getElementById("t_exit_style");
    if (styleEl) out.config.exit_style = styleEl.value;

    // Gate 2 at 100% IS all-in; there is no separate switch to disagree with it.
    out.config.entry_style = "scale";
    const fracEl = document.getElementById("t_fractional");
    if (fracEl) out.config.fractional = !!fracEl.checked;
    const tpEl = document.getElementById("t_take_profit");
    if (tpEl) {
      out.config.take_profit = String(tpEl.value)
        .split(/[,;\s]+/)
        .map(function (x) { return parseFloat(String(x).replace("%", "")); })
        .filter(function (x) { return !isNaN(x) && x > 0; });
    }
    return out;
  }

  function paintStats(m, err, win) {
    const el = document.getElementById("testerStats");
    if (err) {
      el.innerHTML = '<span class="bad">' + err + "</span>";
      return;
    }
    const cls = function (v) { return v > 0 ? "good" : v < 0 ? "bad" : ""; };
    const pf = m.profit_factor;
    // Beating buy-and-hold is the only comparison that matters for a
    // long-only timing rule, so it sits next to the return rather than
    // buried -- a green +30% against a +90% hold is a losing strategy.
    const edge = m.total_return_pct - m.buy_hold_pct;
    /* Dollars AND percent side by side: the dollar figure is the one that
       reads at a glance while tuning, the percent is the one that compares
       across symbols and windows. `money` is the percent scaled by the
       capital box -- not an account balance, so it is labelled "P&L on $100k"
       rather than left as a bare figure (a bare dollar amount in this app has
       already been misread as profit once -- see the whale cutoff in app.js). */
    const money = function (v) {
      return (v >= 0 ? "+$" : "-$") + compact(Math.abs(v));
    };
    el.innerHTML =
      stat("P&L on " + "$" + compact(m.capital), money(m.pnl_dollars), cls(m.pnl_dollars)) +
      stat("return", m.total_return_pct.toFixed(1) + "%", cls(m.total_return_pct)) +
      stat("buy&hold", m.buy_hold_pct.toFixed(1) + "%", "") +
      stat("vs b&h", (edge >= 0 ? "+" : "") + edge.toFixed(1) + "%  " +
           money(m.excess_vs_bh_dollars), cls(edge)) +
      stat("trades", m.trades, "") +
      stat("win", m.win_rate.toFixed(0) + "%", "") +
      stat("PF", pf == null ? "n/a" : pf.toFixed(2), pf == null ? "" : cls(pf - 1)) +
      stat("max DD", m.max_drawdown_pct.toFixed(1) + "%  " +
           money(-Math.abs(m.max_drawdown_dollars)), "bad") +
      stat("sharpe", m.sharpe.toFixed(2), cls(m.sharpe)) +
      stat("expo", m.exposure_pct.toFixed(0) + "%", "") +
      // Average share of CAPITAL at work, not share of bars in the market.
      // Buy-and-hold is 100%, so a return that trails it at 40% capital is a
      // different animal from one that trails it fully invested.
      stat(
        "capital used",
        (m.avg_exposure_pct == null ? "-" : m.avg_exposure_pct.toFixed(0) + "%") +
          "  " + (m.capital_model || ""),
        m.capital_model && m.capital_model !== "cash" ? "warn" : ""
      ) +
      // What the ledger paid to trade and to borrow. Interest is only ever
      // non-zero with the borrowed dial up on a venue that charges it.
      stat("fees", money(-(m.fees_dollars || 0)), (m.fees_dollars || 0) > 0 ? "bad" : "") +
      stat(
        "margin interest",
        money(-(m.interest_dollars || 0)) +
          (m.margin_calls ? "  " + m.margin_calls + " margin call" + (m.margin_calls > 1 ? "s" : "") : ""),
        m.margin_calls ? "bad" : (m.interest_dollars || 0) > 0 ? "warn" : ""
      ) +
      (m.margin_capped && m.venue
        ? stat(
            "borrow capped",
            (100 * m.sizing.margin_pct).toFixed(0) + "% max on " + m.venue.name,
            "warn"
          )
        : "") +
      stat("avg hold", m.avg_bars_held.toFixed(0) + "b", "") +
      // Which bars this actually scored. A narrowed window is called out,
      // because an out-of-sample check is only meaningful if you can see the
      // span it ran on.
      (win
        ? stat(
            win.narrowed ? "window (narrowed)" : "window",
            win.start + " → " + win.end + "  " + win.bars + "b",
            win.narrowed ? "warn" : ""
          )
        : "");
  }

  /* Every fill the ledger made, newest at the bottom: the thing the old unit
     ladder could never show. "BUY 1,052 @ 95.20  $100.2k" is an order a
     broker would print; ".33 of a unit" was not. */
  function paintOrders(orders) {
    const el = document.getElementById("testerOrders");
    if (!el) return;
    if (!orders || !orders.length) {
      el.innerHTML = "";
      return;
    }
    const qtyFmt = function (q) {
      return q >= 100 || q === Math.floor(q)
        ? Math.round(q).toLocaleString()
        : q.toPrecision(4);
    };
    const tail = orders.slice(-60);
    el.innerHTML =
      (orders.length > tail.length
        ? '<div class="o"><i>' + (orders.length - tail.length) + " earlier orders</i></div>"
        : "") +
      tail
        .map(function (o) {
          const cls = o.side === "buy" ? "buy" : "sell";
          return (
            '<div class="o"><span>' + String(o.ts).slice(0, 16).replace("T", " ") + "</span>" +
            '<b class="' + cls + '">' + o.action.toUpperCase() + "</b>" +
            "<span>" + qtyFmt(o.qty) + " @ " + o.price.toFixed(o.price < 10 ? 4 : 2) +
            (o.borrowed > 0 ? "  (loan $" + compact(o.borrowed) + ")" : "") +
            (o.reason === "atr_stop" || o.reason === "margin_call" || o.reason === "take_profit"
              ? "  " + o.reason
              : "") +
            "</span>" +
            "<span>$" + compact(o.notional) + "</span></div>"
          );
        })
        .join("");
    el.scrollTop = el.scrollHeight;
  }

  function stat(label, value, cls) {
    return (
      '<span class="stat"><i>' + label + '</i><b class="' + (cls || "") + '">' +
      value + "</b></span>"
    );
  }

  async function run() {
    if (!enabled) return;
    const body = values();
    document.getElementById("testerStats").classList.add("busy");
    try {
      const res = await fetch("/api/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const out = await res.json();
      if (!out.ok) {
        paintStats(null, out.error || "backtest failed");
        lastResult = null;
      } else {
        paintStats(out.metrics, null, out.window);
        paintOrders(out.orders);
        boundDates(out.cache_span);
        lastResult = out;
      }
    } catch (e) {
      paintStats(null, String(e));
      lastResult = null;
    } finally {
      document.getElementById("testerStats").classList.remove("busy");
      onResult(lastResult);
    }
  }

  async function saveProfile(scope) {
    const v = values();
    /* The live runners (`run_live_perp`, `run_live_equity`, `perp_sleeve`)
       still size with `units x unit_fraction`. Publishing max_units=1 and
       unit_fraction=position_size makes that product the real exposure, and
       keeps their leverage guard (refuse above 1.0x) honest. The engine itself
       ignores both whenever `position_size` is set. */
    v.config.max_units = 1;
    v.config.unit_fraction = v.config.position_size;
    const body = {
      config: v.config,
      elmo: v.elmo,
      capital: v.capital,
      // A slider drag against visible bars IS an in-sample fit. Only
      // `backtest_runner`, after actually running folds, may claim better.
      validation: "in_sample",
      metrics: lastResult ? lastResult.metrics : {},
      note: "saved from the live tester",
    };
    if (scope === "timeframe") body.ticker = "*";
    const res = await fetch("/api/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const out = await res.json();
    flashProfile(out.ok ? "saved " + out.saved.ticker + "|" + out.saved.interval
                        : (out.error || "save failed"), !out.ok);
  }

  async function clearProfile() {
    const res = await fetch("/api/profiles", { method: "DELETE" });
    const out = await res.json();
    flashProfile(out.removed ? "profile cleared" : "no profile to clear", false);
  }

  /* Bound the pickers to the bars that actually exist.

     Typing a range wider than the cache used to intersect straight back to the
     full span, which looks exactly like the dates being ignored -- and on an
     intraday chart no amount of refreshing fixes it, because intraday history
     is capped at 30 days at the provider. Saying so beats letting someone
     retype dates at a window that cannot move. */
  function boundDates(avail) {
    if (!avail) return;
    const s = document.getElementById("t_start");
    const e = document.getElementById("t_end");
    [s, e].forEach(function (el) {
      if (!el) return;
      el.min = avail.start;
      el.max = avail.end;
    });
    fillSpans(avail.interval);
    const note = document.getElementById("dataSpan");
    if (note) {
      note.textContent =
        "data " + avail.start + " \u2192 " + avail.end +
        " (" + avail.bars + "b)" +
        (avail.intraday_chunked ? " \u00b7 deeper pulls chunk, so they are slow" : "");
    }
  }

  /* Span choices depend on the timeframe: a 3-minute chart over two years is
     hundreds of thousands of one-minute rows to aggregate, and a daily chart
     has no business asking for 30 days. */
  const LOAD_SPANS = {
    fast:  ["30d", "60d", "90d", "180d"],           // 3m / 5m / 10m
    mid:   ["60d", "180d", "1y", "2y"],             // 15m / 30m / 1h / 4h
    daily: ["1y", "2y", "3y", "5y"],                // 1d
  };

  function spansFor(interval) {
    if (interval === "1d") return LOAD_SPANS.daily;
    if (["3m", "5m", "10m"].indexOf(interval) >= 0) return LOAD_SPANS.fast;
    return LOAD_SPANS.mid;
  }

  function fillSpans(interval) {
    const sel = document.getElementById("t_loadSpan");
    if (!sel) return;
    const want = spansFor(interval || "15m");
    if (sel.dataset.interval === interval) return;
    sel.dataset.interval = interval || "";
    sel.innerHTML = want
      .map(function (s) { return '<option value="' + s + '">' + s + "</option>"; })
      .join("");
  }

  async function loadHistory() {
    const sel = document.getElementById("t_loadSpan");
    const btn = document.getElementById("loadHistory");
    if (!sel || !btn) return;
    const span = sel.value;
    btn.disabled = true;
    const was = btn.textContent;
    // A deep intraday pull chunks at 21 days and sleeps between chunks, so a
    // year is minutes, not seconds. Say so rather than look hung.
    btn.textContent = "loading " + span + "...";
    flashProfile("fetching " + span + " -- chunked, this can take minutes", false);
    try {
      const res = await fetch("/api/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lookback: span }),
      });
      const out = await res.json();
      if (!out.ok) {
        flashProfile(out.error || "load failed", true);
      } else {
        boundDates(out.cache_span);
        flashProfile(
          "+" + out.upserted + " bars \u00b7 now " +
          (out.available && out.available.start) + " \u2192 " +
          (out.available && out.available.end),
          false
        );
        rerun();   // score the history we just went and got
      }
    } catch (e) {
      flashProfile(String(e), true);
    } finally {
      btn.disabled = false;
      btn.textContent = was;
    }
  }

  function flashProfile(text, bad) {
    const el = document.getElementById("profMsg");
    if (!el) return;
    el.textContent = text;
    el.className = bad ? "bad" : "good";
    setTimeout(function () { el.textContent = ""; el.className = ""; }, 2600);
  }

  function schedule() {
    // Debounced: a slider drag fires an input event per pixel, and each run
    // re-evaluates every indicator over the whole series.
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(run, 180);
  }

  function build(container) {
    const parts = KNOBS.map(function (k) {
      return (
        '<label class="knob" title="' + k[0] + '">' +
        "<i>" + k[1] + "</i>" +
        '<input type="range" id="t_' + k[0] + '" min="' + k[2] + '" max="' + k[3] +
        '" step="' + k[4] + '" value="' + k[5] + '">' +
        '<b id="v_' + k[0] + '">' + k[5] + "</b></label>"
      );
    });
    parts.push(
      '<label class="knob chk"><input type="checkbox" id="t_allow_short"><i>allow short</i></label>'
    );
    /* A CHOICE, not a range: "all" sells everything on an exit signal;
       "scale" sells one `exit %` order per action until it is gone. A STOP
       or a margin call always closes everything either way.

       The old "capital model" select is gone on purpose. "Pyramiding" made
       each added unit a whole extra book of exposure with no loan and no
       interest -- leverage that looked like conviction. Borrowing is now the
       `borrowed %` dial, and it never changes the share count. */
    parts.push(
      '<label class="knob box"><i>exit</i>' +
      '<select id="t_exit_style">' +
      '<option value="all">all out</option>' +
      '<option value="scale">scale out</option>' +
      "</select></label>"
    );
    /* "Take profit at 5% and 10%": gains off the average entry. Each level
       sells one `exit %` chunk as a resting order, once per trade. Without it
       the only ways out of a winner are the score turning and the trail. */
    parts.push(
      '<label class="knob box"><i>take profit %</i>' +
      '<input type="text" id="t_take_profit" placeholder="e.g. 5, 10" size="8"></label>'
    );
    /* Whole shares unless ticked -- for a BRK-A, where one share is an order. */
    parts.push(
      '<label class="knob chk"><input type="checkbox" id="t_fractional"><i>fractional shares</i></label>'
    );
    /* Capital scales the dollar figures; the dates narrow the scored span.
       Both are text/date boxes rather than sliders -- they are not things you
       drag, and a stray drag on a date would re-run the engine per pixel. */
    parts.push(
      '<label class="knob box"><i>capital $</i>' +
      '<input type="text" id="t_capital" value="1000000" size="8"></label>'
    );
    /* THE BUTTON THIS WHOLE THREAD STARTED FOR.
       The date pickers only ever NARROW bars already cached, deliberately --
       dragging a slider must not fetch. So a longer backtest was impossible
       until something explicitly went and got the history. That used to be
       flatly refused above 30 days, which turned out to be httpx's 5s default
       rather than any provider limit (see CLAUDE.md). It is not refused now.

       Nothing here fetches on its own: this pulls only when pressed. */
    parts.push(
      '<div class="knob prof" id="loadRow">' +
      '<select id="t_loadSpan"></select>' +
      '<button type="button" id="loadHistory" title="Fetch this much history for the current symbol and timeframe, into the bar cache">load history</button>' +
      "</div>"
    );
    parts.push(
      '<label class="knob box"><i>from</i>' +
      '<input type="date" id="t_start"></label>'
    );
    parts.push(
      '<label class="knob box"><i>to</i>' +
      '<input type="date" id="t_end"></label>'
    );
    parts.push('<button type="button" id="testerReset">reset</button>');
    /* Saving is what makes tuning reach the CHART: the live snapshot resolves
       a profile for (ticker, interval) and feeds it to ELMo and the signal
       engine. Without this the knobs only ever moved the tester's own P&L.
       Two scopes on purpose -- the timeframe one is structural (windows are
       counted in bars, so a horizon is timeframe-specific), the symbol one is
       the kind the walk-forward warned about and is stamped `in_sample`. */
    parts.push(
      '<div class="knob prof">' +
      '<button type="button" id="profSaveSym" title="Apply these to THIS symbol at this timeframe">save · symbol</button>' +
      '<button type="button" id="profSaveTf" title="Apply these to EVERY symbol at this timeframe (structural: windows are counted in bars)">save · timeframe</button>' +
      '<button type="button" id="profClear" title="Delete this symbol’s profile">clear</button>' +
      "</div>"
    );
    /* A floating panel rather than a strip above the chart. The strip pushed
       the plot down by 62-87px, so every time it opened the candles resized
       and the thing you were reading moved. A panel overlays instead: the
       chart behind it never reflows while you tune. */
    container.innerHTML =
      '<div id="testerHead">' +
      '<span>strategy tester <b id="profMsg"></b></span>' +
      '<span id="dataSpan"></span>' +
      '<button type="button" id="testerClose" title="close (key: t)">×</button>' +
      "</div>" +
      '<div id="testerKnobs">' + parts.join("") + "</div>" +
      '<div id="testerStats">move a knob to score the loaded bars</div>' +
      '<div id="testerOrders"></div>';

    KNOBS.forEach(function (k) {
      const el = document.getElementById("t_" + k[0]);
      const out = document.getElementById("v_" + k[0]);
      el.addEventListener("input", function () {
        if (k[0] === "margin_pct") boundInvest();
        out.textContent = el.value;
        schedule();
      });
    });
    boundInvest();
    document.getElementById("t_allow_short").addEventListener("change", schedule);
    document.getElementById("t_fractional").addEventListener("change", schedule);
    loadDefaults();
    document.getElementById("loadHistory").addEventListener("click", loadHistory);
    document.getElementById("profSaveSym")
      .addEventListener("click", function () { saveProfile("symbol"); });
    document.getElementById("profSaveTf")
      .addEventListener("click", function () { saveProfile("timeframe"); });
    document.getElementById("profClear")
      .addEventListener("click", clearProfile);
    ["t_capital", "t_start", "t_end", "t_exit_style", "t_take_profit"].forEach(function (id) {
      const el = document.getElementById(id);
      if (el) el.addEventListener("change", schedule);
    });
    document.getElementById("testerReset").addEventListener("click", function () {
      // Back to the BASELINE -- the saved profile if there is one, else what
      // the engine ships. Resetting to shipped while a profile was loaded
      // would silently discard it, which is the behaviour this whole change
      // exists to remove.
      paintKnobs();
      if (!(profile && profile.config && profile.config.allow_short != null)) {
        document.getElementById("t_allow_short").checked = false;
      }
      schedule();
    });
  }

  function setEnabled(on, cb) {
    enabled = !!on;
    if (cb) onResult = cb;
    if (enabled) run();
    else {
      lastResult = null;
      onResult(null);
    }
  }

  /* Overlay the tester's run onto a /api/state payload so the chart draws the
     parameters being tested instead of the shipped ones. Returns a shallow
     copy -- the live payload is left untouched so turning the tester off
     restores the real chart with no refetch. */
  function applyTo(state) {
    if (!enabled || !lastResult || !state) return state;
    if (lastResult.ticker !== state.ticker || lastResult.interval !== state.interval) {
      return state;   // stale: symbol changed since the last run
    }
    const out = Object.assign({}, state);
    out.algo = Object.assign({}, state.algo, {
      score: lastResult.score,
      actions: lastResult.actions,
      position: lastResult.position,
      stop: lastResult.stop,
      atr: lastResult.atr,
      components: lastResult.components,
      available: lastResult.available,
      trades: lastResult.trades,
      orders: lastResult.orders,
      config: Object.assign({}, (state.algo || {}).config, lastResult.config),
    });
    out.elmo = lastResult.elmo || state.elmo;
    return out;
  }

  function rerun() { if (enabled) schedule(); }

  return {
    build: build, setEnabled: setEnabled, applyTo: applyTo, rerun: rerun,
    seedProfile: seedProfile,
  };
})();
