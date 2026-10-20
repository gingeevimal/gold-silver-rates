# Gold & Silver Rates (Chennai)

Collects Chennai gold and silver rates every day, keeps the full history in Excel, and shows a Streamlit dashboard with a Research tab.

- **Agent** (`metal_rates_agent.py`): fetches rates from goodreturns.in. On each run it also back-fills any missing days from the site's "last 10 days" table.
- **Schedule** (`.github/workflows/metal-rates.yml`): GitHub Actions runs the agent at 11:30 AM and 3:00 PM IST and commits the Excel files.
- **Dashboard** (`app.py`) has two screens:
  - **Chennai (India)** (`views/chennai.py`): today's rates, a daily table with ▲/▼ change and % columns, and three tabs:
    - **Trend**
    - **Research**: 1W–1Y/YTD moves, highs and lows, volatility, streaks, a monthly table, a weekday pattern and the gold-to-silver ratio
    - **Technical**: candlesticks, moving averages, Bollinger Bands, the trend channel, flag patterns, support/resistance, Fibonacci levels, RSI, MACD, a bottom watch, an outlook bias and statistical price ranges
  - **Global** (`views/global_rates.py`): live spot from goldprice.org in any currency and unit, a Chennai-vs-global premium table, a saved global history with change columns, and technical analysis of COMEX gold and silver in USD or INR.
- **Global data** (`fetch_global.py`): runs on the same schedule. It saves a goldprice.org snapshot to `global_rates.csv` and 10 years of COMEX history (Yahoo Finance) to `global_history.csv`.
- **Analysis code** (`technical.py`): the indicators and pattern detection. Patterns and ranges describe price behaviour and probabilities, not certainties, and are not investment advice.

Run the dashboard locally: `streamlit run app.py`. To host it, deploy `app.py` from this repo on Streamlit Community Cloud (share.streamlit.io). It redeploys whenever the workflow commits new rates.

---

## Agent details

Fetches the day's gold (24K/22K/18K per gram) and silver (per gram and per kg) rates from the URLs in `config.json` and writes them to two local Excel files: gold to `gold_rates.xlsx` and silver to `silver_rates.xlsx` Each file has two sheets:
- **Daily Summary** (first tab): one row per day, newest at the top, with the day's latest rate, the change vs the previous day, the day's high and low, and how many times it ran that day. It is rebuilt after every run.
- **History**: every run is kept as new rows. Nothing is ever overwritten.

You can change the file names under `excel_files` in `config.json`.

## Setup
```
pip install -r requirements.txt
python metal_rates_agent.py --dry-run     # check what it extracts
python metal_rates_agent.py               # write to Excel
```

## Excel behaviour
- Files from the older version are upgraded automatically: the `Rates` sheet is renamed `History` and a `Daily Summary` sheet is added.
- Columns: Date, Fetched At, City, Metal, Purity, Unit, Rate (INR), Change (INR), Method (`rules` or `llm`), Source URL.
- If the workbook is open in Excel, the agent retries five times. After that it saves a timestamped copy so the data isn't lost.

## Adding sources
Add an entry under `sources` in `config.json`, for example another city:
```json
{"name": "GoodReturns Bangalore Gold", "city": "Bangalore", "metal": "Gold",
 "url": "https://www.goodreturns.in/gold-rates/bangalore.html"}
```
For a one-off run: `python metal_rates_agent.py --url <URL> --metal Silver --city Chennai`

Pages laid out like GoodReturns ("24K Gold /g ₹15,028 + 104") are parsed with regex. For any other website, set `ANTHROPIC_API_KEY` and the agent has Claude read the page and pull out the rates. `use_llm_fallback: false` turns this off.

## Schedule it (Windows)
Double-click `setup_scheduler.bat` once. That creates a task called "Gold Silver Rates Agent" which:
- runs every day at **11:30 AM** and **3:00 PM**, using the laptop's clock (IST)
- runs as soon as you switch on or wake the laptop if it was off or asleep at either time
- waits up to 5 minutes for Wi-Fi before it fetches

To stop the automatic runs, double-click `remove_scheduler.bat`.
To check a run, open `agent_console.log`, or open Task Scheduler and look at **Last Run Result**.

Logs: `agent.log`.

## Run it on GitHub Actions (no laptop needed)
`.github/workflows/metal-rates.yml` runs the agent on GitHub's servers every day at **11:30 AM** and **3:00 PM IST**. It then commits the updated `gold_rates.xlsx` and `silver_rates.xlsx` back to the repo.

- **Run it now:** Actions tab → *Gold & Silver Rates* → **Run workflow**.
- **See the data:** open `gold_rates.xlsx` in the repo and click **Download**, or `git pull` to update your local copy.
- **If a run fails** (for example, the website changed or blocked the request), GitHub emails you. Click the failed run to see the log.
- GitHub can start scheduled runs 5 to 30 minutes late when its servers are busy.
- **Optional:** to read sites with a different layout, add the repo secret `ANTHROPIC_API_KEY` (Settings → Secrets and variables → Actions).
- If you use GitHub Actions, run `remove_scheduler.bat` on your laptop so you don't keep two separate copies of the history.

