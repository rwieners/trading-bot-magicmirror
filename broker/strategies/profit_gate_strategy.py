"""
Profit-Gate Trading Strategy
Only executes trades where predicted move > fees + safety margin.
Implements position sizing and risk management rules.
"""

import logging
import json
import os
from enum import Enum
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import time

logger = logging.getLogger(__name__)

# Path to user settings file
USER_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'config', 'user_settings.json')


class Signal(Enum):
    """Trading signals"""
    BUY = 1
    SELL = -1
    HOLD = 0


@dataclass
class TradeSignal:
    """Represents a trading signal"""
    symbol: str
    signal: Signal
    predicted_move: float  # % change
    confidence: float     # [0-1]
    timestamp: int
    reason: str
    position_size: Optional[float] = None  # USDT to trade


class ProfitGateStrategy:
    """
    Implements the profit-gate trading strategy.
    
    Rules:
    1. Only BUY if predicted_move > profit_gate_threshold
       Conservative: 1.42% | Aggressive: 0.7%
    2. Only SELL if position drawdown > max_loss_cutoff OR profit-target reached
    3. Position sizing: respects budget limits
    4. Risk management: portfolio drawdown limits apply
    """
    
    def __init__(self,
                 profit_gate_threshold: float = 0.0142,  # 1.42% (conservative default)
                 min_profit_target: float = 0.01,         # 1%
                 max_loss_cutoff: float = -0.08,          # -8%
                 portfolio_drawdown_limit: float = -0.10,  # -10%
                 position_size_limit: float = 30.0,        # EUR per position
                 max_positions: int = 3):                   # conservative default
        """
        Initialize strategy.
        
        Args:
            profit_gate_threshold: Min profit to execute trade
            min_profit_target: Profit target to exit
            max_loss_cutoff: Stop loss level
            portfolio_drawdown_limit: Max portfolio drawdown before pausing trades
            position_size_limit: Max size per position (EUR)
            max_positions: Max concurrent open positions
        """
        self.profit_gate_threshold = profit_gate_threshold
        self._min_profit_target = min_profit_target
        self._max_loss_cutoff = max_loss_cutoff
        self.portfolio_drawdown_limit = portfolio_drawdown_limit
        self._position_size_limit = position_size_limit
        self.max_positions = max_positions
        self.max_positions_per_symbol = 1  # Default: 1 per coin (set by _apply_trading_mode)
        self._last_settings_check = 0
    
    def _load_user_settings(self):
        """Load user settings from JSON file"""
        try:
            if os.path.exists(USER_SETTINGS_PATH):
                with open(USER_SETTINGS_PATH, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load user settings: {e}")
        return {}
    
    @property
    def min_profit_target(self):
        """Get profit target, reloading from settings every 60 seconds"""
        self._maybe_reload_settings()
        return self._min_profit_target
    
    @property
    def max_loss_cutoff(self):
        """Get stop-loss, reloading from settings every 60 seconds"""
        self._maybe_reload_settings()
        return self._max_loss_cutoff
    
    @property
    def position_size_limit(self):
        """Get position size limit, reloading from settings every 60 seconds"""
        self._maybe_reload_settings()
        return self._position_size_limit
    
    def _maybe_reload_settings(self):
        """Reload settings from file every 60 seconds"""
        now = time.time()
        if now - self._last_settings_check > 60:
            settings = self._load_user_settings()
            if settings:
                if 'min_profit_target' in settings:
                    self._min_profit_target = settings['min_profit_target'] / 100.0  # Convert % to decimal
                if 'max_loss_cutoff' in settings:
                    self._max_loss_cutoff = settings['max_loss_cutoff'] / 100.0  # Convert % to decimal
                if 'max_position_size' in settings:
                    self._position_size_limit = settings['max_position_size']
                if 'portfolio_drawdown_limit' in settings:
                    self.portfolio_drawdown_limit = settings['portfolio_drawdown_limit'] / 100.0  # Convert % to decimal
                logger.debug(f"Reloaded settings: profit={self._min_profit_target*100:.1f}%, stop-loss={self._max_loss_cutoff*100:.1f}%, size={self._position_size_limit}€, drawdown_limit={self.portfolio_drawdown_limit*100:.1f}%")
            self._last_settings_check = now
    
    def _validate_buy_signal(self, predicted_move: float) -> Tuple[bool, str]:
        """
        Validate if we should execute a buy signal.
        
        Returns:
            (is_valid, reason)
        """
        # Check profit gate
        if predicted_move < self.profit_gate_threshold:
            margin = (self.profit_gate_threshold - predicted_move) * 100
            return False, f"Below profit gate: need +{margin:.2f}bps more"
        
        return True, "Profit gate passed"
    
    def _validate_sell_signal(self, current_drawdown: float,
                             unrealized_pnl_pct: float,
                             unrealized_pnl: float = 0,
                             current_value: float = 0,
                             current_price: float = 0,
                             symbol: str = '') -> Tuple[bool, str]:
        """
        Validate if we should execute a sell signal.
        Fee-aware: profit exits only if net P&L after all real trading costs
        is positive (sell fee + spread/slippage).
        Note: Network fees excluded — only apply to withdrawals, not spot trades.
        
        Returns:
            (is_valid, reason)
        """
        # Sell if stop-loss triggered (always, regardless of fees)
        if unrealized_pnl_pct < self.max_loss_cutoff:
            return True, f"Stop-loss triggered at {unrealized_pnl_pct*100:.2f}%"

        from config.settings import TAKER_FEE, TRADING_MODE
        SPREAD_BUFFER = 0.001  # 0.1% spread/slippage estimate
        min_roundtrip_cost = current_value * (TAKER_FEE * 2 + SPREAD_BUFFER)
        net_pnl = unrealized_pnl - min_roundtrip_cost

        if TRADING_MODE == "scalping":
            # Scalping-Gewinnbetrag dynamisch aus user_settings.json laden
            try:
                with open(USER_SETTINGS_PATH, 'r') as f:
                    settings = json.load(f)
                min_abs_profit = float(settings.get('scalping_profit_abs', 0.25))
            except Exception as e:
                logger.warning(f"Konnte scalping_profit_abs nicht laden: {e}, fallback auf 0.25")
                min_abs_profit = 0.25
            if net_pnl >= min_abs_profit:
                return True, f"Scalping: Sicherer Gewinn nach Kosten >= {min_abs_profit:.2f}€ erreicht ({net_pnl:.2f}€)"
            else:
                return False, f"Scalping: Sicherer Gewinn nach Kosten < {min_abs_profit:.2f}€ ({net_pnl:.2f}€)"

        # Sonst wie bisher: min_profit_target + Kosten
        if unrealized_pnl_pct > self.min_profit_target:
            if current_value <= 0:
                return False, "Invalid position data (current_value=0), cannot sell"
            if net_pnl <= 0:
                return False, f"Profit target met ({unrealized_pnl_pct*100:.2f}%) but net after all trading costs is negative"
            return True, f"Profit target reached at {unrealized_pnl_pct*100:.2f}%"

        return False, "No exit condition met"
    
    def evaluate(self, symbol: str,
                predicted_move: float,
                confidence: float,
                current_positions: Dict,
                account_stats: Dict,
                current_time: int = None) -> Optional[TradeSignal]:
        """
        Evaluate current market conditions and generate trading signal.
        
        Args:
            symbol: Trading pair
            predicted_move: Predicted price change (e.g., 0.015 for +1.5%)
            confidence: Model confidence [0-1]
            current_positions: Dict of open positions {symbol: position_data}
            account_stats: Dict with 'total_pnl', 'initial_balance', etc.
            current_time: Unix timestamp (uses current time if None)
        
        Returns:
            TradeSignal or None if no signal
        """
        if current_time is None:
            current_time = int(time.time())
        
        # Check portfolio-wide drawdown limit
        portfolio_drawdown = account_stats.get('portfolio_drawdown', 0)
        if portfolio_drawdown < self.portfolio_drawdown_limit:
            logger.warning(f"Portfolio drawdown {portfolio_drawdown*100:.2f}% exceeds limit, pausing new trades")
            return None
        
        # Count existing positions for this symbol (handles composite keys like BTC/EUR_7)
        symbol_position_count = sum(
            1 for k, v in current_positions.items()
            if (getattr(v, 'original_symbol', None) or k) == symbol
        )
        
        # If already at per-symbol limit, no new buy
        if symbol_position_count >= self.max_positions_per_symbol:
            return None
        
        # New position: check profit gate
        should_buy, reason = self._validate_buy_signal(predicted_move)
        
        if not should_buy:
            return None
        
        # Check position count limit
        open_count = len(current_positions)
        if open_count >= self.max_positions:
            logger.debug(f"Max open positions ({self.max_positions}) reached")
            return None
        
        # Calculate position size (use full available or limit)
        available = account_stats.get('available_balance', 0)
        position_size = min(self.position_size_limit, available)
        
        if position_size <= 0:
            logger.debug(f"Insufficient balance for {symbol}")
            return None
        
        return TradeSignal(
            symbol=symbol,
            signal=Signal.BUY,
            predicted_move=predicted_move,
            confidence=confidence,
            timestamp=current_time,
            reason=f"Profit gate: move={predicted_move*100:.2f}%, conf={confidence:.2f}",
            position_size=position_size
        )
    
    def evaluate_multiple(self, predictions: Dict,
                         current_positions: Dict,
                         account_stats: Dict,
                         max_new_trades: int = 2) -> list:
        """
        Evaluate multiple symbols and generate signals.
        Prioritizes by confidence if multiple BUY signals.
        
        Args:
            predictions: {symbol: (predicted_move, confidence)}
            current_positions: Open positions dict
            account_stats: Account statistics
            max_new_trades: Max new trades per evaluation
        
        Returns:
            List of TradeSignal objects
        """
        signals = []
        
        for symbol, (predicted_move, confidence) in predictions.items():
            signal = self.evaluate(symbol, predicted_move, confidence,
                                 current_positions, account_stats)
            if signal:
                signals.append(signal)
        
        # Sort BUY signals by confidence (highest first)
        buy_signals = [s for s in signals if s.signal == Signal.BUY]
        sell_signals = [s for s in signals if s.signal == Signal.SELL]
        
        buy_signals.sort(key=lambda s: s.confidence, reverse=True)
        
        # Limit new BUY trades
        buy_signals = buy_signals[:max_new_trades]
        
        # Combine (SELL signals always execute)
        final_signals = sell_signals + buy_signals
        
        return final_signals
    
    def get_risk_metrics(self, predictions: Dict,
                        current_positions: Dict) -> Dict:
        """
        Calculate risk metrics for the current state.
        
        Returns:
            Dict with risk metrics
        """
        num_buy_signals = sum(1 for move, _ in predictions.values() if move > self.profit_gate_threshold)
        num_positions = len(current_positions)
        
        metrics = {
            'num_buy_signals': num_buy_signals,
            'num_open_positions': num_positions,
            'position_capacity': self.max_positions - num_positions,
            'profit_gate_threshold': self.profit_gate_threshold,
            'min_profit_target': self.min_profit_target,
            'max_loss_cutoff': self.max_loss_cutoff,
        }
        
        return metrics
    
    def __repr__(self) -> str:
        return f"""ProfitGateStrategy(
    profit_gate={self.profit_gate_threshold*100:.2f}%,
    profit_target={self.min_profit_target*100:.2f}%,
    stop_loss={self.max_loss_cutoff*100:.2f}%,
    portfolio_limit={self.portfolio_drawdown_limit*100:.2f}%,
    max_size={self.position_size_limit} EUR,
    max_positions={self.max_positions}
)"""
