# Pocket Terminal Advanced

Bloomberg-style financial terminal for the command line — with Indian market support, real-time data, F&O analytics, ML engine, and a lag-free differential rendering HUD.

## Features

| Category | Commands |
|---|---|
| **Market Data** | `price` — live animated HUD, `dashboard` — multi-panel overview, `chart` — interactive matplotlib/mplfinance, `news`, `options`, `fno` — chain/calendar/PCR/futures |
| **F&O Analytics** | `fno chain` — OI, IV, max pain, PCR; `fno calendar` — expiry calendar with DTE; `fno pcr` — put-call ratio trend; `futures` — implied futures curve |
| **Fundamentals** | `fundamentals`, `income`, `balance`, `cashflow`, `earnings`, `dividends`, `dcf`, `analyst`, `compare` |
| **Portfolio & Risk** | `portfolio`, `watchlist`, `risk` (Sharpe/VaR/Beta), `invest` (historical calculator) |
| **AI & Quant** | `train` — PyTorch neural net, `predict` — next-session forecast, `backtest` — strategy backtester |
| **FX & Tools** | `fx` — FX dashboard, `fxlive` — animated FX HUD, `calc` — SIP/EMI/position sizing, `theme`, `config` |

## Quick Start

```bash
pip install yfinance pandas numpy matplotlib mplfinance torch
python -m pocket_terminal_advanced
```

Or via launch script:

```bash
python launch.py
```

## Themes

`theme bloomberg` — amber-on-black Bloomberg style  
`theme nexus` — rainbow cyberpunk  
`theme minimal` — clean monochrome  

## F&O Commands

```
fno list                         — List F&O stocks
fno chain TICKER [expiry]        — Options chain with OI/PCR/max pain
fno calendar TICKER              — Expiry calendar with DTE
fno pcr TICKER                   — Put-call ratio trend
fno futures TICKER               — Implied futures curve
futures TICKER                   — Same as fno futures
```

## Data Source

All market data via [yfinance](https://github.com/ranaroussi/yfinance) (Yahoo Finance). Supports US equities, ETFs, crypto, FX, and NSE India stocks (`.NS` suffix).

## Requirements

- Python 3.8+
- yfinance, pandas, numpy
- matplotlib (optional — for charts)
- mplfinance (optional — for candlestick charts)
- torch (optional — for ML engine)
