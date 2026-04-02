# Trading Bot + MagicMirror

LSTM-basierter Krypto-Trading-Bot für [Kraken](https://www.kraken.com/), mit Portfolio-Anzeige auf einem [MagicMirror²](https://magicmirror.builders/).

Läuft 24/7 auf einem Raspberry Pi 4 — vollautomatisch mit 1-Minuten-Zyklen, automatischem Model-Retraining und Live-Spiegelanzeige.

## Features

- **LSTM-Prognosen** — PyTorch-Modell sagt Preisbewegungen 1h voraus (15-Min-Kerzen, 12 Features)
- **Profit-Gate-Strategie** — Trades nur wenn predicted move > Gebühren + Sicherheitsmarge
- **3 Trading-Modi** — Conservative, Aggressive, Scalping (per Web-UI umschaltbar)
- **Automatisches Retraining** — Modell wird alle 24h mit 180 Tagen Daten neu trainiert
- **Kraken-Sync** — Kraken ist immer Datenmaster, Positionen werden automatisch abgeglichen
- **Risk Management** — Stop-Loss, Profit-Target, Portfolio-Drawdown-Limit, Health-Checker
- **MagicMirror-Modul** — Portfolio-Wert, P&L und Bot-Iteration live auf dem Spiegel
- **Web Dashboard** — Trades, Performance-Charts, Swagger API (Port 8000)
- **Backtesting** — Strategien mit historischen Daten validieren

## Unterstützte Coins

BTC, ETH, SOL, XRP, ADA, DOGE — alle gegen EUR (wegen Kraken EU/USDT-Restriktionen).

## Architektur

```
broker/
├── bot.py                  # Haupt-Orchestrator (1-Min-Zyklen)
├── sync_kraken.py          # Kraken → DB Synchronisation
├── data/
│   ├── live_feed.py        # Live-Ticker & Kerzendaten
│   ├── storage.py          # SQLite Trade-Datenbank
│   └── coin_analyzer.py    # Coin-Analyse
├── exchange/
│   └── kraken_trader.py    # Kraken API (Buy/Sell via ccxt)
├── models/
│   ├── lstm_model.py       # LSTM-Modell (PyTorch)
│   ├── features.py         # Feature Engineering (12 Features)
│   └── model_trainer.py    # Training & Auto-Retrain
├── strategies/
│   └── profit_gate_strategy.py  # Profit-Gate Strategie
├── risk/
│   ├── position_manager.py # Positionsverwaltung
│   └── account_monitor.py  # Balance & Drawdown Monitoring
└── utils/
    ├── health_checker.py   # Error-Tracking, Auto-Pause
    ├── logger.py           # Rotating Logs
    └── dashboard.py        # Performance Reports
```

## Setup

### Voraussetzungen

- Python 3.11+
- Kraken API Key (mit Trading-Berechtigung)

### Installation

```bash
git clone https://github.com/rwieners/trading-bot-magicmirror.git
cd trading-bot-magicmirror
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Konfiguration

```bash
cp .env.example .env
# Kraken API Keys eintragen:
#   KRAKEN_API_KEY=...
#   KRAKEN_API_SECRET=...
```

Trading-Parameter lassen sich über `config/user_settings.json` oder das Web-Dashboard anpassen:

| Parameter | Default | Beschreibung |
|---|---|---|
| `max_position_size` | 10 € | Maximale Positionsgröße |
| `min_profit_target` | 5% | Profit-Target für Exits |
| `max_loss_cutoff` | -40% | Stop-Loss |
| `trading_mode` | conservative | conservative / aggressive / scalping |

### Starten

```bash
# Alles starten (Bot + Web-UI + MagicMirror API)
./start.sh

# Nur Bot
./start.sh --bot-only

# Status
./start.sh --status

# Stoppen
./start.sh --stop
```

## MagicMirror-Modul

Das `MMM-Portfolio`-Modul zeigt auf dem Spiegel:
- Portfolio-Gesamtwert
- Unrealisierte / realisierte P&L
- Anzahl offener Positionen
- Letztes Update + Bot-Iteration

### Installation auf dem MagicMirror

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

Siehe [docs/RASPI_DEPLOYMENT.md](docs/RASPI_DEPLOYMENT.md) für die komplette Anleitung mit systemd-Services.

## API

Die Portfolio-API (Port 8090) liefert:

```json
{
  "portfolio_value": 142.50,
  "unrealized_pnl": 3.20,
  "realized_pnl": 12.80,
  "positions": 3,
  "iteration": 428,
  "currency": "EUR",
  "timestamp": "2026-04-02T10:30:00Z"
}
```

Das Web-Dashboard (Port 8000) hat eine Swagger-UI unter `/api/docs`.

## Tests

```bash
pytest
```

## Sicherheit

- API Keys ausschließlich über `.env` (gitignored)
- Swagger-UI optional per Basic Auth geschützt (`SWAGGER_USERNAME` / `SWAGGER_PASSWORD` als Env-Variablen)
- Siehe [docs/SECURITY_CHECKLIST.md](docs/SECURITY_CHECKLIST.md)

## Disclaimer

Dieses Projekt dient ausschließlich zu Bildungs- und Experimentierzwecken. Krypto-Trading birgt erhebliche Verlustrisiken. Kein Finanzberatung. Nutzung auf eigene Gefahr.

## Lizenz

MIT
