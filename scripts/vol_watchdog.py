import sys, time, json, httpx
from concurrent.futures import ThreadPoolExecutor, as_completed

_orig_init = httpx.Client.__init__
def _patched_init(self, *a, **kw):
    kw.setdefault('timeout', 90.0)
    _orig_init(self, *a, **kw)
httpx.Client.__init__ = _patched_init

sys.path.insert(0, '.')
from datetime import datetime, timedelta
from shared.thetadata import ThetaDataController

def pick_expiry(td, t):
    exps = td.list_expirations(t)
    today = datetime.now()
    future = [e for e in exps if datetime.strptime(e, '%Y%m%d') >= today]
    for e in future:
        if datetime.strptime(e, '%Y%m%d') >= today + timedelta(days=28):
            return e
    return future[0] if future else exps[-1]

def _atm(rows, spot, right):
    best = None
    for r in rows:
        if r.get('right') != right:
            continue
        k = float(r['strike']) / 1000.0
        d = abs(k - spot)
        if best is None or d < best[0]:
            best = (d, k, float(r['implied_vol']))
    return best

def analyze(td, t):
    exp = pick_expiry(td, t)
    spot = td.fetch_spot_price(t)
    rows = td.option_bulk_greeks(t, exp)
    c = _atm(rows, spot, 'C')
    p = _atm(rows, spot, 'P')
    cur_iv = None
    if c and p:
        cur_iv = (c[2] + p[2]) / 2.0
    elif c:
        cur_iv = c[2]
    elif p:
        cur_iv = p[2]

    start = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
    end = datetime.now().strftime('%Y%m%d')
    hist = td.option_bulk_hist_eod_greeks(t, exp, start, end)
    closes = td.hist_stock_eod(t, start, end)
    close_by_date = {}
    for r in closes:
        created = r.get('created') or ''
        d = created[:10].replace('-', '') if created else None
        if d and d not in close_by_date:
            close_by_date[d] = float(r.get('close') or 0)

    by_date = {}
    for r in (hist or []):
        d = r.get('date')
        if not d:
            continue
        k = float(r['strike']) / 1000.0
        rt = r.get('right')
        iv = r.get('implied_vol')
        if iv is None:
            continue
        by_date.setdefault(d, {'C': {}, 'P': {}})
        by_date[d][rt][k] = float(iv)

    daily_ivs = []
    for d in sorted(by_date):
        s = close_by_date.get(d)
        if s is None:
            s = spot  # fallback: nearest-to-current-spot per date
        cell = by_date[d]
        ci = min(cell['C'].items(), key=lambda kv: abs(kv[0] - s)) if cell['C'] else None
        pi = min(cell['P'].items(), key=lambda kv: abs(kv[0] - s)) if cell['P'] else None
        if ci and pi:
            daily_ivs.append((ci[1] + pi[1]) / 2.0)
        elif ci:
            daily_ivs.append(ci[1])
        elif pi:
            daily_ivs.append(pi[1])
    daily_ivs = daily_ivs[-20:]
    avg20 = sum(daily_ivs) / len(daily_ivs) if daily_ivs else None
    ratio = (cur_iv / avg20) if (cur_iv and avg20) else None
    pct = None
    if daily_ivs and cur_iv is not None:
        pct = sum(1 for v in daily_ivs if v <= cur_iv) / len(daily_ivs) * 100.0
    return {
        'exp': exp, 'spot': round(spot, 2),
        'iv': round(cur_iv * 100, 1) if cur_iv else None,
        'avg20': round(avg20 * 100, 1) if avg20 else None,
        'ratio': round(ratio, 2) if ratio else None,
        'pct': round(pct, 0) if pct is not None else None,
        'n_days': len(daily_ivs),
    }

def main():
    manifest_tickers = []
    try:
        with open('sentiment-scanner/data/exports/highlighted_ticker_packs/latest_manifest.json') as f:
            m = json.load(f)
        packs = sorted(m.get('packs', []), key=lambda p: p.get('last_updated_at', ''), reverse=True)
        if packs:
            manifest_tickers = packs[0].get('tickers', [])
    except Exception as e:
        print(f'# manifest read failed: {e}', file=sys.stderr)
    default = ['SPY', 'QQQ', 'AAPL', 'NVDA', 'MSFT', 'AMD', 'MU']
    watch = list(dict.fromkeys(manifest_tickers + default))
    print(f'# watchlist manifest={manifest_tickers} -> {watch}', file=sys.stderr)
    out = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_run_one, t): t for t in watch}
        for f in as_completed(futs):
            t = futs[f]
            try:
                out[t] = f.result()
            except Exception as e:
                out[t] = {'error': f'{type(e).__name__}: {str(e)[:120]}'}
    print(f'# all done in {round(time.time()-t0,1)}s', file=sys.stderr)
    print(json.dumps(out, indent=1))

def _run_one(t):
    td = ThetaDataController()
    try:
        return analyze(td, t)
    finally:
        td.close()

if __name__ == '__main__':
    main()
