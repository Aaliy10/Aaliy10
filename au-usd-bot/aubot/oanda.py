"""Minimal OANDA v20 REST client (market data, account, orders)."""
from __future__ import annotations

import os

import pandas as pd
import requests

ENVIRONMENTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}
MAX_CANDLES = 5000  # OANDA's per-request limit


class OandaError(RuntimeError):
    pass


class OandaClient:
    def __init__(self, token: str, account_id: str, environment: str = "practice",
                 session: requests.Session | None = None, timeout: float = 15.0):
        if environment not in ENVIRONMENTS:
            raise ValueError(f"environment must be one of {list(ENVIRONMENTS)}")
        self.base_url = ENVIRONMENTS[environment]
        self.environment = environment
        self.account_id = account_id
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339",
        })

    @classmethod
    def from_env(cls, environment: str = "practice") -> "OandaClient":
        token = os.environ.get("OANDA_TOKEN")
        account_id = os.environ.get("OANDA_ACCOUNT_ID")
        if not token or not account_id:
            raise OandaError("Set OANDA_TOKEN and OANDA_ACCOUNT_ID (see .env.example)")
        return cls(token, account_id, environment)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        resp = self.session.request(method, self.base_url + path, timeout=self.timeout, **kwargs)
        try:
            body = resp.json()
        except ValueError:
            body = {"errorMessage": resp.text}
        if not resp.ok:
            message = body.get("errorMessage", body) if isinstance(body, dict) else body
            raise OandaError(f"{method} {path} -> HTTP {resp.status_code}: {message}")
        return body

    # --- market data -----------------------------------------------------

    def candles(self, instrument: str, granularity: str = "H1", count: int = 500,
                to: pd.Timestamp | None = None) -> pd.DataFrame:
        """Mid-price candles, oldest first, with a boolean `complete` column."""
        params = {"granularity": granularity, "count": min(count, MAX_CANDLES), "price": "M"}
        if to is not None:
            params["to"] = pd.Timestamp(to).isoformat()
        body = self._request("GET", f"/v3/instruments/{instrument}/candles", params=params)
        rows = [{
            "time": c["time"],
            "open": float(c["mid"]["o"]),
            "high": float(c["mid"]["h"]),
            "low": float(c["mid"]["l"]),
            "close": float(c["mid"]["c"]),
            "volume": float(c.get("volume", 0)),
            "complete": bool(c.get("complete", True)),
        } for c in body.get("candles", [])]
        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume", "complete"])
        df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("time"), utc=True), name="time")
        return df

    def history(self, instrument: str, granularity: str = "H1", total: int = 5000) -> pd.DataFrame:
        """Up to `total` completed candles, paging backwards past the 5000-candle limit."""
        frames: list[pd.DataFrame] = []
        to = None
        remaining = total
        while remaining > 0:
            want = min(remaining, MAX_CANDLES)
            df = self.candles(instrument, granularity, count=want, to=to)
            if df.empty:
                break
            frames.append(df)
            remaining -= len(df)
            to = df.index[0]
            if len(df) < want:
                break  # reached the start of available history
        if not frames:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        out = pd.concat(frames[::-1]).sort_index()
        out = out[~out.index.duplicated(keep="last")]
        return out[out["complete"]].drop(columns="complete").tail(total)

    # --- account -----------------------------------------------------------

    def nav(self) -> float:
        body = self._request("GET", f"/v3/accounts/{self.account_id}/summary")
        return float(body["account"]["NAV"])

    def position(self, instrument: str) -> int:
        """Net open units for `instrument` (positive long, negative short, 0 flat)."""
        body = self._request("GET", f"/v3/accounts/{self.account_id}/openPositions")
        for p in body.get("positions", []):
            if p.get("instrument") == instrument:
                return int(float(p["long"]["units"])) + int(float(p["short"]["units"]))
        return 0

    # --- trading -----------------------------------------------------------

    def market_order(self, instrument: str, units: int, stop_loss: float,
                     take_profit: float, precision: int) -> dict:
        """Market order with server-side stop-loss and take-profit attached.

        Positive units buy, negative units sell.
        """
        order = {
            "type": "MARKET",
            "instrument": instrument,
            "units": str(int(units)),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": f"{stop_loss:.{precision}f}", "timeInForce": "GTC"},
            "takeProfitOnFill": {"price": f"{take_profit:.{precision}f}", "timeInForce": "GTC"},
        }
        body = self._request("POST", f"/v3/accounts/{self.account_id}/orders", json={"order": order})
        if "orderCancelTransaction" in body:
            reason = body["orderCancelTransaction"].get("reason", "unknown")
            raise OandaError(f"Order cancelled by OANDA: {reason}")
        return body

    def close_position(self, instrument: str, direction: int) -> dict:
        """Close the whole long (direction > 0) or short (direction < 0) position."""
        side = "longUnits" if direction > 0 else "shortUnits"
        return self._request("PUT", f"/v3/accounts/{self.account_id}/positions/{instrument}/close",
                             json={side: "ALL"})
