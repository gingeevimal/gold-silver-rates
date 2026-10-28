"""
Opens the Streamlit app in a headless browser so it doesn't fall asleep from inactivity.
If the app is already asleep, it clicks Streamlit's wake-up button and waits for it to load.
Run by .github/workflows/keep-awake.yml.
"""

import sys

from playwright.sync_api import sync_playwright

URL = "https://chennai-gold-silver-rates.streamlit.app/"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(URL, timeout=120_000)
    page.wait_for_timeout(10_000)
    wake = page.get_by_role("button", name="Yes, get this app back up!")
    if wake.count():
        print("App was asleep: clicking wake-up button")
        wake.click()
        page.wait_for_timeout(90_000)          # give it time to boot
    else:
        print("App is awake")
    page.wait_for_timeout(15_000)              # keep the session open briefly so it counts as a visit
    browser.close()
sys.exit(0)
