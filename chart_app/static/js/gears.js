/* Per-indicator settings popovers -- the little gears.

   Every pane and price overlay that has a tunable gets a gear beside its
   checkbox. Clicking one opens a popover of sliders for exactly that
   indicator, and the chart recomputes from the bars already cached: no
   refetch, no provider call, nothing billed.

   The knob TABLE below is the only place a period is named on the client.
   Values are seeded from `GET /api/indicator-defaults` rather than hardcoded,
   so a slider can never sit at 14 while the engine quietly uses 20.
*/

window.CGear = (function () {
  // group -> [[key, label, min, max, step, target]]
  //   target "params" -> score_engine.INDICATOR_DEFAULTS
  //   target "elmo"   -> elmo.DEFAULTS
  const TABLE = {
    ema20:  [["ema_fast", "period", 2, 200, 1, "params"]],
    ema50:  [["ema_mid", "period", 2, 300, 1, "params"]],
    ema200: [["ema_slow", "period", 5, 400, 1, "params"]],
    bb: [
      ["bb_window", "window", 5, 100, 1, "params"],
      ["bb_mult", "std mult", 0.5, 5, 0.1, "params"],
    ],
    vwapb: [["vwap_sigma", "sigma", 0.25, 4, 0.25, "params"]],
    atr: [
      ["atr_period", "period", 2, 60, 1, "params"],
      ["atr_mult", "mult", 0.5, 6, 0.1, "params"],
    ],
    rsi:  [["rsi_period", "period", 2, 60, 1, "params"]],
    cci:  [["cci_period", "period", 2, 80, 1, "params"]],
    macd: [
      ["macd_fast", "fast", 2, 40, 1, "params"],
      ["macd_slow", "slow", 5, 100, 1, "params"],
      ["macd_signal", "signal", 2, 40, 1, "params"],
    ],
    elmo: [
      ["entropy_window", "ent window", 5, 80, 1, "elmo"],
      ["entropy_rank_window", "ent rank win", 30, 400, 10, "elmo"],
      ["entropy_fast", "ent fast ema", 2, 40, 1, "elmo"],
      ["entropy_slow", "ent slow ema", 3, 80, 1, "elmo"],
      ["liq_window", "liq window", 2, 60, 1, "elmo"],
      ["liq_norm_window", "liq rank win", 30, 400, 10, "elmo"],
      ["alma_window", "alma window", 3, 50, 1, "elmo"],
      ["alma_offset", "alma offset", 0, 1, 0.05, "elmo"],
      ["alma_sigma", "alma sigma", 1, 20, 0.5, "elmo"],
    ],
  };

  let defaults = null;      // shipped values, from the server
  let profile = null;       // the saved profile active for (ticker, interval)
  let values = {};          // key -> current value
  let open = null;          // which group's popover is showing
  let onChange = function () {};
  let debounce = null;

  function has(group) { return !!TABLE[group]; }

  function current() {
    const out = { params: {}, elmo: {} };
    Object.keys(TABLE).forEach(function (g) {
      TABLE[g].forEach(function (k) {
        const v = values[k[0]];
        if (v != null) out[k[5]][k[0]] = v;
      });
    });
    return out;
  }

  function isDirty(group) {
    return (TABLE[group] || []).some(function (k) {
      const base = seed(k[0], k[5]);
      return base != null && values[k[0]] != null &&
        Math.abs(values[k[0]] - base) > 1e-9;
    });
  }

  /* The BASELINE for a knob: the saved profile's value if it has one, else
     the shipped default.

     This used to read shipped defaults only, and that was the whole "my
     presets don't load" bug. `snapshot.build_state` has always resolved the
     profile and fed it to the live chart -- so the chart was drawing the saved
     parameters while every slider here sat at its shipped value. Worse, the
     panel is all-or-nothing: `current()` posts EVERY key, so the first knob
     you touched sent nine shipped ELMo values along with it and wiped the
     profile out of the run. Hence "I have to reset everything".

     Making the profile the baseline fixes both ends at once: the sliders open
     where the chart actually is, the dirty dot means "differs from what is
     saved" rather than "differs from shipped", and `reset` returns to the
     profile rather than throwing it away. */
  function seed(key, target) {
    if (profile) {
      const saved = target === "elmo" ? profile.elmo : profile.config;
      if (saved && saved[key] != null) return Number(saved[key]);
    }
    if (!defaults) return null;
    const src = target === "elmo" ? defaults.elmo : defaults.indicators;
    return src && src[key] != null ? Number(src[key]) : null;
  }

  /* Re-seed from the profile now live on the chart. Called by `app.js` off
     `state.profile` on the first frame and on every symbol change, so a knob
     can never sit at a value the engine is not using. */
  function seedProfile(next) {
    const before = JSON.stringify(profile || {});
    profile = next || null;
    if (JSON.stringify(profile || {}) === before && Object.keys(values).length) {
      return false;
    }
    // Reset to the new baseline wholesale rather than merging: a knob held
    // over from the previous symbol is exactly the stale value this is meant
    // to stop. Any live popover is showing the old numbers, so close it.
    values = {};
    applySeeds();
    close();
    return true;
  }

  function applySeeds() {
    Object.keys(TABLE).forEach(function (g) {
      TABLE[g].forEach(function (k) {
        const base = seed(k[0], k[5]);
        if (base != null && values[k[0]] == null) values[k[0]] = base;
      });
    });
    markDirty();
  }

  async function load() {
    try {
      const res = await fetch("/api/indicator-defaults");
      defaults = await res.json();
    } catch (_e) {
      defaults = { indicators: {}, elmo: {} };
    }
    // `seedProfile` can land before or after this fetch (it is driven by the
    // first /api/state frame), so seeding is idempotent and order-free: it
    // only fills keys that are still null.
    applySeeds();
  }

  function markDirty() {
    document.querySelectorAll("#overlays .gear").forEach(function (el) {
      // The algo gear has no TABLE entry -- it opens the tester instead.
      if (!has(el.dataset.gear)) return;
      el.classList.toggle("dirty", isDirty(el.dataset.gear));
    });
  }

  function schedule() {
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(function () {
      markDirty();
      onChange(current());
    }, 160);
  }

  function popover(group, anchor) {
    close();
    const box = document.createElement("div");
    box.className = "gearpop";
    box.innerHTML =
      '<div class="gearhead">' + group + "</div>" +
      TABLE[group].map(function (k) {
        const v = values[k[0]] != null ? values[k[0]] : (seed(k[0], k[5]) || k[2]);
        return (
          '<label class="knob"><i>' + k[1] + "</i>" +
          '<input type="range" data-k="' + k[0] + '" min="' + k[2] + '" max="' + k[3] +
          '" step="' + k[4] + '" value="' + v + '">' +
          '<b data-v="' + k[0] + '">' + v + "</b></label>"
        );
      }).join("") +
      '<button type="button" class="gearreset">reset ' + group + "</button>";
    document.body.appendChild(box);
    const r = anchor.getBoundingClientRect();
    box.style.left = Math.min(r.left, window.innerWidth - box.offsetWidth - 12) + "px";
    box.style.top = r.bottom + 4 + "px";

    box.querySelectorAll("input[type=range]").forEach(function (el) {
      el.addEventListener("input", function () {
        const key = el.dataset.k;
        values[key] = parseFloat(el.value);
        box.querySelector('[data-v="' + key + '"]').textContent = el.value;
        schedule();
      });
    });
    box.querySelector(".gearreset").addEventListener("click", function () {
      TABLE[group].forEach(function (k) {
        const base = seed(k[0], k[5]);
        if (base == null) return;
        values[k[0]] = base;
        const input = box.querySelector('[data-k="' + k[0] + '"]');
        if (input) input.value = base;
        const out = box.querySelector('[data-v="' + k[0] + '"]');
        if (out) out.textContent = base;
      });
      schedule();
    });
    open = { group: group, box: box };
  }

  function close() {
    if (open) { open.box.remove(); open = null; }
  }

  function attach(cb, openAlgoSettings) {
    onChange = cb || function () {};
    // A gear beside every overlay checkbox that has a tunable, plus one on
    // `conviction` that opens the strategy tester -- the algo's knobs are
    // levels and stops rather than periods, and they belong with the P&L they
    // move, not in a popover of their own.
    document.querySelectorAll("#overlays [data-overlay]").forEach(function (input) {
      const key = input.dataset.overlay;
      const isAlgo = key === "algo";
      if (!has(key) && !isAlgo) return;
      const label = input.closest("label");
      if (!label || label.querySelector(".gear")) return;
      const gear = document.createElement("span");
      gear.className = "gear";
      gear.dataset.gear = key;
      gear.textContent = "⚙";
      gear.title = isAlgo
        ? "algo settings + live backtest (key: t)"
        : key + " settings";
      gear.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (isAlgo) {
          close();
          if (openAlgoSettings) openAlgoSettings();
          return;
        }
        if (open && open.group === key) close();
        else popover(key, gear);
      });
      label.appendChild(gear);
    });
    document.addEventListener("click", function (e) {
      if (open && !open.box.contains(e.target) && !e.target.classList.contains("gear")) {
        close();
      }
    });
    load();
  }

  /* Overlay recomputed series onto a /api/state payload. Returns a shallow
     copy so clearing the knobs restores the shipped chart with no refetch. */
  function applyTo(state, recomputed) {
    if (!recomputed || !state) return state;
    if (recomputed.ticker !== state.ticker || recomputed.interval !== state.interval) {
      return state;
    }
    const out = Object.assign({}, state);
    if (recomputed.overlays) out.overlays = recomputed.overlays;
    if (recomputed.oscillators) out.oscillators = recomputed.oscillators;
    if (recomputed.elmo) out.elmo = recomputed.elmo;
    return out;
  }

  return {
    attach: attach, current: current, applyTo: applyTo, close: close,
    has: has, seedProfile: seedProfile,
  };
})();
