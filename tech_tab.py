"""Technical-analysis tab for the Streamlit dashboard."""

from __future__ import annotations

from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

import technical as T

BASE = Path(__file__).resolve().parent
GLOBAL_CSV = BASE / "global_history.csv"
OZ = 31.1034768

# Fixed colours per line (colour follows the line, never its rank)
COLORS = {
    "Price": "#2B2B2B", "20-day avg": "#2F6FDE", "50-day avg": "#E08A1E", "200-day avg": "#7D4CC2",
    "Bollinger": "#8A94A6", "Channel": "#138A8A", "Flag": "#B07A12",
}
UP, DOWN = "#1E8E5A", "#C0392B"
VIEW = {"3 months": 63, "6 months": 126, "1 year": 252, "2 years": 504, "5 years": 1260, "All": None}
OVERLAYS = ["20-day avg", "50-day avg", "200-day avg", "Bollinger bands", "Trend channel",
            "Support / resistance", "Fibonacci", "Flag pattern"]


# ---------------------------------------------------------------- data
@st.cache_data(ttl=6 * 3600)
def load_global(mtime: float | None) -> pd.DataFrame | None:
    if GLOBAL_CSV.exists():
        df = pd.read_csv(GLOBAL_CSV, parse_dates=["Date"])
        return df
    try:                                     # first deploy before the workflow has saved the file
        import fetch_global
        return fetch_global.build("10y")
    except Exception:
        return None


def build_series(metal: str, series: str, chennai: pd.DataFrame, source: str):
    """Returns (ohlc DataFrame with Close [+Open/High/Low], note)."""
    ch = chennai[chennai["Series"] == series].groupby("Date")["Rate"].last().sort_index()
    if source.startswith("Chennai"):
        return pd.DataFrame({"Close": ch}), f"{len(ch)} days of collected Chennai rates."
    g = load_global(GLOBAL_CSV.stat().st_mtime if GLOBAL_CSV.exists() else None)
    if g is None or g.empty:
        return pd.DataFrame({"Close": ch}), "Long history unavailable right now, so this uses the collected Chennai rates."
    g = g[g["Metal"] == metal].set_index("Date").sort_index()
    unit = 1000.0 if series.endswith("/kg") else 1.0
    px = g[["Open", "High", "Low", "Close"]].mul(g["USDINR"], axis=0) / OZ * unit      # INR per gram
    both = pd.concat([ch.rename("ch"), px["Close"].rename("gl")], axis=1, join="inner").dropna()
    if len(both):
        factor = float((both["ch"] / both["gl"]).median())
        note = (f"International {metal.lower()} price × USD/INR, scaled to Chennai {series} using {len(both)} "
                f"overlapping days (Chennai ≈ {factor:.3f}× international, which covers import duty and local premium).")
    else:
        factor = 1.0
        note = "International price × USD/INR (not yet scaled to Chennai: no overlapping days)."
    out = px * factor
    # Append collected Chennai days that are newer than the international data
    newer = ch[ch.index > out.index.max()]
    if len(newer):
        out = pd.concat([out, pd.DataFrame({"Close": newer})])
    return out, note + f" {len(out):,} trading days."


# ---------------------------------------------------------------- charts
def _xaxis(start, end):
    days = (end - start).days
    if days <= 120:
        return alt.Axis(format="%d %b", grid=False, tickCount={"interval": "week", "step": 2})
    if days <= 400:
        return alt.Axis(format="%b %y", grid=False, tickCount={"interval": "month", "step": 1})
    if days <= 800:
        return alt.Axis(format="%b %y", grid=False, tickCount={"interval": "month", "step": 3})
    return alt.Axis(format="%Y", grid=False, tickCount={"interval": "year", "step": 1})


def _price_chart(ohlc: pd.DataFrame, ind: pd.DataFrame, show: list[str], ch, flag, sr, fib, start, title, sym="₹"):
    view = ohlc[ohlc.index >= start].reset_index(names="Date")
    layers = []
    x = alt.X("Date:T", title=None, axis=_xaxis(start, ohlc.index.max()))
    ytitle = sym
    has_ohlc = {"Open", "High", "Low"}.issubset(view.columns) and view[["Open", "High", "Low"]].notna().all(axis=1).mean() > 0.9
    if has_ohlc:
        v = view.dropna(subset=["Open", "High", "Low"]).copy()
        v["Dir"] = np.where(v["Close"] >= v["Open"], "Up", "Down")
        color = alt.Color("Dir:N", scale=alt.Scale(domain=["Up", "Down"], range=[UP, DOWN]), legend=None)
        tip = [alt.Tooltip("Date:T", format="%d %b %Y"), alt.Tooltip("Open:Q", format=",.0f"),
               alt.Tooltip("High:Q", format=",.0f"), alt.Tooltip("Low:Q", format=",.0f"),
               alt.Tooltip("Close:Q", format=",.0f")]
        layers.append(alt.Chart(v).mark_rule().encode(x=x, y=alt.Y("Low:Q", title=ytitle, scale=alt.Scale(zero=False)),
                                                      y2="High:Q", color=color, tooltip=tip))
        layers.append(alt.Chart(v).mark_bar(size=3 if len(v) < 200 else 1.5).encode(
            x=x, y="Open:Q", y2="Close:Q", color=color, tooltip=tip))
    else:
        layers.append(alt.Chart(view).mark_line(strokeWidth=2, color=COLORS["Price"]).encode(
            x=x, y=alt.Y("Close:Q", title=ytitle, scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("Date:T", format="%d %b %Y"), alt.Tooltip("Close:Q", format=",.0f")]))

    # Line overlays with a shared legend
    rows = []
    iv = ind[ind.index >= start]
    for name, col in (("20-day avg", "SMA20"), ("50-day avg", "SMA50"), ("200-day avg", "SMA200")):
        if name in show:
            rows.append(pd.DataFrame({"Date": iv.index, "Value": iv[col], "Line": name, "Part": name}))
    if "Bollinger bands" in show:
        for part, col in (("upper", "BBUpper"), ("lower", "BBLower")):
            rows.append(pd.DataFrame({"Date": iv.index, "Value": iv[col], "Line": "Bollinger", "Part": f"bb-{part}"}))
    if "Trend channel" in show and ch is not None:
        c = ch[ch.index >= start]
        for part in ("Upper", "Mid", "Lower"):
            rows.append(pd.DataFrame({"Date": c.index, "Value": c[part], "Line": "Channel", "Part": f"ch-{part}"}))
    if "Flag pattern" in show and flag:
        L = flag["lines"]
        rows.append(pd.DataFrame({"Date": [L["x0"], L["x1"]], "Value": [L["up0"], L["up1"]], "Line": "Flag", "Part": "flag-up"}))
        rows.append(pd.DataFrame({"Date": [L["x0"], L["x1"]], "Value": [L["lo0"], L["lo1"]], "Line": "Flag", "Part": "flag-lo"}))
        rows.append(pd.DataFrame({"Date": [flag["pole_start"], flag["pole_end"]],
                                  "Value": [ohlc["Close"].loc[flag["pole_start"]], ohlc["Close"].loc[flag["pole_end"]]],
                                  "Line": "Flag", "Part": "flag-pole"}))
    if rows:
        lines = pd.concat(rows).dropna()
        dom = [k for k in COLORS if k in lines["Line"].unique()]
        layers.append(alt.Chart(lines).mark_line(strokeWidth=1.6).encode(
            x="Date:T", y="Value:Q", detail="Part:N",
            color=alt.Color("Line:N", scale=alt.Scale(domain=dom, range=[COLORS[d] for d in dom]),
                            legend=alt.Legend(title=None, orient="top")),
            strokeDash=alt.condition(alt.FieldOneOfPredicate(field="Line", oneOf=["Bollinger", "Channel"]),
                                     alt.value([5, 4]), alt.value([1, 0])),
            tooltip=["Line:N", alt.Tooltip("Date:T", format="%d %b %Y"), alt.Tooltip("Value:Q", format=",.0f")]))

    # Horizontal levels with labels
    hl = []
    if "Support / resistance" in show:
        hl += [{"y": v, "label": f"Support {v:,.0f}", "kind": "Support"} for v, _ in sr[0]]
        hl += [{"y": v, "label": f"Resistance {v:,.0f}", "kind": "Resistance"} for v, _ in sr[1]]
    if "Fibonacci" in show and fib:
        hl += [{"y": v, "label": f"Fib {k} {v:,.0f}", "kind": "Fibonacci"} for k, v in fib["levels"].items()]
    if hl:
        h = pd.DataFrame(hl)
        kc = alt.Color("kind:N", scale=alt.Scale(domain=["Support", "Resistance", "Fibonacci"],
                                                 range=[UP, DOWN, "#8A94A6"]), legend=None)
        layers.append(alt.Chart(h).mark_rule(strokeDash=[2, 3], strokeWidth=1).encode(y="y:Q", color=kc))
        layers.append(alt.Chart(h).mark_text(align="left", dx=4, dy=-5, fontSize=10).encode(
            y="y:Q", x=alt.value(2), text="label:N", color=kc))
    return alt.layer(*layers).properties(height=420, title=title).resolve_scale(color="independent")


def _rsi_chart(ind, start):
    d = ind[ind.index >= start].reset_index(names="Date")[["Date", "RSI"]].dropna()
    band = alt.Chart(pd.DataFrame({"lo": [30], "hi": [70]})).mark_rect(opacity=0.08, color="#8A94A6").encode(y="lo:Q", y2="hi:Q")
    rules = alt.Chart(pd.DataFrame({"y": [30, 70]})).mark_rule(strokeDash=[3, 3], color="#8A94A6").encode(y="y:Q")
    line = alt.Chart(d).mark_line(strokeWidth=1.6, color="#7D4CC2").encode(
        x=alt.X("Date:T", title=None, axis=_xaxis(start, ind.index.max())),
        y=alt.Y("RSI:Q", scale=alt.Scale(domain=[0, 100]), title="RSI (14)"),
        tooltip=[alt.Tooltip("Date:T", format="%d %b %Y"), alt.Tooltip("RSI:Q", format=".0f")])
    return (band + rules + line).properties(height=150)


def _macd_chart(ind, start):
    d = ind[ind.index >= start].reset_index(names="Date")[["Date", "MACD", "MACDSignal", "MACDHist"]].dropna()
    x = alt.X("Date:T", title=None, axis=_xaxis(start, ind.index.max()))
    bars = alt.Chart(d).mark_bar(opacity=0.55).encode(
        x=x, y=alt.Y("MACDHist:Q", title="MACD"),
        color=alt.condition("datum.MACDHist >= 0", alt.value(UP), alt.value(DOWN)),
        tooltip=[alt.Tooltip("Date:T", format="%d %b %Y"), alt.Tooltip("MACDHist:Q", format=",.1f", title="Histogram")])
    long = d.melt(id_vars="Date", value_vars=["MACD", "MACDSignal"], var_name="Line", value_name="Value")
    long["Line"] = long["Line"].replace({"MACDSignal": "Signal"})
    lines = alt.Chart(long).mark_line(strokeWidth=1.5).encode(
        x="Date:T", y="Value:Q",
        color=alt.Color("Line:N", scale=alt.Scale(domain=["MACD", "Signal"], range=["#2F6FDE", "#E08A1E"]),
                        legend=alt.Legend(title=None, orient="top")),
        tooltip=["Line:N", alt.Tooltip("Value:Q", format=",.1f")])
    return (bars + lines).properties(height=150)


# ---------------------------------------------------------------- render
def render(metal: str, chennai: pd.DataFrame, series_options: list[str], inr):
    st.caption("Chart-pattern and indicator analysis for your own research. Patterns describe what price has done "
               "and what traders commonly watch next. They are not guarantees, and nothing here is a buy or sell "
               "recommendation.")
    c1, c2, c3 = st.columns([1.2, 2, 1.4])
    series = c1.selectbox("Rate", series_options, key="t_series")
    has_global = GLOBAL_CSV.exists()
    sources = ["Long history (international, scaled to Chennai)", "Chennai collected only"]
    source = c2.radio("Data", sources, index=0, horizontal=True, key="t_source",
                      help="Chennai history started recently. The long history uses COMEX futures × USD/INR, "
                           "scaled to match Chennai prices, so patterns and averages have enough data.")
    view = c3.selectbox("Show", list(VIEW), index=2, key="t_view")

    ohlc, note = build_series(metal, series, chennai, source)
    st.caption(note + ("" if has_global or source.startswith("Chennai") else " (fetched live)"))
    label = series if series.startswith(metal) else f"{metal} {series}"
    render_analysis(ohlc, label, view, inr, key="t")


def render_analysis(ohlc: pd.DataFrame, label: str, view: str, inr, key: str, sym: str = "₹"):
    """Outlook, charts and pattern cards for any OHLC/Close series. `inr` formats a price."""
    s = ohlc["Close"].dropna()
    if len(s) < 30:
        st.info("Technical analysis needs at least 30 days of prices. Switch Data to the long history, "
                "or check back once more days are collected.")
        return

    ind = T.indicators(s)
    ch, ci = T.regression_channel(s, n=min(60, len(s)))
    flag = T.find_flag(s)
    sr = T.support_resistance(s)
    fib = T.fibonacci(s, lookback=min(120, len(s)))
    bsig, bcount, bverdict = T.bottom_signals(ind, ci)
    bias, score, why = T.outlook(ind, ci, flag, bcount)
    ranges = T.expected_range(s)
    last, last_d = float(s.iloc[-1]), s.index[-1]

    # ------------------------------------------------ outlook
    st.subheader(f"Outlook: {bias}")
    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Latest", inr(last), help=f"As of {last_d:%d %b %Y}")
    o2.metric("Bias score", f"{score:+d}", help="Sum of the factors listed below. ≥ +4 bullish, ≤ −4 bearish.")
    o3.metric("Nearest support", inr(sr[0][0][0]) if sr[0] else "–")
    o4.metric("Nearest resistance", inr(sr[1][0][0]) if sr[1] else "–")
    st.markdown("**Why:** " + " · ".join(why) if why else "Not enough data for a bias yet.")

    rg = pd.DataFrame([{
        "Horizon": "Next week" if r["days"] == 5 else "Next month",
        "Likely range (≈68%)": f"{inr(r['low68'])} – {inr(r['high68'])}",
        "Wide range (≈95%)": f"{inr(r['low95'])} – {inr(r['high95'])}",
        "Typical move": f"±{r['sd_pct']:.1f}%",
    } for r in ranges])
    st.markdown("##### Where could it be? (statistical range from recent volatility)")
    st.dataframe(rg, hide_index=True, width="stretch")

    # ------------------------------------------------ main chart
    show = st.multiselect("Chart layers", OVERLAYS, default=["50-day avg", "200-day avg", "Trend channel",
                                                             "Support / resistance", "Flag pattern"], key=f"{key}_layers")
    start = s.index[-VIEW[view]] if VIEW[view] and len(s) > VIEW[view] else s.index[0]
    st.altair_chart(_price_chart(ohlc, ind, show, ch, flag, sr, fib, start,
                                 f"{label} · {view}", sym=sym),
                    width="stretch")
    st.altair_chart(_rsi_chart(ind, start), width="stretch")
    st.altair_chart(_macd_chart(ind, start), width="stretch")

    # ------------------------------------------------ pattern cards
    a, b = st.columns(2)
    with a:
        st.markdown("##### Flag pattern")
        if flag:
            st.markdown(
                f"**{flag['type']}**, status **{flag['status']}**\n\n"
                f"- Pole: {flag['pole_move_pct']:+.1f}% in {flag['pole_days']} days "
                f"({flag['pole_start']:%d %b} → {flag['pole_end']:%d %b})\n"
                f"- Flag: {flag['flag_days']} days, gave back {flag['retrace_pct']:.0f}% of the pole\n"
                f"- Breakout level: **{inr(flag['trigger'])}** · Pattern fails beyond: {inr(flag['invalidation'])}\n"
                f"- Measured-move target: **{inr(flag['target'])}** ({(flag['target'] / last - 1) * 100:+.1f}%)")
            st.caption("Flags are only confirmed by a close beyond the breakout level. Flag-like shapes also "
                       "appear in random price moves, so the breakout matters more than the shape.")
        else:
            st.write("No flag pattern right now. A flag needs a sharp move (the pole) followed by a tight, "
                     "flat or slightly counter-trend pause.")
    with b:
        st.markdown("##### Price channel")
        if ci:
            st.markdown(
                f"**{ci['direction']}** channel over the last {ci['days']} days "
                f"({ci['slope_pct_per_month']:+.1f}% per month)\n\n"
                f"- Status: **{ci['status']}**\n"
                f"- Position: {ci['position'] * 100:.0f}% of the way from bottom line to top line\n"
                f"- Top line {inr(ci['upper'])} · Middle {inr(ci['mid'])} · Bottom line {inr(ci['lower'])}\n"
                f"- Channel width: {ci['width_pct']:.1f}%")
            st.caption("Near the bottom line of a rising channel is where trend-followers usually look for support; "
                       "a close outside the channel signals the trend may be changing.")

    st.markdown(f"##### Bottom watch: {bverdict} ({bcount} of {len(bsig)} signals)")
    st.dataframe(pd.DataFrame([{"Signal": x["name"], "Active": "✅ Yes" if x["active"] else "— No",
                                "Detail": x["detail"]} for x in bsig]), hide_index=True, width="stretch")

    c, d = st.columns(2)
    with c:
        st.markdown("##### Indicators")
        st.dataframe(pd.DataFrame(T.trend_summary(ind), columns=["Indicator", "Reading", "Meaning"]),
                     hide_index=True, width="stretch")
    with d:
        st.markdown(f"##### Fibonacci levels ({fib['direction']})")
        st.caption(f"Swing high {inr(fib['high'][1])} on {fib['high'][0]:%d %b %Y}, "
                   f"swing low {inr(fib['low'][1])} on {fib['low'][0]:%d %b %Y}")
        st.dataframe(pd.DataFrame([{"Level": k, "Price": inr(v),
                                    "vs latest": f"{(v / last - 1) * 100:+.1f}%"} for k, v in fib["levels"].items()]),
                     hide_index=True, width="stretch")
        lv = pd.DataFrame([{"Type": "Support", "Price": inr(v), "Touches": n} for v, n in sr[0]]
                          + [{"Type": "Resistance", "Price": inr(v), "Touches": n} for v, n in sr[1]])
        if len(lv):
            st.markdown("##### Support & resistance")
            st.dataframe(lv, hide_index=True, width="stretch")
