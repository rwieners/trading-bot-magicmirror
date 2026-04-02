#!/bin/bash
# Setup script for MMM-Portfolio API on Raspberry Pi
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== MMM-Portfolio API Setup ==="

# Create venv
if [ ! -d "venv" ]; then
    echo "Creating Python venv..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Check .env
if [ ! -f ".env" ]; then
    echo ""
    echo "WICHTIG: .env Datei fehlt!"
    echo "Erstelle eine .env mit deinen Kraken API Keys:"
    echo "  KRAKEN_API_KEY=dein_key"
    echo "  KRAKEN_API_SECRET=dein_secret"
    exit 1
fi

# Test API
echo ""
echo "Teste Kraken-Verbindung..."
python3 -c "
from dotenv import load_dotenv; load_dotenv()
import ccxt, os
ex = ccxt.kraken({'apiKey': os.environ['KRAKEN_API_KEY'], 'secret': os.environ['KRAKEN_API_SECRET'], 'enableRateLimit': True})
b = ex.fetch_balance()
print(f'Kraken EUR Balance: {b.get(\"EUR\",{}).get(\"free\",0):.2f} EUR')
print('Verbindung OK!')
"

echo ""
echo "=== Setup abgeschlossen ==="
echo ""
echo "Starten mit:"
echo "  cd $SCRIPT_DIR && source venv/bin/activate && python portfolio_api.py"
echo ""
echo "Oder als systemd Service:"
echo "  sudo cp mmm-portfolio.service /etc/systemd/system/"
echo "  sudo systemctl enable mmm-portfolio"
echo "  sudo systemctl start mmm-portfolio"
