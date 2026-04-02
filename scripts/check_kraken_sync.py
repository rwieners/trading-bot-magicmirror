#!/usr/bin/env python3
"""Compare DB open trades with actual Kraken holdings."""
import sqlite3
import ccxt
import os
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "logs" / "trades.db"

# DB open trades
conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute("SELECT id, symbol, entry_price, entry_size FROM trades WHERE status = 'OPEN' OR status IS NULL ORDER BY id")
trades = c.fetchall()
conn.close()

# Kraken balances
exchange = ccxt.kraken({
    'apiKey': os.getenv('KRAKEN_API_KEY'),
    'secret': os.getenv('KRAKEN_API_SECRET'),
    'enableRateLimit': True
})
balance = exchange.fetch_balance()

kraken = {}
for currency, data in balance.items():
    if isinstance(data, dict) and 'total' in data and data['total'] and data['total'] > 0:
        kraken[currency] = data['total']

print("DB OPEN Trades vs Kraken:")
print(f"{'#ID':>5s} {'Symbol':10s} {'EUR invested':>12s} {'Coins (calc)':>14s} {'Kraken holds':>14s}")
print("-" * 60)

db_coins = {}
for t in trades:
    sym = t['symbol'].split('/')[0]
    coin_qty = t['entry_size'] / t['entry_price'] if t['entry_price'] > 0 else 0
    if sym not in db_coins:
        db_coins[sym] = 0
    db_coins[sym] += coin_qty
    kraken_has = kraken.get(sym, 0)
    print(f"#{t['id']:4d} {t['symbol']:10s} {t['entry_size']:>10.4f}E {coin_qty:>14.8f} {kraken_has:>14.8f}")

print()
print("Summary per coin:")
all_syms = sorted(set(list(db_coins.keys()) + [k for k in kraken.keys() if k != 'EUR']))
for sym in all_syms:
    db = db_coins.get(sym, 0)
    kr = kraken.get(sym, 0)
    diff = kr - db
    match = "OK" if abs(diff) < 0.001 else "MISMATCH"
    print(f"  {sym:5s}: DB expects {db:.8f}, Kraken has {kr:.8f}, diff={diff:+.8f} {match}")
