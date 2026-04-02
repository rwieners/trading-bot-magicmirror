import time
from config.settings import KRAKEN_API_KEY, KRAKEN_API_SECRET, ALLOWED_COINS
import sqlite3
from datetime import datetime
try:
    import ccxt
except ImportError:
    ccxt = None

def sync_kraken_to_db(db_path):
    """
    Synchronize holdings from Kraken and update trades database.
    Kraken is always the data master.
    """
    if not ccxt or not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
        raise RuntimeError('Kraken API not configured')
    exchange = ccxt.kraken({
        'apiKey': KRAKEN_API_KEY,
        'secret': KRAKEN_API_SECRET,
        'enableRateLimit': True
    })
    exchange.load_markets()
    allowed_symbols = list(ALLOWED_COINS.keys())
    holdings = {}
    all_balances = exchange.fetch_balance()
    print("[SYNC_KRAKEN] fetch_balance result:", all_balances)
    for symbol in allowed_symbols:
        base = symbol.split('/')[0]
        balance = None
        for key in [base, base.upper(), 'X'+base, 'X'+base.upper(), 'Z'+base, 'Z'+base.upper()]:
            if key in all_balances:
                balance = all_balances[key]
                break
        if not balance:
            print(f"[SYNC_KRAKEN] Kein Balance-Eintrag für {base} gefunden (Keys: {list(all_balances.keys())})")
            continue
        if isinstance(balance, dict):
            amount = balance.get('total')
            if amount is None:
                amount = balance.get('free')
        else:
            amount = balance
        if not amount or amount <= 0:
            continue
        ticker = exchange.fetch_ticker(symbol)
        current_price = ticker['last']
        holdings[symbol] = {
            'amount': amount,
            'entry_price': current_price,  # Will be overridden by trade history if available
            'current_price': current_price
        }

    # Fetch actual entry prices from trade history
    try:
        all_trades = exchange.fetch_my_trades(symbol=None, limit=200)
        for symbol in list(holdings.keys()):
            symbol_trades = [t for t in all_trades if t['symbol'] == symbol]
            total_amount = 0.0
            total_cost = 0.0
            total_fees = 0.0
            for trade in symbol_trades:
                if trade['side'] == 'buy':
                    total_amount += trade['amount']
                    total_cost += trade['amount'] * trade['price']
                    fee = trade.get('fee', {})
                    total_fees += fee.get('cost', 0) if isinstance(fee, dict) else 0
                elif trade['side'] == 'sell':
                    if total_amount > 0:
                        avg_price = total_cost / total_amount
                        sell_amount = min(trade['amount'], total_amount)
                        total_amount -= sell_amount
                        total_cost -= sell_amount * avg_price
            if total_amount > 0:
                real_entry = total_cost / total_amount
                holdings[symbol]['entry_price'] = real_entry
                holdings[symbol]['entry_fee'] = total_fees
                print(f"[SYNC_KRAKEN] {symbol}: real entry price €{real_entry:.6f} from trade history")
    except Exception as e:
        print(f"[SYNC_KRAKEN] Could not fetch trade history: {e}. Using current prices as fallback.")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get all currently open trades from DB (with EUR sizes for quantity comparison)
    cursor.execute('SELECT id, symbol, entry_price, entry_size, entry_time FROM trades WHERE status = "OPEN" OR status IS NULL')
    open_rows = cursor.fetchall()
    # Group open trades by symbol: total EUR invested and list of trades
    open_by_symbol = {}
    for row in open_rows:
        sym = row['symbol']
        if sym not in open_by_symbol:
            open_by_symbol[sym] = {'total_eur': 0, 'total_coins': 0, 'trades': []}
        open_by_symbol[sym]['total_eur'] += row['entry_size']
        # Calculate actual coin count per trade using its own entry_price
        if row['entry_price'] and row['entry_price'] > 0:
            open_by_symbol[sym]['total_coins'] += row['entry_size'] / row['entry_price']
        open_by_symbol[sym]['trades'].append(row)

    # Close trades that are no longer on Kraken
    symbols_on_kraken = set(holdings.keys())
    symbols_in_db = set(open_by_symbol.keys())
    to_close = list(symbols_in_db - symbols_on_kraken)
    if to_close:
        now_ts_close = int(datetime.now().timestamp())
        for sym in to_close:
            for row in open_by_symbol[sym]['trades']:
                trade_id = row['id']
                # Skip trades that already have exit_price (bot's _execute_sell was faster)
                cursor.execute('SELECT exit_price FROM trades WHERE id = ?', (trade_id,))
                existing = cursor.fetchone()
                if existing and existing['exit_price']:
                    continue
                # Fetch current price so the close has real exit data
                try:
                    ticker = exchange.fetch_ticker(sym)
                    exit_price = ticker['last']
                except Exception:
                    exit_price = row['entry_price']  # fallback
                coin_qty = row['entry_size'] / row['entry_price'] if row['entry_price'] else 0
                cursor.execute(
                    'UPDATE trades SET status = "CLOSED_MANUAL_SYNC", exit_time = ?, '
                    'exit_price = ?, exit_size = ? '
                    'WHERE id = ? AND (status = "OPEN" OR status IS NULL)',
                    (now_ts_close, exit_price, coin_qty, trade_id)
                )
                print(f"[SYNC_KRAKEN] Closed trade #{trade_id} {sym}: exit_price=€{exit_price:.4f}, coins={coin_qty:.6f}")

    now_ts = int(datetime.now().timestamp())
    MIN_POSITION_EUR = 1.00  # Ignore dust positions below €1.00

    for symbol, data in holdings.items():
        coin_amount = data['amount']
        entry_price = data['entry_price']
        current_price = data.get('current_price', entry_price)
        # Use current price for EUR value calculation (what it's worth NOW)
        kraken_total_eur = coin_amount * current_price

        if kraken_total_eur < MIN_POSITION_EUR:
            continue

        if symbol not in open_by_symbol:
            # Completely new position — use entry_price from trade history for accurate P&L
            entry_value = coin_amount * entry_price  # What we actually paid
            cursor.execute('INSERT INTO trades (symbol, entry_price, entry_size, entry_value, entry_time, status, reason) VALUES (?, ?, ?, ?, ?, "OPEN", "SYNC_KRAKEN")',
                (symbol, entry_price, entry_value, entry_value, now_ts))
            print(f"[SYNC_KRAKEN] Neue Position: {symbol} €{entry_value:.2f} @ €{entry_price:.6f}")
        else:
            # Position exists — check if Kraken has significantly more coins
            # Use pre-computed coin total (each trade's EUR / its own entry_price)
            db_total_coins = open_by_symbol[symbol]['total_coins']
            diff_coins = coin_amount - db_total_coins
            diff_eur = diff_coins * entry_price

            if diff_eur >= MIN_POSITION_EUR:
                # Kraken has more than DB — user bought more, create additional trade
                cursor.execute('INSERT INTO trades (symbol, entry_price, entry_size, entry_value, entry_time, status, reason) VALUES (?, ?, ?, ?, ?, "OPEN", "SYNC_KRAKEN_ADDITIONAL")',
                    (symbol, entry_price, diff_eur, diff_eur, now_ts))
                print(f"[SYNC_KRAKEN] Zusätzlicher Kauf erkannt: {symbol} +€{diff_eur:.2f} (Kraken: {coin_amount:.6f}, DB: {db_total_coins:.6f})")

    conn.commit()
    conn.close()
