/*
 * sync-bus.js — page-global quant scope bus.
 *
 * Phase 4 of the widget-native-quant-console plan: a page-global
 * {ticker, expiry, basket} store with EventTarget-based publish/subscribe.
 * Every synced <quant-widget> subscribes; toggling a widget's sync off
 * detaches its listener and its inputs become local-only.
 *
 * Vanilla JS, zero dependencies, no DOM requirement (works in Node too —
 * it only needs EventTarget). ES module. No bundler.
 *
 * Scope shape (canonical):
 *   { ticker: string, expiry: string, basket: string[] | null }
 *   - ticker / expiry are trimmed strings (may be '').
 *   - basket is normalized to an array of trimmed ticker strings, or null
 *     when empty. setScope() accepts a comma-separated string as well.
 *
 * API:
 *   import { syncBus, ScopeBus } from './sync-bus.js';
 *   syncBus.getScope()                      -> shallow copy of the store
 *   syncBus.setScope(partial)               -> merge; returns true if changed
 *   syncBus.subscribe(fn)                   -> fn(scope, changedKeys, bus);
 *                                              returns an unsubscribe fn
 *   syncBus.addEventListener('scopechange', (e) => e.detail.scope ...)
 *   syncBus.reset(scope)                    -> replace entire store (tests)
 *
 * 'scopechange' CustomEvent detail: { scope, changed }
 * A singleton (syncBus) is exported for page-global use; ScopeBus is the
 * class so isolated instances can be built for tests.
 *
 * The singleton is also cached on globalThis so two copies of this module
 * (e.g. loaded twice by a page) still share one store instead of silently
 * splitting state between two bus instances.
 */

const GLOBAL_KEY = '__quantSyncBus';

const SCOPE_KEYS = ['ticker', 'expiry', 'basket'];

function normalizeScopeField(key, value) {
  if (key === 'basket') {
    if (value == null) return null;
    if (Array.isArray(value)) {
      const out = value
        .map(function (t) { return typeof t === 'string' ? t.trim() : String(t).trim(); })
        .filter(function (t) { return t.length > 0; });
      return out.length ? out : null;
    }
    if (typeof value === 'string') {
      // Comma-separated CSV ("SPY, QQQ") is the form the widget's single
      // basket input produces; split on commas only.
      const out = value
        .split(',')
        .map(function (t) { return t.trim(); })
        .filter(function (t) { return t.length > 0; });
      return out.length ? out : null;
    }
    return null;
  }
  // ticker / expiry
  return typeof value === 'string' ? value.trim() : '';
}

export class ScopeBus extends EventTarget {
  constructor(initial) {
    super();
    this._scope = { ticker: '', expiry: '', basket: null };
    if (initial && typeof initial === 'object') {
      this._merge(initial, { notify: false });
    }
  }

  _merge(partial) {
    let changedKeys = [];
    SCOPE_KEYS.forEach(function (key) {
      if (key in partial) {
        const normalized = normalizeScopeField(key, partial[key]);
        const before = this._scope[key];
        const after = normalized;
        if (key === 'basket') {
          const a = before || [];
          const b = after || [];
          const same =
            a.length === b.length &&
            a.every(function (t, i) { return t === b[i]; });
          if (!same) {
            this._scope[key] = after;
            changedKeys.push(key);
          }
        } else if (before !== after) {
          this._scope[key] = after;
          changedKeys.push(key);
        }
      }
    }, this);
    return changedKeys;
  }

  /** Deep-enough copy so callers can't mutate the store by hand. */
  getScope() {
    const scope = this._scope;
    return {
      ticker: scope.ticker,
      expiry: scope.expiry,
      basket: scope.basket ? scope.basket.slice() : null
    };
  }

  /**
   * Merge `partial` into the store and publish. Only fires when something
   * actually changed. Returns true if any field changed.
   */
  setScope(partial) {
    if (!partial || typeof partial !== 'object') return false;
    const changed = this._merge(partial);
    if (changed.length) {
      this._publish(changed);
      return true;
    }
    return false;
  }

  /** Replace the whole store (used by tests / page init). */
  reset(scope) {
    this._scope = { ticker: '', expiry: '', basket: null };
    const changed = this._merge(scope || {});
    if (changed.length) this._publish(changed);
    return changed.length > 0;
  }

  _publish(changedKeys) {
    const scope = this.getScope();
    const detail = { scope: scope, changed: changedKeys.slice() };
    this.dispatchEvent(new CustomEvent('scopechange', { detail: detail }));
  }

  /**
   * Subscribe to scope changes.
   * @param {Function} fn  fn(scope, changedKeys, bus)
   * @returns {Function}   unsubscribe
   */
  subscribe(fn) {
    if (typeof fn !== 'function') throw new TypeError('subscribe(fn): fn must be a function');
    const handler = function (event) {
      const d = event.detail;
      fn(d.scope, d.changed, this);
    }.bind(this);
    this.addEventListener('scopechange', handler);
    let unsubscribed = false;
    return function unsubscribe() {
      if (unsubscribed) return;
      unsubscribed = true;
      this.removeEventListener('scopechange', handler);
    }.bind(this);
  }
}

/*
 * Cross-TAB persistence.
 *
 * The bus is page-global, which was enough while the desk was the only page
 * with a scope bar. With Volatility / Models / Sentiment / Chart each owning
 * one, "scope is entered once" stopped being true the moment you changed
 * tabs: every navigation dropped the ticker and you retyped it on arrival.
 *
 * So the SINGLETON (only the singleton -- an explicitly constructed ScopeBus
 * stays isolated, which is what the tests rely on) hydrates from
 * localStorage on creation and writes back on every change. Every access is
 * wrapped: localStorage throws in a private window and is simply absent in
 * Node, and a storage failure must never take the scope bar down with it.
 *
 * Note this is per-viewer convenience state, not the Context Store. The
 * Context Store (server-side, scope-keyed) is what carries actual RESULTS
 * between tabs; this only carries which ticker you were looking at.
 */
const STORAGE_KEY = 'quantScope.v1';

function loadPersistedScope() {
  try {
    const raw = globalThis.localStorage && globalThis.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    return {
      ticker: typeof parsed.ticker === 'string' ? parsed.ticker : '',
      expiry: typeof parsed.expiry === 'string' ? parsed.expiry : '',
      basket: Array.isArray(parsed.basket) ? parsed.basket : null
    };
  } catch (e) {
    return null;
  }
}

function persistScope(scope) {
  try {
    if (!globalThis.localStorage) return;
    globalThis.localStorage.setItem(STORAGE_KEY, JSON.stringify(scope));
  } catch (e) {
    /* private window, quota, blocked site data -- scope still works in-page */
  }
}

function ensureGlobalSingleton() {
  if (!globalThis[GLOBAL_KEY]) {
    const bus = new ScopeBus(loadPersistedScope() || undefined);
    bus.addEventListener('scopechange', function (event) {
      persistScope(event.detail.scope);
    });
    globalThis[GLOBAL_KEY] = bus;
  }
  return globalThis[GLOBAL_KEY];
}

/** Page-global bus, persisted across tabs. Import this; widgets subscribe. */
export const syncBus = ensureGlobalSingleton();
