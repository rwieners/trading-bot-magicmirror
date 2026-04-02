"""
Dashboard and Reporting Utilities
Provides real-time monitoring and performance reporting.
"""

import logging
from typing import Dict, List
from datetime import datetime
import json
from pathlib import Path

logger = logging.getLogger(__name__)


class PerformanceReport:
    """Generate performance reports from trading history"""
    
    def __init__(self, db = None):
        """
        Initialize reporter.
        
        Args:
            db: TradeDatabase instance
        """
        self.db = db
    
    def get_daily_pnl(self) -> Dict:
        """Get daily P&L breakdown"""
        if not self.db:
            return {}
        
        trades = self.db.get_closed_trades(limit=1000)
        daily_pnl = {}
        
        for trade in trades:
            exit_time = trade['exit_time']
            if exit_time:
                date = datetime.fromtimestamp(exit_time).date()
                date_str = str(date)
                
                if date_str not in daily_pnl:
                    daily_pnl[date_str] = {'count': 0, 'pnl': 0.0}
                
                daily_pnl[date_str]['count'] += 1
                daily_pnl[date_str]['pnl'] += trade.get('pnl', 0)
        
        return daily_pnl
    
    def get_symbol_stats(self) -> Dict:
        """Get stats per symbol"""
        if not self.db:
            return {}
        
        trades = self.db.get_closed_trades(limit=1000)
        symbol_stats = {}
        
        for trade in trades:
            symbol = trade['symbol']
            
            if symbol not in symbol_stats:
                symbol_stats[symbol] = {
                    'count': 0,
                    'wins': 0,
                    'losses': 0,
                    'total_pnl': 0.0,
                    'avg_pnl': 0.0,
                }
            
            symbol_stats[symbol]['count'] += 1
            pnl = trade.get('pnl', 0)
            
            if pnl > 0:
                symbol_stats[symbol]['wins'] += 1
            elif pnl < 0:
                symbol_stats[symbol]['losses'] += 1
            
            symbol_stats[symbol]['total_pnl'] += pnl
            symbol_stats[symbol]['avg_pnl'] = symbol_stats[symbol]['total_pnl'] / symbol_stats[symbol]['count']
            symbol_stats[symbol]['win_rate'] = symbol_stats[symbol]['wins'] / symbol_stats[symbol]['count'] * 100 if symbol_stats[symbol]['count'] > 0 else 0
        
        return symbol_stats
    
    def generate_html_report(self, output_path: str = None) -> str:
        """Generate HTML performance report"""
        if not output_path:
            output_path = "trading_report.html"
        
        stats = self.db.get_trade_stats() if self.db else {}
        daily = self.get_daily_pnl()
        symbols = self.get_symbol_stats()
        
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Trading Bot Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h1 {{ color: #333; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #4CAF50; color: white; }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }}
        .positive {{ color: green; }}
        .negative {{ color: red; }}
    </style>
</head>
<body>
    <h1>Trading Bot Performance Report</h1>
    <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    
    <h2>Summary Statistics</h2>
    <table>
        <tr><th>Metric</th><th>Value</th></tr>
        <tr><td>Total Trades</td><td>{stats.get('total_trades', 0)}</td></tr>
        <tr><td>Winning Trades</td><td class="positive">{stats.get('winning_trades', 0)}</td></tr>
        <tr><td>Losing Trades</td><td class="negative">{stats.get('losing_trades', 0)}</td></tr>
        <tr><td>Win Rate</td><td>{stats.get('win_rate', 0):.2f}%</td></tr>
        <tr><td>Total P&L</td><td class="{'positive' if stats.get('total_pnl', 0) > 0 else 'negative'}">{stats.get('total_pnl', 0):.4f} EUR</td></tr>
        <tr><td>Avg Win</td><td class="positive">{stats.get('avg_win', 0):.4f}</td></tr>
        <tr><td>Avg Loss</td><td class="negative">{stats.get('avg_loss', 0):.4f}</td></tr>
    </table>
    
    <h2>Symbol Performance</h2>
    <table>
        <tr>
            <th>Symbol</th>
            <th>Trades</th>
            <th>Win Rate</th>
            <th>Total P&L</th>
            <th>Avg P&L</th>
        </tr>
"""
        
        for symbol, data in sorted(symbols.items()):
            html += f"""        <tr>
            <td>{symbol}</td>
            <td>{data['count']}</td>
            <td>{data['win_rate']:.2f}%</td>
            <td class="{'positive' if data['total_pnl'] > 0 else 'negative'}">{data['total_pnl']:.4f}</td>
            <td class="{'positive' if data['avg_pnl'] > 0 else 'negative'}">{data['avg_pnl']:.4f}</td>
        </tr>
"""
        
        html += """    </table>
</body>
</html>
"""
        
        # Write to file
        with open(output_path, 'w') as f:
            f.write(html)
        
        logger.info(f"HTML report saved to {output_path}")
        return html


class Dashboard:
    """Real-time trading dashboard"""
    
    def __init__(self):
        self.current_state = {}
    
    def update(self, bot_state: Dict):
        """Update dashboard with current bot state"""
        self.current_state = bot_state
    
    def print_dashboard(self):
        """Print ASCII dashboard"""
        print("\n" + "="*80)
        print("TRADING BOT DASHBOARD".center(80))
        print("="*80)
        
        if not self.current_state:
            print("No data available")
            return
        
        state = self.current_state
        
        # Account section
        print("\n[ACCOUNT]")
        print(f"Balance: {state.get('balance', 0):.2f} EUR | "
              f"P&L: {state.get('pnl', 0):+.4f} ({state.get('pnl_pct', 0):+.2f}%)")
        print(f"Max Drawdown: {state.get('max_drawdown', 0):.2f}% | "
              f"Peak Balance: {state.get('peak_balance', 0):.2f}")
        
        # Positions section
        print("\n[POSITIONS]")
        if state.get('open_positions'):
            for symbol, pos in state['open_positions'].items():
                entry = pos.get('entry_price', 0)
                current = pos.get('current_price', 0)
                pnl_pct = ((current - entry) / entry * 100) if entry > 0 else 0
                print(f"  {symbol}: {pos.get('size', 0):.4f} @ {entry:.2f} "
                      f"({current:.2f} {pnl_pct:+.2f}%)")
        else:
            print("  No open positions")
        
        # Signals section
        print("\n[SIGNALS]")
        if state.get('signals'):
            for sig in state['signals'][:5]:  # Show last 5
                print(f"  {sig}")
        else:
            print("  No recent signals")
        
        # Performance section
        print("\n[PERFORMANCE]")
        perf = state.get('performance', {})
        if perf:
            print(f"Total Trades: {perf.get('total_trades', 0)} | "
                  f"Win Rate: {perf.get('win_rate', 0):.2f}% | "
                  f"Profit Factor: {perf.get('profit_factor', 0):.2f}")
        
        print("="*80 + "\n")
    
    def export_json(self, output_path: str = None):
        """Export current state to JSON"""
        if not output_path:
            output_path = "dashboard_state.json"
        
        with open(output_path, 'w') as f:
            json.dump(self.current_state, f, indent=2, default=str)
        
        logger.info(f"Dashboard state exported to {output_path}")


class AlertSystem:
    """Alert management for critical events"""
    
    def __init__(self):
        self.alerts: List = []
        self.max_alerts = 100
    
    def add_alert(self, level: str, message: str, timestamp: int = None):
        """Add an alert"""
        if timestamp is None:
            timestamp = int(__import__('time').time())
        
        alert = {
            'timestamp': timestamp,
            'level': level,
            'message': message,
            'datetime': datetime.fromtimestamp(timestamp).isoformat()
        }
        
        self.alerts.append(alert)
        
        # Keep only recent alerts
        if len(self.alerts) > self.max_alerts:
            self.alerts = self.alerts[-self.max_alerts:]
        
        if level == 'CRITICAL':
            logger.critical(message)
        elif level == 'WARNING':
            logger.warning(message)
        else:
            logger.info(message)
    
    def get_alerts(self, level: str = None, limit: int = 20) -> List:
        """Get recent alerts"""
        alerts = self.alerts
        
        if level:
            alerts = [a for a in alerts if a['level'] == level]
        
        return alerts[-limit:]
    
    def get_critical_alerts(self) -> List:
        """Get only critical alerts"""
        return self.get_alerts(level='CRITICAL')
    
    def clear_alerts(self):
        """Clear all alerts"""
        self.alerts.clear()
