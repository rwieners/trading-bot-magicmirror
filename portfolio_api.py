"""
MagicMirror Portfolio API
Provides a simplified REST endpoint for the MMM-Portfolio MagicMirror module.
Reads directly from the trades database and fetches live prices from Kraken.
"""
from flask import Flask, jsonify
from flask_cors import CORS
import sys
import os
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

app = Flask(__name__)
CORS(app)

DB_PATH = PROJECT_DIR / 'logs' / 'trades.db'
FEE_RATE = 0.0016  # Kraken maker fee

def get_db_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def get_portfolio_data():
    """Read portfolio directly from DB and fetch live prices."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, symbol, entry_price, entry_size, entry_value, entry_time,
                   model_confidence, entry_fee
            FROM trades
            WHERE status = 'OPEN' OR status IS NULL
            ORDER BY entry_time DESC
        ''')
        open_trades = cursor.fetchall()

        # Get realized P&L
        cursor.execute("""
            SELECT COALESCE(SUM(pnl), 0) as total_pnl
            FROM trades WHERE status LIKE 'CLOSED%'
        """)
        realized_pnl = cursor.fetchone()[0]
        conn.close()

        # Fetch live prices from Kraken
        live_prices = {}
        try:
            import ccxt
            exchange = ccxt.kraken({'enableRateLimit': True, 'timeout': 5000})
            symbols = set(t['symbol'] for t in open_trades)
            for symbol in symbols:
                try:
                    ticker = exchange.fetch_ticker(symbol)
                    live_prices[symbol] = ticker['last']
                except Exception:
                    pass
        except Exception:
            pass

        # Calculate portfolio value
        total_invested = 0
        total_current_value = 0
        total_unrealized_pnl = 0
        positions = []

        for trade in open_trades:
            entry_value_eur = trade['entry_size']
            if entry_value_eur < 1.0:  # Skip dust
                continue
            entry_fee = trade['entry_fee'] or 0
            total_invested += entry_value_eur
            current_price = live_prices.get(trade['symbol'], trade['entry_price'])
            if trade['entry_price'] > 0:
                current_value = entry_value_eur * (current_price / trade['entry_price'])
            else:
                current_value = entry_value_eur
            estimated_exit_fee = current_value * FEE_RATE
            unrealized_pnl = current_value - entry_value_eur - entry_fee - estimated_exit_fee
            total_current_value += current_value
            total_unrealized_pnl += unrealized_pnl
            positions.append({
                'symbol': trade['symbol'],
                'entry_value': round(entry_value_eur, 2),
                'current_value': round(current_value, 2),
                'pnl': round(unrealized_pnl, 2),
            })

        # Get Kraken EUR balance
        kraken_balance = None
        try:
            import ccxt
            from dotenv import load_dotenv
            load_dotenv(str(PROJECT_DIR / '.env'))
            exchange = ccxt.kraken({
                'apiKey': os.environ.get('KRAKEN_API_KEY', ''),
                'secret': os.environ.get('KRAKEN_API_SECRET', ''),
                'enableRateLimit': True,
                'timeout': 10000,
            })
            balance = exchange.fetch_balance()
            kraken_balance = balance.get('EUR', {}).get('free', 0)
        except Exception:
            pass

        if kraken_balance is not None:
            total_balance = kraken_balance + total_current_value
        else:
            total_balance = total_invested + realized_pnl

        return {
            'portfolio_value': round(total_balance, 2),
            'total_invested': round(total_invested, 2),
            'total_current_value': round(total_current_value, 2),
            'unrealized_pnl': round(total_unrealized_pnl, 2),
            'realized_pnl': round(realized_pnl, 2),
            'positions': len(positions),
            'currency': 'EUR',
        }
    except Exception as e:
        logging.error(f"Portfolio error: {e}")
        return None

def get_bot_iteration():
    """Read current bot iteration from status file."""
    try:
        status_path = PROJECT_DIR / 'logs' / 'bot_status.json'
        if status_path.exists():
            import json
            with open(status_path) as f:
                return json.load(f).get('iteration')
    except Exception:
        pass
    return None

def get_trading_settings():
    """Read trading mode and scalping profit from user settings."""
    try:
        import json
        settings_path = PROJECT_DIR / 'config' / 'user_settings.json'
        if settings_path.exists():
            with open(settings_path) as f:
                s = json.load(f)
                return s.get('trading_mode'), s.get('scalping_profit_abs')
    except Exception:
        pass
    return None, None

@app.route("/portfolio")
def portfolio():
    data = get_portfolio_data()
    trading_mode, scalping_profit = get_trading_settings()
    if data is not None:
        data['timestamp'] = datetime.utcnow().isoformat() + 'Z'
        data['iteration'] = get_bot_iteration()
        data['trading_mode'] = trading_mode
        data['scalping_profit_abs'] = scalping_profit
        return jsonify(data)
    else:
        return jsonify({
            "portfolio_value": None,
            "currency": None,
            "timestamp": datetime.utcnow().isoformat() + 'Z',
            "iteration": get_bot_iteration(),
            "trading_mode": trading_mode,
            "scalping_profit_abs": scalping_profit,
            "error": "Portfolio-Wert konnte nicht geladen werden."
        }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090)
