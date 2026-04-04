# Trading Bot + MagicMirror

This module is nearly completly ai generated, so be check everything twice :-)

LSTM-based crypto trading bot for [Kraken](https://www.kraken.com/), with a live portfolio display on a [MagicMirror²](https://magicmirror.builders/).

Runs 24/7 on a Raspberry Pi 4 — fully automated with 1-minute cycles, automatic model retraining, and a live mirror display.

## Features

- **LSTM Predictions** — PyTorch model predicts price movements 1h ahead (15-min candles, 12 features)
- **Profit-Gate Strategy** — Only trades when predicted move > fees + safety margin
- **3 Trading Modes** — Conservative, Aggressive, Scalping (switchable via web UI)
- **Automatic Retraining** — Model retrains every 24h using 180 days of data
- **Kraken Sync** — Kraken is always the data master, positions are automatically reconciled
- **Risk Management** — Stop-loss, profit target, portfolio drawdown limit, health checker
- **MagicMirror Module** — Portfolio value, P&L, and bot iteration displayed live on the mirror
- **Web Dashboard** — Trades, performance charts, Swagger API (port 8000)
- **Backtesting** — Validate strategies against historical data

## Supported Coins

BTC, ETH, SOL, XRP, ADA, DOGE — all traded against EUR (due to Kraken EU/USDT restrictions).

## Architecture

```
broker/
├── bot.py                  # Main orchestrator (1-min cycles)
├── sync_kraken.py          # Kraken → DB synchronization
├── data/
│   ├── live_feed.py        # Live ticker & candle data
│   ├── storage.py          # SQLite trade database
│   └── coin_analyzer.py    # Coin analysis
├── exchange/
│   └── kraken_trader.py    # Kraken API (buy/sell via ccxt)
├── models/
│   ├── lstm_model.py       # LSTM model (PyTorch)
│   ├── features.py         # Feature engineering (12 features)
│   └── model_trainer.py    # Training & auto-retrain
├── strategies/
│   └── profit_gate_strategy.py  # Profit-gate strategy
├── risk/
│   ├── position_manager.py # Position management
│   └── account_monitor.py  # Balance & drawdown monitoring
└── utils/
    ├── health_checker.py   # Error tracking, auto-pause
    ├── logger.py           # Rotating logs
    └── dashboard.py        # Performance reports
```

## Setup

### Prerequisites

- Python 3.11+
- Kraken API key (with trading permissions)

### Installation

```bash
git clone https://github.com/rwieners/trading-bot-magicmirror.git
cd trading-bot-magicmirror
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration

```bash
cp .env.example .env
# Add your Kraken API keys:
#   KRAKEN_API_KEY=...
#   KRAKEN_API_SECRET=...
```

Trading parameters can be adjusted via `config/user_settings.json` or the web dashboard:

| Parameter | Default | Description |
|---|---|---|
| `max_position_size` | 10 € | Maximum position size |
| `min_profit_target` | 5% | Profit target for exits |
| `max_loss_cutoff` | -40% | Stop-loss |
| `trading_mode` | conservative | conservative / aggressive / scalping |

### Running

```bash
# Start everything (bot + web UI + MagicMirror API)
./start.sh

# Bot only
./start.sh --bot-only

# Status
./start.sh --status

# Stop
./start.sh --stop
```

## MagicMirror Module

The `MMM-Portfolio` module displays on the mirror:
- Total portfolio value
- Unrealized / realized P&L
- Number of open positions
- Last update + bot iteration
- Trading mode + scalping profit target

### Installation on MagicMirror

```bash
cp -r raspi/MMM-Portfolio/ ~/MagicMirror/modules/MMM-Portfolio/
```

In `~/MagicMirror/config/config.js`:

```js
{
  module: "MMM-Portfolio",
  header: "Trading Portfolio",
  position: "top_right",
  config: {
    apiEndpoint: "http://localhost:8090/portfolio",
    updateInterval: 60000,
  }
}
```

## Raspberry Pi Deployment

See [docs/RASPI_DEPLOYMENT.md](docs/RASPI_DEPLOYMENT.md) for the full guide with systemd services.

## API

The portfolio API (port 8090) returns:

```json
{
  "portfolio_value": 142.50,
  "unrealized_pnl": 3.20,
  "realized_pnl": 12.80,
  "positions": 3,
  "iteration": 428,
  "trading_mode": "scalping",
  "scalping_profit_abs": 0.5,
  "currency": "EUR",
  "timestamp": "2026-04-02T10:30:00Z"
}
```

The web dashboard (port 8000) includes a Swagger UI at `/api/docs`.

## Tests

```bash
pytest
```

## Security

- API keys stored exclusively in `.env` (gitignored)
- Swagger UI optionally protected via Basic Auth (`SWAGGER_USERNAME` / `SWAGGER_PASSWORD` as env variables)
- See [docs/SECURITY_CHECKLIST.md](docs/SECURITY_CHECKLIST.md)

## Disclaimer

This project is for educational and experimental purposes only. Crypto trading carries significant risk of loss. Not financial advice. Use at your own risk.

## License

MIT
