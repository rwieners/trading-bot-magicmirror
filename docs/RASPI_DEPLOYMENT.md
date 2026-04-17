# Raspberry Pi Deployment

## System

| Eigenschaft | Wert |
|---|---|
| Gerät | Raspberry Pi 4 (8 GB RAM) |
| OS | Debian GNU/Linux 12 (Bookworm) |
| Architektur | aarch64 |
| IP | `<raspi-ip>` |
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

### Web Dashboard

```
/etc/systemd/system/trading-bot-ui.service
```

- **Beschreibung:** Web UI für Trade-Monitoring und Bot-Einstellungen
- **ExecStart:** `/home/rene/trading-bot/venv/bin/python3 /home/rene/trading-bot/scripts/web_ui.py`
- **Port:** 8000 (LAN-weit erreichbar: `http://<raspi-ip>:8000`)
- **Restart:** always (nach 10s)
- **Funktionen:** Trades, Portfolio, Charts, Settings ändern (live übernommen)

### VNC (wayvnc)

Wayvnc läuft als User-Service für Remote-Zugriff auf das MagicMirror-Display:

```
~/.config/systemd/user/wayvnc.service
```

- **Port:** 5900 (kein Passwort, nur LAN)
- **Verbindung:** `vnc://<raspi-ip>:5900`
- **Steuerung:** `systemctl --user start|stop|restart wayvnc`

## Nützliche Befehle

### Status prüfen

```bash
sudo systemctl status trading-bot
sudo systemctl status mmm-portfolio
sudo systemctl status trading-bot-ui
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
sudo systemctl restart trading-bot-ui

# Stoppen
sudo systemctl stop trading-bot
sudo systemctl stop mmm-portfolio
sudo systemctl stop trading-bot-ui

# Starten
sudo systemctl start trading-bot
sudo systemctl start mmm-portfolio
sudo systemctl start trading-bot-ui
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
ssh rene@<raspi-ip>
```

SSH-Key ist eingerichtet — kein Passwort nötig.

## Code-Deployment (kein Git auf dem Raspi)

Auf dem Raspi ist kein Git installiert. Änderungen müssen per SCP übertragen werden.

### Einzelne Dateien übertragen

```bash
# Vom Mac aus (im Projektverzeichnis):
scp <datei> rene@<raspi-ip>:~/trading-bot/<datei>

# Beispiel:
scp config/settings.py rene@<raspi-ip>:~/trading-bot/config/settings.py
scp scripts/web_ui.py rene@<raspi-ip>:~/trading-bot/scripts/web_ui.py
scp scripts/templates/dashboard.html rene@<raspi-ip>:~/trading-bot/scripts/templates/dashboard.html
```

### Ganzes Projekt synchronisieren (ohne venv/logs/data)

```bash
rsync -avz --exclude='venv' --exclude='.venv' --exclude='logs' --exclude='data' \
  --exclude='.git' --exclude='.env' --exclude='__pycache__' --exclude='*.pyc' \
  /Users/rene/dev/trading-bot-magicmirror/ rene@<raspi-ip>:~/trading-bot/
```

### Nach dem Übertragen: Services neustarten

```bash
ssh rene@<raspi-ip> "sudo systemctl restart trading-bot trading-bot-ui"
```

## MagicMirror Konfiguration

Das MMM-Portfolio Modul ist in der MagicMirror `config.js` registriert:

- **Position:** top_right
- **API-Endpoint:** http://localhost:8090/portfolio
- **Update-Intervall:** 60 Sekunden
- **Anzeige:** P&L (offen + realisiert), Positionen, Iteration, Trading Mode
- **Hinweis:** Portfoliowert wird aus Datenschutzgründen nicht angezeigt

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

### Technische Details

Das Script setzt automatisch `XDG_RUNTIME_DIR` und `WAYLAND_DISPLAY`, da diese Variablen in der Cron-Umgebung nicht gesetzt sind. Es sucht den Wayland-Socket unter `/run/user/1000/wayland-*` und erkennt den HDMI-Output (`HDMI-A-2`) per `wlr-randr`.

**Wichtig:** Das Script darf kein `set -euo pipefail` verwenden, da in der minimalen Cron-Umgebung sonst Befehle wie `find` oder `grep` (bei keinem Treffer) den Abbruch auslösen.

### Troubleshooting

```bash
# Log prüfen
cat /home/rene/trading-bot/logs/display_schedule.log

# Manuell testen (simuliert Cron-Umgebung ohne gesetzte Variablen)
env -i HOME=/home/rene PATH=/usr/bin:/bin /home/rene/bin/mirror-display.sh off

# Monitor manuell ein-/ausschalten
/home/rene/bin/mirror-display.sh on
/home/rene/bin/mirror-display.sh off

# HDMI-Output prüfen (welcher Output ist verbunden?)
wlr-randr

# Cron-Jobs anzeigen
crontab -l
```

### Deployment nach Änderungen

```bash
# Vom Mac aus:
scp raspi/mirror-display.sh rene@<raspi-ip>:~/bin/mirror-display.sh
ssh rene@<raspi-ip> "chmod +x ~/bin/mirror-display.sh"
```
