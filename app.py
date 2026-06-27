"""Streamlit entrypoint for the UK Power Markets Dashboard.

Run with:
    streamlit run app.py
"""
from __future__ import annotations

import streamlit as st


st.set_page_config(
    page_title="UK Power Markets Dashboard",
    page_icon="bolt",
    layout="wide",
    initial_sidebar_state="expanded",
)


def main() -> None:
    st.title("UK Power Markets Dashboard")
    st.caption(
        "System & DA prices, demand and generation, interconnector flows, "
        "renewables vs weather forecasts, and BESS trading margins. "
        "Sources: Elexon BMRS Insights Solution, NESO Data Portal, Open-Meteo."
    )

    st.markdown(
        """
        ### What you can explore

        - **Prices** — system buy/sell, day-ahead, market index, spreads.
        - **Demand & Generation** — outturn vs forecast, fuel mix.
        - **Interconnectors** — flows by link, net import/export.
        - **Renewables vs Weather** — wind / solar forecast vs ERA5 outturn.
        - **BESS Margins** — perfect-foresight arbitrage revenue for a battery.
        - **Ancillary** — frequency response (DC / DM / DR) auction results.

        Use the sidebar to navigate between pages.
        """
    )

    st.info(
        "First load fetches live data from public APIs and caches it under "
        "`data/cache`. Subsequent loads are near-instant."
    )


if __name__ == "__main__":
    main()
