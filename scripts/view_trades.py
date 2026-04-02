#!/usr/bin/env python3
"""
Trade Log Viewer - Display open and closed trades from database
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime
from tabulate import tabulate

def format_time(timestamp):
    """Convert Unix timestamp to readable format"""
    if not timestamp:
        return "N/A"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

def format_pnl(pnl, pnl_pct):
    """Format P&L with color coding"""
    if pnl is None:
        return "Offen"
    
    symbol = "+" if pnl >= 0 else "-"
    pnl_str = f"{symbol} {abs(pnl):.2f}€"
    pnl_pct_str = f"({symbol}{abs(pnl_pct):.2f}%)" if pnl_pct else ""
    return f"{pnl_str} {pnl_pct_str}"

def view_trades(db_path):
    """Display all trades from database"""
    if not Path(db_path).exists():
        print(f"ERROR: Database not found at {db_path}")
        sys.exit(1)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get all trades
    cursor.execute('''
        SELECT 
            id, symbol, entry_price, entry_size, entry_time,
            exit_price, exit_time, pnl, pnl_pct, entry_fee,
            exit_fee, status, model_confidence, reason
        FROM trades
        ORDER BY entry_time DESC
    ''')
    
    trades = cursor.fetchall()
    conn.close()
    
    if not trades:
        print("No trades found in database")
        return
    
    print("\n" + "="*120)
    print("TRADING LOG - All Trades")
    print("="*120 + "\n")
    
    # Separate open and closed trades
    open_trades = [t for t in trades if t['status'] in ('OPEN', None)]
    closed_trades = [t for t in trades if t['status'] not in ('OPEN', None)]
    
    # Further separate by profit/loss
    winning_closed = [t for t in closed_trades if t['status'] == 'CLOSED_PROFIT']
    losing_closed = [t for t in closed_trades if t['status'] == 'CLOSED_LOSS']
    breakeven_closed = [t for t in closed_trades if t['status'] == 'CLOSED_BREAK_EVEN']
    
    # Display OPEN trades
    if open_trades:
        print(f"\n{'OPEN POSITIONS':<20} ({len(open_trades)} active)\n")
        open_table = []
        for t in open_trades:
            open_table.append([
                t['id'],
                t['symbol'],
                f"€{t['entry_price']:.2f}",
                f"{t['entry_size']:.6f}",
                format_time(t['entry_time']),
                f"€{(t['entry_fee'] or 0):.4f}",
                f"{(t['model_confidence'] or 0):.2f}",
                t['status'] or 'OPEN'
            ])
        
        print(tabulate(
            open_table,
            headers=['ID', 'Pair', 'Entry Price', 'Size', 'Entry Time', 'Fee', 'Confidence', 'Status'],
            tablefmt='grid'
        ))
    
    # Display CLOSED trades
    if closed_trades:
        print(f"\n\n{'CLOSED TRADES':<20} ({len(closed_trades)} completed)\n")
        
        # Group by status
        if winning_closed:
            print(f"✓ PROFITABLE ({len(winning_closed)})")
            winning_table = []
            for t in winning_closed:
                pnl_display = format_pnl(t['pnl'], t['pnl_pct'])
                winning_table.append([
                    t['id'], t['symbol'], 
                    f"€{t['entry_price']:.2f}", f"€{t['exit_price']:.2f}",
                    f"{t['entry_size']:.6f}",
                    format_time(t['exit_time']),
                    pnl_display,
                    t['reason'][:40] if t['reason'] else '-'
                ])
            print(tabulate(winning_table, headers=['ID', 'Pair', 'Entry', 'Exit', 'Size', 'Exit Time', 'P&L', 'Reason'], tablefmt='grid'))
        
        if losing_closed:
            print(f"\n✗ LOSS ({len(losing_closed)})")
            losing_table = []
            for t in losing_closed:
                pnl_display = format_pnl(t['pnl'], t['pnl_pct'])
                losing_table.append([
                    t['id'], t['symbol'],
                    f"€{t['entry_price']:.2f}", f"€{t['exit_price']:.2f}",
                    f"{t['entry_size']:.6f}",
                    format_time(t['exit_time']),
                    pnl_display,
                    t['reason'][:40] if t['reason'] else '-'
                ])
            print(tabulate(losing_table, headers=['ID', 'Pair', 'Entry', 'Exit', 'Size', 'Exit Time', 'P&L', 'Reason'], tablefmt='grid'))
        
        if breakeven_closed:
            print(f"\n= BREAK-EVEN ({len(breakeven_closed)})")
            breakeven_table = []
            for t in breakeven_closed:
                breakeven_table.append([
                    t['id'], t['symbol'],
                    f"€{t['entry_price']:.2f}", f"€{t['exit_price']:.2f}",
                    f"{t['entry_size']:.6f}",
                    format_time(t['exit_time']),
                    "€0.00 (0.00%)",
                    t['reason'][:40] if t['reason'] else '-'
                ])
            print(tabulate(breakeven_table, headers=['ID', 'Pair', 'Entry', 'Exit', 'Size', 'Exit Time', 'P&L', 'Reason'], tablefmt='grid'))
    
    # Summary
    print("\n" + "="*120)
    print("SUMMARY")
    print("="*120)
    
    total_trades = len(trades)
    closed_trades_count = len(closed_trades)
    open_trades_count = len(open_trades)
    
    total_pnl = sum(t['pnl'] for t in closed_trades if t['pnl'])
    winning_trades = len([t for t in closed_trades if t['pnl'] and t['pnl'] > 0])
    losing_trades = len([t for t in closed_trades if t['pnl'] and t['pnl'] < 0])
    
    print(f"Total Trades: {total_trades} | Open: {open_trades_count} | Closed: {closed_trades_count}")
    print(f"Realized P&L: {total_pnl:+.2f}€")
    if closed_trades_count > 0:
        win_rate = (winning_trades / closed_trades_count) * 100
        print(f"Win/Loss: {winning_trades}W / {losing_trades}L ({win_rate:.1f}% win rate)")
    print("="*120 + "\n")

if __name__ == '__main__':
    db_path = '/Users/rene/dev/Broker/logs/trades.db'
    view_trades(db_path)
