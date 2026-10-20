"""
Metal Rates Agent
-----------------
Fetches gold & silver rates from the source URLs in config.json and
appends them to local Excel workbooks (gold_rates.xlsx, silver_rates.xlsx):
every run is kept in 'History', and 'Daily Summary' shows one row per day (one row per date/city/metal/unit).

Extraction strategy:
  1. Rule-based: regex over the page's visible text for card patterns like
     "24K Gold /g ₹15,028 + 104" or "Silver /kg ₹2,45,000 + 5,000"
     (works for goodreturns.in and similar sites).
  2. LLM fallback (optional): if nothing matches and ANTHROPIC_API_KEY is set,
     Claude reads the page text and returns the rates as JSON. This lets you
     point the agent at any other rate website without writing a parser.

Usage:
  python metal_rates_agent.py                 # run all sources in config.json
  python metal_rates_agent.py --url <URL> --metal Gold --city Chennai
  python metal_rates_agent.py --dry-run       # print, don't write Excel
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

BASE_DIR = Path(__file__).resolve().parent
LOG = logging.getLogger("metal_rates_agent")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

COLUMNS = [
    "Date", "Fetched At", "City", "Metal", "Purity", "Unit",
    "Rate (INR)", "Change (INR)", "Method", "Source URL",
]


@dataclass
class Rate:
    metal: str          # Gold / Silver
    purity: str         # 24K / 22K / 18K / 999 (silver: "-")
    unit: str           # g / kg
    rate: float
    change: float | None


# ---------------------------------------------------------------- fetching
def fetch_text(url: str, retries: int = 3) -> str:
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text(" ", strip=True)
            return re.sub(r"\s+", " ", text)
        except requests.RequestException as e:
            last_err = e
            LOG.warning("Fetch attempt %d failed for %s: %s", attempt, url, e)
            time.sleep(2 * attempt)
    raise RuntimeError(f"Could not fetch {url}: {last_err}")


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.replace(",", "").replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None


# ------------------------------------------------------- rule-based parser
# "24K Gold /g ₹15,028 + 104"   |   "Silver /kg ₹2,45,000 + 5,000"
_CHANGE = r"(?:\s*([+\-−]\s?[\d,]+(?:\.\d+)?|0))?"
GOLD_RE = re.compile(
    r"(24|22|18|14)\s*K\s*Gold\s*/\s*(g|gm|gram|kg)\s*₹\s*([\d,]+(?:\.\d+)?)" + _CHANGE,
    re.IGNORECASE,
)
SILVER_RE = re.compile(
    r"Silver\s*/\s*(g|gm|gram|kg)\s*₹\s*([\d,]+(?:\.\d+)?)" + _CHANGE,
    re.IGNORECASE,
)


def _norm_unit(u: str) -> str:
    return "kg" if u.lower() == "kg" else "g"


def _norm_change(c: str | None) -> float | None:
    if c is None:
        return None
    return _num(c.replace("−", "-"))


def parse_rules(text: str, metal: str) -> list[Rate]:
    found: dict[tuple, Rate] = {}
    if metal.lower() in ("gold", "any"):
        for k, u, r, c in GOLD_RE.findall(text):
            key = ("Gold", f"{k}K", _norm_unit(u))
            found.setdefault(key, Rate("Gold", f"{k}K", _norm_unit(u), _num(r), _norm_change(c)))
    if metal.lower() in ("silver", "any"):
        for u, r, c in SILVER_RE.findall(text):
            key = ("Silver", "-", _norm_unit(u))
            found.setdefault(key, Rate("Silver", "-", _norm_unit(u), _num(r), _norm_change(c)))
    return [r for r in found.values() if r.rate]


# ----------------------------------------------- last-10-days table (backfill)
_DATE = r"([A-Z][a-z]{2} \d{1,2}, \d{4})"
_RS = r"\s*₹\s*([\d,]+(?:\.\d+)?)"
_PAREN = r"\s*\(\s*([+\-−]?\s?[\d,]+(?:\.\d+)?)\s*\)"
GOLD_HIST_RE = re.compile(_DATE + _RS + _PAREN + _RS + _PAREN)                 # date, 24K (chg), 22K (chg)
SILVER_HIST_RE = re.compile(_DATE + _RS + _RS + _RS + _PAREN)                  # date, 10g, 100g, 1kg (chg)


def parse_history(text: str, metal: str) -> list[tuple[str, Rate]]:
    """Rows from the page's 'last 10 days' table -> [(YYYY-MM-DD, Rate), ...]."""
    out: list[tuple[str, Rate]] = []

    def iso(d: str) -> str:
        return datetime.strptime(d, "%b %d, %Y").strftime("%Y-%m-%d")

    if metal.lower() in ("gold", "any"):
        for d, r24, c24, r22, c22 in GOLD_HIST_RE.findall(text):
            out.append((iso(d), Rate("Gold", "24K", "g", _num(r24), _norm_change(c24))))
            out.append((iso(d), Rate("Gold", "22K", "g", _num(r22), _norm_change(c22))))
    if metal.lower() in ("silver", "any"):
        for d, r10, _r100, rkg, ckg in SILVER_HIST_RE.findall(text):
            chg = _norm_change(ckg)
            out.append((iso(d), Rate("Silver", "-", "kg", _num(rkg), chg)))
            out.append((iso(d), Rate("Silver", "-", "g", round(_num(r10) / 10, 2),
                                     None if chg is None else round(chg / 1000, 2))))
    return [(d, r) for d, r in out if r.rate]


# ------------------------------------------------------------ LLM fallback
LLM_PROMPT = """Extract TODAY's {metal} rates in INR from this web page text.
Return ONLY a JSON array, no prose. Each item:
{{"metal": "Gold"|"Silver", "purity": "24K"|"22K"|"18K"|"-", "unit": "g"|"kg",
  "rate": number, "change": number|null}}
Use per-gram rates for gold; for silver include per-gram and per-kg if shown.
If no rates are present return [].

PAGE TEXT:
{text}"""


def parse_llm(text: str, metal: str, model: str) -> list[Rate]:
    if not os.getenv("ANTHROPIC_API_KEY"):
        LOG.info("LLM fallback skipped (ANTHROPIC_API_KEY not set)")
        return []
    import anthropic  # optional dependency

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": LLM_PROMPT.format(metal=metal, text=text[:30000])}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    out = []
    for item in json.loads(m.group(0)):
        try:
            out.append(Rate(item["metal"], item.get("purity", "-"), item["unit"],
                            float(item["rate"]),
                            None if item.get("change") is None else float(item["change"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ------------------------------------------------------------ excel output
HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="1F4E78")


def _style_header(ws, widths):
    for i, w in enumerate(widths, 1):
        c = ws.cell(1, i)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def open_workbook(path: Path):
    """Open (or create) the workbook and return its History sheet."""
    if path.exists():
        wb = load_workbook(path)
        if "History" not in wb.sheetnames and "Rates" in wb.sheetnames:
            wb["Rates"].title = "History"          # migrate files from the older version
        ws = wb["History"] if "History" in wb.sheetnames else wb.create_sheet("History", 0)
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "History"
    if ws.max_row == 1 and ws.cell(1, 1).value is None:
        for i, name in enumerate(COLUMNS, 1):
            ws.cell(1, i, name)
        _style_header(ws, [12, 20, 12, 8, 8, 6, 14, 13, 8, 55])
    return wb, ws


def append_history(ws, row: list):
    """Every run is kept: always add a new row, never overwrite."""
    ws.append(row)
    r = ws.max_row
    ws.cell(r, 7).number_format = "₹#,##0.00"
    ws.cell(r, 8).number_format = "+#,##0;-#,##0;0"


def _label(metal: str, purity: str, unit: str) -> str:
    return f"{purity} /{unit}" if purity and purity != "-" else f"{metal} /{unit}"


def rebuild_summary(wb, hist):
    """Daily Summary: one row per Date + City with the day's latest rate per purity/unit,
    the day's change vs previous day (from the site), and the day's high/low across runs."""
    days: dict[tuple, dict] = {}
    labels: list[str] = []
    for date, fetched, city, metal, purity, unit, rate, change, *_ in hist.iter_rows(min_row=2, values_only=True):
        if not date or rate is None:
            continue
        lab = _label(metal, purity, unit)
        if lab not in labels:
            labels.append(lab)
        d = days.setdefault((str(date), city or ""), {"runs": set(), "last": "", "vals": {}})
        d["runs"].add(fetched)
        d["last"] = max(d["last"], str(fetched))
        v = d["vals"].setdefault(lab, {"rate": rate, "change": change, "at": str(fetched), "hi": rate, "lo": rate})
        v["hi"], v["lo"] = max(v["hi"], rate), min(v["lo"], rate)
        if str(fetched) >= v["at"]:
            v.update(rate=rate, change=change, at=str(fetched))

    order = {"24K /g": 0, "22K /g": 1, "18K /g": 2, "14K /g": 3, "Silver /g": 4, "Silver /kg": 5}
    labels.sort(key=lambda l: order.get(l, 9))

    if "Daily Summary" in wb.sheetnames:
        del wb["Daily Summary"]
    ws = wb.create_sheet("Daily Summary", 0)   # first tab
    header = ["Date", "City"]
    header += [f"{l} Rate" for l in labels]
    header += [f"{l} Change vs Prev Day" for l in labels]
    header += [f"{l} Day High" for l in labels] + [f"{l} Day Low" for l in labels]
    header += ["Runs", "Last Updated"]
    ws.append(header)

    for (date, city) in sorted(days, reverse=True):          # newest day on top
        d = days[(date, city)]
        vals = [d["vals"].get(l, {}) for l in labels]
        ws.append([date, city]
                  + [v.get("rate") for v in vals]
                  + [v.get("change") for v in vals]
                  + [v.get("hi") for v in vals]
                  + [v.get("lo") for v in vals]
                  + [len(d["runs"]), d["last"]])
        r = ws.max_row
        n = len(labels)
        for c in range(3, 3 + 4 * n):
            ws.cell(r, c).number_format = "+#,##0;-#,##0;0" if 3 + n <= c < 3 + 2 * n else "₹#,##0.00"

    _style_header(ws, [12, 11] + [14] * (4 * len(labels)) + [7, 20])
    ws.row_dimensions[1].height = 32
    wb.active = 0


def save_with_retry(wb, path: Path, retries: int = 5):
    for attempt in range(1, retries + 1):
        try:
            wb.save(path)
            return
        except PermissionError:
            LOG.warning("%s is open in Excel; close it (retry %d/%d in 10s)", path.name, attempt, retries)
            time.sleep(10)
    fallback = path.with_name(f"{path.stem}_{datetime.now():%Y%m%d_%H%M%S}{path.suffix}")
    wb.save(fallback)
    LOG.error("Saved to %s instead because %s was locked", fallback, path.name)


# ------------------------------------------------------------------- agent
def run(sources: list[dict], cfg: dict, dry_run: bool = False) -> int:
    tz = ZoneInfo(cfg.get("timezone", "Asia/Kolkata"))
    now = datetime.now(tz)
    excel_files = cfg.get("excel_files", {"Gold": "gold_rates.xlsx", "Silver": "silver_rates.xlsx"})

    all_rows, backfill_rows = [], []
    today = now.strftime("%Y-%m-%d")
    for src in sources:
        url, metal, city = src["url"], src.get("metal", "any"), src.get("city", "")
        LOG.info("Fetching %s (%s, %s)", url, metal, city)
        try:
            text = fetch_text(url)
        except RuntimeError as e:
            LOG.error(e)
            continue

        rates, method = parse_rules(text, metal), "rules"
        if not rates and cfg.get("use_llm_fallback", True):
            rates, method = parse_llm(text, metal, cfg.get("llm_model", "claude-sonnet-5-5")), "llm"
        if not rates:
            LOG.error("No rates found on %s", url)
            continue

        for r in rates:
            LOG.info("  %-6s %-3s /%-2s ₹%s (%s)", r.metal, r.purity, r.unit, f"{r.rate:,.2f}", r.change)
            all_rows.append([
                now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M:%S"),
                city, r.metal, r.purity, r.unit, r.rate, r.change, method, url,
            ])
        if cfg.get("backfill_last_10_days", True):
            for d, r in parse_history(text, metal):
                if d < today:
                    backfill_rows.append([d, f"{d} 00:00:00", city, r.metal, r.purity, r.unit,
                                          r.rate, r.change, "backfill", url])

    if not all_rows:
        LOG.error("Nothing extracted; Excel not touched.")
        return 1
    if dry_run:
        LOG.info("Dry run: %d rows extracted, not written.", len(all_rows))
        return 0

    # One Excel file per metal (Gold -> gold_rates.xlsx, Silver -> silver_rates.xlsx)
    by_metal: dict[str, list] = {}
    for row in all_rows:
        by_metal.setdefault(row[3], []).append(row)
    for metal, rows in by_metal.items():
        excel_path = Path(excel_files.get(metal, f"{metal.lower()}_rates.xlsx"))
        if not excel_path.is_absolute():
            excel_path = BASE_DIR / excel_path
        wb, ws = open_workbook(excel_path)
        have = {(str(r[0]), r[2], r[3], r[4], r[5]) for r in ws.iter_rows(min_row=2, values_only=True) if r[0]}
        filled = 0
        for row in sorted((b for b in backfill_rows if b[3] == metal), key=lambda b: b[0]):
            key = (row[0], row[2], row[3], row[4], row[5])
            if key not in have:
                append_history(ws, row)
                have.add(key)
                filled += 1
        if filled:
            LOG.info("%s: backfilled %d rows from the site's last-10-days table", metal, filled)
        for row in rows:
            append_history(ws, row)
        rebuild_summary(wb, ws)
        save_with_retry(wb, excel_path)
        LOG.info("%s -> %s: %d rows added to History, Daily Summary refreshed", metal, excel_path.name, len(rows))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Gold/Silver rate agent → Excel")
    ap.add_argument("--config", default=str(BASE_DIR / "config.json"))
    ap.add_argument("--url", help="One-off source URL (overrides config sources)")
    ap.add_argument("--metal", default="any", help="Gold | Silver | any")
    ap.add_argument("--city", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    log_file = BASE_DIR / "agent.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_file, encoding="utf-8")],
    )

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    sources = ([{"url": args.url, "metal": args.metal, "city": args.city}]
               if args.url else cfg["sources"])
    sys.exit(run(sources, cfg, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
