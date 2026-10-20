"""
Global gold & silver data for the dashboard's Global screen and Technical tabs.

1. global_history.csv : long daily OHLC history of COMEX gold & silver futures (USD per troy ounce)
                        plus USD/INR, from Yahoo Finance. Used for charts and technical analysis.
2. global_rates.csv   : a snapshot of the live spot price (goldprice.org, or gold-api.com when
                        goldprice.org blocks the server) appended on every run.

Run:  python fetch_global.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

BASE = Path(__file__).resolve().parent
HISTORY_CSV = BASE / "global_history.csv"
RATES_CSV = BASE / "global_rates.csv"
TICKERS = {"Gold": "GC=F", "Silver": "SI=F"}
TROY_OUNCE_G = 31.1034768
FEED = "https://data-asg.goldprice.org/dbXRates/{cur}"
FEED_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/128.0 Safari/537.36",
    "Referer": "https://goldprice.org/",
    "Origin": "https://goldprice.org",
    "Accept": "application/json, text/plain, */*",
}


# ------------------------------------------------------------- goldprice.org
def live_spot(currencies=("USD", "INR"), timeout: int = 10) -> dict:
    """Live spot from goldprice.org. Returns {'date': str, 'items': {CUR: {...}}} (prices per troy ounce)."""
    r = requests.get(FEED.format(cur=",".join(currencies)), headers=FEED_HEADERS, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    items = {it["curr"]: it for it in j.get("items", [])}
    if not items:
        raise RuntimeError("goldprice.org returned no prices")
    return {"date": j.get("date", ""), "ts": j.get("ts"), "items": items}


GOLD_API = "https://api.gold-api.com/price/{sym}"
FX_API = "https://open.er-api.com/v6/latest/USD"


def gold_api_spot(currencies=("USD", "INR"), timeout: int = 10) -> dict:
    """Spot from gold-api.com (free, CORS-enabled) converted with open.er-api.com FX rates.
    Day change comes from COMEX futures (Yahoo Finance) when available."""
    px, upd = {}, ""
    for k, sym in (("Xau", "XAU"), ("Xag", "XAG")):
        j = requests.get(GOLD_API.format(sym=sym), timeout=timeout).json()
        px[k] = float(j["price"])
        upd = j.get("updatedAtReadable") or j.get("updatedAt", "")
    fx = {"USD": 1.0}
    if any(c != "USD" for c in currencies):
        fx.update(requests.get(FX_API, timeout=timeout).json().get("rates", {}))
    pcs = {}
    try:
        import yfinance as yf
        for k, sym in (("Xau", "GC=F"), ("Xag", "SI=F")):
            fi = yf.Ticker(sym).fast_info
            if fi.previous_close:
                pcs[k] = (fi.last_price / fi.previous_close - 1) * 100
    except Exception:
        pass
    items = {}
    for cur in currencies:
        if cur not in fx:
            continue
        r = float(fx[cur])
        it = {"curr": cur}
        for k, p in px.items():
            pc = pcs.get(k)
            close = p / (1 + pc / 100) if pc is not None else float("nan")
            it[f"{k.lower()}Price"] = p * r
            it[f"{k.lower()}Close"] = close * r
            it[f"chg{k}"] = (p - close) * r
            it[f"pc{k}"] = pc if pc is not None else float("nan")
        items[cur] = it
    return {"date": f"{upd}".strip(), "items": items, "source": "gold-api.com"}


def live_spot_any(currencies=("USD", "INR")) -> dict:
    """goldprice.org first (works from home networks); gold-api.com where goldprice.org blocks servers."""
    try:
        d = live_spot(currencies)
        d["source"] = "goldprice.org"
        return d
    except Exception:
        return gold_api_spot(currencies)


def snapshot() -> int:
    """Append the current spot (USD + INR) to global_rates.csv."""
    data = live_spot_any(("USD", "INR"))
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    rows = []
    for cur, it in data["items"].items():
        for metal, k in (("Gold", "Xau"), ("Silver", "Xag")):
            rows.append({
                "Date": now.strftime("%Y-%m-%d"), "Time (IST)": now.strftime("%H:%M"),
                "Feed time": data["date"], "Metal": metal, "Currency": cur,
                "Per oz": it[f"{k.lower()}Price"],
                "Change": it[f"chg{k}"],
                "Change %": it[f"pc{k}"],
                "Prev close": it[f"{k.lower()}Close"],
                "Source": data.get("source", ""),
            })
    df = pd.DataFrame(rows)
    if RATES_CSV.exists():
        df = pd.concat([pd.read_csv(RATES_CSV), df], ignore_index=True)
    df.to_csv(RATES_CSV, index=False)
    return len(rows)


# ------------------------------------------------------------- long history
def _hist(symbol: str, period: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"No data for {symbol}")
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df[["Open", "High", "Low", "Close"]]


def build(period: str = "10y") -> pd.DataFrame:
    """Daily OHLC in USD per troy ounce + USDINR, both metals stacked."""
    fx = _hist("INR=X", period)["Close"].rename("USDINR")
    frames = []
    for metal, sym in TICKERS.items():
        df = _hist(sym, period).join(fx, how="left")
        df["USDINR"] = df["USDINR"].ffill().bfill()
        df["Metal"] = metal
        frames.append(df.dropna(subset=["Close"])[["Metal", "Open", "High", "Low", "Close", "USDINR"]])
    out = pd.concat(frames).reset_index(names="Date").sort_values(["Metal", "Date"])
    num = ["Open", "High", "Low", "Close", "USDINR"]
    out[num] = out[num].round(4)
    return out


if __name__ == "__main__":
    # Each part is independent: a failure in one never stops the daily job.
    try:
        n = snapshot()
        print(f"Spot snapshot: {n} rows appended to {RATES_CSV.name}")
    except Exception as e:
        print(f"Spot snapshot skipped: {e}")
    try:
        data = build()
        data.to_csv(HISTORY_CSV, index=False)
        print(f"Saved {len(data)} rows to {HISTORY_CSV.name}: "
              + ", ".join(f"{m} {g['Date'].min():%Y-%m-%d}..{g['Date'].max():%Y-%m-%d}" for m, g in data.groupby("Metal")))
    except Exception as e:
        print(f"Long history not updated: {e}")
    sys.exit(0)
