/**
 * direct_cdp.mjs — drive TradingView CDP directly (bypasses the Hermes MCP
 * transport). Recovery path when the in-session MCP server has a stale
 * TV_CDP_PORT env (config changes need a gateway restart to propagate).
 *
 * Usage:
 *   node direct_cdp.mjs health
 *   node direct_cdp.mjs watchlist
 *   node direct_cdp.mjs add AAPL
 *   node direct_cdp.mjs addbulk AAPL,MSFT,SPY
 *   node direct_cdp.mjs quote AAPL
 *   node direct_cdp.mjs chart-state
 *   node direct_cdp.mjs lists-debug        # dump the watchlist panel's React
 *                                           # fiber state -- exploratory, read-only
 *   node direct_cdp.mjs switch-list NAME    # switch the active watchlist by
 *                                           # clicking the UI (mutates chart state)
 */
import { healthCheck } from './src/core/health.js';
import * as wl from './src/core/watchlist.js';

const [, , cmd, arg] = process.argv;

async function main() {
  switch (cmd) {
    case 'health': {
      const r = await healthCheck();
      console.log(JSON.stringify(r, null, 1));
      break;
    }
    case 'watchlist': {
      const r = await wl.get();
      console.log(JSON.stringify(r, null, 1));
      break;
    }
    case 'add': {
      if (!arg) throw new Error('usage: add SYMBOL');
      console.log(JSON.stringify(await wl.add({ symbol: arg }), null, 1));
      break;
    }
    case 'addbulk': {
      if (!arg) throw new Error('usage: addbulk SYM1,SYM2,...');
      const symbols = arg.split(',').map((s) => s.trim()).filter(Boolean);
      console.log(JSON.stringify(await wl.addBulk({ symbols }), null, 1));
      break;
    }
    case 'quote': {
      const { quote } = await import('./src/core/chart.js');
      console.log(JSON.stringify(await quote({ symbol: arg }), null, 1));
      break;
    }
    case 'chart-state': {
      const { getState } = await import('./src/core/chart.js');
      console.log(JSON.stringify(await getState({}), null, 1));
      break;
    }
    case 'lists-debug': {
      const { evaluateAsync, evaluate } = await import('./src/connection.js');
      const raw = await evaluateAsync(`
        fetch('https://www.tradingview.com/api/v1/symbols_list/custom/', { credentials: 'include' })
          .then(function(r) { return r.text(); })
          .catch(function(e) { return 'ERR ' + String(e); })
      `);
      const fiber = await evaluate(`
        (function() {
          var panel = document.querySelector('[class*="layout__area--right"]');
          if (!panel) return null;
          var rows = panel.querySelectorAll('[data-symbol-full]');
          if (!rows.length) return null;
          var row = rows[0];
          var reactKey = Object.keys(row).find(function(k) { return k.indexOf('__reactFiber') === 0; });
          if (!reactKey) return null;
          var fiber = row[reactKey];
          var found = [];
          var count = 0;
          while (fiber && count < 45) {
            var p = fiber.memoizedProps;
            if (p) {
              var keys = Object.keys(p);
              if (keys.indexOf('current') >= 0 && p.current && typeof p.current === 'object' && p.current.id) {
                found.push({ id: p.current.id, name: p.current.name, symbols: (p.current.symbols || []).slice(0, 5) });
              }
              if (p.watchlists && Array.isArray(p.watchlists)) {
                found.push({ watchlists: p.watchlists.slice(0, 15).map(function(w) { return w && (w.id + ':' + w.name); }) });
              }
              if (p.lists && Array.isArray(p.lists)) {
                found.push({ lists: p.lists.slice(0, 15).map(function(w) { return w && (w.id + ':' + w.name); }) });
              }
            }
            fiber = fiber.return;
            count++;
          }
          return found;
        })()
      `);
      console.log(JSON.stringify({ raw: raw && raw.substring(0, 1500), fiber: fiber }, null, 1));
      break;
    }
    case 'switch-list': {
      if (!arg) throw new Error('usage: switch-list NAME');
      const { evaluate, evaluateAsync, getClient } = await import('./src/connection.js');
      // 1. find + click the current list name button in the watchlist header.
      // The button's own text is whatever list is currently active, so ask
      // watchlist.js for that name instead of hardcoding one -- this only
      // ever worked when the active list happened to be named "Energy".
      const current = await (await import('./src/core/watchlist.js')).get();
      const currentName = current?.list_name;
      const clicked = await evaluate(`
        (function() {
          var currentName = ${JSON.stringify(currentName)};
          var panel = document.querySelector('[class*="layout__area--right"]');
          if (!panel) return { found: false };
          var cands = panel.querySelectorAll('button, [role="button"], [class*="menu"], [class*="select"]');
          for (var i = 0; i < cands.length; i++) {
            var t = (cands[i].textContent || '').trim();
            var title = (cands[i].getAttribute('aria-label') || '');
            if (t && t.length <= 30 && (t === currentName || title.indexOf('watchlist') >= 0 || title.indexOf('Watchlist') >= 0)) {
              cands[i].click();
              return { found: true, text: t, title: title };
            }
          }
          // fallback: any element whose exact text is the current list name
          if (currentName) {
            var all = panel.querySelectorAll('*');
            for (var j = 0; j < all.length; j++) {
              var tt = (all[j].textContent || '').trim();
              if (tt === currentName && all[j].children.length === 0) {
                all[j].click();
                return { found: true, text: tt, title: 'exact-text' };
              }
            }
          }
          return { found: false };
        })()
      `);
      if (!clicked?.found) throw new Error('List selector button not found: ' + JSON.stringify(clicked));
      await new Promise((r) => setTimeout(r, 600));
      // 2. click the target item in the opened popup
      const chosen = await evaluate(`
        (function() {
          var target = ${JSON.stringify(arg)};
          var items = document.querySelectorAll('[role="option"], [role="menuitem"], [class*="item"], [class*="option"]');
          for (var i = 0; i < items.length; i++) {
            var t = (items[i].textContent || '').trim();
            if (t === target || t.indexOf(target) === 0) {
              items[i].click();
              return { found: true, text: t };
            }
          }
          return { found: false };
        })()
      `);
      if (!chosen?.found) throw new Error('List item not found: ' + JSON.stringify(chosen));
      await new Promise((r) => setTimeout(r, 800));
      // 3. verify
      const wl = await (await import('./src/core/watchlist.js')).get();
      console.log(JSON.stringify({ clicked, chosen, watchlist: { list_name: wl.list_name, list_id: wl.list_id, count: wl.count } }, null, 1));
      break;
    }
    default:
      throw new Error(`unknown command: ${cmd}`);
  }
}

main().catch((e) => {
  console.error(JSON.stringify({ success: false, error: String(e?.message || e) }));
  process.exit(1);
}).finally(() => setTimeout(() => process.exit(0), 300));
