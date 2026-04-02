#!/usr/bin/env python3
"""
Transaction Viewer
Display all trades with reasons, entry/exit prices, and P&L.
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from tabulate import tabulate
import sqlite3

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from broker.data.storage import TradeDatabase
from config.settings import PROJECT_ROOT

def format_timestamp(ts):
    """Convert timestamp to readable format"""
    if not ts:
        return "N/A"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except:
        return str(ts)

def format_price(price):
    """Format price with 2 decimals"""
    if price is None or price == 0:
        return "—"
    return f"${price:.2f}"

def format_pnl(pnl):
    """Format P&L with color"""
    if pnl is None or pnl == 0:
        return "—"
    
    color_code = "\033[92m" if pnl > 0 else "\033[91m"  # Green or Red
    reset_code = "\033[0m"
    return f"{color_code}{pnl:+.4f}{reset_code}"

def format_percentage(val):
    """Format as percentage"""
    if val is None or val == 0:
        return "—"
    return f"{val:+.2f}%"

def show_transactions(db_path=None, symbol=None, limit=50, status="CLOSED"):
    """
    Display transactions in table format.
    
    Args:
        db_path: Path to database
        symbol: Filter by symbol (e.g., "BTC/EUR")
        limit: Max number of trades to show
        status: Filter by status (OPEN, CLOSED, CLOSED_PROFIT, CLOSED_LOSS)
    """
    if not db_path:
        db_path = PROJECT_ROOT / "logs" / "trades.db"
    
    if not Path(db_path).exists():
        print(f"❌ Database not found: {db_path}")
        print("Run the bot first to create trade history")
        return
    
    # Connect to database
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Build query
    query = "SELECT * FROM trades WHERE 1=1"
    params = []
    
    if status:
        query += " AND status = ?"
        params.append(status)
    
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
    
    query += " ORDER BY entry_time DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    trades = cursor.fetchall()
    conn.close()
    
    if not trades:
        print(f"No trades found (status={status}, symbol={symbol})")
        return
    
    # Format table
    table_data = []
    
    for trade in trades:
        reason = trade['reason'] or '—'
        model_conf = trade['model_confidence']
        
        # Reason description
        if reason == 'PROFIT_GATE':
            reason_desc = f"📈 Profit Gate (conf={model_conf:.1%})"
        elif reason == 'STOP_LOSS':
            reason_desc = "🛑 Stop Loss (-8%)"
        elif reason == 'PROFIT_TARGET':
            reason_desc = "✓ Profit Target (+1%)"
        elif reason == 'MANUAL':
            reason_desc = "👤 Manual"
        else:
            reason_desc = reason
        
        # Price movement
        entry_price = trade['entry_price']
        exit_price = trade['exit_price']
        
        if entry_price and exit_price:
            move_pct = (exit_price - entry_price) / entry_price
        else:
            move_pct = None
        
        row = [
            # Trade ID
            f"#{trade['id']}",
            
            # Symbol
            trade['symbol'],
            
            # Entry
            format_timestamp(trade['entry_time']),
            format_price(entry_price),
            
            # Exit
            format_timestamp(trade['exit_time']),
            format_price(exit_price),
            
            # Movement
            format_percentage(move_pct * 100) if move_pct else "OPEN",
            
            # Size & Value
            f"{trade['entry_size']:.4f}" if trade['entry_size'] else "—",
            
            # P&L
            format_pnl(trade['pnl']),
            
            # P&L %
            format_percentage(trade['pnl_pct']),
            
            # Reason
            reason_desc,
        ]
        
        table_data.append(row)
    
    # Headers
    headers = [
        "ID",
        "Symbol",
        "Entry Time",
        "Entry",
        "Exit Time",
        "Exit",
        "Move",
        "Size",
        "P&L",
        "P&L %",
        "Reason",
    ]
    
    # Print table
    print("\n" + "="*150)
    print(f"TRADING TRANSACTIONS ({len(trades)} trades)")
    print("="*150 + "\n")
    
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    
    # Summary stats
    print("\n" + "="*150)
    print("SUMMARY")
    print("="*150 + "\n")
    
    total_pnl = sum(float(t['pnl'] or 0) for t in trades)
    wins = sum(1 for t in trades if t['pnl'] and t['pnl'] > 0)
    losses = sum(1 for t in trades if t['pnl'] and t['pnl'] < 0)
    avg_pnl = total_pnl / len(trades) if trades else 0
    win_rate = wins / len(trades) * 100 if trades else 0
    
    total_fees = sum(float(t['entry_fee'] or 0) + float(t['exit_fee'] or 0) for t in trades)
    
    summary = [
        ["Total P&L", f"€{total_pnl:+.2f}"],
        ["Avg P&L per trade", f"€{avg_pnl:+.2f}"],
        ["Win Rate", f"{win_rate:.1f}% ({wins}W / {losses}L)"],
        ["Total Fees Paid", f"€{total_fees:.2f}"],
        ["Net P&L after fees", f"€{total_pnl - total_fees:+.2f}"],
        ["Trades Analyzed", f"{len(trades)}"],
    ]
    
    print(tabulate(summary, tablefmt="simple"))
    print("\n")

def show_open_positions(db_path=None):
    """Show currently open positions"""
    if not db_path:
        db_path = PROJECT_ROOT / "logs" / "trades.db"
    
    if not Path(db_path).exists():
        print(f"❌ Database not found: {db_path}")
        return
    
    # Connect to database
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY entry_time DESC")
    trades = cursor.fetchall()
    conn.close()
    
    if not trades:
        print("✓ No open positions")
        return
    
    print("\n" + "="*120)
    print(f"OPEN POSITIONS ({len(trades)} active)")
    print("="*120 + "\n")
    
    table_data = []
    
    for trade in trades:
        row = [
            f"#{trade['id']}",
            trade['symbol'],
            format_timestamp(trade['entry_time']),
            format_price(trade['entry_price']),
            f"{trade['entry_size']:.4f}",
            f"${float(trade['entry_price'] * trade['entry_size']):.2f}",
            f"Entry +{int((datetime.now() - datetime.fromtimestamp(trade['entry_time'])).total_seconds() // 60)}m ago",
        ]
        table_data.append(row)
    
    headers = ["ID", "Symbol", "Entry Time", "Price", "Size", "Position Value", "Duration"]
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    print("\n")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="View trading transactions")
    parser.add_argument("--symbol", help="Filter by symbol (e.g., BTC/EUR)")
    parser.add_argument("--limit", type=int, default=50, help="Max trades to show (default: 50)")
    parser.add_argument("--status", default="CLOSED", 
                       choices=["CLOSED", "OPEN", "CLOSED_PROFIT", "CLOSED_LOSS"],
                       help="Filter by status")
    parser.add_argument("--db", help="Path to database")
    parser.add_argument("--open", action="store_true", help="Show only open positions")
    
    args = parser.parse_args()
    
    if args.open:
        show_open_positions(args.db)
    else:
        show_transactions(
            db_path=args.db,
            symbol=args.symbol,
            limit=args.limit,
            status=args.status
        )
