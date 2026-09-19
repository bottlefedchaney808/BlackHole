/* Chart construction. One entry point: CR.render(chart, state, view).

   `view` is {mode, overlays, resetZoom} -- everything about what is on screen
   that is not data. `state` is the /api/state payload, untouched.

   Three display modes, because an algo you cannot turn off is an algo you
   stop trusting:

     clean    candles + volume + EMA20/50. Nothing computed shows.
     signals  the above, plus ONE conviction line in its own pane, the buy/
              sell/add/trim marks, and the live ATR trailing stop.
     full     every overlay the checkbox row exposes.
*/

window.CR = (function () {
  const { C, priceFormatter, compact } = window.CT;

  /* ---------------------------------------------------------------- utils */

  function tsLabel(iso, interval) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return String(iso).replace("T", " ").slice(0, 16);
    const date = iso.slice(5, 10);
    if (interval === "1d") return iso.slice(0, 10);
    return date + " " + iso.slice(11, 16);
  }

  /* Category-axis label formatter.

     The axis categories are full ISO timestamps, and printing them raw is
     what made the bottom of this chart unreadable: eighteen copies of
     "2026-09-14T11:15:00" overlapping each other. Every desk platform solves
     this the same way -- show the DATE on the first bar of a session and the
     TIME on every other bar -- so the day boundaries read as landmarks
     instead of as repetition. */
  function catLabelFormatter(cats, interval) {
    if (interval === "1d") {
      return function (v) { return String(v).slice(5, 10); };
    }
    const firstOfDay = Object.create(null);
    let prevDay = null;
    for (let i = 0; i < cats.length; i++) {
      const day = String(cats[i]).slice(0, 10);
      if (day !== prevDay) { firstOfDay[cats[i]] = true; prevDay = day; }
    }
    return function (v) {
      const str = String(v);
      return firstOfDay[str] ? str.slice(5, 10) : str.slice(11, 16);
    };
  }

  function line(name, data, color, width, dash, pane, opts) {
    return Object.assign(
      {
        name: name,
        type: "line",
        xAxisIndex: pane,
        yAxisIndex: pane,
        data: data || [],
        showSymbol: false,
        connectNulls: false,
        // `sampling` only kicks in above the threshold, and at that point one
        // pixel column holds several bars anyway -- drawing all of them is
        // what makes a zoomed-out chart look furry rather than clean.
        sampling: "lttb",
        lineStyle: { color: color, width: width || 1, type: dash || "solid" },
        itemStyle: { color: color },
        emphasis: { disabled: true },
        z: 3,
      },
      opts || {}
    );
  }

  function band(name, upper, lower, color, pane, opacity) {
    /* Two lines plus a fill between them, drawn as a stacked pair. ECharts has
       no native "band" series; stacking a transparent base under a filled
       delta is the standard way, and it keeps both edges hoverable. */
    const delta = upper.map(function (u, i) {
      const l = lower[i];
      return u == null || l == null ? null : u - l;
    });
    return [
      Object.assign(line(name + "_base", lower, color, 1, "solid", pane), {
        stack: name,
        lineStyle: { opacity: 0.35, color: color, width: 1 },
        silent: true,
        z: 1,
      }),
      Object.assign(line(name + "_fill", delta, color, 0, "solid", pane), {
        stack: name,
        lineStyle: { opacity: 0 },
        areaStyle: { color: color, opacity: opacity == null ? 0.07 : opacity },
        silent: true,
        z: 1,
      }),
    ];
  }

  /* ------------------------------------------------------------- markers */

  const MARK = {
    buy:  { color: C.long,  text: "BUY",   rotate: 0,   size: 26, below: true },
    add:  { color: C.long,  text: "ADD",   rotate: 0,   size: 18, below: true },
    sell: { color: C.down,  text: "SELL",  rotate: 180, size: 26, below: false },
    trim: { color: C.warn,  text: "TRIM",  rotate: 180, size: 18, below: false },
    short:{ color: C.short, text: "SHORT", rotate: 180, size: 26, below: false },
    cover:{ color: C.long,  text: "COVER", rotate: 0,   size: 26, below: true },
  };

  function markerSeries(bars, actions, atr, priceFmt) {
    /* Transitions only, and deliberately LOUD.

       Two earlier versions of this were wrong in opposite directions. The
       original drew a grey `hold` dot on every qualifying bar, which put a
       dotted line under most of the tape and buried the two marks that
       actually changed the position. The replacement was honest but tiny -- a
       13px triangle you had to hunt for. These are the only marks on the
       chart that say "do something", so they get a filled arrow, the word,
       and the price, in the position's colour. */
    const data = [];
    for (let i = 0; i < actions.length; i++) {
      const spec = MARK[actions[i]];
      if (!spec || !bars[i]) continue;
      // Offset by a fraction of ATR so the glyph sits clear of the wick
      // instead of on top of it. A fixed pixel offset would overlap on a
      // volatile bar and float away on a quiet one.
      const pad = (atr && atr[i] ? atr[i] : (bars[i].high - bars[i].low) || 0) * 1.1;
      data.push({
        name: actions[i],
        value: [i, spec.below ? bars[i].low - pad : bars[i].high + pad],
        symbol: "arrow",
        symbolRotate: spec.rotate,
        symbolSize: spec.size,
        itemStyle: {
          color: spec.color,
          borderColor: "#04070c",
          borderWidth: 1.5,
          shadowBlur: 10,
          shadowColor: spec.color,
        },
        label: {
          show: true,
          position: spec.below ? "bottom" : "top",
          distance: 6,
          formatter: spec.text + "  " + priceFmt(bars[i].close),
          color: "#04070c",
          backgroundColor: spec.color,
          padding: [3, 6],
          borderRadius: 3,
          fontSize: 11,
          fontWeight: "bold",
          fontFamily: "ui-monospace, Consolas, monospace",
        },
      });
    }
    return {
      name: "signals",
      type: "scatter",
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: data,
      z: 20,
      tooltip: { show: false },
    };
  }

  function heldRanges(position) {
    /* Contiguous runs where a position was open, as [start, end, qty]. */
    const out = [];
    let start = null;
    for (let i = 0; i < position.length; i++) {
      const open = position[i] !== 0;
      if (open && start === null) start = i;
      if (!open && start !== null) { out.push([start, i, position[start]]); start = null; }
    }
    if (start !== null) out.push([start, position.length - 1, position[start]]);
    return out;
  }

  function positionShading(position) {
    /* Tint the whole period a position was held.

       The clearest possible answer to "was I in or out here?". Arrows tell
       you where it CHANGED; the band tells you the state in between, which is
       what the eye actually reads when scanning a chart. */
    const ranges = heldRanges(position || []);
    if (!ranges.length) return null;
    return ranges.map(function (r) {
      const long = r[2] > 0;
      return [
        { xAxis: r[0], itemStyle: { color: long ? C.long : C.short, opacity: 0.07 } },
        { xAxis: r[1] },
      ];
    });
  }

  function signalLines(actions) {
    /* A vertical rule through every pane at each entry and exit, so the
       conviction line, volume and oscillators all line up with the decision
       without the eye having to trace across. */
    const data = [];
    for (let i = 0; i < actions.length; i++) {
      const spec = MARK[actions[i]];
      if (!spec) continue;
      data.push({
        xAxis: i,
        lineStyle: {
          color: spec.color,
          width: actions[i] === "buy" || actions[i] === "sell" ? 1.4 : 1,
          type: "dashed",
          opacity: 0.5,
        },
        label: { show: false },
      });
    }
    return data;
  }

  function whaleSeries(bars, whaleBars) {
    /* Size the dot by premium instead of drawing every qualifying print the
       same. A $28k print and a $4M print are not the same event, and a chart
       that renders them identically is throwing away the only information
       the whale tape carries. */
    if (!whaleBars || !whaleBars.length) return null;
    let maxPrem = 0;
    for (let i = 0; i < whaleBars.length; i++) {
      if (whaleBars[i] && whaleBars[i].max_premium > maxPrem) maxPrem = whaleBars[i].max_premium;
    }
    if (maxPrem <= 0) return null;
    const data = [];
    for (let i = 0; i < bars.length; i++) {
      const w = whaleBars[i];
      if (!w || !w.count) continue;
      // sqrt so area, not radius, scales with premium -- the eye reads area.
      const frac = Math.sqrt(w.max_premium / maxPrem);
      const net = w.net_premium || 0;
      data.push({
        value: [i, bars[i].high],
        symbolSize: 5 + 13 * frac,
        itemStyle: {
          color: net > 0 ? C.long : net < 0 ? C.down : C.warn,
          opacity: 0.5 + 0.4 * frac,
          borderColor: C.bg,
          borderWidth: 1,
        },
        w: w,
      });
    }
    if (!data.length) return null;
    return {
      name: "whale",
      type: "scatter",
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: data,
      z: 10,
      tooltip: { show: false },
    };
  }

  /* ---------------------------------------------------------------- panes */

  function buildPanes(mode, ov, has) {
    // Price keeps the lion's share in `full` too: with every pane enabled the
    // old 62 left the candles about a third of the glass, which is the one
    // thing on screen you cannot read at a glance when it is squashed.
    /* Volume does NOT get a pane of its own. It rides inside the price grid on
       a hidden axis scaled so the bars occupy the bottom slice (see
       VOLUME_FRACTION) -- the way a desk chart does it. As a pane it cost
       11-22% of chart height permanently, which is height the flow, ELMo and
       oscillator panels need more: with volume + algo + flow + elmo + rsi open
       every pane was under 15% and none of them was readable. */
    const panes = [{ key: "price", weight: mode === "clean" ? 100 : 88 }];
    if (mode === "clean") {
      return panes;
    }
    // `signals` simplifies the PRICE pane (no overlay spaghetti) -- it does
    // NOT throw away the lower panes. Switching mode used to delete whatever
    // flow/elmo/rsi panels were open, which is not what "show me one line and
    // the buy/sells" asked for. Sub-panes follow the checkboxes in both
    // `signals` and `full`; only the price-pane overlays differ between them.
    if (mode === "signals" || ov.algo) {
      panes.push({ key: "algo", weight: 17, min: -100, max: 100 });
    }
    if (ov.flow && has.flow) panes.push({ key: "flow", weight: 14 });
    if (ov.elmo && has.elmo) panes.push({ key: "elmo", weight: 14, min: 0, max: 100 });
    if (ov.rsi) panes.push({ key: "rsi", weight: 13, min: 0, max: 100 });
    if (ov.cci) panes.push({ key: "cci", weight: 13 });
    if (ov.macd) panes.push({ key: "macd", weight: 13 });
    return panes;
  }

  // Share of the price pane's height the tallest volume bar may occupy.
  const VOLUME_FRACTION = 0.18;

  function layout(panes, cats, priceFmt, interval) {
    const GAP = 2.6;                 // % of chart height between panes
    const TOP = 1.0;
    // Date labels AND the zoom slider live down here; 7% fits both without
    // the labels colliding with the slider handles.
    const BOTTOM = 7.0;
    const total = panes.reduce(function (a, p) { return a + p.weight; }, 0);
    const usable = 100 - TOP - BOTTOM - GAP * (panes.length - 1);
    const out = { grid: [], xAxis: [], yAxis: [], index: {}, all: [] };
    let cursor = TOP;
    panes.forEach(function (pane, i) {
      const height = usable * (pane.weight / total);
      out.index[pane.key] = i;
      out.all.push(i);
      out.grid.push({
        left: 8,
        right: 62,             // price axis lives on the RIGHT, as on a desk
        top: cursor.toFixed(2) + "%",
        height: height.toFixed(2) + "%",
        containLabel: false,
      });
      const bottom = i === panes.length - 1;
      out.xAxis.push({
        type: "category",
        data: cats,
        gridIndex: i,
        boundaryGap: true,
        axisLabel: {
          show: bottom,
          color: C.dim,
          fontSize: 10,
          hideOverlap: true,
          margin: 7,
          formatter: catLabelFormatter(cats, interval),
        },
        axisLine: { lineStyle: { color: C.axis } },
        axisTick: { show: false },
        splitLine: { show: false },
        axisPointer: { label: { show: bottom, backgroundColor: "#243044" } },
      });
      out.yAxis.push({
        scale: pane.min === undefined,
        min: pane.min,
        max: pane.max,
        gridIndex: i,
        position: "right",
        axisLabel: {
          color: C.dim,
          fontSize: 10,
          margin: 6,
          formatter: pane.key === "price" ? priceFmt
            : pane.key === "volume" || pane.key === "flow" ? compact
            : undefined,
        },
        axisLine: { show: false },
        axisTick: { show: false },
        splitNumber: pane.key === "price" ? 6 : 3,
        splitLine:
          pane.splitLine === false
            ? { show: false }
            : { lineStyle: { color: C.grid, width: 1 } },
      });
      cursor += height + GAP;
    });

    /* The volume axis: gridIndex 0, invisible, and deliberately NOT auto-scaled
       against the price axis. Its max is the data max divided by
       VOLUME_FRACTION, so the tallest bar fills the bottom 18% of the price
       pane and the candles keep the rest. Anything larger and volume starts
       drawing through the price action it is supposed to sit under. */
    out.volumeAxis = out.yAxis.length;
    out.yAxis.push({
      gridIndex: 0,
      show: false,
      min: 0,
      max: function (v) { return v.max / VOLUME_FRACTION; },
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { show: false },
      splitLine: { show: false },
    });
    return out;
  }

  /* -------------------------------------------------------------- tooltip */

  function tooltipFormatter(state, bars, P) {
    const algo = state.algo || {};
    const whale = (state.whale || {}).bars || [];
    const elmo = state.elmo || {};
    const interval = state.interval;
    const fmt = priceFormatter(bars.length ? bars[bars.length - 1].close : 100);
    return function (params) {
      if (!params || !params.length) return "";
      const i = params[0].dataIndex;
      const b = bars[i];
      if (!b) return "";
      const up = b.close >= b.open;
      const chg = b.open ? (100 * (b.close - b.open)) / b.open : 0;
      const rows = [
        '<div style="color:' + C.dim + ';font-size:10px">' + tsLabel(b.ts, interval) + "</div>",
        '<div style="font:12px ui-monospace,Consolas,monospace">' +
          "O " + fmt(b.open) + "  H " + fmt(b.high) + "<br>" +
          "L " + fmt(b.low) + "  C " +
          '<b style="color:' + (up ? C.up : C.down) + '">' + fmt(b.close) + "</b>" +
          '  <span style="color:' + (up ? C.up : C.down) + '">' +
          (chg >= 0 ? "+" : "") + chg.toFixed(2) + "%</span>" +
          "</div>",
      ];
      if (b.volume != null) {
        rows.push('<div style="color:' + C.dim + ';font-size:11px">vol ' + compact(b.volume) + "</div>");
      }
      if (algo.score && algo.score[i] != null) {
        const s = algo.score[i];
        rows.push(
          '<div style="font-size:11px;color:' + (s >= 0 ? C.long : C.down) + '">' +
            "conviction " + s.toFixed(0) + "</div>"
        );
      }
      const w = whale[i];
      if (w && w.count) {
        rows.push(
          '<div style="font-size:11px;color:' + C.warn + '">whale x' + w.count +
            "  max $" + compact(w.max_premium) +
            "  net " + (w.net_premium >= 0 ? "+" : "") + "$" + compact(w.net_premium) +
            "</div>"
        );
      }
      if (elmo.liquidity && elmo.liquidity[i] != null) {
        rows.push(
          '<div style="font-size:11px;color:' + C.dim + '">liq ' +
            elmo.liquidity[i].toFixed(0) + "  entropy " +
            (elmo.entropy_rank && elmo.entropy_rank[i] != null
              ? elmo.entropy_rank[i].toFixed(0)
              : "-") +
            "</div>"
        );
      }
      return rows.join("");
    };
  }

  /* --------------------------------------------------------------- render */

  function render(chart, state, view) {
    const mode = view.mode || "full";
    const ov = view.overlays || {};
    const bars = state.bars || [];
    const overlays = state.overlays || {};
    const osc = state.oscillators || {};
    const elmo = state.elmo || {};
    const algo = state.algo || {};
    const flow = state.flow || null;
    const whaleBars = (state.whale || {}).bars || [];
    const cats = bars.map(function (b) { return b.ts; });
    const ohlc = bars.map(function (b) { return [b.open, b.close, b.low, b.high]; });
    const lastClose = bars.length ? bars[bars.length - 1].close : 100;
    const priceFmt = priceFormatter(lastClose);

    const has = {
      volume: bars.some(function (b) { return b.volume != null; }),
      flow: !!flow,
      elmo: !!(elmo.entropy && elmo.entropy.length),
      algo: !!(algo.score && algo.score.length),
    };

    const panes = buildPanes(mode, ov, has);
    const P = layout(panes, cats, priceFmt, state.interval);
    const at = function (k) { return P.index[k]; };
    const showAlgo = at("algo") !== undefined && has.algo;

    /* ---- series --------------------------------------------------------- */
    const series = [
      {
        name: "ohlc",
        type: "candlestick",
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: ohlc,
        // Width is a PROPORTION of the category slot, not a pixel constant.
        // A fixed `barMaxWidth` (this was 20px) looks right at one zoom level
        // and wrong at every other: zoomed in, sixteen bars share ~1400px, so
        // each 87px slot held a 20px candle marooned in whitespace. 76% of
        // the slot is the desk-platform proportion -- the candles stay in
        // step with the spacing however far you zoom -- and the pixel cap
        // only bites at extreme magnification, where it stops a single bar
        // becoming a billboard.
        barWidth: "72%",
        barMaxWidth: 38,
        barMinWidth: 1,
        itemStyle: {
          color: C.up,
          color0: C.down,
          borderColor: C.up,
          borderColor0: C.down,
          borderWidth: 1,
        },
        // `large` swaps to a batched renderer once there are enough bars to
        // make per-item styling invisible anyway.
        large: true,
        largeThreshold: 400,
        z: 5,
        markLine: bars.length
          ? {
              silent: true,
              symbol: "none",
              animation: false,
              lineStyle: { color: C.dim, width: 1, type: "dashed", opacity: 0.7 },
              label: {
                show: true,
                position: "end",
                formatter: priceFmt(lastClose),
                color: C.bg,
                backgroundColor: C.dim,
                padding: [2, 4],
                fontSize: 10,
                fontFamily: "ui-monospace, Consolas, monospace",
              },
              data: [{ yAxis: lastClose }].concat(
                mode === "clean" ? [] : signalLines(algo.actions || [])
              ),
            }
          : undefined,
        // Shade every bar a position was open. Attached to the candlestick
        // series so it sits behind the candles rather than over them.
        markArea:
          mode === "clean"
            ? undefined
            : {
                silent: true,
                animation: false,
                data: positionShading(algo.position) || [],
              },
      },
    ];

    const wantMA = mode !== "full" || ov.ema20 || ov.ema50;
    if (mode !== "full" ? true : ov.ema20) {
      series.push(line("ema20", overlays.ema20, C.ema20, 1.3, null, 0));
    }
    if (mode !== "full" ? true : ov.ema50) {
      series.push(line("ema50", overlays.ema50, C.ema50, 1.3, null, 0));
    }

    if (mode === "full") {
      if (ov.ema200) series.push(line("ema200", overlays.ema200, C.ema200, 1.5, null, 0));
      if (ov.vwap) series.push(line("vwap", overlays.vwap, C.vwap, 1.2, "dashed", 0));
      if (ov.bb && overlays.bb_upper) {
        Array.prototype.push.apply(
          series, band("bb", overlays.bb_upper, overlays.bb_lower, C.band, 0, 0.06)
        );
        series.push(line("bb_mid", overlays.bb_mid, C.band, 1, "dotted", 0));
      }
      if (ov.vwapb && overlays.vwap_up) {
        Array.prototype.push.apply(
          series, band("vwapb", overlays.vwap_up, overlays.vwap_dn, C.vwap, 0, 0.05)
        );
      }
      if (ov.atr && overlays.atr_up) {
        series.push(line("atr_up", overlays.atr_up, C.atrband, 1, "dashed", 0));
        series.push(line("atr_dn", overlays.atr_dn, C.atrband, 1, "dashed", 0));
      }
      if (ov.pdhl && overlays.pdh) {
        // Step, not a plain line. A prior-session level is flat for a whole
        // session and then jumps; joining the two sessions with a diagonal
        // draws a price that never existed, and across several sessions it
        // renders as a box around the chart.
        const step = { step: "end" };
        series.push(line("pdh", overlays.pdh, C.pd, 1, "dashed", 0, step));
        series.push(line("pdl", overlays.pdl, C.pd, 1, "dashed", 0, step));
      }
      if (ov.alma && elmo.alma) series.push(line("alma", elmo.alma, C.alma, 1.5, null, 0));
    } else if (mode === "signals" && elmo.alma) {
      series.push(line("alma", elmo.alma, C.alma, 1.4, null, 0));
    }

    /* the trailing stop -- the line that actually takes you out */
    if (mode !== "clean" && algo.stop && (mode === "signals" || ov.stop)) {
      series.push(
        line("stop", algo.stop, C.short, 1.2, "dotted", 0, { z: 6 })
      );
    }

    /* whale + action marks */
    if (mode !== "clean" && (mode === "signals" || ov.whale)) {
      const w = whaleSeries(bars, whaleBars);
      if (w) series.push(w);
    }
    if (mode !== "clean" && algo.actions) {
      series.push(markerSeries(bars, algo.actions, algo.atr || null, priceFmt));
    }

    /* volume -- inside the price pane, under the candles */
    if (ov.volume && P.volumeAxis !== undefined) {
      series.push({
        name: "volume",
        type: "bar",
        xAxisIndex: 0,
        yAxisIndex: P.volumeAxis,
        // Under everything: the candles, the overlays and the marks all read
        // over the top of it.
        z: 1,
        barWidth: "72%",
        barMaxWidth: 38,
        large: true,
        largeThreshold: 400,
        data: bars.map(function (b) {
          return {
            value: b.volume,
            itemStyle: { color: b.close >= b.open ? C.up : C.down, opacity: 0.32 },
          };
        }),
      });
    }

    /* the one line */
    if (showAlgo) {
      const pane = at("algo");
      const cfg = algo.config || {};
      series.push(
        Object.assign(line("conviction", algo.score, C.conviction, 1.6, null, pane), {
          areaStyle: { color: C.conviction, opacity: 0.06 },
          markLine: {
            silent: true,
            symbol: "none",
            animation: false,
            label: { show: false },
            data: [
              { yAxis: 0, lineStyle: { color: C.dim, width: 1, opacity: 0.5 } },
              {
                yAxis: cfg.entry_long == null ? 30 : cfg.entry_long,
                lineStyle: { color: C.long, width: 1, type: "dashed", opacity: 0.55 },
              },
              {
                yAxis: cfg.exit_long == null ? -12 : cfg.exit_long,
                lineStyle: { color: C.down, width: 1, type: "dashed", opacity: 0.55 },
              },
            ],
          },
        })
      );
    }

    /* flow */
    if (at("flow") !== undefined && flow) {
      const pane = at("flow");
      series.push({
        name: "net flow",
        type: "bar",
        xAxisIndex: pane,
        yAxisIndex: pane,
        barMaxWidth: 12,
        data: (flow.net || []).map(function (v) {
          return { value: v, itemStyle: { color: v >= 0 ? C.long : C.down, opacity: 0.6 } };
        }),
      });
      series.push(line("cum flow", flow.cum_net, C.text, 1.3, null, pane));
    }

    /* elmo */
    if (at("elmo") !== undefined) {
      const pane = at("elmo");
      series.push(line("entropy rank", elmo.entropy_rank, C.entropy, 1.3, null, pane));
      series.push(line("liquidity", elmo.liquidity, C.liquidity, 1.3, null, pane));
    }

    if (at("rsi") !== undefined) series.push(line("rsi", osc.rsi, C.up, 1.3, null, at("rsi")));
    if (at("cci") !== undefined) series.push(line("cci", osc.cci, C.warn, 1.3, null, at("cci")));
    if (at("macd") !== undefined) {
      const pane = at("macd");
      series.push({
        name: "macd hist",
        type: "bar",
        xAxisIndex: pane,
        yAxisIndex: pane,
        barMaxWidth: 10,
        data: (osc.macd_hist || []).map(function (v) {
          return { value: v, itemStyle: { color: v >= 0 ? C.long : C.down, opacity: 0.55 } };
        }),
      });
      series.push(line("macd", osc.macd, C.ema50, 1.3, null, pane));
      series.push(line("macd signal", osc.macd_signal, C.atrband, 1.1, null, pane));
    }

    /* ---- option -------------------------------------------------------- */
    const opt = {
      backgroundColor: C.bg,
      animation: false,
      legend: { show: false },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross", link: [{ xAxisIndex: P.all }] },
        backgroundColor: "rgba(21,27,38,0.96)",
        borderColor: C.axis,
        borderWidth: 1,
        padding: [6, 9],
        textStyle: { color: C.text, fontSize: 11 },
        // The default axis tooltip prints every series on the pointer, which
        // with a dozen overlays is a wall of numbers nobody reads.
        formatter: tooltipFormatter(state, bars, P),
      },
      axisPointer: {
        link: [{ xAxisIndex: P.all }],
        label: { backgroundColor: "#243044", fontSize: 10 },
        lineStyle: { color: C.dim, width: 1, type: "dashed", opacity: 0.8 },
      },
      grid: P.grid,
      xAxis: P.xAxis,
      yAxis: P.yAxis,
      series: series,
      textStyle: { color: C.text, fontFamily: "system-ui, sans-serif" },
    };

    /* ---- zoom ----------------------------------------------------------- */
    const rebuild = view.rebuild;
    if (rebuild) {
      const show = Math.min(bars.length, window.CT.visibleBars(state.interval));
      let start = bars.length ? (100 * (bars.length - show)) / bars.length : 0;
      let end = 100;
      if (!view.resetZoom) {
        const cur = (chart.getOption() || {}).dataZoom;
        if (cur && cur.length) {
          if (cur[0].start != null) start = cur[0].start;
          if (cur[0].end != null) end = cur[0].end;
        }
      }
      opt.dataZoom = [
        {
          type: "inside",
          xAxisIndex: P.all,
          // `filter`, NOT `none`. With `none` ECharts keeps off-screen points
          // in the dataset, so `yAxis.scale` auto-ranges against the WHOLE
          // series -- which is why zooming in never rescaled the price axis
          // and left the candles as a flat ribbon. `filter` drops them, and
          // the y-axis then fits what you are actually looking at.
          filterMode: "filter",
          start: start,
          end: end,
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
          moveOnMouseWheel: false,
          throttle: 40,
        },
        {
          type: "slider",
          xAxisIndex: P.all,
          filterMode: "filter",
          height: 14,
          bottom: 4,
          showDetail: false,
          left: 8,
          right: 62,
          start: start,
          end: end,
          borderColor: "transparent",
          backgroundColor: "#131a25",
          fillerColor: "rgba(56,189,248,0.10)",
          handleStyle: { color: C.up, borderColor: C.up },
          moveHandleSize: 4,
          textStyle: { color: C.dim, fontSize: 9 },
          labelFormatter: function (_i, v) { return tsLabel(v, state.interval); },
          dataBackground: {
            lineStyle: { color: C.dim, opacity: 0.5 },
            areaStyle: { color: C.grid },
          },
          selectedDataBackground: {
            lineStyle: { color: C.up, opacity: 0.7 },
            areaStyle: { color: C.up, opacity: 0.12 },
          },
        },
      ];
    }

    chart.setOption(opt, rebuild);
    chart.resize();
  }

  return { render: render, tsLabel: tsLabel };
})();
