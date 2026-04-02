#!/usr/bin/env python3
"""One-time migration: Fix historical trades missing exit_size, exit_fee, exit_value.
Recalculates net P&L to include exit fees (0.16% of exit value)."""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "logs" / "trades.db"

def fix_historical_fees():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Find closed trades with exit_price but missing exit_size/exit_fee
    c.execute("""
        SELECT id, symbol, entry_price, entry_size, entry_value, entry_fee,
               exit_price, exit_size, exit_value, exit_fee, pnl, status
        FROM trades
        WHERE status LIKE 'CLOSED%'
          AND exit_price IS NOT NULL
          AND (exit_size IS NULL OR exit_fee IS NULL OR exit_fee = 0)
    """)
    trades = c.fetchall()
    print(f"Trades to fix: {len(trades)}")

    total_correction = 0.0
    for t in trades:
        tid = t["id"]
        ep = t["entry_price"]
        es_eur = t["entry_size"]  # EUR invested
        xp = t["exit_price"]
        ef = t["entry_fee"] or 0

        # Calculate coin quantity from EUR / entry_price
        coin_qty = es_eur / ep if ep > 0 else 0

        # Calculate exit values
        exit_size = coin_qty
        exit_value = coin_qty * xp
        exit_fee = exit_value * 0.0016  # 0.16% fee rate (consistent with estimate_fees)

        # Recalculate net P&L properly including both fees
        gross_pnl = (xp - ep) * coin_qty
        net_pnl = gross_pnl - ef - exit_fee

        # Fix entry_value if it was incorrectly huge (some trades had entry_price * entry_size)
        correct_entry_value = es_eur
        entry_value_to_use = t["entry_value"] or es_eur
        if entry_value_to_use > es_eur * 2:
            entry_value_to_use = correct_entry_value
            print(f"  #{tid:3d} Also fixing entry_value: {t['entry_value']:.2f} -> {correct_entry_value:.2f}")

        entry_cost = entry_value_to_use + ef
        pnl_pct = (net_pnl / entry_cost * 100) if entry_cost > 0 else 0

        old_pnl = t["pnl"] or 0
        diff = old_pnl - net_pnl
        total_correction += diff

        print(f"  #{tid:3d} {t['symbol']:10s} coins={coin_qty:.6f} exit_val={exit_value:.4f} "
              f"exit_fee={exit_fee:.4f} old_pnl={old_pnl:+.4f} new_pnl={net_pnl:+.4f} correction={diff:+.4f}")

        # Update DB
        c.execute("""
            UPDATE trades
            SET exit_size = ?, exit_value = ?, exit_fee = ?, pnl = ?, pnl_pct = ?,
                entry_value = CASE WHEN entry_value > entry_size * 2 THEN entry_size ELSE entry_value END
            WHERE id = ?
        """, (exit_size, exit_value, exit_fee, net_pnl, pnl_pct, tid))

    conn.commit()
    print(f"\nTotal correction applied: {total_correction:+.4f} EUR")

    # Show new totals
    c.execute("""
        SELECT
            COALESCE(SUM(pnl), 0) as total_pnl,
            COALESCE(SUM(COALESCE(entry_fee, 0) + COALESCE(exit_fee, 0)), 0) as total_fees
        FROM trades WHERE status LIKE 'CLOSED%'
    """)
    r = c.fetchone()
    print(f"New total realized P&L (net, after all fees): {r['total_pnl']:.4f} EUR")
    print(f"Total fees (entry+exit): {r['total_fees']:.4f} EUR")
    conn.close()


if __name__ == "__main__":
    fix_historical_fees()
