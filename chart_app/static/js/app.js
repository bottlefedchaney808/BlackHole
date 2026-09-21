/* Controls, polling and view state for the native chart window. */

(function () {
  const { C, LOOKBACK, compact } = window.CT;
  const chart = echarts.init(document.getElementById("chart"), null, {
    renderer: "canvas",
  });

  const MODES = ["clean", "signals", "full"];
  const MODE_STORE = "chart-app-mode";
  const OVERLAY_STORE = "chart-app-overlays";

  const OVERLAY_KEYS = [
    "ema20", "ema50", "ema200", "vwap", "bb", "vwapb", "atr", "pdhl", "alma",
    "whale", "stop", "volume", "algo", "flow", "elmo", "rsi", "cci", "macd",
  ];

  let lastState = null;
  let lastKey = "";
  let lastViewKey = "";
  let mode = "signals";

  /* ---------------------------------------------------------------- store */

  function readStore(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw == null ? fallback : JSON.parse(raw);
    } catch (_e) {
      return fallback; // private mode / quota -- session-only is fine
    }
  }
  function writeStore(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (_e) { /* ignore */ }
  }

  function overlays() {
    const out = {};
    OVERLAY_KEYS.forEach(function (k) { out[k] = false; });
    document.querySelectorAll("#overlays [data-overlay]").forEach(function (el) {
      out[el.dataset.overlay] = !!el.checked;
    });
    return out;
  }

  function restoreOverlays() {
    const saved = readStore(OVERLAY_STORE, null);
    if (!saved || typeof saved !== "object") return;
    document.querySelectorAll("#overlays [data-overlay]").forEach(function (el) {
      if (typeof saved[el.dataset.overlay] === "boolean") {
        el.checked = saved[el.dataset.overlay];
      }
    });
  }

  /* ----------------------------------------------------------------- mode */

  function setMode(next, repaint) {
    mode = MODES.indexOf(next) >= 0 ? next : "signals";
    writeStore(MODE_STORE, mode);
    document.querySelectorAll("#modes button").forEach(function (b) {
      b.classList.toggle("on", b.dataset.mode === mode);
    });
    // The overlay row is the algo's control surface; in clean and signals it
    // is not just unused, it would be lying about what is on screen.
    const row = document.getElementById("overlays");
    row.classList.toggle("hidden", mode !== "full");
    document.getElementById("chart").classList.toggle("with-overlays", mode === "full");
    // Conviction readout is meaningless in clean mode -- hide it rather than
    // show a number nothing on the chart corresponds to.
    document.getElementById("algo").style.visibility =
      mode === "clean" ? "hidden" : "visible";
    if (repaint && lastState) render(lastState);
    chart.resize();
  }

  /* --------------------------------------------------------------- header */

  function paintHeader(state) {
    const live = state.live || {};
    const algo = state.algo || {};
    const dir = state.direction || { active: true };
    const whale = state.whale || {};

    document.getElementById("label").textContent =
      (state.ticker || "") + " " + (state.interval || "");

    const bits = [];
    if (state.as_of) bits.push(window.CR.tsLabel(state.as_of, state.interval));
    if (state.bars) bits.push(state.bars.length + " bars");
    // Whale coverage, stated. A dark WH leg used to be indistinguishable from
    // "the flow provider was never asked about these sessions", which for
    // most timeframes it was not.
    const cov = whale.coverage;
    if (whale.status === "unavailable") {
      bits.push("no options tape");
    } else if (whale.status === "loading") {
      bits.push("whale loading...");
    } else if (cov && cov.sessions) {
      const txt = "WH " + cov.covered + "/" + cov.sessions + " sess";
      bits.push(cov.partial ? '<span class="warn">' + txt + "</span>" : txt);
    }
    /* This used to read "103k prints >$1.5mm", and a bare dollar figure in a
       header reads as money made -- it was misread as backtest P&L on sight.
       It is a THRESHOLD: the premium a print must clear to count as a whale,
       set adaptively from the day's own distribution. Labelling it and giving
       the share of the tape it keeps turns an opaque number into a statement
       about selectivity (and P&L lives in the tester strip, all in percent). */
    if (whale.prints) {
      const whales = (whale.bars || []).reduce(function (a, b) {
        return a + (b.count || 0);
      }, 0);
      const pct = whale.prints ? (100 * whales / whale.prints) : 0;
      const share = pct >= 1 ? pct.toFixed(0) : pct >= 0.01 ? pct.toFixed(2) : "<0.01";
      bits.push(
        compact(whale.prints) + " prints · whale cutoff $" + compact(whale.min_premium) +
        " (top " + share + "% of tape, " + compact(whales) + " hits)"
      );
    }
    if (!dir.active) {
      bits.push('<span class="warn">legs need ' + dir.min_bars + " bars, have " + dir.bars + "</span>");
    }
    if (state.elmo && state.elmo.has_volume === false) {
      bits.push('<span class="warn">no volume: LQ unavailable</span>');
    }
    document.getElementById("stamp").innerHTML = bits.join(" · ");

    const sig = live.signals || {};
    // The whale leg is the only DIRECTIONAL one, so it is the only one that
    // can be lit in red. "A whale printed here" and "that whale was bullish"
    // are different claims, and colouring both green conflated them: the
    // pill read bullish while the signed whale component was pulling the
    // conviction line down. The chart's whale dots already coloured by net
    // premium; the pill now agrees with them.
    const lastWhale = (state.whale && state.whale.bars || []).slice(-1)[0];
    const whaleNet = lastWhale ? lastWhale.net_premium || 0 : 0;
    document.querySelectorAll("#legs [data-leg]").forEach(function (el) {
      const leg = el.dataset.leg;
      const on = dir.active && !!sig[leg];
      el.classList.toggle("on", on);
      el.classList.toggle("off", !on);
      el.classList.toggle("na", !dir.active);
      const bear = on && leg === "whale" && whaleNet < 0;
      const flat = on && leg === "whale" && whaleNet === 0;
      el.classList.toggle("bear", bear);
      el.classList.toggle("flat", flat);
      if (!dir.active) {
        el.title = "needs " + dir.min_bars + " bars, have " + dir.bars +
          " at this timeframe";
      } else if (leg === "whale" && on) {
        el.title = "whale print on this bar, net " +
          (whaleNet >= 0 ? "+" : "") + "$" + compact(whaleNet) +
          (whaleNet < 0 ? " (put-side)" : whaleNet > 0 ? " (call-side)" : " (balanced)");
      } else {
        el.title = leg + (on ? ": ON" : ": off");
      }
    });

    const score = live.algo_score == null ? 0 : live.algo_score;
    const el = document.getElementById("algoScore");
    el.textContent = (score >= 0 ? "+" : "") + score.toFixed(0);
    el.style.color = score >= 0 ? C.long : C.down;
    const fill = document.querySelector("#algoBar i");
    const frac = Math.min(1, Math.abs(score) / 100);
    fill.style.background = score >= 0 ? C.long : C.down;
    fill.style.left = score >= 0 ? "50%" : (50 - frac * 50) + "%";
    fill.style.width = frac * 50 + "%";

    const action = live.action && live.action !== "none" ? live.action : "flat";
    const pos = live.position || 0;
    const actionEl = document.getElementById("algoAction");
    actionEl.textContent = pos > 0 ? "LONG " + pos : pos < 0 ? "SHORT " + -pos : action.toUpperCase();
    actionEl.className = pos > 0 ? "buy" : pos < 0 ? "sell" : "";
    actionEl.title =
      "conviction " + score.toFixed(1) +
      " | entry " + ((algo.config || {}).entry_long) +
      " | exit " + ((algo.config || {}).exit_long) +
      " | trades " + ((algo.trades || []).length);
  }

  /* --------------------------------------------------------------- render */

  function render(rawState) {
    // Two optional layers sit between the payload and the chart, and both are
    // shallow copies so switching either off restores the shipped chart with
    // no refetch:
    //   gears  -> retuned indicator periods (EMA/BB/ATR/RSI/CCI/MACD/ELMo)
    //   tester -> a whole alternative algo run
    // Gears first, because the tester's ELMo result should win where they
    // overlap: it is the thing being scored.
    let state = window.CGear ? window.CGear.applyTo(rawState, gearResult) : rawState;
    state = window.CTest ? window.CTest.applyTo(state) : state;
    paintHeader(state);
    if (window.parent && window.parent !== window) {
      // The dashboard's Chart tab hosts this app in a cross-origin iframe
      // (:8791 inside :8787); postMessage is the only channel it has to
      // filter its position panel down to the charted symbol.
      try {
        window.parent.postMessage(
          { type: "chart-symbol", ticker: state.ticker, interval: state.interval },
          "*"
        );
      } catch (_e) { /* embedder gone */ }
    }
    const ov = overlays();
    const key = state.ticker + "|" + state.interval + "|" + (state.bars || []).length;
    const viewKey = mode + "|" + JSON.stringify(ov);
    const resetZoom = key.split("|").slice(0, 2).join("|") !==
      lastKey.split("|").slice(0, 2).join("|");
    // Dropping a series only takes effect under notMerge, so a mode or
    // overlay change has to force a full setOption -- but keep the window the
    // user had scrolled to unless the SYMBOL changed.
    const rebuild = key !== lastKey || viewKey !== lastViewKey;
    lastKey = key;
    lastViewKey = viewKey;
    window.CR.render(chart, state, {
      mode: mode,
      overlays: ov,
      resetZoom: resetZoom,
      rebuild: rebuild,
    });
  }

  /* ------------------------------------------------------------- transport */

  /* The poll is conditional now, and that is the whole performance story.

     `/api/state` on a 35k-bar chart is a 32MB payload the server spends ~4s
     building, and this ran every 5 seconds unconditionally -- so the browser
     was re-downloading, re-parsing and re-rendering an identical frame
     forever, while the server queued builds it had already done. Measured
     2026-09-20 the endpoint answered in 30s against 5.6s in isolation: that
     gap was the backlog this loop created, and every slider drag queued
     behind it.

     Now the server ETags the frame and we send it back. Nothing moved -> 304,
     ~200 bytes, no parse, no render, no setOption. A new bar -> a normal 200
     and everything below runs exactly as it did. */
  let lastEtag = null;
  let polling = false;

  async function poll() {
    // A poll still in flight when the next tick fires used to start a second
    // one; on a slow frame that stacked requests on the client too.
    if (polling) return;
    polling = true;
    try {
      const headers = lastEtag ? { "If-None-Match": lastEtag } : {};
      const res = await fetch("/api/state", { headers: headers, cache: "no-store" });
      if (res.status === 304) return;   // identical frame; keep the one drawn
      if (!res.ok) return;
      const etag = res.headers.get("ETag");
      const state = await res.json();
      lastEtag = etag;
      lastState = state;
      render(state);
      if (state.ticker && document.activeElement.id !== "ticker") {
        document.getElementById("ticker").value = state.ticker;
      }
      if (state.interval) document.getElementById("interval").value = state.interval;
      // Two different triggers, deliberately separated.
      //
      // The TAG repaints on every frame whose profile block moved, including
      // one you just saved on the symbol already loaded -- the server bumps
      // its epoch on a save, so that frame arrives on the next poll and the
      // header has to stop claiming the old validation.
      //
      // The KNOBS reseed only when the SYMBOL changed, because reseeding is
      // destructive: it throws away whatever is on the sliders. Doing that on
      // a profile change would wipe the tuning you were mid-way through at the
      // exact moment you saved a checkpoint of it.
      if (state.profile) {
        const stamp = JSON.stringify(state.profile);
        if (stamp !== lastProfileStamp) {
          lastProfileStamp = stamp;
          paintProfile(state.profile);
        }
        const scope = state.ticker + "|" + state.interval;
        if (seededFor !== scope) {
          seededFor = scope;
          if (window.CGear) window.CGear.seedProfile(state.profile);
          if (window.CTest) window.CTest.seedProfile(state.profile);
        }
      }
    } catch (_e) {
      /* keep the last frame rather than blanking the chart */
    } finally {
      polling = false;
    }
  }

  /* ------------------------------------------------------------- profile */

  let seededFor = null;        // which (ticker|interval) the knobs hold
  let lastProfileStamp = null; // the profile block the tag is showing

  /* Say which saved profile the chart is running, and how it was validated.

     `snapshot.build_state` has published this block since profiles existed and
     nothing on the page ever read it -- which is exactly why a restart looked
     like it had lost the presets. The engine was running them; the UI just
     never said so and never showed their values. */
  function paintProfile(profile) {
    const el = document.getElementById("profileTag");
    if (!el) return;
    const src = profile && profile.source;
    if (!src || src === "defaults") {
      el.textContent = "shipped defaults";
      el.className = "prof none";
      el.title = "no saved profile matches this symbol and timeframe";
      return;
    }
    el.textContent = src;
    // `in_sample` is a warning, not a credential -- a profile saved from a
    // slider drag against visible bars IS an in-sample fit.
    el.className = "prof " + (profile.validation || "none");
    el.title =
      "profile " + src +
      " | validation: " + (profile.validation || "none") +
      (profile.saved_at ? " | saved " + profile.saved_at : "") +
      (profile.note ? " | " + profile.note : "");
  }

  async function loadSymbol() {
    const btn = document.getElementById("apply");
    const ticker = document.getElementById("ticker").value.trim().toUpperCase();
    const interval = document.getElementById("interval").value;
    if (!ticker) return;
    btn.disabled = true;
    const stampEl = document.getElementById("stamp");
    stampEl.textContent = "loading " + ticker + " " + interval + "...";
    try {
      const sw = await fetch("/api/symbol", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: ticker, interval: interval }),
      });
      if (!sw.ok) {
        stampEl.innerHTML = '<span class="warn">bad ticker or interval</span>';
        return;
      }
      const refreshRes = await fetch("/api/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lookback: LOOKBACK[interval] || "5d" }),
      });
      let body = null;
      try { body = await refreshRes.json(); } catch (_e) { /* non-JSON */ }
      lastEtag = null;    // new symbol: never let the old frame's etag answer
      seededFor = null;   // and reseed the knobs from the new symbol's profile
      await poll();
      window.CTest.rerun();   // new bars -> the tester's result is stale
      if (body && body.ok === false) {
        // A coarse interval is aggregated from a lot of one-minute rows and
        // the provider can time out; that used to leave an empty chart with
        // no explanation whatsoever.
        stampEl.innerHTML =
          '<span class="warn">load failed for ' + (body.interval || "") + " — " +
          (body.error || "unknown error") + " — press Load to retry</span>";
      }
    } finally {
      btn.disabled = false;
      chart.resize();
    }
  }

  /* ----------------------------------------------------------------- wire */

  document.getElementById("apply").addEventListener("click", loadSymbol);
  document.getElementById("ticker").addEventListener("keydown", function (e) {
    if (e.key === "Enter") loadSymbol();
  });
  document.getElementById("interval").addEventListener("change", loadSymbol);
  document.querySelectorAll("#modes button").forEach(function (b) {
    b.addEventListener("click", function () { setMode(b.dataset.mode, true); });
  });
  document.querySelectorAll("#overlays [data-overlay]").forEach(function (el) {
    el.addEventListener("change", function () {
      writeStore(OVERLAY_STORE, overlays());
      if (lastState) render(lastState); else poll();
    });
  });

  // Keyboard: 1/2/3 switch modes, r refits the window to the default span.
  document.addEventListener("keydown", function (e) {
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
    if (e.key === "1") setMode("clean", true);
    else if (e.key === "2") setMode("signals", true);
    else if (e.key === "3") setMode("full", true);
    else if (e.key.toLowerCase() === "t") setTester(!testerOn);
    else if (e.key.toLowerCase() === "r" && lastState) {
      lastKey = "";  // forces a rebuild with resetZoom
      render(lastState);
    }
  });

  /* ---------------------------------------------------------------- tester */

  const TESTER_STORE = "chart-app-tester";
  let testerOn = false;

  function setTester(on) {
    testerOn = !!on;
    writeStore(TESTER_STORE, testerOn);
    document.getElementById("tester").classList.toggle("open", testerOn);
    document.getElementById("testerToggle").classList.toggle("on", testerOn);
    // The tester is a floating panel now, so the chart does NOT reflow when it
    // opens -- resizing the plot under the cursor mid-tune made the candles
    // jump every time the panel appeared.
    window.CTest.setEnabled(testerOn, function () {
      // Re-render from the frame we already have: a tester result is a view
      // change, not new market data.
      if (lastState) {
        lastViewKey = "";  // force a rebuild so dropped series actually go
        render(lastState);
      }
    });
    chart.resize();
  }

  /* ----------------------------------------------------------------- gears */

  let gearResult = null;

  async function applyGears(payload) {
    try {
      const res = await fetch("/api/indicators", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const out = await res.json();
      gearResult = out && out.ok ? out : null;
    } catch (_e) {
      gearResult = null;
    }
    if (lastState) {
      lastViewKey = "";   // force a rebuild so retuned series actually redraw
      render(lastState);
    }
    // ELMo periods feed the conviction line, so a gear change invalidates the
    // tester's last run too.
    window.CTest.rerun();
  }

  window.CGear.attach(applyGears, function () { setTester(true); });
  window.CTest.build(document.getElementById("tester"));
  const closeBtn = document.getElementById("testerClose");
  if (closeBtn) closeBtn.addEventListener("click", function () { setTester(false); });
  document.getElementById("testerToggle").addEventListener("click", function () {
    setTester(!testerOn);
  });

  restoreOverlays();
  setMode(readStore(MODE_STORE, "signals"), false);
  setTester(readStore(TESTER_STORE, false));

  // Embedded use (dashboard Chart tab / Overview hero):
  // ?ticker=SPY&interval=15m pins the frame on boot instead of inheriting
  // whatever the server session was last pointed at.
  const qs = new URLSearchParams(window.location.search);
  const qsTicker = (qs.get("ticker") || "").trim().toUpperCase();
  const qsInterval = (qs.get("interval") || "").trim();
  const qsMode = (qs.get("mode") || "").trim();
  if (qsMode) setMode(qsMode, false);
  if (qsTicker || qsInterval) {
    if (qsTicker) document.getElementById("ticker").value = qsTicker;
    if (qsInterval) {
      const sel = document.getElementById("interval");
      if (Array.prototype.some.call(sel.options, function (o) { return o.value === qsInterval; })) {
        sel.value = qsInterval;
      }
    }
    loadSymbol();
  } else {
    poll();
  }
  setInterval(poll, 5000);

  window.addEventListener("resize", function () { chart.resize(); });
  // The iframe's own box can change without a window resize (parent relayout,
  // sidebar add/remove); ResizeObserver is the only thing that catches that.
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(function () { chart.resize(); })
      .observe(document.getElementById("chart"));
  }
  window.addEventListener("message", function (e) {
    if (e.data && e.data.type === "chart-resize") chart.resize();
  });
})();
