"""
MagicMirror Portfolio API (Raspi-Standalone)
Fetches all data directly from Kraken API — no local database needed.
"""
from flask import Flask, jsonify
from flask_cors import CORS
import ccxt
import os
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

FEE_RATE = 0.0026  # Kraken taker fee

def get_exchange():
    return ccxt.kraken({
        'apiKey': os.environ.get('KRAKEN_API_KEY', ''),
        'secret': os.environ.get('KRAKEN_API_SECRET', ''),
        'enableRateLimit': True,
        'timeout': 15000,
    })

def get_portfolio_data():
    """Fetch portfolio data directly from Kraken."""
    try:
        exchange = get_exchange()

        # Fetch full balance
        balance = exchange.fetch_balance()
        eur_free = balance.get('EUR', {}).get('free', 0) or 0
        eur_total = balance.get('EUR', {}).get('total', 0) or 0

        # Find all non-zero crypto holdings
        positions = []
        total_current_value = 0

        for currency, amounts in balance.items():
            if currency in ('EUR', 'info', 'free', 'used', 'total', 'timestamp', 'datetime'):
                continue
            total_amt = amounts.get('total', 0) or 0
            if total_amt <= 0:
                continue

            # Get current price in EUR
            symbol = f"{currency}/EUR"
            try:
                ticker = exchange.fetch_ticker(symbol)
                price = ticker['last']
                value_eur = total_amt * price
                if value_eur < 0.50:  # Skip dust
                    continue
                positions.append({
                    'symbol': symbol,
                    'amount': round(total_amt, 8),
                    'price': round(price, 4),
                    'value_eur': round(value_eur, 2),
                })
                total_current_value += value_eur
            except Exception:
                continue

        # Fetch closed trades for realized P&L
        realized_pnl = 0
        try:
            trades = exchange.fetch_my_trades(limit=200)
            # Group by buy/sell to estimate realized PnL
            # Simple approach: sum all sell proceeds minus buy costs for completed round-trips
            buy_costs = {}
            for t in trades:
                sym = t['symbol']
                cost = t['cost'] or (t['amount'] * t['price'])
                fee = t['fee']['cost'] if t.get('fee') else 0
                if t['side'] == 'buy':
                    if sym not in buy_costs:
                        buy_costs[sym] = []
                    buy_costs[sym].append({'cost': cost, 'fee': fee, 'amount': t['amount']})
                elif t['side'] == 'sell':
                    if sym in buy_costs and buy_costs[sym]:
                        buy = buy_costs[sym].pop(0)
                        sell_net = cost - fee
                        buy_total = buy['cost'] + buy['fee']
                        realized_pnl += sell_net - buy_total
        except Exception as e:
            logging.warning(f"Could not fetch trade history: {e}")

        portfolio_value = eur_free + total_current_value

        return {
            'portfolio_value': round(portfolio_value, 2),
            'eur_balance': round(eur_free, 2),
            'total_current_value': round(total_current_value, 2),
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
        import json as _json
        status_path = Path(__file__).parent / 'logs' / 'bot_status.json'
        if not status_path.exists():
            # Try parent trading-bot directory
            status_path = Path(__file__).parent.parent / 'trading-bot' / 'logs' / 'bot_status.json'
        if status_path.exists():
            with open(status_path) as f:
                return _json.load(f).get('iteration')
    except Exception:
        pass
    return None

def get_trading_settings():
    """Read trading mode and scalping profit from user settings."""
    try:
        import json as _json
        for p in [
            Path(__file__).parent / 'config' / 'user_settings.json',
            Path(__file__).parent.parent / 'trading-bot' / 'config' / 'user_settings.json',
        ]:
            if p.exists():
                with open(p) as f:
                    s = _json.load(f)
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
            "error": "Portfolio konnte nicht geladen werden."
        }), 500

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090)
