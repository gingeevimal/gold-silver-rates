"""
Chennai (India) screen of the Gold & Silver Rates dashboard.

Reads gold_rates.xlsx / silver_rates.xlsx (kept up to date by the GitHub Actions
workflow) and shows today's rates, the trend, and the daily table.
Run locally:  streamlit run app.py
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

import tech_tab

BASE = Path(__file__).resolve().parent.parent
IST = ZoneInfo("Asia/Kolkata")
FILES = {"Gold": BASE / "gold_rates.xlsx", "Silver": BASE / "silver_rates.xlsx"}
SERIES = {
    "Gold": ["24K /g", "22K /g", "18K /g"],
    "Silver": ["Silver /g", "Silver /kg"],
}
LINE_COLOR = {"Gold": "#B07A12", "Silver": "#5B6B7F"}
RANGES = {"7 days": 7, "1 month": 30, "3 months": 90, "1 year": 365, "All": None}



# ---------------------------------------------------------------- data
@st.cache_data(ttl=300)
def load_summary(metal: str, mtime: float) -> pd.DataFrame:
    """Daily Summary sheet -> long table: Date, City, Series, Rate, Change, High, Low."""
    path = FILES[metal]
    df = pd.read_excel(path, sheet_name="Daily Summary")
    df["Date"] = pd.to_datetime(df["Date"])
    rows = []
    for s in SERIES[metal]:
        if f"{s} Rate" not in df.columns:
            continue
        part = pd.DataFrame({
            "Date": df["Date"],
            "City": df.get("City", ""),
            "Series": s,
            "Rate": df[f"{s} Rate"],
            "Change": df.get(f"{s} Change vs Prev Day"),
            "High": df.get(f"{s} Day High"),
            "Low": df.get(f"{s} Day Low"),
            "Last Updated": df.get("Last Updated"),
        })
        rows.append(part)
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows).dropna(subset=["Rate"])
    return out.sort_values("Date")


def inr(v) -> str:
    """Indian digit grouping: 245000 -> ₹2,45,000."""
    if v is None or pd.isna(v):
        return "–"
    v = round(float(v))
    s = str(abs(int(v)))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
        s = f"{head},{tail}"
    return f"{'-' if v < 0 else ''}₹{s}"


def signed(v) -> str | None:
    if v is None or pd.isna(v):
        return None
    v = round(float(v))
    return f"{'+' if v > 0 else '-' if v < 0 else ''}{inr(abs(v))[1:]}"


# ---------------------------------------------------------------- UI
st.title("Chennai (India) Gold & Silver Rates")

available = {m: p for m, p in FILES.items() if p.exists()}
if not available:
    st.warning("No data yet. Run the agent once (or the GitHub Actions workflow) to create the Excel files.")
    st.stop()

metal = st.radio("Metal", list(available), horizontal=True, label_visibility="collapsed")
data = load_summary(metal, FILES[metal].stat().st_mtime)
if data.empty:
    st.warning(f"{FILES[metal].name} has no Daily Summary rows yet.")
    st.stop()

cities = sorted(c for c in data["City"].dropna().unique() if c)
if len(cities) > 1:
    city = st.selectbox("City", cities)
    data = data[data["City"] == city]
else:
    city = cities[0] if cities else ""

latest_date = data["Date"].max()
latest = data[data["Date"] == latest_date].set_index("Series")
last_upd = latest["Last Updated"].dropna().max() if "Last Updated" in latest else None
st.caption(f"{city} · {latest_date:%d %b %Y}" + (f" · last updated {last_upd}" if last_upd else ""))

# Today's rates (stat tiles)
cols = st.columns(len(SERIES[metal]))
for col, s in zip(cols, SERIES[metal]):
    if s in latest.index:
        row = latest.loc[s]
        col.metric(s.replace(" /g", " per gram").replace(" /kg", " per kg"),
                   inr(row["Rate"]), signed(row["Change"]),
                   help="Change is vs the previous day, as published by the source site.")

st.divider()

tab_trend, tab_research, tab_tech = st.tabs(["Trend", "Research", "Technical"])

with tab_trend:
    # Trend chart: one series at a time on one axis
    c1, c2 = st.columns([1, 2])
    present = [s for s in SERIES[metal] if s in data["Series"].unique()]
    series = c1.selectbox("Rate", present)
    rng = c2.radio("Range", list(RANGES), index=1, horizontal=True)

    view = data[data["Series"] == series]
    if RANGES[rng]:
        view = view[view["Date"] >= latest_date - timedelta(days=RANGES[rng])]

    if len(view) < 2:
        st.info("The trend chart appears once there are at least two days of data.")
    else:
        base = alt.Chart(view).encode(
            x=alt.X("Date:T", title=None, axis=alt.Axis(format="%d %b", grid=False)),
            y=alt.Y("Rate:Q", title="₹", scale=alt.Scale(zero=False),
                    axis=alt.Axis(gridOpacity=0.35, format=",.0f")),
            tooltip=[alt.Tooltip("Date:T", format="%d %b %Y"),
                     alt.Tooltip("Rate:Q", format=",.0f", title="Rate ₹"),
                     alt.Tooltip("Change:Q", format="+,.0f", title="Change vs prev day"),
                     alt.Tooltip("High:Q", format=",.0f", title="Day high"),
                     alt.Tooltip("Low:Q", format=",.0f", title="Day low")],
        )
        hover = alt.selection_point(on="pointerover", nearest=True, empty=False, fields=["Date"])
        line = base.mark_line(strokeWidth=2, color=LINE_COLOR[metal])
        pts = base.mark_circle(size=70, color=LINE_COLOR[metal]).encode(
            opacity=alt.condition(hover, alt.value(1), alt.value(0))
        ).add_params(hover)
        st.altair_chart((line + pts).properties(height=340, title=f"{series if series.startswith(metal) else metal + ' ' + series} · {rng}"),
                        width="stretch")

    # Daily table
    with st.expander("Daily table", expanded=True):
        rates = data.pivot_table(index="Date", columns="Series", values="Rate", aggfunc="last").sort_index()
        site_chg = data.pivot_table(index="Date", columns="Series", values="Change", aggfunc="last").sort_index()
        cols = [s for s in SERIES[metal] if s in rates.columns]
        tbl = pd.DataFrame(index=rates.index)
        for c in cols:
            chg = rates[c].diff()                                   # vs previous day in the table
            if c in site_chg:                                       # first day: use the site's own change
                chg = chg.fillna(site_chg[c])
            tbl[c] = rates[c]
            tbl[f"{c} change"] = chg
            tbl[f"{c} change %"] = chg / (rates[c] - chg) * 100
        tbl = tbl.sort_index(ascending=False)
        tbl.index = tbl.index.strftime("%d %b %Y")
        tbl.index.name = "Date"

        def arrow(v):
            if v is None or pd.isna(v):
                return "–"
            return f"▲ +{inr(v)[1:]}" if v > 0 else f"▼ -{inr(abs(v))[1:]}" if v < 0 else "● 0"

        def pct_fmt(v):
            return "–" if v is None or pd.isna(v) else f"{v:+.2f}%"

        def tone(v):
            if v is None or pd.isna(v) or v == 0:
                return "color: #6B7280"
            return "color: #1E8E5A; font-weight: 600" if v > 0 else "color: #C0392B; font-weight: 600"

        chg_cols = [c for c in tbl.columns if c.endswith("change")]
        pct_cols = [c for c in tbl.columns if c.endswith("change %")]
        disp = pd.DataFrame(index=tbl.index)
        for c in tbl.columns:
            f = arrow if c in chg_cols else pct_fmt if c in pct_cols else (lambda v: "–" if pd.isna(v) else inr(v))
            disp[c] = tbl[c].map(f)
        css = pd.DataFrame("", index=tbl.index, columns=tbl.columns)
        for c in chg_cols + pct_cols:
            css[c] = tbl[c].map(tone)
        styled = disp.style.apply(lambda _: css, axis=None)
        st.dataframe(styled, width="stretch")
        st.caption("Change = increase (▲ green) or decrease (▼ red) versus the previous day in the table.")
        st.download_button(f"Download {FILES[metal].name}", FILES[metal].read_bytes(),
                           file_name=FILES[metal].name,
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # Live check straight from the website (does not change the Excel files)
    with st.expander("Check live rate now"):
        st.caption("Fetches the source website right now. The saved history only changes on scheduled runs.")
        if st.button("Fetch live"):
            try:
                import json
                import metal_rates_agent as agent
                cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
                srcs = [s for s in cfg["sources"] if s.get("metal", "").lower() == metal.lower()]
                for src in srcs:
                    rates = agent.parse_rules(agent.fetch_text(src["url"]), metal)
                    st.write(f"**{src.get('city', '')}**: " + ", ".join(
                        f"{agent._label(r.metal, r.purity, r.unit)} {inr(r.rate)}"
                        + (f" ({signed(r.change)})" if r.change is not None else "") for r in rates)
                        or "no rates found on the page")
                st.caption(f"Fetched {datetime.now(IST):%d %b %Y %H:%M} IST")
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not fetch live rates: {e}")


# ---------------------------------------------------------------- research
def pct(a, b):
    return (a / b - 1) * 100 if b else None


def streak(changes: pd.Series) -> tuple[int, int]:
    """(length, sign) of the run of same-direction daily moves ending today."""
    n, sign = 0, 0
    for v in reversed(changes.tolist()):
        sg = (v > 0) - (v < 0)
        if sg == 0:
            break
        if sign == 0:
            sign = sg
        if sg != sign:
            break
        n += 1
    return n, sign


with tab_research:
    st.caption("Built only from the daily rates saved in the Excel files, so it gets richer as more days "
               "are collected. This is information for your own research, not investment advice.")
    present_r = [s for s in SERIES[metal] if s in data["Series"].unique()]
    rc1, rc2 = st.columns([1, 2])
    rs = rc1.selectbox("Rate to analyse", present_r, key="r_series")
    rrng = rc2.radio("Period", list(RANGES), index=4, horizontal=True, key="r_range")

    full = data[data["Series"] == rs].groupby("Date")["Rate"].last().sort_index()
    ser = full if not RANGES[rrng] else full[full.index >= full.index.max() - timedelta(days=RANGES[rrng])]

    if len(ser) < 2:
        st.info("Research needs at least two days of data for this rate. Check back after a few runs.")
    else:
        cur, last_d = ser.iloc[-1], ser.index[-1]
        hi_d, lo_d = ser.idxmax(), ser.idxmin()
        hi, lo, avg = ser.max(), ser.min(), ser.mean()
        daily = ser.pct_change().dropna() * 100
        vol = daily.std() if len(daily) > 1 else float("nan")
        up, down = int((daily > 0).sum()), int((daily < 0).sum())
        flat = len(daily) - up - down
        days_cov = (ser.index[-1] - ser.index[0]).days

        st.subheader(f"{rs if rs.startswith(metal) else metal + ' ' + rs} · {rrng.lower()} ({len(ser)} days of data)")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Change over period", signed(cur - ser.iloc[0]), f"{pct(cur, ser.iloc[0]):+.2f}%")
        k2.metric("Average", inr(avg), f"now {pct(cur, avg):+.2f}% vs avg", delta_color="off")
        k3.metric("Highest", inr(hi), f"{hi_d:%d %b %Y}", delta_color="off")
        k4.metric("Lowest", inr(lo), f"{lo_d:%d %b %Y}", delta_color="off")

        # Plain-language findings
        pos = (cur - lo) / (hi - lo) * 100 if hi != lo else 50
        n_st, sg = streak(daily)
        notes = [
            f"Today's rate {inr(cur)} is **{abs(pct(cur, avg)):.2f}% {'above' if cur >= avg else 'below'}** "
            f"the period average of {inr(avg)}.",
            f"It sits at **{pos:.0f}%** of the period's range "
            f"(low {inr(lo)} on {lo_d:%d %b}, high {inr(hi)} on {hi_d:%d %b}).",
            f"Day-to-day moves: **{up} up**, **{down} down**, {flat} unchanged.",
        ]
        if vol == vol:  # not NaN
            notes.append(f"On a typical day the rate moves about **±{vol:.2f}%** "
                         f"(≈ {inr(cur * vol / 100)}).")
        if n_st >= 2:
            notes.append(f"It has **{'risen' if sg > 0 else 'fallen'} {n_st} days in a row**.")
        if len(daily):
            br, bf = daily.idxmax(), daily.idxmin()
            if daily[br] > 0:
                notes.append(f"Biggest one-day rise: **{daily[br]:+.2f}%** on {br:%d %b %Y}.")
            if daily[bf] < 0:
                notes.append(f"Biggest one-day fall: **{daily[bf]:+.2f}%** on {bf:%d %b %Y}.")
        st.markdown("\n".join(f"- {n}" for n in notes))

        # Returns over standard periods (always from full history)
        st.markdown("##### How much has it moved?")
        rows = []
        last_full = full.index[-1]
        for name, days in {"1 week": 7, "1 month": 30, "3 months": 90, "6 months": 182, "1 year": 365}.items():
            past = full[full.index <= last_full - timedelta(days=days)]
            if past.empty:
                rows.append([name, "–", "–", "not enough data yet"])
            else:
                b = past.iloc[-1]
                rows.append([name, inr(b), signed(full.iloc[-1] - b), f"{pct(full.iloc[-1], b):+.2f}%"])
        ystart = full[full.index < pd.Timestamp(last_full.year, 1, 1)]
        ybase = ystart.iloc[-1] if not ystart.empty else full.iloc[0]
        rows.append(["This year (YTD)", inr(ybase), signed(full.iloc[-1] - ybase), f"{pct(full.iloc[-1], ybase):+.2f}%"])
        rows.append([f"Since {full.index[0]:%d %b %Y}", inr(full.iloc[0]), signed(full.iloc[-1] - full.iloc[0]),
                     f"{pct(full.iloc[-1], full.iloc[0]):+.2f}%"])
        st.dataframe(pd.DataFrame(rows, columns=["Period", "Rate then", "Change ₹", "Change %"]),
                     hide_index=True, width="stretch")

        # Monthly summary
        st.markdown("##### Month by month")
        mon = full.resample("MS").agg(["first", "last", "max", "min", "mean", "count"])
        mon = mon[mon["count"] > 0].sort_index(ascending=False)
        mtbl = pd.DataFrame({
            "Month": mon.index.strftime("%b %Y"),
            "Open": mon["first"].map(inr), "Close": mon["last"].map(inr),
            "High": mon["max"].map(inr), "Low": mon["min"].map(inr), "Average": mon["mean"].map(inr),
            "Change %": [f"{pct(c, o):+.2f}%" for c, o in zip(mon["last"], mon["first"])],
            "Days": mon["count"].astype(int),
        })
        st.dataframe(mtbl, hide_index=True, width="stretch")

        # Weekday pattern (needs a few weeks)
        if len(daily) >= 14:
            st.markdown("##### Does the day of the week matter?")
            wd = daily.groupby(daily.index.day_name()).agg(["mean", "count"])
            order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            wd = wd.reindex([d for d in order if d in wd.index])
            st.dataframe(pd.DataFrame({"Day": wd.index, "Average daily change %": wd["mean"].map(lambda v: f"{v:+.3f}%"),
                                       "Days": wd["count"].astype(int)}), hide_index=True, width="stretch")
            st.caption("Small differences here are usually noise; treat them as a hint, not a rule.")

    # Gold-to-silver ratio (needs both files)
    if all(p.exists() for p in FILES.values()):
        g = load_summary("Gold", FILES["Gold"].stat().st_mtime)
        sv = load_summary("Silver", FILES["Silver"].stat().st_mtime)
        g = g[g["Series"] == "24K /g"].groupby("Date")["Rate"].last()
        sv = sv[sv["Series"] == "Silver /g"].groupby("Date")["Rate"].last()
        ratio = (g / sv).dropna()
        if len(ratio) >= 2:
            st.markdown("##### Gold-to-silver ratio")
            st.caption("Grams of silver needed to buy 1 gram of 24K gold. A rising ratio means gold is getting "
                       "dearer relative to silver; a falling ratio means silver is catching up.")
            rdf = ratio.rename("Ratio").reset_index()
            st.metric("Ratio today", f"{ratio.iloc[-1]:.1f}", f"{ratio.iloc[-1] - ratio.mean():+.1f} vs average {ratio.mean():.1f}",
                      delta_color="off")
            rch = alt.Chart(rdf).mark_line(strokeWidth=2, color="#3D6E8F").encode(
                x=alt.X("Date:T", title=None, axis=alt.Axis(format="%d %b", grid=False)),
                y=alt.Y("Ratio:Q", title="grams of silver per gram of gold", scale=alt.Scale(zero=False),
                        axis=alt.Axis(gridOpacity=0.35)),
                tooltip=[alt.Tooltip("Date:T", format="%d %b %Y"), alt.Tooltip("Ratio:Q", format=".2f")],
            ).properties(height=260)
            st.altair_chart(rch, width="stretch")

with tab_tech:
    tech_tab.render(metal, data, [s for s in SERIES[metal] if s in data["Series"].unique()], inr)

st.caption("Source: goodreturns.in (Chennai) · long history: COMEX futures × USD/INR via Yahoo Finance · rates are indicative and exclude GST, TCS and making charges · not investment advice.")
