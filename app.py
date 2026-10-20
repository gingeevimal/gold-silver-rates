"""
Gold & Silver Rates dashboard: two screens.
  - Chennai (India): daily Chennai rates from goodreturns.in, research and technical analysis
  - Global: live spot from goldprice.org, global history and technical analysis
Run locally:  streamlit run app.py
"""

import streamlit as st

st.set_page_config(page_title="Gold & Silver Rates", page_icon=":material/paid:", layout="wide")

pg = st.navigation(
    [
        st.Page("views/chennai.py", title="Chennai (India)", icon=":material/location_on:", default=True),
        st.Page("views/global_rates.py", title="Global", icon=":material/public:"),
    ],
    position="top",
)
pg.run()
