# Power Markets Dashboard

A Streamlit dashboard for analysing UK power market data — system & day-ahead
prices, demand and generation, interconnector flows, ancillary services,
weather forecast vs outturn, and a perfect-foresight **BESS trading margin**
calculator.

## Data sources

| Source | URL | What we use |
| --- | --- | --- |
| Elexon BMRS Insights Solution | <https://data.elexon.co.uk/bmrs/api/v1> | System prices, demand, generation by fuel, interconnector flows, wind/solar day-ahead forecast, market index |
| NESO Data Portal (CKAN) | <https://api.neso.energy/api/3/action> | Day-ahead hourly prices (N2EX / EPEX), Dynamic Containment / Moderation / Regulation results, STOR, historic demand |
| Open-Meteo | <https://open-meteo.com/> | Day-ahead weather forecasts and ERA5 reanalysis (outturn weather) |

All endpoints are public and unauthenticated.

## Install & run

```bash
pip install -r requirements.txt
streamlit run app.py
```

The first load fetches live data and caches the JSON responses under
`data/cache/` (overridable via `POWERDASH_CACHE_DIR`). Subsequent loads
are near-instant. The Streamlit `@st.cache_data` layer adds in-memory
caching with a 30 minute TTL.

## Pages

- **Prices** — system buy / sell, intraday market index, NESO day-ahead.
- **Demand & Generation** — outturn demand and fuel mix area chart.
- **Interconnectors** — flows by link, net GB import/export.
- **BESS Margins** — sized battery with adjustable power, duration, efficiency
  and cycles. Computes daily perfect-foresight revenue from DA prices and the
  uplift available from half-hourly imbalance trading.
- **Renewables vs Weather** — wind & solar forecast vs outturn alongside
  day-ahead wind speed forecast vs ERA5 reanalysis.
- **Ancillary Markets** — DC / DM / DR / STOR auction results.

## BESS margin model

For each day in the window we:

1. Pick the `D * cycles` cheapest price slots → charging.
2. Pick the `D * cycles` most expensive slots → discharging.
3. Apply round-trip efficiency: to deliver `1 MWh` you buy `1 / η_rt MWh`.
4. Daily revenue = discharge price × delivered − charge price × bought.

This is the **upper bound** on what a battery could earn. Real-world
optimisers typically capture 40–70% of this number; the gap is your
trading margin.

## Layout

```
app.py                          Streamlit entrypoint
pages/                          Multipage Streamlit pages
powerdash/
├── collectors/
│   ├── _http.py                Cached, retrying GET
│   ├── elexon.py               BMRS client
│   ├── neso.py                 NESO CKAN client
│   └── weather.py              Open-Meteo client
├── services/
│   └── bess.py                 BESS arbitrage math
└── dashboard/
    ├── data.py                 Streamlit-cached loaders
    └── utils.py                Small UI helpers
tests/                          pytest suite
```

## Tests

```bash
pytest tests/ -v
```

## Notes & caveats

- NESO CKAN resource UUIDs occasionally change when the portal publishes
  new dataset versions. Override the IDs in
  `powerdash/collectors/neso.py:RESOURCES` if a dataset returns empty.
- Open-Meteo ERA5 archive has a ~5 day lag.
- Imbalance prices come from Elexon at 30-minute settlement resolution;
  day-ahead prices are hourly. The BESS calculator handles each cadence
  separately and merges by date.

## License

MIT
