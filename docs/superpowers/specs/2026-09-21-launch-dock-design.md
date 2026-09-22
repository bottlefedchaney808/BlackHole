# Launch dock — Design Spec

Date: 2026-09-21
Status: Design approved by operator in brainstorm. Spec written for review. No implementation plan yet.
Repo: `E:/BlackHole_Investments/BlackHole`. Code will live at `launch_dock/`. Not Event Desk. Not the chart server. Not the Hermes dashboard.

## What this is

A local page and process that collects existing automations onto one dock so they can be edited, copied, launched, and stopped. It does not grow a new runner, a new algo, or a new order path.

The night case is: a perp sleeve is already up, the saved preset is wrong, you change it on the chart or on the pad, you stop the sleeve, you launch again. That next launch is what it trades. The process that is already up keeps the copy it took at start.

## Locked decisions

| Decision | Value |
|---|---|
| Shape | One dock, one card shape, two seed families that differ in function. |
| Seeds | `stocks` (whale-flow scan) and `perp` (Kalshi margin perp). Event-desk sleeves are collected stop/launch cards, not a third algo. |
| Copy | Changes the instrument only. Architecture and framing stay in the seed's lane. |
| Algo store | `artifacts/chart_app_profiles.json` only. A copy does not get its own algo blob. |
| Card store | `artifacts/launch_dock.json`. Holds cards, seed knobs, pid, argv, and the launch snapshot. No algo keys. |
| Editors | Chart (`:8791`) and pad. Either may save the preset. |
| Running sleeve | Does not re-read. `run_live_perp.py` resolves the profile once, before the loop. A later save marks the card stale. Stop, then Launch, to apply. |
| Leverage | On the card, per market. BTC-PERP is 6x. XRP-PERP is 2x. A copy does not inherit the other market's multiple. |
| Live flag | A new copy is dry-run. It does not inherit `--live`. |
| Restart books | A relaunch passes the card's original `--pnl-since` and `--base-capital`. The dock never defaults those to now. |
| Stop | Perp stop calls `cancel_resting` for that ticker only, then kills the pid. Never `cancel_all`. Cancel failure means the process stays up. |
| Event desk | Stop/launch only. Does not write the chart profile. Does not Buy. `config.py` is not a form. |
| Out | Hot reload inside a runner, a cron console, a new order client, cloning one market's profile onto another. |

## Process and page

- Package: `E:/BlackHole_Investments/BlackHole/launch_dock/`.
- Bind: `127.0.0.1:8792` only. Not `0.0.0.0`. Not `:8791` (chart). Not `:8787` (BlackHole dashboard).
- Interpreter for stocks and perp: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe`. Do not use PATH python.
- The page is one HTML file served by this process. No CDN. No artifact board. Closing the page does not kill a launched sleeve.
- Launch flags are the same detach set `_overnight_bucket.py` already uses: `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW`.

## Two files

Both paths are absolute, under `E:/BlackHole_Investments/BlackHole/artifacts/`. The dock passes that path into `chart_app.profiles`. It does not depend on the process cwd. It does not set `CHART_APP_PROFILES` to a different file than the chart uses.

### Algo file

`artifacts/chart_app_profiles.json`, already owned by `chart_app/profiles.py`.

A record is one key, `TICKER|interval`. Fields the pad may write: `config`, `elmo`, `capital`, `note`. Resolution order stays:

1. `TICKER|interval`
2. `*|interval`
3. `TICKER|*`
4. `*|*`
5. shipped defaults (`source: "defaults"`)

The pad displays `profiles.resolve`, so a card with no symbol profile shows the timeframe or desk record that is actually in force, and names `source`.

### Card file

`artifacts/launch_dock.json`. Schema:

```json
{
  "seeds": {
    "perp": {"hours": 0.0, "cap_dollars": null},
    "stocks": {},
    "event_desk": {}
  },
  "cards": []
}
```

A card:

| Field | Meaning |
|---|---|
| `id` | Stable slug. `perp-btc`, `stocks-spy`. Unique. |
| `seed` | `stocks`, `perp`, or `event_desk`. |
| `instrument` | Chart ticker for stocks and perp (`NVDA`, `BTC-PERP`). Empty for event desk. |
| `interval` | Perp and stocks profile interval. Default `15m`. Ignored by event desk. |
| `leverage` | Perp only. Number, or null if unset. |
| `live` | Perp only. Default false. |
| `subaccount` | Perp only. Default 0. Not copied from another card. |
| `pnl_since` | ISO UTC. Empty until the first successful launch writes it back. |
| `base_capital` | Number. Empty until the first successful launch writes it back. |
| `pid` | OS pid or null. |
| `argv` | Exact argv of the running or last launch. |
| `running_saved_at` | `saved_at` of the profile resolved at launch. Null if never launched. |
| `state` | `stopped`, `running`, `stale`, `launching`, or `unknown`. |

`hours` and `cap_dollars` are seed fields, not card fields. Editing the perp seed changes every perp card's next launch. It does not touch a process that is already up.

`cap_dollars: null` means unset. A perp launch is refused until the seed has a positive `cap_dollars`. There is no silent default. `hours: 0` means until stopped, which is the runner's own meaning, and is allowed.

## Sync

Chart and pad are writers. The running sleeve is not.

1. Pad checks `GET http://127.0.0.1:8791/api/profiles` with a 2 second timeout.
2. Chart up: pad reads that response. Pad saves algo knobs with `POST /api/profiles`. That route already calls `profiles.save` and `_bump_epoch()`, so the open chart picks the save up on its next poll. Pad does not also write the file itself on this path.
3. Chart down (connection refused or timeout): pad calls `profiles.save` on the absolute artifacts path. The chart loads that file the next time it starts.
4. Chart up but `POST` returns `ok: false`: pad shows the error and does not fall back to a direct write. A direct write would skip `_bump_epoch()` and the open chart would keep drawing the old preset.
5. While the pad is open and the chart is up, the pad re-reads `GET /api/profiles` every 2 seconds so a slider save on the chart shows on the card. It does not rebuild chart state.

`profiles.save` and `profiles.delete` take a cross-process lock on `artifacts/chart_app_profiles.lock`, then write `chart_app_profiles.json.tmp` and `os.replace` it onto the json. A thread lock is not enough: chart and pad are different processes. The dock never holds that lock across the HTTP call. The lock lives inside `profiles.save` / `profiles.delete` only.

The chart's own save uses the cwd-relative default, `artifacts/chart_app_profiles.json`. That is the same file as the dock's absolute path only when the chart is started with cwd `E:/BlackHole_Investments/BlackHole`, which is how it is already launched. The dock does not start a second profile file. A pad save always sends `ticker` and `interval` in the POST body. It does not rely on the chart session's current symbol.

Validation rule for a pad save:

- If `config`, `elmo`, or `capital` differ from the stored record, `validation` is `in_sample`.
- If the save changes nothing, keep the stored validation.
- The pad has no control that sets `walk_forward` or `permutation`.

## Seeds and commands

### Stocks

Command, cwd `E:/BlackHole_Investments/BlackHole`:

```text
.venv/Scripts/python.exe -m Direction.whale_scanner TICKER
```

One-shot. It does not read `chart_app_profiles.json`. The pad still shows that ticker's resolved chart preset so the number on the dock matches the chart. Launch passes the ticker only.

Copy asks for a new ticker. It does not copy another ticker's profile onto the new one. The new card resolves whatever profile already exists for that ticker, or defaults.

### Perp

Command, cwd `E:/BlackHole_Investments/BlackHole`:

```text
.venv/Scripts/python.exe -m chart_app.run_live_perp
  --ticker TICKER
  --interval INTERVAL
  --cap-dollars SEED_CAP
  --leverage CARD_LEVERAGE
  --hours SEED_HOURS
  --subaccount CARD_SUBACCOUNT
  [--pnl-since CARD_PNL_SINCE]
  [--base-capital CARD_BASE_CAPITAL]
  [--live]
```

The Kalshi ticker map stays inside `run_live_perp.py`: `BTC-PERP` → `KXBTCPERP`, `XRP-PERP` → `KXXRPPERP`, `GOLD` → `KXGOLDPERP`. The dock does not send orders and does not add a ticker to that map.

Standing leverage, prefilled on a new card and not overridable by copy:

| Instrument | Leverage |
|---|---|
| `BTC-PERP` | 6 |
| `XRP-PERP` | 2 |
| anything else in the map, including `GOLD` | null until set |

Live launch is refused when `leverage` is null. Dry-run is also refused when `leverage` is null, so a 1x runner default is never stored as if it were the sleeve's multiple.

A new copy sets `live` false. Collecting an already-live sleeve is a hand entry of that card's existing flag, not a property of the seed.

First launch of a card with empty `pnl_since` / `base_capital` may omit those flags. The runner then defaults them to now and current equity. Within 15 seconds the dock reads the `session` object from the new journal `artifacts/perp_live/{ticker}_{live|dry}_{stamp}.jsonl` and writes `pnl_since`, `base_capital`, `argv`, `pid`, and `running_saved_at` onto the card. The dock stores the runner's values. It does not invent the timestamp. If that record is missing at 15 seconds, state stays `launching` and the launch is not reported as success.

Every later launch passes the stored `--pnl-since` and `--base-capital`. The dock does not recompute them.

### Event desk

Command, cwd `E:/BlackHole_Investments/BlackHole/Event_Desk`:

```text
py -3.11 event_desk/churn.py watch
```

There is no Event Desk venv. Do not run this card with the BlackHole `.venv`. If `py -3.11` is missing, the launch fails and names that fact.

`watch` is one-shot and does not Buy. The card has Stop and Launch only. Edit and Copy are disabled. Stop kills the pid if that pid's command line is this command. It does not call `profiles.save`. It does not cancel Kalshi orders.

## Stop and launch

Perp stop, in order:

1. Confirm the pid's command line contains `run_live_perp` and this card's `--ticker`. If the pid is alive and the command line does not match, state is `unknown` and the dock does not kill.
2. Call `PerpsClient.cancel_resting` for the mapped Kalshi ticker only. Never `cancel_all`.
3. If cancel raises, stop. The process stays up. The page shows the error.
4. If the pid is already dead, still run step 2 when `live` is true. A dead process can leave a resting bid. Then mark `stopped`.
5. If cancel returns, kill the pid, mark `stopped`, clear `pid`.

On dock startup, a dead pid is marked `stopped`. The dock does not cancel on startup. Cancel happens only on an explicit Stop.

Launch is refused when another card with the same `seed` and `instrument` is `running` or `launching`. One perp ticker, one process. One stock ticker, one scan. One event-desk watch at a time, globally.

A perp card is `stale` when `state` is `running` and `profiles.resolve(instrument, interval)` returns a `saved_at` different from that card's `running_saved_at`. The comparison is that card's own ticker and interval, not any other key in the file. Stale does not change the running process. The page says so. Stop, then Launch, clears it. Stocks and event-desk cards are never `stale`: the whale scan does not read the profile, and event desk does not use it.

The dock polls pid liveness on the same 2 second loop. When a pid has exited, state becomes `stopped` and `pid` is cleared. That poll does not cancel orders. A one-shot stocks scan or event-desk `watch` therefore does not sit on `running` after it exits.

## Page

One card row: seed, instrument, state, saved preset source (`SPY|15m` or `*|15m` or `defaults`), running snapshot time, Stop, Launch.

Edit, stocks and perp only:

- Algo: keys in the resolved `config` and `elmo`, plus shipped defaults for keys the record does not have, so a new ticker is not a blank form. Save writes that `ticker|interval` only.
- Seed: perp `hours` and `cap_dollars`. Save updates the seed. Every perp card uses the new values on its next launch.
- Card: instrument is fixed after create. Leverage, and a live checkbox that defaults off. Turning live on requires a second confirm on the page. The pad does not send the order itself.

Copy, stocks and perp only: new id, new instrument, same seed, `live` false, empty `pnl_since` and `base_capital`, leverage from the standing table or null. Profile is not copied.

## Errors

| Case | Behavior |
|---|---|
| Chart POST fails | Show the error. Do not direct-write. |
| Lock timeout | Save fails. No partial replace. |
| Cancel fails | Do not kill. |
| Pid command line mismatch | Do not kill. State `unknown`. |
| Two cards, same running instrument | Second Launch refused. |
| Perp seed `cap_dollars` null | Launch refused. |
| Perp card `leverage` null | Launch refused, live and dry-run. |
| Relaunch missing stored `pnl_since` after a prior success | Launch refused. Do not invent now. |
| `py -3.11` missing | Event-desk launch refused. |

## Tests

- Two processes save different profile keys under the lock. Both keys survive. A crash mid-write does not leave a truncated file.
- Copy does not copy leverage, `live`, `pnl_since`, `base_capital`, or the profile record.
- BTC copy stays 6x. XRP copy stays 2x. A GOLD copy has null leverage and both launch paths refuse.
- A running perp card flips to `stale` when that card's own resolved `saved_at` changes. The recorded argv is unchanged. A stocks card and an event-desk card do not flip to `stale`.
- When a one-shot pid exits, the 2 second poll marks the card `stopped` and does not call `cancel_resting`.
- Stop calls `cancel_resting` with that ticker. It does not call `cancel_all`. Kill is not called if cancel raises.
- Dead pid and `live` true still calls `cancel_resting`, then marks stopped, and does not kill.
- Alive pid with the wrong command line does not cancel and does not kill.
- Chart-down path calls `profiles.save` on the absolute artifacts path. Chart-up path is a POST and does not also write the file.
- POST `ok: false` does not fall back to a direct write.
- A pad save that changes a knob stores `validation: in_sample` and cannot store `walk_forward`.
- Event-desk Launch does not call `profiles.save`. Its argv is `py -3.11 event_desk/churn.py watch` with the Event Desk cwd.
- A second perp Launch of the same instrument while the first is `running` is refused.
- Restart argv contains the stored `--pnl-since` and `--base-capital`, not the current time.

## Operational notes

- The dock loads `launch_dock.json` once at startup and never reloads it. Hand-editing the file while the dock is up is invisible until restart. Edit cards through the API (`POST /api/copy`, `/api/seed`, `/api/preset`) or restart the dock after hand edits.
- Seeded cards on 2026-09-21: `perp-btc` (BTC-PERP, 6x, dry-run), `stocks-spy` (SPY scan), `desk-watch` (event-desk `churn.py watch`). The perp seed ships unset; a perp launch stays refused until `cap_dollars` is saved.

## Out of scope

- Re-reading the profile inside `run_live_perp.py` or any other runner.
- Editing `event_desk/config.py` from the pad.
- Hermes cron, bots, or scrapers as dock seeds.
- A new Kalshi order path, a new ticker map entry, or `cancel_all`.
- Copying a profile from one instrument onto another.
- Auto-discovering processes that were started outside the dock. An already-running sleeve is a card the operator fills in, including pid, `pnl_since`, and `base_capital`, before the dock is allowed to stop it.
- Binding anything other than `127.0.0.1:8792`.
