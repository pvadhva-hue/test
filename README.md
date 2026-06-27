# Power Markets Dashboard

A comprehensive dashboard for analyzing UK power market data including system prices, DA (Day-Ahead) prices, reserve data, interconnector flows, and BESS trading margins.

## Features

- **Market Data Collection**: Real-time and historical data from:
  - NESO (National Electricity System Operator)
  - Elexon Portal
  - EPEX Spot
  - Nord Pool
  - BMU (Balancing Mechanism Unit) data

- **Key Dashboards**:
  - System Prices & DA Pricing
  - Interconnector Flows Analysis
  - BESS Trading Margins Calculator
  - Demand & Generation Profiles
  - Renewable Generation Forecasting (vs. Actual)
  - Wholesale DA Market Analysis
  - Ancillary Services Markets
  - Balancing Mechanism (EAC, NoRD Pool, EPEX DA)

## Project Structure

```
├── data/
│   ├── collectors/          # Data collection modules
│   ├── processors/          # Data processing pipelines
│   └── database/            # Database schemas
├── api/
│   ├── endpoints/           # API endpoints
│   └── services/            # Business logic
├── dashboard/
│   ├── components/          # UI components
│   ├── pages/               # Dashboard pages
│   └── assets/              # Static files
├── notebooks/               # Jupyter notebooks for analysis
└── config/                  # Configuration files
```

## Installation

```bash
git clone https://github.com/pvadhva-hue/test.git
cd test
pip install -r requirements.txt
npm install
```

## Usage

### Start the Dashboard

```bash
# Backend API
python -m api.main

# Frontend Dashboard
npm start
```

### Collect Data

```bash
python -m data.collectors.neso_collector
python -m data.collectors.elexon_collector
```

## Data Sources

- **NESO**: https://www.neso.energy/
- **Elexon**: https://www.elexon.co.uk/
- **EPEX Spot**: https://www.epexspot.com/
- **Nord Pool**: https://www.nordpoolgroup.com/

## License

MIT
