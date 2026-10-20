"""Global screen: live spot from goldprice.org, India premium, saved history, technical analysis."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import fetch_global as FG  # noqa: E402
import tech_tab  # noqa: E402

OZ = FG.TROY_OUNCE_G
IST = ZoneInfo("Asia/Kolkata")
CURRENCIES = ["USD", "INR", "EUR", "GBP", "AED", "SAR", "QAR", "KWD", "SGD", "MYR", "AUD", "CAD", "CHF", "JPY", "CNY"]
SYM = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥"}
UNITS = {"Troy ounce": 1.0, "Gram": 1 / OZ, "10 grams": 10 / OZ, "Tola (11.66 g)": 11.6638 / OZ, "Kilogram": 1000 / OZ}
UNIT_SHORT = {"Troy ounce": "oz", "Gram": "g", "10 grams": "10 g", "Tola (11.66 g)": "tola", "Kilogram": "kg"}


# ---------------------------------------------------------------- formatting
def _indian(n: float, dec: int) -> str:
    neg, n = n < 0, abs(n)
    whole, frac = f"{n:.{dec}f}".split(".") if dec else (f"{n:.0f}", "")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
        whole = f"{head},{tail}"
    return ("-" if neg else "") + whole + (f".{frac}" if dec else "")


def money(v, cur: str, dec: int | None = None) -> str:
    if v is None or pd.isna(v):
        return "–"
    if dec is None:
        dec = 0 if abs(v) >= 10000 else 2
    sym = SYM.get(cur, cur + " ")
    if cur == "INR":
        s = _indian(v, dec)
        return f"-{sym}{s[1:]}" if s.startswith("-") else f"{sym}{s}"
    return f"{'-' if v < 0 else ''}{sym}{abs(v):,.{dec}f}"


def signed(v, cur: str, dec: int | None = None) -> str | None:
    if v is None or pd.isna(v):
        return None
    m = money(abs(v), cur, dec)
    return ("+" if v > 0 else "-" if v < 0 else "") + m


# ---------------------------------------------------------------- data
@st.cache_data(ttl=55, show_spinner=False)
def get_live(currencies: tuple[str, ...]) -> dict:
    return FG.live_spot_any(currencies)


def fallback_spot() -> dict | None:
    """When goldprice.org is unreachable: last saved snapshot, else last COMEX close."""
    if FG.RATES_CSV.exists():
        r = pd.read_csv(FG.RATES_CSV)
        if len(r):
            last = r[r["Date"] + r["Time (IST)"] == (r["Date"] + r["Time (IST)"]).max()]
            items = {}
            for cur, g in last.groupby("Currency"):
                gd = {row["Metal"]: row for _, row in g.iterrows()}
                if {"Gold", "Silver"} <= gd.keys():
                    items[cur] = {"xauPrice": gd["Gold"]["Per oz"], "xagPrice": gd["Silver"]["Per oz"],
                                  "chgXau": gd["Gold"]["Change"], "chgXag": gd["Silver"]["Change"],
                                  "pcXau": gd["Gold"]["Change %"], "pcXag": gd["Silver"]["Change %"],
                                  "xauClose": gd["Gold"]["Prev close"], "xagClose": gd["Silver"]["Prev close"]}
            if items:
                t = last.iloc[0]
                return {"date": f"saved {t['Date']} {t['Time (IST)']} IST", "items": items, "stale": True}
    g = tech_tab.load_global(tech_tab.GLOBAL_CSV.stat().st_mtime if tech_tab.GLOBAL_CSV.exists() else None)
    if g is None or g.empty:
        return None
    items = {"USD": {}, "INR": {}}
    for metal, k in (("Gold", "Xau"), ("Silver", "Xag")):
        m = g[g["Metal"] == metal].sort_values("Date")
        c, p, fx = m["Close"].iloc[-1], m["Close"].iloc[-2], m["USDINR"].iloc[-1]
        for cur, f in (("USD", 1.0), ("INR", fx)):
            items[cur].update({f"{k.lower()}Price": c * f, f"chg{k}": (c - p) * f, f"pc{k}": (c / p - 1) * 100,
                               f"{k.lower()}Close": p * f})
    return {"date": f"COMEX close {m['Date'].iloc[-1]:%d %b %Y}", "items": items, "stale": True}


def chennai_latest() -> dict:
    out = {}
    for metal, f, series in (("Gold", "gold_rates.xlsx", "24K /g"), ("Silver", "silver_rates.xlsx", "Silver /g")):
        p = BASE / f
        if not p.exists():
            continue
        try:
            d = pd.read_excel(p, sheet_name="Daily Summary")
            col = f"{series} Rate"
            if col in d and d[col].notna().any():
                row = d.dropna(subset=[col]).iloc[0]          # newest first
                out[metal] = (str(row["Date"])[:10], float(row[col]))
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- UI
st.title("Global Gold & Silver Rates")
st.caption("Live spot prices from goldprice.org, or gold-api.com where goldprice.org is not reachable "
           "(per troy ounce, converted to your unit). "
           "International spot excludes India's import duty and local premium. See the comparison below.")

c1, c2 = st.columns([1, 2])
cur = c1.selectbox("Currency", CURRENCIES, index=0, key="g_cur")
unit = c2.radio("Unit", list(UNITS), index=0, horizontal=True, key="g_unit")
mult, ushort = UNITS[unit], UNIT_SHORT[unit]


@st.fragment(run_every="60s")
def live_panel(cur: str, mult: float, ushort: str):
    wanted = tuple(sorted({cur, "USD", "INR"}))
    try:
        data, stale = get_live(wanted), False
    except Exception as e:  # noqa: BLE001
        data, stale = fallback_spot(), True
        if data is None:
            st.error(f"Live prices unavailable right now ({e}). Try again in a minute.")
            return
    items = data["items"]
    show_cur = cur if cur in items else "USD"
    if show_cur != cur:
        st.warning(f"{cur} is not available from the saved data, so prices are shown in USD.")
    it = items[show_cur]

    st.markdown(f"##### Spot price per {ushort} · {show_cur}")
    a, b, c = st.columns(3)
    def delta(k):
        pc = it[f"pc{k}"]
        return None if pd.isna(pc) else f"{signed(it[f'chg{k}'] * mult, show_cur)} ({pc:+.2f}%)"

    a.metric(f"Gold per {ushort}", money(it["xauPrice"] * mult, show_cur), delta("Xau"))
    b.metric(f"Silver per {ushort}", money(it["xagPrice"] * mult, show_cur), delta("Xag"))
    c.metric("Gold / silver ratio", f"{it['xauPrice'] / it['xagPrice']:.1f}",
             help="Ounces of silver needed to buy one ounce of gold.")
    now = datetime.now(IST)
    src = data.get("source", "")
    src_note = {"goldprice.org": "goldprice.org",
                "gold-api.com": "gold-api.com spot (goldprice.org blocks cloud servers) · FX by open.er-api.com · "
                                "day change from COMEX futures"}.get(src, src)
    st.caption(("⚠️ Live feed unreachable, showing " if stale else f"Source: {src_note} · ")
               + f"{data['date']} · checked {now:%d %b %Y %H:%M:%S} IST · refreshes every minute")

    rows = []
    for name, k in (("Gold", "xau"), ("Silver", "xag")):
        row = {"Metal": name}
        for u, m in UNITS.items():
            row[f"per {UNIT_SHORT[u]}"] = money(it[f"{k}Price"] * m, show_cur)
        pc = it["pc" + k.capitalize()]
        row["Change"] = "–" if pd.isna(pc) else f"{signed(it['chg' + k.capitalize()], show_cur)} ({pc:+.2f}%)"
        row["Prev close /oz"] = money(it[f"{k}Close"], show_cur)
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    # India vs global
    if "INR" in items:
        ch = chennai_latest()
        if ch:
            st.markdown("##### Chennai vs global (INR per gram)")
            inr = items["INR"]
            comp = []
            for metal, k in (("Gold", "xau"), ("Silver", "xag")):
                if metal in ch:
                    d, rate = ch[metal]
                    g = inr[f"{k}Price"] / OZ
                    comp.append({"Metal": metal + (" 24K" if metal == "Gold" else ""),
                                 "Chennai rate": money(rate, "INR"), "Chennai date": d,
                                 "Global spot": money(g, "INR", 2),
                                 "Difference": signed(rate - g, "INR", 2),
                                 "Chennai premium": f"{(rate / g - 1) * 100:+.2f}%"})
            st.dataframe(pd.DataFrame(comp), hide_index=True, width="stretch")
            st.caption("Chennai rates include import duty and the local market premium; spot is the "
                       "international wholesale price. Dates can differ by a day.")


live_panel(cur, mult, ushort)

st.divider()

# ---------------------------------------------------------------- saved history
st.subheader("Saved global rate history")
if FG.RATES_CSV.exists():
    h = pd.read_csv(FG.RATES_CSV)
    hc = cur if cur in h["Currency"].unique() else "USD"
    h = h[h["Currency"] == hc]
    st.caption(f"Spot price saved at every scheduled run (11:30 AM and 3:00 PM IST), in {hc}, "
               f"per {ushort}. Change is versus the previous saved reading.")
    piv = h.assign(Stamp=h["Date"] + " " + h["Time (IST)"]).pivot_table(
        index="Stamp", columns="Metal", values="Per oz", aggfunc="last").sort_index()
    tbl = pd.DataFrame(index=piv.index)
    for m in ("Gold", "Silver"):
        if m in piv:
            v = piv[m] * mult
            tbl[m] = v
            tbl[f"{m} change"] = v.diff()
            tbl[f"{m} change %"] = piv[m].pct_change() * 100
    tbl = tbl.sort_index(ascending=False)
    disp = pd.DataFrame(index=tbl.index)
    css = pd.DataFrame("", index=tbl.index, columns=tbl.columns)

    def tone(v):
        if pd.isna(v) or v == 0:
            return "color: #6B7280"
        return "color: #1E8E5A; font-weight: 600" if v > 0 else "color: #C0392B; font-weight: 600"

    for c in tbl.columns:
        if c.endswith("change %"):
            disp[c] = tbl[c].map(lambda v: "–" if pd.isna(v) else f"{v:+.2f}%")
            css[c] = tbl[c].map(tone)
        elif c.endswith("change"):
            disp[c] = tbl[c].map(lambda v: "–" if pd.isna(v) else
                                 (f"▲ {signed(v, hc)}" if v > 0 else f"▼ {signed(v, hc)}" if v < 0 else "● 0"))
            css[c] = tbl[c].map(tone)
        else:
            disp[c] = tbl[c].map(lambda v: money(v, hc))
    disp.index.name = "Saved at (IST)"
    st.dataframe(disp.style.apply(lambda _: css, axis=None), width="stretch")
else:
    st.info("The history starts after the next scheduled run (11:30 AM or 3:00 PM IST).")

st.divider()

# ---------------------------------------------------------------- technical
st.subheader("Technical analysis (international price)")
t1, t2, t3, t4 = st.columns([1, 1, 1.2, 1.2])
metal = t1.radio("Metal", ["Gold", "Silver"], horizontal=True, key="gt_metal")
tcur = t2.radio("Currency", ["USD", "INR"], horizontal=True, key="gt_cur")
tunit = t3.radio("Per", ["Troy ounce", "Gram"], horizontal=True, key="gt_unit")
view = t4.selectbox("Show", list(tech_tab.VIEW), index=2, key="gt_view")

g = tech_tab.load_global(tech_tab.GLOBAL_CSV.stat().st_mtime if tech_tab.GLOBAL_CSV.exists() else None)
if g is None or g.empty:
    st.info("Long price history is not available yet. It is saved by the scheduled job; check back after the next run.")
else:
    m = g[g["Metal"] == metal].set_index("Date").sort_index()
    f = (m["USDINR"] if tcur == "INR" else 1.0)
    u = 1.0 if tunit == "Troy ounce" else 1 / OZ
    ohlc = m[["Open", "High", "Low", "Close"]].mul(f, axis=0) * u
    st.caption(f"COMEX {metal.lower()} futures (Yahoo Finance), daily, {len(ohlc):,} trading days to "
               f"{ohlc.index.max():%d %b %Y}" + (", converted at each day's USD/INR rate." if tcur == "INR" else "."))
    tech_tab.render_analysis(ohlc, f"{metal} ({tcur}/{'oz' if u == 1 else 'g'})", view,
                             lambda v: money(v, tcur), key="g", sym=SYM[tcur])

st.caption("Sources: goldprice.org / gold-api.com (live spot), [Rates by Exchange Rate API](https://www.exchangerate-api.com) (currency conversion), Yahoo Finance (COMEX history). For research only, not investment advice.")
