#!/usr/bin/env python3
"""Compare Kraken actual balances with DB open trades."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccxt
import sqlite3
from config.settings import KRAKEN_API_KEY, KRAKEN_API_SECRET, ALLOWED_COINS

exchange = ccxt.kraken({'apiKey': KRAKEN_API_KEY, 'secret': KRAKEN_API_SECRET, 'enableRateLimit': True})
balance = exchange.fetch_balance()
symbols = list(ALLOWED_COINS.keys())

kraken = {}
for symbol in symbols:
    base = symbol.split('/')[0]
    coins = 0
    for key in [base, base.upper(), 'X'+base, 'X'+base.upper()]:
        if key in balance and isinstance(balance[key], dict):
            coins = balance[key].get('total', 0) or 0
            if coins:
                break
    ticker = exchange.fetch_ticker(symbol)
    price = ticker['last']
    kraken[symbol] = {'coins': coins, 'price': price, 'value': coins * price}
    print(f"Kraken {symbol}: {coins:.8f} coins x €{price:.6f} = €{coins * price:.4f}")

print()

conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'trades.db'))
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("SELECT id, symbol, entry_price, entry_size FROM trades WHERE status='OPEN' OR status IS NULL ORDER BY symbol, id")

db_by_symbol = {}
for row in cur.fetchall():
    sym = row['symbol']
    implied_coins = row['entry_size'] / row['entry_price'] if row['entry_price'] > 0 else 0
    if sym not in db_by_symbol:
        db_by_symbol[sym] = {'coins': 0, 'trades': []}
    db_by_symbol[sym]['coins'] += implied_coins
    db_by_symbol[sym]['trades'].append({'id': row['id'], 'coins': implied_coins, 'entry_size': row['entry_size'], 'entry_price': row['entry_price']})

for sym, data in sorted(db_by_symbol.items()):
    price = kraken.get(sym, {}).get('price', 0)
    db_value = data['coins'] * price
    kraken_coins = kraken.get(sym, {}).get('coins', 0)
    kraken_value = kraken.get(sym, {}).get('value', 0)
    diff_coins = kraken_coins - data['coins']
    diff_value = kraken_value - db_value
    print(f"DB {sym}: {data['coins']:.8f} implied coins x €{price:.6f} = €{db_value:.4f}")
    print(f"   Kraken: {kraken_coins:.8f} coins = €{kraken_value:.4f}")
    print(f"   DIFF: {diff_coins:+.8f} coins = €{diff_value:+.4f}")
    for t in data['trades']:
        print(f"     Trade #{t['id']}: {t['coins']:.8f} coins (€{t['entry_size']:.2f} @ €{t['entry_price']:.6f})")
    print()

conn.close()
