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
    default:
      throw new Error(`unknown command: ${cmd}`);
  }
}

main().catch((e) => {
  console.error(JSON.stringify({ success: false, error: String(e?.message || e) }));
  process.exit(1);
});
