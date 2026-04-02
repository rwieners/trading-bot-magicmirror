"""
Account Monitoring and Status Tracking
Real-time account balance and equity monitoring with alerts.
"""

import logging
import time
import os
import json
from typing import Dict, List, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Path to user settings for dynamic threshold loading
USER_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'config', 'user_settings.json')


@dataclass
class AlertEvent:
    """Alert event record"""
    timestamp: int
    level: str  # 'INFO', 'WARNING', 'CRITICAL'
    message: str


class AccountMonitor:
    """
    Monitors account health in real-time.
    Tracks balance changes, generates alerts, manages kill switches.
    """
    
    def __init__(self,
                 initial_balance: float = None,
                 critical_threshold: float = 50.0,  # 50% of initial
                 warning_threshold: float = 95.0,   # 95 EUR
                 performance_check_interval: int = 60,  # seconds
                 db = None):
        """
        Initialize account monitor.
        
        Args:
            initial_balance: Starting balance (loaded from DB if None)
            critical_threshold: Balance level for emergency stop
            warning_threshold: Balance level for warning
            db: Optional TradeDatabase to load actual portfolio balance from
            performance_check_interval: How often to check (seconds)
        """
        # Try to load actual balance from database (for repeated runs)
        actual_balance = initial_balance or 0
        if db:
            try:
                latest = db.get_latest_account_balance()
                if latest and latest['balance']:
                    actual_balance = latest['balance']
            except Exception as e:
                pass  # Silent fail, use initial_balance
        
        # Use actual_balance as initial if none provided
        self.initial_balance = initial_balance if initial_balance is not None else (actual_balance or 100.0)
        self.critical_threshold = critical_threshold
        self.warning_threshold = warning_threshold
        self.performance_check_interval = performance_check_interval
        
        self.current_balance = actual_balance or self.initial_balance
        self.previous_balance = actual_balance
        self.peak_balance = actual_balance
        self.lowest_balance = actual_balance
        
        self.last_check_time = int(time.time())
        self._last_settings_load = 0  # For caching settings
        self._cached_critical = critical_threshold
        self._cached_warning = warning_threshold
        self.alerts: List[AlertEvent] = []
        self.is_critical = False
        self.is_warning = False
        self.trade_count = 0
        self.winning_trades = 0
        self.losing_trades = 0
    
    def _load_thresholds(self):
        """Load thresholds from user settings (cached for 30 seconds)"""
        now = time.time()
        if now - self._last_settings_load < 30:
            return self._cached_critical, self._cached_warning
        
        try:
            if os.path.exists(USER_SETTINGS_PATH):
                with open(USER_SETTINGS_PATH, 'r') as f:
                    settings = json.load(f)
                    self._cached_critical = settings.get('critical_balance_level', self.critical_threshold)
                    self._cached_warning = settings.get('warning_balance_level', self.warning_threshold)
                    self._last_settings_load = now
        except Exception as e:
            logger.debug(f"Could not load thresholds from settings: {e}")
        
        return self._cached_critical, self._cached_warning
    
    def update(self, current_balance: float, open_positions: int = 0,
              total_pnl: float = 0.0) -> Dict:
        """
        Update account status.
        
        Args:
            current_balance: Current total balance
            open_positions: Number of open positions
            total_pnl: Total realized + unrealized P&L
        
        Returns:
            Status dict with alerts
        """
        now = int(time.time())
        self.current_balance = current_balance
        
        # Update extremes
        if current_balance > self.peak_balance:
            self.peak_balance = current_balance
        if current_balance < self.lowest_balance:
            self.lowest_balance = current_balance
        
        # Calculate metrics
        balance_change = current_balance - self.previous_balance
        balance_change_pct = (balance_change / self.previous_balance) * 100 if self.previous_balance > 0 else 0
        max_drawdown = ((self.lowest_balance - self.peak_balance) / self.peak_balance) * 100 if self.peak_balance > 0 else 0
        return_pct = ((current_balance - self.initial_balance) / self.initial_balance) * 100 if self.initial_balance and self.initial_balance > 0 else 0
        
        status = {
            'timestamp': now,
            'current_balance': current_balance,
            'initial_balance': self.initial_balance,
            'balance_change': balance_change,
            'balance_change_pct': balance_change_pct,
            'total_return_pct': return_pct,
            'peak_balance': self.peak_balance,
            'lowest_balance': self.lowest_balance,
            'max_drawdown_pct': max_drawdown,
            'open_positions': open_positions,
            'total_pnl': total_pnl,
            'alerts': [],
            'is_critical': False,
            'is_warning': False,
        }
        
        # Load dynamic thresholds from user settings
        critical_threshold, warning_threshold = self._load_thresholds()
        
        # Check thresholds
        if current_balance < critical_threshold:
            if not self.is_critical:
                alert = AlertEvent(
                    timestamp=now,
                    level='CRITICAL',
                    message=f"EMERGENCY: Balance {current_balance:.2f} below critical level {critical_threshold:.2f}"
                )
                self.alerts.append(alert)
                status['alerts'].append(alert.message)
                self.is_critical = True
                logger.critical(alert.message)
        else:
            self.is_critical = False
        
        if current_balance < warning_threshold:
            if not self.is_warning:
                alert = AlertEvent(
                    timestamp=now,
                    level='WARNING',
                    message=f"WARNING: Balance {current_balance:.2f} below threshold {warning_threshold:.2f}"
                )
                self.alerts.append(alert)
                status['alerts'].append(alert.message)
                self.is_warning = True
                logger.warning(alert.message)
        else:
            self.is_warning = False
        
        # Large loss alert
        if balance_change_pct < -5:
            alert = AlertEvent(
                timestamp=now,
                level='WARNING',
                message=f"Large loss: {balance_change_pct:.2f}% ({balance_change:.2f} EUR)"
            )
            self.alerts.append(alert)
            status['alerts'].append(alert.message)
            logger.warning(alert.message)
        
        # Large gain alert
        if balance_change_pct > 5:
            alert = AlertEvent(
                timestamp=now,
                level='INFO',
                message=f"Significant gain: +{balance_change_pct:.2f}% (+{balance_change:.2f} EUR)"
            )
            self.alerts.append(alert)
            status['alerts'].append(alert.message)
            logger.info(alert.message)
        
        self.previous_balance = current_balance
        status['is_critical'] = self.is_critical
        status['is_warning'] = self.is_warning
        
        return status
    
    def record_trade_result(self, pnl: float, pnl_pct: float):
        """Record result of closed trade"""
        self.trade_count += 1
        if pnl > 0:
            self.winning_trades += 1
        elif pnl < 0:
            self.losing_trades += 1
    
    def get_performance_summary(self) -> Dict:
        """Get trading performance summary"""
        if self.trade_count == 0:
            return {
                'total_trades': 0,
                'win_rate': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
            }
        
        win_rate = (self.winning_trades / self.trade_count) * 100
        
        return {
            'total_trades': self.trade_count,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': win_rate,
        }
    
    def get_alerts(self, level: str = None, limit: int = 20) -> List[AlertEvent]:
        """
        Get recent alerts.
        
        Args:
            level: Filter by level ('INFO', 'WARNING', 'CRITICAL')
            limit: Maximum alerts to return
        
        Returns:
            List of AlertEvent
        """
        alerts = self.alerts
        
        if level:
            alerts = [a for a in alerts if a.level == level]
        
        return alerts[-limit:]
    
    def should_pause_trading(self) -> Tuple[bool, str]:
        """
        Determine if trading should be paused.
        
        Returns:
            (should_pause, reason)
        """
        critical_threshold, _ = self._load_thresholds()
        if self.is_critical:
            return True, f"Critical balance level {self.current_balance:.2f} < {critical_threshold:.2f}"
        
        return False, ""
    
    def get_health_report(self) -> str:
        """Get human-readable health report"""
        return_pct = ((self.current_balance - self.initial_balance) / self.initial_balance) * 100
        
        report = f"""
Account Health Report
====================
Current Balance: {self.current_balance:.2f} EUR
Initial Balance: {self.initial_balance:.2f} EUR
Return: {return_pct:+.2f}%
Peak: {self.peak_balance:.2f} EUR
Low: {self.lowest_balance:.2f} EUR
Max DD: {((self.lowest_balance - self.peak_balance) / self.peak_balance) * 100:.2f}%

Status: {'CRITICAL' if self.is_critical else 'WARNING' if self.is_warning else 'OK'}
Alerts: {len(self.alerts)} total
Recent Alerts:
"""
        for alert in self.get_alerts(limit=5):
            report += f"  {alert.level}: {alert.message}\n"
        
        return report


from typing import Tuple
