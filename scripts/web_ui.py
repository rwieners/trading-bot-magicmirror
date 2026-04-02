
import os
import sys
import subprocess
import time
import threading
from flask import Flask, render_template, jsonify, request, Response
from flasgger import Swagger
import sqlite3
from datetime import datetime
from pathlib import Path
import json
import atexit
import logging
from functools import wraps
from dotenv import load_dotenv
load_dotenv()

PID_FILE = '/tmp/broker_flask.pid'


# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(SCRIPT_DIR, 'templates')

app = Flask(__name__, template_folder=TEMPLATES_DIR)

# Single instance enforcement
def check_single_instance():
  if os.path.exists(PID_FILE):
    try:
      with open(PID_FILE, 'r') as f:
        pid = int(f.read().strip())
      # Check if process is running
      if pid > 0:
        try:
          os.kill(pid, 0)
          print(f"Flask läuft bereits mit PID {pid}. Beende Start.")
          sys.exit(1)
        except OSError:
          pass
    except Exception:
      pass
  with open(PID_FILE, 'w') as f:
    f.write(str(os.getpid()))
  atexit.register(lambda: os.path.exists(PID_FILE) and os.remove(PID_FILE))

check_single_instance()

# API route for manual sync
@app.route('/api/sync_kraken', methods=['POST'])
def api_sync_kraken():
    """
    Synchronize holdings from Kraken and update trades database.
    Kraken is always the data master.
    """
    try:
      # Open DB connection
      conn = get_db_connection()
      cursor = conn.cursor()
      # Hole alle offenen Trades (mit Mengen für Vergleich)
      cursor.execute('SELECT id, symbol, entry_price, entry_size, entry_time FROM trades WHERE status = "OPEN" OR status IS NULL')
      open_trades = cursor.fetchall()
      open_symbols = {t['symbol'] for t in open_trades}
      # Gruppiere offene Trades nach Symbol: Gesamt-EUR und Liste
      open_by_symbol = {}
      for t in open_trades:
        sym = t['symbol']
        if sym not in open_by_symbol:
          open_by_symbol[sym] = {'total_eur': 0, 'trades': []}
        open_by_symbol[sym]['total_eur'] += t['entry_size']
        open_by_symbol[sym]['trades'].append(t)

      # Setup Kraken API
      if not ccxt or not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Kraken API not configured'}), 400
      exchange = ccxt.kraken({
        'apiKey': KRAKEN_API_KEY,
        'secret': KRAKEN_API_SECRET,
        'enableRateLimit': True
      })
      exchange.load_markets()

      # Dynamisch alle erlaubten Symbole aus der globalen Whitelist übernehmen
      from config.settings import ALLOWED_COINS
      allowed_symbols = list(ALLOWED_COINS.keys())
      holdings = {}
      all_balances = exchange.fetch_balance()
      for symbol in allowed_symbols:
        base = symbol.split('/')[0]
        balance = all_balances.get(base, {})
        amount = balance.get('total', 0)
        if amount > 0:
          ticker = exchange.fetch_ticker(symbol)
          entry_price = ticker['last']
          holdings[symbol] = {
            'amount': amount,
            'entry_price': entry_price
          }

      # Schließe Trades für Coins, die nicht mehr auf Kraken gehalten werden
      for trade in open_trades:
        if trade['symbol'] not in holdings:
          # Skip trades that already have exit_price (bot's _execute_sell was faster)
          cursor.execute('SELECT exit_price FROM trades WHERE id = ?', (trade['id'],))
          existing = cursor.fetchone()
          if existing and existing['exit_price']:
            continue
          # Fetch current price for real exit data
          try:
            ticker = exchange.fetch_ticker(trade['symbol'])
            exit_price = ticker['last']
          except Exception:
            exit_price = trade.get('entry_price', 0)
          coin_qty = trade['entry_size'] / trade['entry_price'] if trade.get('entry_price') else 0
          cursor.execute(
            'UPDATE trades SET status = "CLOSED_MANUAL_SYNC", exit_time = ?, exit_price = ?, exit_size = ? WHERE id = ?',
            (int(datetime.now().timestamp()), exit_price, coin_qty, trade['id'])
          )

      # Importiere neue und zusätzliche Kraken-Bestände als offene Trades
      MIN_POSITION_EUR = 0.50
      for symbol, data in holdings.items():
        coin_amount = data['amount']
        entry_price = data['entry_price']
        kraken_total_eur = coin_amount * entry_price

        if kraken_total_eur < MIN_POSITION_EUR:
          continue

        if symbol not in open_symbols:
          # Komplett neue Position
          cursor.execute('SELECT entry_time FROM trades WHERE symbol = ? ORDER BY entry_time ASC LIMIT 1', (symbol,))
          row = cursor.fetchone()
          entry_time = row['entry_time'] if row and row['entry_time'] else int(datetime.now().timestamp())
          cursor.execute('INSERT INTO trades (symbol, entry_price, entry_size, entry_value, entry_time, status, reason) VALUES (?, ?, ?, ?, ?, "OPEN", "SYNC_KRAKEN")',
            (symbol, entry_price, kraken_total_eur, kraken_total_eur, entry_time))
        else:
          # Position existiert — prüfe ob Kraken mehr Coins hat
          db_total_eur = open_by_symbol.get(symbol, {}).get('total_eur', 0)
          db_total_coins = db_total_eur / entry_price if entry_price > 0 else 0
          diff_coins = coin_amount - db_total_coins
          diff_eur = diff_coins * entry_price

          if diff_eur >= MIN_POSITION_EUR:
            # Zusätzlicher Kauf erkannt — neuen Trade anlegen
            cursor.execute('INSERT INTO trades (symbol, entry_price, entry_size, entry_value, entry_time, status, reason) VALUES (?, ?, ?, ?, ?, "OPEN", "SYNC_KRAKEN_ADDITIONAL")',
              (symbol, entry_price, diff_eur, diff_eur, int(datetime.now().timestamp())))

      conn.commit()
      conn.close()
      return jsonify({'status': 'success', 'message': 'Bestände von Kraken synchronisiert.'})
    except Exception as e:
      if 'conn' in locals():
        try:
          conn.close()
        except:
          pass
      return jsonify({'status': 'error', 'message': str(e)}), 500
#!/usr/bin/env python3
"""
Web UI for Trade Monitoring - Flask Dashboard
Real-time trade visualization with auto-refresh
"""
from flask import Flask, render_template, jsonify, request, Response
from flasgger import Swagger
import sqlite3
from datetime import datetime
from pathlib import Path
import json
import os
import sys
import logging
from functools import wraps
from dotenv import load_dotenv
load_dotenv()

# Add parent directory to path to import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import KRAKEN_API_KEY, KRAKEN_API_SECRET

try:
    import ccxt
except ImportError:
    ccxt = None


# (Doppelte Initialisierung entfernt)

# Swagger Configuration
swagger_config = {
    "headers": [],
    "specs": [
        {
            "endpoint": "apispec",
            "route": "/apispec.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/api/docs"
}

swagger_template = {
    "info": {
        "title": "Crypto Trading Bot API",
        "description": "REST API for the autonomous cryptocurrency trading bot",
        "version": "1.0.0",
        "contact": {
            "name": "Trading Bot"
        }
    },
    "basePath": "/",
    "schemes": ["http"],
    "tags": [
        {"name": "Portfolio", "description": "Portfolio and position management"},
        {"name": "Trades", "description": "Trade history and statistics"},
        {"name": "System", "description": "System health and monitoring"},
        {"name": "Market Data", "description": "Price and market data"}
    ]
}

swagger = Swagger(app, config=swagger_config, template=swagger_template)

# Swagger UI Basic Auth Protection
SWAGGER_USERNAME = os.getenv('SWAGGER_USERNAME', 'admin')
SWAGGER_PASSWORD = os.getenv('SWAGGER_PASSWORD', '')
if not SWAGGER_PASSWORD:
    logging.warning('SWAGGER_PASSWORD not set — Swagger UI auth disabled')

def check_swagger_auth(username, password):
    """Check if username/password combination is valid for Swagger UI"""
    return username == SWAGGER_USERNAME and password == SWAGGER_PASSWORD

@app.before_request
def protect_swagger():
    """Require Basic Auth for Swagger UI routes"""
    swagger_routes = ['/api/docs', '/apispec.json', '/flasgger_static']
    if any(request.path.startswith(route) for route in swagger_routes):
        auth = request.authorization
        if not auth or not check_swagger_auth(auth.username, auth.password):
            return Response(
                'Swagger UI requires authentication',
                401,
                {'WWW-Authenticate': 'Basic realm="Swagger UI"'}
            )

# Disable caching for development
@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

DB_PATH = '/Users/rene/dev/Broker/logs/trades.db'

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def format_timestamp(ts):
    """Convert Unix timestamp or datetime string to readable format"""
    if not ts:
        return "N/A"
    # Handle string timestamps (e.g., "2026-02-25 14:57:57")
    if isinstance(ts, str):
        try:
            return ts  # Already formatted
        except:
            return "N/A"
    # Handle Unix timestamps (int or float)
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return "N/A"

def get_kraken_eur_balance():
    """Get available EUR balance from Kraken"""
    if not ccxt or not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
      return None
    try:
      # Always create a new instance per call
      exchange = ccxt.kraken({
        'apiKey': KRAKEN_API_KEY,
        'secret': KRAKEN_API_SECRET,
        'enableRateLimit': True,
        'timeout': 5000,
      })
      balance = exchange.fetch_balance()
      return balance.get('EUR', {}).get('free', 0)
    except Exception as e:
      logging.warning(f"Could not fetch Kraken balance: {e}")
      return None

def get_kraken_exchange():
    """Get authenticated Kraken exchange instance"""
    if not ccxt or not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
        return None
    return ccxt.kraken({
        'apiKey': KRAKEN_API_KEY,
        'secret': KRAKEN_API_SECRET,
        'enableRateLimit': True,
        'timeout': 10000,
    })

@app.route('/api/open_orders')
def api_open_orders():
    """
    Get currently open orders from Kraken
    ---
    tags:
      - Orders
    responses:
      200:
        description: List of open orders on Kraken exchange
    """
    try:
        exchange = get_kraken_exchange()
        if not exchange:
            return jsonify({'status': 'error', 'message': 'Kraken API not configured'}), 500

        open_orders = exchange.fetch_open_orders()

        orders = []
        for order in open_orders:
            orders.append({
                'id': order.get('id'),
                'symbol': order.get('symbol'),
                'side': order.get('side'),  # 'buy' or 'sell'
                'type': order.get('type'),  # 'limit', 'market', etc.
                'price': order.get('price') or order.get('average') or 0,
                'amount': order.get('amount', 0),
                'filled': order.get('filled', 0),
                'remaining': order.get('remaining', 0),
                'cost': order.get('cost', 0),
                'status': order.get('status'),
                'timestamp': order.get('datetime', ''),
            })

        return jsonify({
            'status': 'success',
            'open_orders': orders,
            'count': len(orders)
        })
    except Exception as e:
        logging.error(f"Open orders API error: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/')
def dashboard():
    """Main dashboard page"""
    return render_template('dashboard.html')

@app.route('/api/trades')
def api_trades():
    """
    Get all trades (open and closed)
    ---
    tags:
      - Trades
    responses:
      200:
        description: List of all trades with statistics
        schema:
          type: object
          properties:
            open_trades:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: integer
                    example: 3
                  symbol:
                    type: string
                    example: ETH/EUR
                  entry_price:
                    type: number
                    example: 1650.78
                  entry_value:
                    type: number
                    example: 30.0
                  entry_size:
                    type: number
                    example: 30.0
                  entry_fee:
                    type: number
                    example: 0.05
                  entry_time:
                    type: string
                    example: "2026-02-22 21:30:18"
                  current_price:
                    type: number
                    example: 1571.68
                  current_value:
                    type: number
                    example: 28.56
                  unrealized_pnl:
                    type: number
                    example: -1.44
                  unrealized_pnl_pct:
                    type: number
                    example: -4.79
                  confidence:
                    type: number
                    example: 1.0
                  status:
                    type: string
                    example: OPEN
                  reason:
                    type: string
                    example: ENTRY
            closed_trades:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: integer
                  symbol:
                    type: string
                  entry_price:
                    type: number
                  exit_price:
                    type: number
                  pnl:
                    type: number
                  pnl_pct:
                    type: number
                  status:
                    type: string
                    enum: [CLOSED_PROFIT, CLOSED_LOSS]
      500:
        description: Server error
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                id, symbol, entry_price, entry_size, entry_time,
                exit_price, exit_time, pnl, pnl_pct, entry_fee,
                exit_fee, status, model_confidence, reason, entry_value
            FROM trades
            ORDER BY entry_time DESC
        ''')
        
        trades = cursor.fetchall()
        
        # Get live prices for open trades
        live_prices = {}
        prices_are_live = True
        if ccxt and trades:
            try:
                # Use public API without authentication with timeout
                exchange = ccxt.kraken({
                    'enableRateLimit': True,
                    'timeout': 5000  # 5 second timeout
                })
                symbols_to_fetch = set()
                for trade in trades:
                    if trade['status'] in ('OPEN', None):
                        symbols_to_fetch.add(trade['symbol'])
                
                # Fetch prices for all unique symbols
                for symbol in symbols_to_fetch:
                    try:
                        ticker = exchange.fetch_ticker(symbol)
                        live_prices[symbol] = ticker['last']
                    except Exception as e:
                        logging.warning(f"Failed to fetch {symbol}: {str(e)}")
                        continue
            except Exception as e:
                logging.warning(f"Failed to initialize Kraken exchange: {str(e)}")
                prices_are_live = False
        
        # Separate trades
        open_trades = []
        closed_trades = []
        
        for trade in trades:
            trade_dict = {
                'id': trade['id'],
                'symbol': trade['symbol'],
                'entry_price': round(trade['entry_price'], 6) if trade['entry_price'] < 1 else round(trade['entry_price'], 2),
                'entry_size': round(trade['entry_size'], 6),
                'entry_time': format_timestamp(trade['entry_time']),
                'entry_fee': round(trade['entry_fee'], 2) if trade['entry_fee'] else 0,
                'confidence': round(trade['model_confidence'], 2) if trade['model_confidence'] else 0,
                'status': trade['status'],
                'reason': trade['reason']
            }
            
            if trade['status'] in ('OPEN', None):
                # Calculate actual coin quantity from entry data
                entry_value_eur = trade['entry_size']  # EUR invested
                coin_qty = entry_value_eur / trade['entry_price'] if trade['entry_price'] > 0 else 0
                trade_dict['entry_value'] = round(entry_value_eur, 2)
                
                # Get current price — flag if stale (falling back to entry price)
                if trade['symbol'] in live_prices:
                    current_price = live_prices[trade['symbol']]
                    trade_dict['price_stale'] = False
                else:
                    current_price = trade['entry_price']
                    trade_dict['price_stale'] = True
                trade_dict['current_price'] = round(current_price, 6) if current_price < 1 else round(current_price, 2)
                
                # Calculate current value from actual coin quantity × current price
                current_value = coin_qty * current_price
                trade_dict['current_value'] = round(current_value, 2)
                trade_dict['unrealized_pnl'] = round(current_value - entry_value_eur, 2)
                trade_dict['unrealized_pnl_pct'] = round(((current_value - entry_value_eur) / entry_value_eur * 100), 2) if entry_value_eur > 0 else 0
                
                open_trades.append(trade_dict)
            else:
                # Filter out CLOSED_BREAK_EVEN dust cleanup trades (but keep ghost-closed trades)
                if trade['status'] == 'CLOSED_BREAK_EVEN' and trade['reason'] != 'Ghost position: coins not on exchange':
                    continue
                trade_dict['exit_price'] = round(trade['exit_price'], 2) if trade['exit_price'] else 0
                trade_dict['exit_time'] = format_timestamp(trade['exit_time'])
                trade_dict['exit_fee'] = round(trade['exit_fee'], 2) if trade['exit_fee'] else 0
                trade_dict['pnl'] = round(trade['pnl'], 2) if trade['pnl'] else 0
                trade_dict['pnl_pct'] = round(trade['pnl_pct'], 2) if trade['pnl_pct'] else 0
                closed_trades.append(trade_dict)
        
        conn.close()
        
        # Calculate stats
        total_pnl = sum(t['pnl'] for t in closed_trades if 'pnl' in t)
        winning = len([t for t in closed_trades if t.get('pnl', 0) > 0])
        losing = len([t for t in closed_trades if t.get('pnl', 0) < 0])
        win_rate = (winning / (winning + losing)) * 100 if (winning + losing) > 0 else 0
        
        stats = {
            'total_trades': len(open_trades) + len(closed_trades),
            'open_positions': len(open_trades),
            'closed_trades': len(closed_trades),
            'total_pnl': round(total_pnl, 2),
            'winning_trades': winning,
            'losing_trades': losing,
            'win_rate': round(win_rate, 1),
            'last_update': datetime.now().strftime("%H:%M:%S")
        }
        
        return jsonify({
            'status': 'success',
            'open_trades': open_trades,
            'closed_trades': closed_trades,
            'stats': stats,
            'prices_live': prices_are_live
        })
    
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/stats')
def api_stats():
    """
    Get quick trading statistics
    ---
    tags:
      - Trades
    responses:
      200:
        description: Quick stats summary
        schema:
          type: object
          example:
            open_positions: 5
            total_pnl: 0
            wins: 0
            losses: 0
            timestamp: "2026-02-23T20:37:03.987486"
          properties:
            open_positions:
              type: integer
              description: Number of open positions
              example: 5
            total_pnl:
              type: number
              description: Total realized P&L in EUR
              example: 0
            wins:
              type: integer
              description: Number of winning trades
              example: 0
            losses:
              type: integer
              description: Number of losing trades
              example: 0
            timestamp:
              type: string
              description: ISO timestamp
              example: "2026-02-23T20:37:03.987486"
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) as count FROM trades WHERE status = "OPEN" OR status IS NULL')
        open_count = cursor.fetchone()['count']
        
        cursor.execute('SELECT SUM(pnl) as total FROM trades WHERE status LIKE "CLOSED%"')
        total_pnl_row = cursor.fetchone()
        total_pnl = total_pnl_row['total'] if total_pnl_row['total'] else 0
        
        cursor.execute('SELECT COUNT(*) as count FROM trades WHERE status = "CLOSED_PROFIT"')
        wins = cursor.fetchone()['count']
        
        cursor.execute('SELECT COUNT(*) as count FROM trades WHERE status = "CLOSED_LOSS"')
        losses = cursor.fetchone()['count']
        
        conn.close()
        
        return jsonify({
            'open_positions': open_count,
            'total_pnl': round(total_pnl, 2),
            'wins': wins,
            'losses': losses,
            'timestamp': datetime.now().isoformat()
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/portfolio')
def api_portfolio():
    """
    Get live portfolio with real-time prices
    ---
    tags:
      - Portfolio
    responses:
      200:
        description: Portfolio positions with live Kraken prices
        schema:
          type: object
          properties:
            status:
              type: string
              example: success
            portfolio:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: integer
                    example: 2
                  symbol:
                    type: string
                    example: BTC/EUR
                  entry_price:
                    type: number
                    example: 57223.9
                  entry_size:
                    type: number
                    example: 0.000524
                  entry_time:
                    type: string
                    example: "2026-02-22 21:20:17"
                  entry_value:
                    type: number
                    example: 30.0
                  current_price:
                    type: number
                    example: 54571.5
                  current_value:
                    type: number
                    example: 28.61
                  unrealized_pnl:
                    type: number
                    example: -1.39
                  unrealized_pnl_pct:
                    type: number
                    example: -4.64
                  confidence:
                    type: number
                    example: 1.0
                  price_error:
                    type: boolean
                    example: false
            summary:
              type: object
              properties:
                positions:
                  type: integer
                  example: 5
                total_balance:
                  type: number
                  example: 300.0
                total_invested:
                  type: number
                  example: 119.9
                available_cash:
                  type: number
                  example: 180.1
                total_current_value:
                  type: number
                  example: 114.32
                total_unrealized_pnl:
                  type: number
                  example: -5.58
                total_unrealized_pnl_pct:
                  type: number
                  example: -4.65
      500:
        description: Server error
    """
    try:
      conn = get_db_connection()
      try:
        cursor = conn.cursor()
        # Get all open trades
        cursor.execute('''
          SELECT id, symbol, entry_price, entry_size, entry_value, entry_time, model_confidence, entry_fee
          FROM trades
          WHERE status = 'OPEN' OR status IS NULL
          ORDER BY entry_time DESC
        ''')
        open_trades = cursor.fetchall()
      finally:
        conn.close()

      # Fetch live prices
      live_prices = {}
      if ccxt and open_trades:
        try:
          exchange = ccxt.kraken({
            'enableRateLimit': True,
            'timeout': 5000
          })
          symbols_to_fetch = set(trade['symbol'] for trade in open_trades)
          for symbol in symbols_to_fetch:
            try:
              ticker = exchange.fetch_ticker(symbol)
              live_prices[symbol] = ticker['last']
            except Exception as e:
              logging.warning(f"Failed to fetch {symbol}: {str(e)}")
        except Exception as e:
          logging.warning(f"Failed to initialize exchange: {str(e)}")

      # Build portfolio with live prices
      portfolio_items = []
      total_invested = 0
      total_current_value = 0
      total_unrealized_pnl = 0
      MIN_DISPLAY_EUR = 1.00  # Skip dust positions below €1.00
      FEE_RATE = 0.0016  # Kraken maker fee rate
      for trade in open_trades:
        entry_value_eur = trade['entry_size']  # entry_size contains EUR amount
        # Skip dust/ghost positions that shouldn't be displayed
        if entry_value_eur < MIN_DISPLAY_EUR:
          continue
        entry_fee = trade['entry_fee'] or 0
        total_invested += entry_value_eur
        current_price = live_prices.get(trade['symbol'], trade['entry_price'])
        price_error = trade['symbol'] not in live_prices
        if trade['entry_price'] > 0:
          current_value = entry_value_eur * (current_price / trade['entry_price'])
        else:
          current_value = entry_value_eur
        # Net unrealized P&L: subtract entry fee (already paid) and estimated exit fee (would be paid on sell)
        estimated_exit_fee = current_value * FEE_RATE
        unrealized_pnl = current_value - entry_value_eur - entry_fee - estimated_exit_fee
        unrealized_pnl_pct = (unrealized_pnl / entry_value_eur * 100) if entry_value_eur > 0 else 0
        total_current_value += current_value
        total_unrealized_pnl += unrealized_pnl
        portfolio_items.append({
          'id': trade['id'],
          'symbol': trade['symbol'],
          'entry_price': round(trade['entry_price'], 2),
          'current_price': round(current_price, 2),
          'entry_size': round(entry_value_eur / trade['entry_price'], 6) if trade['entry_price'] > 0 else 0,
          'entry_value': round(entry_value_eur, 2),
          'current_value': round(current_value, 2),
          'unrealized_pnl': round(unrealized_pnl, 2),
          'unrealized_pnl_pct': round(unrealized_pnl_pct, 2),
          'entry_time': format_timestamp(trade['entry_time']),
          'confidence': round(trade['model_confidence'], 2) if trade['model_confidence'] else 0,
          'price_error': price_error
        })
      total_unrealized_pnl_pct = (total_unrealized_pnl / total_invested * 100) if total_invested > 0 else 0
      # Get realized P&L and total fees from closed trades
      conn = get_db_connection()
      try:
        cursor = conn.cursor()
        cursor.execute("""
          SELECT
            COALESCE(SUM(pnl), 0) as total_pnl,
            COALESCE(SUM(COALESCE(entry_fee, 0) + COALESCE(exit_fee, 0)), 0) as total_fees
          FROM trades WHERE status LIKE 'CLOSED%'
        """)
        row = cursor.fetchone()
        realized_pnl = row[0]
        total_closed_fees = row[1]
      finally:
        conn.close()
      
      # Get real Kraken balance for available cash
      kraken_balance = get_kraken_eur_balance()
      if kraken_balance is not None:
        available_cash = kraken_balance  # Actual free EUR on Kraken
        total_balance = kraken_balance + total_current_value + realized_pnl  # Free cash + current position values + realized gains
      else:
        # Fallback: use database info
        available_cash = realized_pnl  # Only realized gains/losses if API unavailable
        total_balance = total_invested + realized_pnl  # Approximate total
      # Get model age info
      try:
        from broker.models.lstm_model import ModelManager
        mm = ModelManager()
        model_age_hours = mm.get_model_age_hours()
      except Exception:
        model_age_hours = None
      
      return jsonify({
        'status': 'success',
        'portfolio': portfolio_items,
        'summary': {
          'positions': len(portfolio_items),
          'total_balance': round(total_balance, 2),
          'total_invested': round(total_invested, 2),
          'available_cash': round(available_cash, 2),
          'total_current_value': round(total_current_value, 2),
          'total_unrealized_pnl': round(total_unrealized_pnl, 2),
          'total_unrealized_pnl_pct': round(total_unrealized_pnl_pct, 2),
          'total_realized_pnl': round(realized_pnl, 2),
          'total_fees': round(total_closed_fees, 2),
          'model_age_hours': round(model_age_hours, 1) if model_age_hours is not None else None,
          'timestamp': datetime.now().strftime("%H:%M:%S")
        }
      })
    except Exception as e:
      logging.error(f"Portfolio API error: {str(e)}")
      return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/health')
def api_health():
    """
    Get bot and monitor health status
    ---
    tags:
      - System
    responses:
      200:
        description: Health status of bot and monitor processes
        schema:
          type: object
          example:
            status: success
            bot:
              running: true
              last_log: "2026-02-23 20:37:12,309 - __main__ - INFO - [ITERATION 428] Fetching ticker for DOGE/EUR..."
            monitor:
              running: true
              last_log: "[2026-02-23 14:31:54] ✅ Bot is running (recovered or started)"
            timestamp: "20:37:12"
          properties:
            status:
              type: string
              example: success
            bot:
              type: object
              properties:
                running:
                  type: boolean
                  example: true
                last_log:
                  type: string
                  example: "2026-02-23 20:37:12,309 - __main__ - INFO - [ITERATION 428] Fetching ticker..."
            monitor:
              type: object
              properties:
                running:
                  type: boolean
                  example: true
                last_log:
                  type: string
                  example: "[2026-02-23 14:31:54] ✅ Bot is running"
            timestamp:
              type: string
              example: "20:37:12"
      500:
        description: Server error
    """
    try:
        import subprocess
        
        # Überprüfe ob Bot läuft
        result = subprocess.run(
            ["pgrep", "-f", "python3 -m broker.bot"],
            capture_output=True,
            text=True
        )
        bot_running = result.returncode == 0
        
        # Überprüfe ob Monitor läuft
        result_monitor = subprocess.run(
            ["pgrep", "-f", "bot_monitor.py"],
            capture_output=True,
            text=True
        )
        monitor_running = result_monitor.returncode == 0
        
        # Lese letzte Log-Einträge
        bot_log_path = '/Users/rene/dev/Broker/logs/bot.log'
        monitor_log_path = '/Users/rene/dev/Broker/logs/monitor.log'
        
        bot_last_log = "N/A"
        monitor_last_log = "N/A"
        
        try:
            if Path(bot_log_path).exists():
                with open(bot_log_path, 'r') as f:
                    lines = f.readlines()
                    bot_last_log = lines[-1].strip() if lines else "No logs"
        except:
            pass
        
        try:
            if Path(monitor_log_path).exists():
                with open(monitor_log_path, 'r') as f:
                    lines = f.readlines()
                    monitor_last_log = lines[-1].strip() if lines else "No logs"
        except:
            pass
        
        # Read health checker status file
        health_checker_status = None
        health_status_path = '/Users/rene/dev/Broker/logs/health_status.json'
        try:
            if Path(health_status_path).exists():
                with open(health_status_path, 'r') as f:
                    health_checker_status = json.load(f)
        except:
            pass
        
        return jsonify({
            'status': 'success',
            'bot': {
                'running': bot_running,
                'last_log': bot_last_log
            },
            'monitor': {
                'running': monitor_running,
                'last_log': monitor_last_log
            },
            'health_checker': health_checker_status,
            'timestamp': datetime.now().strftime("%H:%M:%S")
        })
    
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# Settings file path
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'user_settings.json')

def load_user_settings():
    """Load user settings from JSON file"""
    default_settings = {
        'max_position_size': 10.0,
        'max_loss_cutoff': -40.0,      # Stop-loss in % (negative)
        'min_profit_target': 5.0,      # Profit target in %
        'check_interval': 60,          # Signal check interval in seconds
        'critical_balance_level': 5.0, # EUR - Emergency stop below this
        'warning_balance_level': 20.0, # EUR - Warning below this
        'trading_mode': 'conservative', # conservative, aggressive or scalping
        'portfolio_drawdown_limit': -10.0  # Max portfolio drawdown in % (negative)
    }
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                saved = json.load(f)
                return {**default_settings, **saved}
    except Exception as e:
        logging.error(f"Error loading settings: {e}")
    return default_settings

def save_user_settings(settings):
    """Save user settings to JSON file"""
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception as e:
        logging.error(f"Error saving settings: {e}")
        return False


@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    """
    Get current user settings
    ---
    tags:
      - Settings
    responses:
      200:
        description: Current settings
        schema:
          type: object
          properties:
            max_position_size:
              type: number
              description: Investment amount per trade in EUR
              example: 10.0
            max_loss_cutoff:
              type: number
              description: Stop-loss percentage (negative value)
              example: -40.0
            min_profit_target:
              type: number
              description: Profit target percentage
              example: 5.0
            check_interval:
              type: integer
              description: Signal check interval in seconds
              example: 60
            critical_balance_level:
              type: number
              description: Critical balance threshold in EUR (emergency stop)
              example: 5.0
            warning_balance_level:
              type: number
              description: Warning balance threshold in EUR
              example: 20.0
    """
    settings = load_user_settings()
    return jsonify(settings)


@app.route('/api/settings', methods=['POST'])
def api_save_settings():
    """
    Save user settings
    ---
    tags:
      - Settings
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            max_position_size:
              type: number
              description: Investment amount per trade in EUR (0-100)
              example: 15.0
            max_loss_cutoff:
              type: number
              description: Stop-loss percentage as negative value (-80 to -5)
              example: -40.0
            min_profit_target:
              type: number
              description: Profit target percentage (1-50)
              example: 5.0
            check_interval:
              type: integer
              description: Signal check interval in seconds (30-300)
              example: 60
            critical_balance_level:
              type: number
              description: Critical balance threshold in EUR (0+)
              example: 5.0
            warning_balance_level:
              type: number
              description: Warning balance threshold in EUR (0+)
              example: 20.0
    responses:
      200:
        description: Settings saved successfully
      400:
        description: Invalid settings
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400
        
        # Merge incoming fields with existing settings before validation
        settings = load_user_settings()
        settings.update(data)

        # Validate and update max_position_size
        if 'max_position_size' in data:
          value = float(data['max_position_size'])
          if 0 <= value <= 100:
            settings['max_position_size'] = value
          else:
            return jsonify({'status': 'error', 'message': 'Investition muss zwischen 0 und 100 liegen'}), 400

        # Validate and update max_loss_cutoff (stop-loss)
        if 'max_loss_cutoff' in data:
          value = float(data['max_loss_cutoff'])
          # Ensure it's stored as negative
          if value > 0:
            value = -value
          if -80 <= value <= -5:
            settings['max_loss_cutoff'] = value
          else:
            return jsonify({'status': 'error', 'message': 'Stop-Loss muss zwischen 5% und 80% liegen'}), 400

        # Validate and update min_profit_target
        if 'min_profit_target' in data:
          value = float(data['min_profit_target'])
          if 1 <= value <= 50:
            settings['min_profit_target'] = value
          else:
            return jsonify({'status': 'error', 'message': 'Gewinnziel muss zwischen 1% und 50% liegen'}), 400

        # Validate and update check_interval
        if 'check_interval' in data:
          value = int(data['check_interval'])
          if 30 <= value <= 300:
            settings['check_interval'] = value
          else:
            return jsonify({'status': 'error', 'message': 'Prüfintervall muss zwischen 30 und 300 Sekunden liegen'}), 400

        # Validate and update critical_balance_level
        if 'critical_balance_level' in data:
          value = float(data['critical_balance_level'])
          if value >= 0:
            settings['critical_balance_level'] = value
          else:
            return jsonify({'status': 'error', 'message': 'Kritischer Schwellwert darf nicht negativ sein'}), 400

        # Validate and update warning_balance_level
        if 'warning_balance_level' in data:
          value = float(data['warning_balance_level'])
          if value >= 0:
            settings['warning_balance_level'] = value
          else:
            return jsonify({'status': 'error', 'message': 'Warn-Schwellwert darf nicht negativ sein'}), 400
        
        # Validate and update trading_mode
        if 'trading_mode' in data:
            mode = str(data['trading_mode']).lower()
            if mode in ('conservative', 'aggressive', 'scalping'):
                settings['trading_mode'] = mode
            else:
                return jsonify({'status': 'error', 'message': 'Trading-Modus muss conservative, aggressive oder scalping sein'}), 400
        
        # Validate and update portfolio_drawdown_limit
        if 'portfolio_drawdown_limit' in data:
            value = float(data['portfolio_drawdown_limit'])
            # Ensure it's stored as negative
            if value > 0:
                value = -value
            if -50 <= value <= -5:
                settings['portfolio_drawdown_limit'] = value
            else:
                return jsonify({'status': 'error', 'message': 'Max. Drawdown muss zwischen 5% und 50% liegen'}), 400
        
        # Validate and update scalping_profit_abs
        if 'scalping_profit_abs' in data:
          try:
            value = float(data['scalping_profit_abs'])
            if 0.01 <= value <= 10.0:
              settings['scalping_profit_abs'] = value
            else:
              return jsonify({'status': 'error', 'message': 'Scalping-Gewinn muss zwischen 0.01€ und 10€ liegen'}), 400
          except Exception:
            return jsonify({'status': 'error', 'message': 'Ungültiger Wert für Scalping-Gewinn'}), 400

        # Always save settings after merging and validation
        if save_user_settings(settings):
            return jsonify({'status': 'success', 'settings': settings})
        else:
            return jsonify({'status': 'error', 'message': 'Failed to save settings'}), 500

    except ValueError as e:
        return jsonify({'status': 'error', 'message': 'Invalid number format'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/ohlcv')
def api_ohlcv():
    """
    Get OHLCV price data for portfolio symbols
    ---
    tags:
      - Market Data
    parameters:
      - name: hours
        in: query
        type: integer
        required: false
        default: 48
        description: Number of hours to fetch (1-168, default 48)
        example: 24
    responses:
      200:
        description: OHLCV data for the requested time range
        schema:
          type: object
          example:
            status: success
            data:
              BTC/EUR:
                timestamps: [1740236400000, 1740240000000, 1740243600000]
                prices: [57195.1, 57050.0, 56890.5]
              ETH/EUR:
                timestamps: [1740236400000, 1740240000000, 1740243600000]
                prices: [1650.78, 1645.20, 1635.50]
            entries:
              BTC/EUR: 1740254011000
              ETH/EUR: 1740254418000
            timestamp: "20:37:12"
          properties:
            status:
              type: string
              example: success
            data:
              type: object
              description: Symbol -> price data mapping (BTC/EUR, ETH/EUR, SOL/EUR, etc.)
              additionalProperties:
                type: object
                properties:
                  timestamps:
                    type: array
                    items:
                      type: integer
                    description: Unix timestamps in milliseconds
                  prices:
                    type: array
                    items:
                      type: number
                    description: Close prices
            entries:
              type: object
              description: Symbol -> entry time mapping (Unix ms)
              additionalProperties:
                type: integer
            timestamp:
              type: string
              example: "20:37:12"
      500:
        description: Server error
    """
    try:
        if not ccxt:
            return jsonify({'status': 'error', 'message': 'CCXT not installed'}), 500
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get all unique symbols and their entry times from open trades
        cursor.execute('''
            SELECT symbol, entry_time FROM trades 
            WHERE status = 'OPEN' OR status IS NULL
        ''')
        trades = cursor.fetchall()
        
        # Get closed trades with exit times (for exit markers on chart)
        cursor.execute('''
            SELECT symbol, exit_time, pnl FROM trades 
            WHERE status LIKE 'CLOSED%' AND exit_time IS NOT NULL
            ORDER BY exit_time DESC
        ''')
        closed_trades = cursor.fetchall()
        conn.close()
        
        # Build symbol -> entry_time mapping (use earliest if multiple)
        symbol_entries = {}
        for trade in trades:
            symbol = trade['symbol']
            entry_time = trade['entry_time']
            if symbol not in symbol_entries or entry_time < symbol_entries[symbol]:
                symbol_entries[symbol] = entry_time
        
        # Also include symbols from closed trades that have exits in the visible range
        exit_symbols = set()
        for trade in closed_trades:
            exit_symbols.add(trade['symbol'])
        
        # Combine all symbols (open + recently closed)
        symbols = list(set(symbol_entries.keys()) | exit_symbols)
        
        if not symbols:
            return jsonify({'status': 'success', 'data': {}, 'entries': {}})
        
        # Get hours parameter (default 48 hours = 2 days)
        hours = request.args.get('hours', 48, type=int)
        hours = max(1, min(hours, 168))  # Clamp between 1h and 7 days
        
        exchange = ccxt.kraken({
            'enableRateLimit': True,
            'timeout': 10000
        })
        
        ohlcv_data = {}
        since = int((datetime.now().timestamp() - hours * 60 * 60) * 1000)
        
        # Determine timeframe based on hours requested
        if hours <= 2:
            timeframe = '5m'
            limit = hours * 12  # 12 candles per hour at 5m
        elif hours <= 24:
            timeframe = '15m'
            limit = hours * 4  # 4 candles per hour at 15m
        elif hours <= 72:
            timeframe = '1h'
            limit = hours  # 1 candle per hour
        else:
            timeframe = '4h'
            limit = hours // 4  # 1 candle per 4 hours
        
        for symbol in symbols:
            try:
                # Fetch OHLCV data for requested range
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
                
                # Format: [[timestamp, open, high, low, close, volume], ...]
                ohlcv_data[symbol] = {
                    'timestamps': [candle[0] for candle in ohlcv],
                    'prices': [candle[4] for candle in ohlcv]  # close prices
                }
            except Exception as e:
                logging.warning(f"Failed to fetch OHLCV for {symbol}: {e}")
                ohlcv_data[symbol] = {'timestamps': [], 'prices': []}
        
        # Convert entry times to milliseconds for JS
        entries_ms = {symbol: int(ts * 1000) for symbol, ts in symbol_entries.items()}
        
        # Build exits list: [{symbol, time, pnl}, ...] within visible range
        since_sec = since / 1000  # Convert back to seconds for comparison
        exits_list = []
        for trade in closed_trades:
            exit_time = trade['exit_time']
            if exit_time and exit_time >= since_sec:
                exits_list.append({
                    'symbol': trade['symbol'],
                    'time': int(exit_time * 1000),
                    'pnl': round(trade['pnl'], 2) if trade['pnl'] else 0
                })
        
        return jsonify({
            'status': 'success',
            'data': ohlcv_data,
            'entries': entries_ms,
            'exits': exits_list,
            'timestamp': datetime.now().strftime("%H:%M:%S")
        })
    
    except Exception as e:
        logging.error(f"OHLCV API error: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/close-position/<int:trade_id>', methods=['POST'])
def api_close_position(trade_id):
    """
    Close an open position at market price
    ---
    tags:
      - Portfolio
    parameters:
      - name: trade_id
        in: path
        type: integer
        required: true
        description: ID of the trade to close
        example: 3
    responses:
      200:
        description: Position closed successfully
        schema:
          type: object
          example:
            status: success
            trade_id: 3
            symbol: ETH/EUR
            entry_price: 1650.78
            exit_price: 1575.50
            pnl: -1.37
            pnl_pct: -4.56
          properties:
            status:
              type: string
              example: success
            trade_id:
              type: integer
              example: 3
            symbol:
              type: string
              example: ETH/EUR
            entry_price:
              type: number
              example: 1650.78
            exit_price:
              type: number
              example: 1575.50
            pnl:
              type: number
              description: Realized P&L in EUR
              example: -1.37
            pnl_pct:
              type: number
              description: Realized P&L percentage
              example: -4.56
      404:
        description: Trade not found
        schema:
          type: object
          properties:
            status:
              type: string
              example: error
            message:
              type: string
              example: Trade not found
      400:
        description: Trade is not open
        schema:
          type: object
          properties:
            status:
              type: string
              example: error
            message:
              type: string
              example: Trade is not open
      500:
        description: Server error
    """
    try:
        if not ccxt:
            return jsonify({'status': 'error', 'message': 'CCXT not installed'}), 500
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get the trade
        cursor.execute('SELECT * FROM trades WHERE id = ?', (trade_id,))
        trade = cursor.fetchone()
        
        if not trade:
            conn.close()
            return jsonify({'status': 'error', 'message': 'Trade not found'}), 404
        
        if trade['status'] not in ('OPEN', None):
            conn.close()
            return jsonify({'status': 'error', 'message': 'Trade is not open'}), 400
        
        # Get current price - use public API with timeout
        kraken = ccxt.kraken({
            'enableRateLimit': True,
            'timeout': 5000  # 5 second timeout
        })
        ticker = kraken.fetch_ticker(trade['symbol'])
        current_price = ticker['last']
        
        # Calculate actual coin size
        # entry_size is the EUR amount invested
        coin_size = trade['entry_size'] / trade['entry_price'] if trade['entry_price'] > 0 else 0
        
        # Calculate exit values
        exit_value = coin_size * current_price
        # entry_size is the EUR invested (entry_value in DB may be wrong for old trades)
        invested_eur = trade['entry_size']
        pnl = exit_value - invested_eur
        pnl_pct = (pnl / invested_eur * 100) if invested_eur > 0 else 0
        
        # Update database
        exit_time = datetime.now().timestamp()
        
        if pnl >= 0:
            status = 'CLOSED_PROFIT'
        else:
            status = 'CLOSED_LOSS'
        
        cursor.execute('''
            UPDATE trades 
            SET status = ?, exit_price = ?, exit_time = ?, pnl = ?, pnl_pct = ?
            WHERE id = ?
        ''', (status, current_price, exit_time, pnl, pnl_pct, trade_id))
        
        conn.commit()
        conn.close()
        
        # Log the exit
        logger = logging.getLogger('trades')
        logger.info(f"EXIT | Trade #{trade_id} | {trade['symbol']} | Exit Price: €{current_price:.2f} | Size: {coin_size:.6f} | P&L: €{pnl:.2f} ({pnl_pct:.2f}%) | Reason: Manual Close")
        
        return jsonify({
            'status': 'success',
            'trade_id': trade_id,
            'symbol': trade['symbol'],
            'entry_price': trade['entry_price'],
            'exit_price': current_price,
            'pnl': pnl,
            'pnl_pct': pnl_pct
        })
    
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ============================================================
# BOT / SERVICE CONTROL ENDPOINTS
# ============================================================

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_PID_FILE = os.path.join(PROJECT_DIR, 'logs', 'bot.pid')
BOT_LOCK_FILE = os.path.join(PROJECT_DIR, 'logs', 'bot.lock')
VENV_PYTHON = os.path.join(PROJECT_DIR, 'venv', 'bin', 'python3')
VENV_ACTIVATE = os.path.join(PROJECT_DIR, 'venv', 'bin', 'activate')


def _is_process_running(pattern):
    """Check if a process matching the pattern is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def _get_process_pid(pattern):
    """Get PID of a process matching the pattern."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split('\n')
            return int(pids[0]) if pids else None
    except Exception:
        pass
    return None


def _kill_process(pattern, signal_name=None):
    """Kill processes matching the pattern. Returns True if any were killed."""
    try:
        cmd = ["pkill"]
        if signal_name:
            cmd.append(signal_name)
        cmd += ["-f", pattern]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False


def _kill_bot():
    """Reliably kill the bot and caffeinate wrapper. Uses SIGTERM then SIGKILL."""
    # SIGTERM first (graceful)
    _kill_process(r"broker\.bot")
    time.sleep(2)
    # Check if still alive and SIGKILL
    if _is_process_running(r"broker\.bot"):
        _kill_process(r"broker\.bot", "-9")
        time.sleep(1)
    # Clean up lock/pid files
    for f in [BOT_LOCK_FILE, BOT_PID_FILE]:
        if os.path.exists(f):
            os.remove(f)


def _delayed_stop_webui(delay=1.5):
    """Stop the Web UI process after a delay (so HTTP response can be sent first)."""
    def _stop():
        time.sleep(delay)
        # Remove PID file
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        os.kill(os.getpid(), 9)
    t = threading.Thread(target=_stop, daemon=True)
    t.start()


def _delayed_restart_webui(delay=1.5):
    """Restart the Web UI process after a delay (so HTTP response can be sent first)."""
    def _restart():
        time.sleep(delay)
        webui_script = os.path.join(PROJECT_DIR, 'scripts', 'web_ui.py')
        webui_log = os.path.join(PROJECT_DIR, 'logs', 'webui.log')
        env = os.environ.copy()
        env_file = os.path.join(PROJECT_DIR, '.env')
        if os.path.exists(env_file):
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, _, value = line.partition('=')
                        env[key.strip()] = value.strip()
        # Remove PID file so new instance can start
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        log_fd = open(webui_log, 'a')
        subprocess.Popen(
            [VENV_PYTHON, webui_script],
            cwd=PROJECT_DIR,
            stdout=log_fd,
            stderr=log_fd,
            env=env,
            start_new_session=True
        )
        time.sleep(0.5)
        os.kill(os.getpid(), 9)
    t = threading.Thread(target=_restart, daemon=True)
    t.start()


@app.route('/api/services/status')
def api_services_status():
    """
    Get status of all services (bot, monitor)
    ---
    tags:
      - System
    responses:
      200:
        description: Status of all services
    """
    bot_running = _is_process_running(r"broker\.bot")
    bot_pid = _get_process_pid(r"broker\.bot")
    monitor_running = _is_process_running("bot_monitor.py")
    monitor_pid = _get_process_pid("bot_monitor.py")
    # WebUI is obviously running if it can respond to this request
    webui_running = True
    webui_pid = os.getpid()

    return jsonify({
        'status': 'success',
        'services': {
            'bot': {
                'running': bot_running,
                'pid': bot_pid
            },
            'monitor': {
                'running': monitor_running,
                'pid': monitor_pid
            },
            'webui': {
                'running': webui_running,
                'pid': webui_pid
            }
        },
        'timestamp': datetime.now().strftime("%H:%M:%S")
    })


@app.route('/api/services/bot/stop', methods=['POST'])
def api_bot_stop():
    """
    Stop the trading bot
    ---
    tags:
      - System
    responses:
      200:
        description: Bot stopped
    """
    was_running = _is_process_running(r"broker\.bot")
    if was_running:
        _kill_bot()
    return jsonify({
        'status': 'success',
        'message': 'Bot gestoppt' if was_running else 'Bot war nicht aktiv',
        'was_running': was_running
    })


@app.route('/api/services/bot/start', methods=['POST'])
def api_bot_start():
    """
    Start the trading bot
    ---
    tags:
      - System
    responses:
      200:
        description: Bot started
    """
    if _is_process_running(r"broker\.bot"):
        return jsonify({'status': 'error', 'message': 'Bot läuft bereits'}), 400

    try:
        # Load .env environment variables
        env = os.environ.copy()
        env_file = os.path.join(PROJECT_DIR, '.env')
        if os.path.exists(env_file):
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, _, value = line.partition('=')
                        env[key.strip()] = value.strip()

        bot_log = os.path.join(PROJECT_DIR, 'logs', 'bot.log')
        log_fd = open(bot_log, 'a')

        # Start bot process with caffeinate (macOS sleep prevention)
        proc = subprocess.Popen(
            ["caffeinate", "-i", VENV_PYTHON, "-m", "broker.bot"],
            cwd=PROJECT_DIR,
            stdout=log_fd,
            stderr=log_fd,
            env=env,
            start_new_session=True
        )

        # Write lock and PID files
        with open(BOT_LOCK_FILE, 'w') as f:
            f.write('')
        with open(BOT_PID_FILE, 'w') as f:
            f.write(str(proc.pid))

        # Wait for bot to initialize, then verify via pgrep (more reliable than proc.poll with caffeinate)
        time.sleep(3)

        if _is_process_running(r"broker\.bot"):
            bot_pid = _get_process_pid(r"broker\.bot")
            return jsonify({
                'status': 'success',
                'message': f'Bot gestartet (PID: {bot_pid})',
                'pid': bot_pid
            })
        else:
            for fpath in [BOT_LOCK_FILE, BOT_PID_FILE]:
                if os.path.exists(fpath):
                    os.remove(fpath)
            return jsonify({'status': 'error', 'message': 'Bot konnte nicht gestartet werden'}), 500
    except Exception as e:
        logging.error(f"Bot start error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/services/bot/restart', methods=['POST'])
def api_bot_restart():
    """
    Restart the trading bot
    ---
    tags:
      - System
    responses:
      200:
        description: Bot restarted
    """
    # Stop
    _kill_bot()

    # Start (delegate to start endpoint logic)
    return api_bot_start()


@app.route('/api/services/monitor/stop', methods=['POST'])
def api_monitor_stop():
    """
    Stop the bot monitor
    ---
    tags:
      - System
    responses:
      200:
        description: Monitor stopped
    """
    was_running = _is_process_running("bot_monitor.py")
    if was_running:
        _kill_process("bot_monitor.py")
        time.sleep(1)
    return jsonify({
        'status': 'success',
        'message': 'Monitor gestoppt' if was_running else 'Monitor war nicht aktiv',
        'was_running': was_running
    })


@app.route('/api/services/monitor/start', methods=['POST'])
def api_monitor_start():
    """
    Start the bot monitor
    ---
    tags:
      - System
    responses:
      200:
        description: Monitor started
    """
    if _is_process_running("bot_monitor.py"):
        return jsonify({'status': 'error', 'message': 'Monitor läuft bereits'}), 400

    try:
        env = os.environ.copy()
        env_file = os.path.join(PROJECT_DIR, '.env')
        if os.path.exists(env_file):
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, _, value = line.partition('=')
                        env[key.strip()] = value.strip()

        monitor_log = os.path.join(PROJECT_DIR, 'logs', 'monitor.log')
        log_fd = open(monitor_log, 'a')
        monitor_script = os.path.join(PROJECT_DIR, 'scripts', 'bot_monitor.py')

        proc = subprocess.Popen(
            [VENV_PYTHON, monitor_script],
            cwd=PROJECT_DIR,
            stdout=log_fd,
            stderr=log_fd,
            env=env,
            start_new_session=True
        )

        time.sleep(1)

        if proc.poll() is None:
            return jsonify({
                'status': 'success',
                'message': f'Monitor gestartet (PID: {proc.pid})',
                'pid': proc.pid
            })
        else:
            return jsonify({'status': 'error', 'message': 'Monitor konnte nicht gestartet werden'}), 500
    except Exception as e:
        logging.error(f"Monitor start error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/services/monitor/restart', methods=['POST'])
def api_monitor_restart():
    """
    Restart the bot monitor
    ---
    tags:
      - System
    responses:
      200:
        description: Monitor restarted
    """
    _kill_process("bot_monitor.py")
    time.sleep(1)
    return api_monitor_start()


@app.route('/api/services/all/restart', methods=['POST'])
def api_all_restart():
    """
    Restart all services (bot + monitor)
    ---
    tags:
      - System
    responses:
      200:
        description: All services restarted
    """
    # Stop all (monitor first to prevent it from restarting the bot)
    _kill_process("bot_monitor.py")
    time.sleep(0.5)
    _kill_bot()

    results = {}

    # Start bot FIRST (before monitor, so monitor doesn't race to start it)
    try:
        resp = api_bot_start()
        data = resp.get_json() if hasattr(resp, 'get_json') else resp[0].get_json()
        results['bot'] = data.get('status') == 'success'
    except Exception:
        results['bot'] = False

    # Start monitor after bot is running
    try:
        resp = api_monitor_start()
        data = resp.get_json() if hasattr(resp, 'get_json') else resp[0].get_json()
        results['monitor'] = data.get('status') == 'success'
    except Exception:
        results['monitor'] = False

    # Restart Web UI last (delayed self-restart so response is sent first)
    _delayed_restart_webui(delay=1.5)
    results['webui'] = True

    all_ok = all(results.values())
    return jsonify({
        'status': 'success' if all_ok else 'partial',
        'message': 'Alle Services werden neu gestartet' if all_ok else 'Einige Services konnten nicht gestartet werden',
        'results': results
    })


@app.route('/api/services/all/stop', methods=['POST'])
def api_all_stop():
    """
    Stop all services (bot + monitor)
    ---
    tags:
      - System
    responses:
      200:
        description: All services stopped
    """
    # Stop monitor first to prevent it from restarting the bot
    _kill_process("bot_monitor.py")
    time.sleep(0.5)
    _kill_bot()

    # Delayed self-stop so the response can be sent first
    _delayed_stop_webui(delay=1.0)

    return jsonify({
        'status': 'success',
        'message': 'Alle Services werden gestoppt (inkl. Web UI)'
    })


@app.route('/api/services/webui/stop', methods=['POST'])
def api_webui_stop():
    """
    Stop the Web UI (delayed self-stop)
    ---
    tags:
      - System
    responses:
      200:
        description: Web UI will stop
    """
    _delayed_stop_webui(delay=1.0)
    return jsonify({
        'status': 'success',
        'message': 'Web UI wird beendet...'
    })


@app.route('/api/services/webui/restart', methods=['POST'])
def api_webui_restart():
    """
    Restart the Web UI (delayed self-restart)
    ---
    tags:
      - System
    responses:
      200:
        description: Web UI will restart
    """
    _delayed_restart_webui(delay=1.0)
    return jsonify({
        'status': 'success',
        'message': 'Web UI wird neu gestartet...'
    })


if __name__ == '__main__':
    print("\n" + "="*60)
    print("Trading Bot Web Dashboard")
    print("="*60)
    print("\n✓ Starting server on http://localhost:8000")
    print("✓ Press Ctrl+C to stop\n")
    app.run(debug=True, host='localhost', port=8000, use_reloader=False)
