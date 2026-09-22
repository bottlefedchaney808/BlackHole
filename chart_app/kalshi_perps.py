"""Signed Kalshi margin (perps) client -- the only module in chart_app that can trade.

Every other venue in chart_app is a public, keyless, read-only endpoint. This one
is not: it signs with the Kalshi API key and can place, cancel and protect real
orders on `KXBTCPERP`. It is used only by `run_live_perp`, which refuses to start
without `--live` and a dollar cap.

Credentials come from the environment (`KALSHI_KEYID`, `KALSHI_PRIVATE_KEY_PATH`)
or the Event_Desk lab `.env`, never from this file.

Units, measured 2026-09-20 on `GET /margin/markets/KXBTCPERP`:
    contract_size 0.0001 BTC, price quoted in dollars PER CONTRACT (BTC $81,288
    reads 8.1288), tick 0.0001 (= $1 of BTC), whole contracts only
    (`fractional_trading_enabled: false`). Fees: maker 2bp, taker 12bp
    (`GET /margin/fee_tiers`).
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

HOST = "https://external-api.kalshi.com"
PREFIX = "/trade-api/v2"
TICKER = "KXBTCPERP"
_LAB_ENV = (
    Path(__file__).resolve().parent.parent / "Event_Desk" / "kalshi_btc15m" / ".env"
)


class KalshiPerpsError(RuntimeError):
    def __init__(self, status: int, body: Any, path: str):
        self.status = status
        self.body = body
        super().__init__(f"Kalshi {status} on {path}: {str(body)[:300]}")


def _load_lab_env() -> None:
    """Fill KALSHI_* from the lab .env if the caller did not export them."""
    if os.environ.get("KALSHI_KEYID") or not _LAB_ENV.exists():
        return
    for line in _LAB_ENV.read_text().splitlines():
        key, sep, val = line.partition("=")
        if sep and key.strip().startswith("KALSHI_"):
            os.environ.setdefault(key.strip(), val.strip().strip('"'))


def sign(pem: bytes, ts_ms: int, method: str, path: str) -> str:
    """RSA-PSS SHA256 over `{ts}{METHOD}{path}`, as Kalshi documents it."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(pem, password=None)
    sig = key.sign(
        f"{ts_ms}{method.upper()}{path}".encode(),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode()


def fmt_price(value: float) -> str:
    return f"{round(value, 4):.4f}"


class PerpsClient:
    def __init__(self, *, subaccount: int = 0, ticker: str = TICKER, timeout: float = 15.0) -> None:
        _load_lab_env()
        self.keyid = os.environ.get("KALSHI_KEYID", "")
        key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
        if not self.keyid or not key_path or not Path(key_path).is_file():
            raise KalshiPerpsError(
                0, "KALSHI_KEYID / KALSHI_PRIVATE_KEY_PATH not set", ""
            )
        self._pem = Path(key_path).read_bytes()
        self.subaccount = subaccount
        self.ticker = ticker
        self.timeout = timeout
        self._http = requests.Session()

    # -- transport ------------------------------------------------------------
    def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        body: dict | None = None,
    ) -> dict[str, Any]:
        full = PREFIX + path
        ts = int(datetime.now(UTC).timestamp() * 1000)
        headers = {
            "Accept": "application/json",
            "KALSHI-ACCESS-KEY": self.keyid,
            "KALSHI-ACCESS-TIMESTAMP": str(ts),
            "KALSHI-ACCESS-SIGNATURE": sign(self._pem, ts, method, full),
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        resp = self._http.request(
            method,
            HOST + full,
            headers=headers,
            params=params,
            data=json.dumps(body) if body is not None else None,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            try:
                err: Any = resp.json()
            except ValueError:
                err = resp.text
            raise KalshiPerpsError(resp.status_code, err, path)
        return resp.json() if resp.content else {}

    # -- reads ----------------------------------------------------------------
    def market(self, ticker: str | None = None) -> dict[str, Any]:
        ticker = ticker or self.ticker
        return self._call("GET", f"/margin/markets/{ticker}")["market"]

    def top_of_book(self, ticker: str | None = None) -> tuple[float, float]:
        m = self.market(ticker)
        return float(m["bid"]), float(m["ask"])

    def position(self, ticker: str | None = None) -> float:
        ticker = ticker or self.ticker
        rows = self._call(
            "GET",
            "/margin/positions",
            params={"ticker": ticker, "subaccount": self.subaccount},
        ).get("positions", [])
        return sum(
            float(r.get("position") or 0)
            for r in rows
            if r.get("market_ticker") == ticker
            and int(r.get("subaccount", 0)) == self.subaccount
        )

    def equity(self) -> float:
        bal = self._call("GET", "/margin/balance")
        for row in bal.get("subaccount_balances", []):
            if int(row.get("subaccount", -1)) == self.subaccount:
                return float(row.get("account_equity") or 0)
        return 0.0

    def total_equity(self) -> float:
        """Equity summed over EVERY subaccount -- the money that actually exists.

        Moving collateral between subaccounts (an isolated manual trade in 64
        takes its margin out of 0) changes each subaccount's number but not
        this one.
        """
        bal = self._call("GET", "/margin/balance")
        return sum(
            float(row.get("account_equity") or 0)
            for row in bal.get("subaccount_balances", [])
        )

    def unrealized_pnl(self, ticker: str | None = None) -> float:
        ticker = ticker or self.ticker
        rows = self._call(
            "GET",
            "/margin/positions",
            params={"ticker": ticker, "subaccount": self.subaccount},
        ).get("positions", [])
        return sum(
            float(r.get("unrealized_pnl") or 0)
            for r in rows
            if r.get("market_ticker") == ticker
            and int(r.get("subaccount", 0)) == self.subaccount
        )

    def fills(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """This subaccount's fills, newest first, following the cursor.

        The `subaccount` filter matters: without it the endpoint returns every
        subaccount's fills, and a manual trade in 64 would read as the sleeve's.
        """
        out: list[dict[str, Any]] = []
        cursor = ""
        while True:
            params: dict[str, Any] = {"subaccount": self.subaccount, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            page = self._call("GET", "/margin/fills", params=params)
            out.extend(page.get("fills", []))
            cursor = page.get("cursor") or ""
            if not cursor or not page.get("fills"):
                return out

    def order(self, order_id: str) -> dict[str, Any]:
        out = self._call("GET", f"/margin/orders/{order_id}")
        return out.get("order", out)

    # -- writes ---------------------------------------------------------------
    def place(
        self,
        side: str,
        count: int,
        price: float,
        *,
        post_only: bool,
        tif: str = "good_till_canceled",
        reduce_only: bool = False,
        ticker: str | None = None,
    ) -> dict[str, Any]:
        if side not in ("bid", "ask") or count <= 0:
            raise ValueError(f"bad order: side={side} count={count}")
        ticker = ticker or self.ticker
        body = {
            "ticker": ticker,
            "client_order_id": f"perp-sleeve-{uuid.uuid4().hex}",
            "side": side,
            "count": f"{int(count)}.00",
            "price": fmt_price(price),
            "time_in_force": tif,
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": post_only,
            "subaccount": self.subaccount,
        }
        if reduce_only:
            body["reduce_only"] = True
        return self._call("POST", "/margin/orders", body=body)

    def cancel(self, order_id: str) -> dict[str, Any]:
        return self._call(
            "DELETE",
            f"/margin/orders/{order_id}",
            params={"subaccount": self.subaccount},
        )

    def cancel_all(self) -> dict[str, Any]:
        return self._call(
            "DELETE", "/margin/orders", params={"subaccount": self.subaccount}
        )

    def cancel_resting(self, ticker: str | None = None) -> int:
        """Cancel this ticker's resting orders only. Never the other sleeve's."""
        ticker = ticker or self.ticker
        n = 0
        data = self._call(
            "GET", "/margin/orders", params={"subaccount": self.subaccount, "limit": 200}
        )
        for order in data.get("orders", []):
            if order.get("ticker") != ticker:
                continue
            if float(order.get("remaining_count") or 0) <= 0:
                continue
            self.cancel(order["order_id"])
            n += 1
        return n

    def set_stop(self, stop_price: float, ticker: str | None = None) -> dict[str, Any]:
        """Bracket stop-loss on the whole position; fires a reduce-only order."""
        ticker = ticker or self.ticker
        return self._call(
            "PUT",
            f"/margin/cross/positions/{ticker}/exit_trigger",
            params={"subaccount": self.subaccount},
            body={"kind": "bracket", "stop_loss_price": fmt_price(stop_price)},
        )

    def clear_stop(self, ticker: str | None = None) -> dict[str, Any]:
        ticker = ticker or self.ticker
        return self._call(
            "DELETE",
            f"/margin/cross/positions/{ticker}/exit_trigger",
            params={"subaccount": self.subaccount},
        )
