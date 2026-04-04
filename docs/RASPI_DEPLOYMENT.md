# Raspberry Pi Deployment

## System

| Eigenschaft | Wert |
|---|---|
| Gerät | Raspberry Pi 4 (8 GB RAM) |
| OS | Debian GNU/Linux 12 (Bookworm) |
| Architektur | aarch64 |
| IP | 192.168.178.254 |
| User | rene |
| Python | 3.11.2 (venv) |
| PyTorch | 2.11.0+cpu |

## Verzeichnisstruktur

```
/home/rene/
├── trading-bot/              # Trading Bot + Portfolio API
│   ├── broker/               # Bot-Code (bot.py, strategies, models, etc.)
│   ├── config/               # Konfiguration
│   ├── logs/                 # Logs + trades.db
│   ├── models/               # LSTM-Modell (lstm_model.pt)
│   ├── scripts/              # Hilfsskripte
│   ├── venv/                 # Python Virtual Environment
│   ├── portfolio_api.py      # Flask API für MagicMirror (Port 8090)
│   ├── start.sh              # Manueller Start-Script
│   ├── requirements.txt
│   └── .env                  # Kraken API Keys
│
└── MagicMirror/
    ├── config/config.js      # MagicMirror Konfiguration
    └── modules/
        └── MMM-Portfolio/    # MagicMirror Modul
            ├── MMM-Portfolio.js
            ├── MMM-Portfolio.css
            └── node_helper.js
```

## Systemd Services

Beide Services starten automatisch beim Boot und werden bei Fehlern neu gestartet.

### Trading Bot

```
/etc/systemd/system/trading-bot.service
```

- **Beschreibung:** Crypto Trading Bot (Kraken, LSTM-basiert)
- **ExecStart:** `/home/rene/trading-bot/venv/bin/python3 -m broker.bot`
- **Restart:** on-failure (nach 30s)
- **Strategie:** 15-Min-Kerzen, max 8 Positionen, max 35 EUR pro Trade

### Portfolio API

```
/etc/systemd/system/mmm-portfolio.service
```

- **Beschreibung:** Flask REST API für MagicMirror
- **ExecStart:** `/home/rene/trading-bot/venv/bin/python3 /home/rene/trading-bot/portfolio_api.py`
- **Port:** 8090
- **Restart:** always (nach 10s)
- **Endpoint:** `GET /portfolio` — liest aus `trades.db` + live Kraken-Preise

## Nützliche Befehle

### Status prüfen

```bash
sudo systemctl status trading-bot
sudo systemctl status mmm-portfolio
```

### Logs anzeigen

```bash
# Live-Logs Bot
sudo journalctl -u trading-bot -f

# Live-Logs API
sudo journalctl -u mmm-portfolio -f

# Bot-Log-Datei
tail -f /home/rene/trading-bot/logs/bot.log
```

### Services steuern

```bash
# Neustarten
sudo systemctl restart trading-bot
sudo systemctl restart mmm-portfolio

# Stoppen
sudo systemctl stop trading-bot
sudo systemctl stop mmm-portfolio

# Starten
sudo systemctl start trading-bot
sudo systemctl start mmm-portfolio
```

### Portfolio API testen

```bash
curl -s http://localhost:8090/portfolio | python3 -m json.tool
```

### Python Packages verwalten

```bash
# Kein activate-Script vorhanden (Debian-Bug), daher immer direkt:
/home/rene/trading-bot/venv/bin/python3 -m pip install <paket>
/home/rene/trading-bot/venv/bin/python3 -m pip list
```

## SSH-Zugriff vom Mac

```bash
ssh rene@192.168.178.254
```

SSH-Key ist eingerichtet — kein Passwort nötig.

## MagicMirror Konfiguration

Das MMM-Portfolio Modul ist in der MagicMirror `config.js` registriert:

- **Position:** top_right
- **API-Endpoint:** http://localhost:8090/portfolio
- **Update-Intervall:** 60 Sekunden
- **Anzeige:** Realized P&L + Anzahl offene Trades

## Display-Zeitplan (HDMI nachts aus)

Der Monitor wird nachts automatisch abgeschaltet:

- **23:00 Uhr:** HDMI aus
- **06:00 Uhr:** HDMI an

Installierte Cron-Jobs auf dem Raspberry Pi:

```cron
0 23 * * * /home/rene/bin/mirror-display.sh off >> /home/rene/trading-bot/logs/display_schedule.log 2>&1
0 6 * * * /home/rene/bin/mirror-display.sh on >> /home/rene/trading-bot/logs/display_schedule.log 2>&1
```

Das zugehörige Script liegt im Repo unter `raspi/mirror-display.sh` und auf dem Pi unter `/home/rene/bin/mirror-display.sh`.
Für Raspberry Pi OS Bookworm wird bevorzugt `wlr-randr` verwendet; ältere Setups fallen auf `vcgencmd` bzw. `xset` zurück.
