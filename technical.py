"""
Technical analysis helpers for the dashboard: indicators, regression channel,
flag-pattern detection and bottom signals. Pure pandas/numpy, no I/O.
All functions take a daily closing-price Series indexed by date.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------- indicators
def indicators(s: pd.Series) -> pd.DataFrame:
    s = s.astype(float)
    df = pd.DataFrame({"Close": s})
    for n in (20, 50, 200):
        df[f"SMA{n}"] = s.rolling(n).mean()
    ema12 = s.ewm(span=12, adjust=False).mean()
    ema26 = s.ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACDSignal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACDHist"] = df["MACD"] - df["MACDSignal"]

    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = (100 - 100 / (1 + rs)).fillna(100).where(delta.notna().cumsum() >= 14)

    std20 = s.rolling(20).std()
    df["BBUpper"] = df["SMA20"] + 2 * std20
    df["BBLower"] = df["SMA20"] - 2 * std20
    return df


# ------------------------------------------------------------------- channel
def regression_channel(s: pd.Series, n: int = 60, k: float = 2.0):
    """Linear-regression channel over the last n days (mid ± k·std of residuals)."""
    y = s.iloc[-n:].astype(float)
    if len(y) < 10:
        return None, None
    x = np.arange(len(y))
    slope, intercept = np.polyfit(x, y.values, 1)
    mid = intercept + slope * x
    sd = float(np.std(y.values - mid, ddof=1))
    ch = pd.DataFrame({"Mid": mid, "Upper": mid + k * sd, "Lower": mid - k * sd}, index=y.index)
    last, up, lo = y.iloc[-1], ch["Upper"].iloc[-1], ch["Lower"].iloc[-1]
    slope_pct = slope / mid[-1] * 100
    info = {
        "days": len(y),
        "slope_pct_per_day": slope_pct,
        "slope_pct_per_month": slope_pct * 21,
        "direction": "Rising" if slope_pct > 0.03 else "Falling" if slope_pct < -0.03 else "Sideways",
        "position": (last - lo) / (up - lo) if up != lo else 0.5,   # 0 = bottom line, 1 = top line
        "width_pct": (up - lo) / mid[-1] * 100,
        "status": "Above channel (breakout)" if last > up else "Below channel (breakdown)" if last < lo else "Inside channel",
        "upper": up, "lower": lo, "mid": mid[-1],
    }
    return ch, info


# ---------------------------------------------------------------------- flag
def find_flag(s: pd.Series, pole_min_move: float = 0.03, pole_max: int = 12,
              flag_min: int = 4, flag_max: int = 20, max_retrace: float = 0.5, min_z: float = 2.5):
    """
    Look for a flag ending at the latest bar:
      pole  = sharp move of at least `pole_min_move` within `pole_max` days
      flag  = tight, flat or slightly counter-trend consolidation of `flag_min`-`flag_max` days
              that gives back at most `max_retrace` of the pole.
    Returns the best candidate dict or None.
    """
    s = s.astype(float)
    n = len(s)
    v = s.values
    logret = np.diff(np.log(v))
    best = None
    for flag_len in range(flag_min, flag_max + 1):
        f0 = n - flag_len - 1                 # pole top/bottom = flag start
        if f0 < 3:
            break
        for pole_len in range(3, pole_max + 1):
            p0 = f0 - pole_len
            if p0 < 0:
                break
            move = (v[f0] - v[p0]) / v[p0]
            if abs(move) < pole_min_move:
                continue
            bull = move > 0
            # pole must be statistically unusual vs the 60 days before it
            hist = logret[max(0, p0 - 60):p0]
            if len(hist) >= 20:
                z = abs(np.log(v[f0] / v[p0])) / (hist.std() * np.sqrt(pole_len) + 1e-12)
                if z < min_z:
                    continue
            pole = v[p0:f0 + 1]
            if bull and (v[f0] < pole.max() or v[p0] > pole.min()):
                continue
            if not bull and (v[f0] > pole.min() or v[p0] < pole.max()):
                continue
            height = abs(v[f0] - v[p0])
            body = v[f0 + 1:n - 1] if flag_len > 1 else v[f0 + 1:n]   # flag bars before today
            if len(body) < flag_min - 1:
                continue
            hi, lo = body.max(), body.min()
            if hi - lo > height * max_retrace:
                continue
            if bull and lo < v[f0] - height * max_retrace:
                continue
            if not bull and hi > v[f0] + height * max_retrace:
                continue
            xs = np.arange(len(body))
            fslope = np.polyfit(xs, body, 1)[0] if len(body) > 1 else 0.0
            pole_speed = height / pole_len
            if bull and fslope > 0.25 * pole_speed:       # still rising hard = not a flag
                continue
            if not bull and fslope < -0.25 * pole_speed:
                continue
            # Sloped flag channel through the consolidation, projected to today
            fit = np.polyval(np.polyfit(xs, body, 1), xs) if len(body) > 1 else body
            resid = body - fit
            today_mid = np.polyval(np.polyfit(xs, body, 1), len(body)) if len(body) > 1 else body[-1]
            up_today, lo_today = today_mid + resid.max(), today_mid + resid.min()
            tol = 0.002 * v[f0]
            last = v[-1]
            if bull:
                status = ("Breakout up" if last > up_today + tol else
                          "Failed (broke down)" if last < lo_today - tol else "Forming")
                trigger, invalid = up_today, lo_today
                target = up_today + height
            else:
                status = ("Breakdown" if last < lo_today - tol else
                          "Failed (broke up)" if last > up_today + tol else "Forming")
                trigger, invalid = lo_today, up_today
                target = lo_today - height
            flag_line = {"x0": s.index[f0 + 1], "x1": s.index[-1],
                         "up0": fit[0] + resid.max(), "up1": up_today,
                         "lo0": fit[0] + resid.min(), "lo1": lo_today}
            score = (abs(move) / max((hi - lo) / v[f0], 1e-4)) * (1 + 0.02 * pole_len ** -1)
            cand = {
                "type": "Bull flag" if bull else "Bear flag",
                "pole_start": s.index[p0], "pole_end": s.index[f0], "flag_end": s.index[-1],
                "pole_move_pct": move * 100, "pole_days": pole_len, "flag_days": flag_len,
                "flag_high": hi, "flag_low": lo, "trigger": trigger, "invalidation": invalid,
                "target": target, "status": status, "score": score,
                "retrace_pct": ((v[f0] - lo) if bull else (hi - v[f0])) / height * 100,
                "lines": flag_line,
            }
            if best is None or cand["score"] > best["score"]:
                best = cand
    return best


# ------------------------------------------------------------------- bottoms
def swing_lows(s: pd.Series, w: int = 3) -> pd.Series:
    v = s.values
    idx = [i for i in range(w, len(v) - w) if v[i] == v[i - w:i + w + 1].min()]
    return s.iloc[idx]


def double_bottom(s: pd.Series, lookback: int = 90, tol: float = 0.015, min_gap: int = 5, min_bounce: float = 0.025):
    seg = s.iloc[-lookback:]
    lows = swing_lows(seg)
    best = None
    for i in range(len(lows)):
        for j in range(i + 1, len(lows)):
            a, b = lows.iloc[i], lows.iloc[j]
            ia, ib = seg.index.get_loc(lows.index[i]), seg.index.get_loc(lows.index[j])
            if ib - ia < min_gap or abs(a - b) / a > tol:
                continue
            neck = seg.iloc[ia:ib + 1].max()
            if neck / max(a, b) - 1 < min_bounce:
                continue
            if seg.iloc[ib:].min() < min(a, b) * (1 - tol):      # broke below the bottoms since
                continue
            cand = {"low1": (lows.index[i], a), "low2": (lows.index[j], b), "neckline": neck,
                    "confirmed": seg.iloc[-1] > neck, "target": neck + (neck - min(a, b))}
            if best is None or cand["low2"][0] > best["low2"][0]:
                best = cand
    return best


def bottom_signals(df: pd.DataFrame, channel_info: dict | None) -> tuple[list[dict], int, str]:
    """Returns (signals, active_count, verdict). Each signal: name, active, detail."""
    s = df["Close"]
    last = s.iloc[-1]
    rsi = df["RSI"].dropna()
    sig = []

    def add(name, active, detail):
        sig.append({"name": name, "active": bool(active), "detail": detail})

    if len(rsi):
        r = rsi.iloc[-1]
        add("RSI oversold", r < 30, f"RSI(14) is {r:.0f} (below 30 = oversold)")
        recent_min = rsi.iloc[-10:].min()
        add("RSI turning up from oversold", recent_min < 30 <= r,
            f"Lowest RSI in last 10 days {recent_min:.0f}, now {r:.0f}")
    if len(s) >= 40 and len(rsi) >= 40:
        p_now, p_prev = s.iloc[-20:], s.iloc[-40:-20]
        r_now, r_prev = df["RSI"].iloc[-20:], df["RSI"].iloc[-40:-20]
        div = p_now.min() < p_prev.min() and r_now.min() > r_prev.min()
        add("Bullish RSI divergence", div,
            "Price made a lower low but RSI made a higher low" if div else "No divergence in the last 40 days")
    lows = swing_lows(s.iloc[-120:])
    if len(lows):
        sup = lows.min()
        near = abs(last - sup) / sup <= 0.015
        add("Near support", near, f"Nearest major support ≈ {sup:,.0f} ({(last / sup - 1) * 100:+.1f}% away)")
    if pd.notna(df["BBLower"].iloc[-1]):
        lb = df["BBLower"].iloc[-1]
        add("At lower Bollinger band", last <= lb * 1.005, f"Lower band {lb:,.0f}")
    if channel_info:
        add("Bottom of price channel", channel_info["position"] <= 0.15,
            f"Price is at {channel_info['position'] * 100:.0f}% of the channel (0% = bottom line)")
    h = df["MACDHist"].dropna()
    if len(h) >= 4:
        cross = (h.iloc[-1] > 0) and (h.iloc[-4:-1] <= 0).any() and df["MACD"].iloc[-1] < 0
        add("MACD bullish cross below zero", cross, "MACD crossed above its signal line in the last 3 days while still negative"
            if cross else f"MACD {df['MACD'].iloc[-1]:,.1f} vs signal {df['MACDSignal'].iloc[-1]:,.1f}")
    db = double_bottom(s)
    if db:
        add("Double bottom", True,
            f"Lows {db['low1'][1]:,.0f} ({db['low1'][0]:%d %b}) and {db['low2'][1]:,.0f} ({db['low2'][0]:%d %b}), "
            f"neckline {db['neckline']:,.0f} — {'confirmed (price above neckline)' if db['confirmed'] else 'not yet confirmed'}")
    else:
        add("Double bottom", False, "No double bottom in the last 90 days")

    active = sum(x["active"] for x in sig)
    verdict = ("Strong bottom setup" if active >= 4 else "Several bottom signs" if active == 3
               else "Early bottom signs" if active >= 1 else "No bottom signs")
    return sig, active, verdict


# ------------------------------------------------------------------- summary
def trend_summary(df: pd.DataFrame) -> list[tuple[str, str, str]]:
    """(Indicator, Reading, Meaning) rows."""
    last = df.iloc[-1]
    c = last["Close"]
    rows = []
    for n in (20, 50, 200):
        v = last[f"SMA{n}"]
        if pd.notna(v):
            rows.append((f"{n}-day average", f"{v:,.0f}",
                         f"Price {'above' if c > v else 'below'} it ({(c / v - 1) * 100:+.1f}%) — "
                         f"{'bullish' if c > v else 'bearish'} on the {'short' if n == 20 else 'medium' if n == 50 else 'long'} term"))
    if pd.notna(last["SMA50"]) and pd.notna(last["SMA200"]):
        prev = df[["SMA50", "SMA200"]].dropna().iloc[-6:]
        crossed = ((prev["SMA50"] > prev["SMA200"]).astype(int).diff().fillna(0))
        note = (" (golden cross in the last 5 days)" if (crossed > 0).any()
                else " (death cross in the last 5 days)" if (crossed < 0).any() else "")
        rows.append(("50 vs 200-day", "50 above 200" if last["SMA50"] > last["SMA200"] else "50 below 200",
                     ("Long-term uptrend" if last["SMA50"] > last["SMA200"] else "Long-term downtrend") + note))
    if pd.notna(last["RSI"]):
        r = last["RSI"]
        rows.append(("RSI (14)", f"{r:.0f}", "Overbought (>70)" if r > 70 else "Oversold (<30)" if r < 30
                     else "Strong momentum" if r >= 55 else "Weak momentum" if r <= 45 else "Neutral"))
    if pd.notna(last["MACD"]):
        rows.append(("MACD", f"{last['MACD']:,.1f}", ("Above" if last["MACD"] > last["MACDSignal"] else "Below")
                     + " signal line — momentum " + ("improving" if last["MACDHist"] > df["MACDHist"].iloc[-2] else "fading")))
    if pd.notna(last["BBUpper"]):
        pos = (c - last["BBLower"]) / (last["BBUpper"] - last["BBLower"])
        rows.append(("Bollinger (20, 2)", f"{pos * 100:.0f}% of band",
                     "Stretched high" if pos > 0.95 else "Stretched low" if pos < 0.05 else "Within normal range"))
    return rows


# ------------------------------------------------------- levels & outlook
def support_resistance(s: pd.Series, lookback: int = 180, w: int = 5, tol: float = 0.012, k: int = 3):
    """Cluster swing highs/lows into horizontal levels. Returns (supports, resistances) below/above price."""
    seg = s.iloc[-lookback:].astype(float)
    v = seg.values
    pts = []
    for i in range(w, len(v) - w):
        win = v[i - w:i + w + 1]
        if v[i] == win.min() or v[i] == win.max():
            pts.append(v[i])
    pts.sort()
    clusters: list[list[float]] = []
    for p in pts:
        if clusters and abs(p - np.mean(clusters[-1])) / p <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    levels = sorted(((float(np.mean(c)), len(c)) for c in clusters), key=lambda x: -x[1])
    last = v[-1]
    sup = sorted([lv for lv in levels if lv[0] < last * (1 - 0.002)], key=lambda x: -x[0])[:k]
    res = sorted([lv for lv in levels if lv[0] > last * (1 + 0.002)], key=lambda x: x[0])[:k]
    return sup, res


def fibonacci(s: pd.Series, lookback: int = 120) -> dict:
    """Retracement levels of the main swing in the lookback window."""
    seg = s.iloc[-lookback:].astype(float)
    hi_d, lo_d = seg.idxmax(), seg.idxmin()
    hi, lo = seg.max(), seg.min()
    up_swing = lo_d < hi_d                       # low came first -> retracing an up-move
    ratios = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]
    if up_swing:
        levels = {f"{r * 100:.1f}%": hi - (hi - lo) * r for r in ratios}
    else:
        levels = {f"{r * 100:.1f}%": lo + (hi - lo) * r for r in ratios}
    return {"direction": "up-swing" if up_swing else "down-swing", "high": (hi_d, hi), "low": (lo_d, lo),
            "levels": levels}


def expected_range(s: pd.Series, horizons=(5, 21), window: int = 60) -> list[dict]:
    """Statistical range from recent volatility (log returns). ~68% and ~95% bands."""
    r = np.log(s.astype(float)).diff().dropna().iloc[-window:]
    sd, last = float(r.std()), float(s.iloc[-1])
    out = []
    for h in horizons:
        k = sd * np.sqrt(h)
        out.append({"days": h, "low68": last * np.exp(-k), "high68": last * np.exp(k),
                    "low95": last * np.exp(-2 * k), "high95": last * np.exp(2 * k), "sd_pct": k * 100})
    return out


def outlook(df: pd.DataFrame, ch_info: dict | None, flag: dict | None, bottom_active: int) -> tuple[str, int, list[str]]:
    """Score simple, transparent factors into a bias. Returns (label, score, reasons)."""
    last = df.iloc[-1]
    c = last["Close"]
    score, why = 0, []

    def f(cond, pts, text_pos, text_neg=None):
        nonlocal score
        if cond is None:
            return
        score += pts if cond else -pts
        why.append(("▲ " if cond else "▼ ") + (text_pos if cond else (text_neg or text_pos)))

    f(c > last["SMA20"] if pd.notna(last["SMA20"]) else None, 1, "Above 20-day average", "Below 20-day average")
    f(c > last["SMA50"] if pd.notna(last["SMA50"]) else None, 1, "Above 50-day average", "Below 50-day average")
    f(last["SMA50"] > last["SMA200"] if pd.notna(last["SMA200"]) else None, 1,
      "50-day above 200-day (long-term uptrend)", "50-day below 200-day (long-term downtrend)")
    f(last["MACD"] > last["MACDSignal"] if pd.notna(last["MACD"]) else None, 1,
      "MACD above signal", "MACD below signal")
    if pd.notna(last["RSI"]):
        if last["RSI"] >= 55:
            score += 1; why.append("▲ RSI shows strong momentum")
        elif last["RSI"] <= 45:
            score -= 1; why.append("▼ RSI shows weak momentum")
        if last["RSI"] > 70:
            score -= 1; why.append("▼ RSI overbought: pullback risk")
    if ch_info:
        if ch_info["direction"] == "Rising":
            score += 1; why.append("▲ Price channel is rising")
        elif ch_info["direction"] == "Falling":
            score -= 1; why.append("▼ Price channel is falling")
    if flag and flag["status"] in ("Forming", "Breakout up", "Breakdown"):
        bull = flag["type"] == "Bull flag"
        score += 2 if bull else -2
        why.append(("▲ " if bull else "▼ ") + f"{flag['type']} ({flag['status'].lower()})")
    if bottom_active >= 3:
        score += 1; why.append(f"▲ {bottom_active} bottom signals active")
    label = ("Bullish" if score >= 4 else "Mildly bullish" if score >= 2 else
             "Bearish" if score <= -4 else "Mildly bearish" if score <= -2 else "Neutral")
    return label, score, why
