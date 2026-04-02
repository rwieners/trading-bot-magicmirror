"""
Position and Budget Management
Enforces hard limits on budget, position sizes, and open positions.
"""

import logging
import time
import sys
import os
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

# Import dynamic settings
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config.settings import get_user_settings, MAX_OPEN_POSITIONS, MAX_POSITIONS_PER_SYMBOL

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents an open position"""
    symbol: str
    entry_price: float
    entry_size: float
    entry_time: int
    entry_fee: float
    current_price: float = field(default=0.0)
    exit_price: Optional[float] = field(default=None)
    exit_time: Optional[int] = field(default=None)
    status: str = field(default='OPEN')  # OPEN, CLOSED, CLOSED_LOSS, CLOSED_PROFIT
    trade_id: Optional[int] = field(default=None)  # DB trade ID for individual tracking
    original_symbol: Optional[str] = field(default=None)  # Original symbol (e.g., BTC/EUR) when using composite key
    
    @property
    def entry_value(self) -> float:
        """Total entry value including fees"""
        return self.entry_price * self.entry_size + self.entry_fee
    
    @property
    def current_value(self) -> float:
        """Current position value"""
        return self.current_price * self.entry_size
    
    @property
    def unrealized_pnl(self) -> float:
        """Unrealized P&L in EUR"""
        return self.current_value - self.entry_value
    
    @property
    def unrealized_pnl_pct(self) -> float:
        """Unrealized P&L as percentage"""
        if self.entry_value == 0:
            return 0
        return self.unrealized_pnl / self.entry_value
    
    @property
    def current_drawdown(self) -> float:
        """Current drawdown from entry"""
        if self.entry_price == 0:
            return 0
        return (self.current_price - self.entry_price) / self.entry_price


class PositionManager:
    """
    Manages open positions and enforces budget constraints.
    Uses actual Kraken balance - no artificial limits.
    
    Configurable limits:
    - Max per position: From user settings (default 10 EUR)
    - Max positions: 3
    """
    
    # MAX_POSITION_SIZE is now loaded dynamically from user settings
    _max_open_positions_override = None  # Set by bot._apply_trading_mode()
    _max_positions_per_symbol_override = None  # Set by bot._apply_trading_mode()
    
    @property
    def MAX_OPEN_POSITIONS(self):
        """Load max open positions dynamically (mode-aware)"""
        if self._max_open_positions_override is not None:
            return self._max_open_positions_override
        return MAX_OPEN_POSITIONS
    
    @MAX_OPEN_POSITIONS.setter
    def MAX_OPEN_POSITIONS(self, value):
        self._max_open_positions_override = value
    
    @property
    def MAX_POSITIONS_PER_SYMBOL(self):
        """Max concurrent positions per symbol (mode-aware). Default 1."""
        if self._max_positions_per_symbol_override is not None:
            return self._max_positions_per_symbol_override
        return MAX_POSITIONS_PER_SYMBOL
    
    @MAX_POSITIONS_PER_SYMBOL.setter
    def MAX_POSITIONS_PER_SYMBOL(self, value):
        self._max_positions_per_symbol_override = value
    
    @property
    def MAX_POSITION_SIZE(self):
        """Load max position size from user settings (dynamic)"""
        return get_user_settings().get('max_position_size', 10.0)
    
    def __init__(self, initial_balance: float = None, db = None):
        """
        Initialize position manager.
        
        Args:
            initial_balance: Starting balance (defaults to loading from DB or 0)
            db: Optional TradeDatabase to load actual portfolio balance from
        """
        # Try to load actual balance from database (for repeated runs)
        actual_balance = initial_balance or 0
        if db:
            try:
                latest = db.get_latest_account_balance()
                if latest and latest['balance']:
                    actual_balance = latest['balance']
                    logger.info(f"Loaded balance from database: {actual_balance:.2f} EUR")
            except Exception as e:
                logger.warning(f"Could not load balance from database: {e}. Using provided balance.")
        
        if actual_balance <= 0:
            logger.warning(f"No balance available (got {actual_balance}). Positions may fail.")
            actual_balance = 0
        
        self.initial_balance = actual_balance
        self.current_balance = actual_balance  # May be different if trading occurred
        self.cash = actual_balance
        self.positions: Dict[str, Position] = {}
        self.closed_positions: list = []
        self.peak_balance = actual_balance
    
    def count_positions_for_symbol(self, symbol: str) -> int:
        """Count open positions for a given symbol (handles composite keys like BTC/EUR_7)."""
        count = 0
        for key, pos in self.positions.items():
            original = getattr(pos, 'original_symbol', None) or key
            if original == symbol:
                count += 1
        return count
    
    def can_open_position(self, symbol: str, size: float) -> Tuple[bool, str]:
        """
        Check if a new position can be opened.
        
        Args:
            symbol: Trading pair (plain symbol like BTC/EUR)
            size: Position size in EUR
        
        Returns:
            (can_open, reason)
        """
        # Check per-symbol limit (supports multiple positions per coin in scalping mode)
        symbol_count = self.count_positions_for_symbol(symbol)
        if symbol_count >= self.MAX_POSITIONS_PER_SYMBOL:
            return False, f"Max {self.MAX_POSITIONS_PER_SYMBOL} position(s) already open for {symbol}"
        
        # Check max open positions
        if len(self.positions) >= self.MAX_OPEN_POSITIONS:
            return False, f"Max {self.MAX_OPEN_POSITIONS} positions already open"
        
        # Check position size limit
        if size > self.MAX_POSITION_SIZE:
            return False, f"Position size {size} > limit {self.MAX_POSITION_SIZE}"
        
        # Check available balance
        if size > self.cash:
            return False, f"Insufficient cash: need {size}, have {self.cash:.2f}"
        
        return True, "OK"
    
    def open_position(self, symbol: str, entry_price: float, size: float,
                     entry_fee: float, entry_time: int = None) -> Tuple[bool, str]:
        """
        Open a new position.
        
        Args:
            symbol: Trading pair
            entry_price: Price at entry
            size: Position size in EUR
            entry_fee: Fee paid at entry
            entry_time: Unix timestamp (uses current time if None)
        
        Returns:
            (success, reason)
        """
        if entry_time is None:
            entry_time = int(time.time())
        
        # Validate
        can_open, reason = self.can_open_position(symbol, size)
        if not can_open:
            logger.warning(f"Cannot open position for {symbol}: {reason}")
            return False, reason
        
        # Create position
        # Use composite key if there's already a position for this symbol
        if symbol in self.positions:
            suffix = 1
            while f"{symbol}_{suffix}" in self.positions:
                suffix += 1
            position_key = f"{symbol}_{suffix}"
        else:
            position_key = symbol
        
        position = Position(
            symbol=position_key,
            entry_price=entry_price,
            entry_size=size / entry_price,  # Convert EUR to coin amount
            entry_time=entry_time,
            entry_fee=entry_fee,
            current_price=entry_price,
            original_symbol=symbol  # Always store original for composite key lookups
        )
        
        self.positions[position_key] = position
        
        # Deduct from cash
        self.cash -= (size + entry_fee)
        self.current_balance = self.cash + sum(p.current_value for p in self.positions.values())
        
        logger.info(f"Opened {position_key} @ {entry_price:.2f}, size={size:.2f} EUR "
                   f"(Cash remaining: {self.cash:.2f})")
        
        return True, "Position opened"
    
    def import_position(self, symbol: str, amount: float, entry_price: float,
                       current_price: float, entry_time: int = None,
                       entry_fee: float = 0) -> Tuple[bool, str]:
        """
        Import an existing position from exchange (e.g., on bot restart).
        Does NOT deduct cash - assumes position already exists on exchange.
        Uses actual entry price from trade history for accurate P&L tracking.
        
        Args:
            symbol: Trading pair (e.g., 'BTC/EUR')
            amount: Amount of crypto held
            entry_price: Actual entry price from trade history
            current_price: Current market price
            entry_time: Unix timestamp (uses current time if None)
            entry_fee: Buy fee from DB (0 if unknown — will be estimated conservatively)
        
        Returns:
            (success, reason)
        """
        if entry_time is None:
            entry_time = int(time.time())
        
        # Skip if already tracking this position
        if symbol in self.positions:
            logger.debug(f"Already tracking position for {symbol}")
            return False, "Position already tracked"
        
        # Do NOT block import by max positions: Kraken is data master
        # (max positions only applies to new bot trades, not to imported positions)
        
        # If no entry_fee recorded in DB, estimate conservatively using taker fee
        if entry_fee <= 0:
            from config.settings import TAKER_FEE
            entry_fee = amount * entry_price * TAKER_FEE
            logger.debug(f"No entry_fee in DB for {symbol}, estimating: €{entry_fee:.4f}")
        
        # Create position with actual entry price from trade history
        value_eur = amount * current_price
        position = Position(
            symbol=symbol,
            entry_price=entry_price,  # Use actual entry price
            entry_size=amount,
            entry_time=entry_time,
            entry_fee=entry_fee,
            current_price=current_price
        )
        
        self.positions[symbol] = position
        
        # Update balance (don't deduct cash, just add position value)
        self.current_balance = self.cash + sum(p.current_value for p in self.positions.values())
        
        pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
        logger.info(f"Imported {symbol}: {amount:.6f} @ entry €{entry_price:.2f}, current €{current_price:.2f}, P&L: {pnl_pct:+.2f}%")
        
        return True, "Position imported"
    
    def close_position(self, symbol: str, exit_price: float,
                      exit_fee: float, exit_time: int = None) -> Tuple[bool, Dict]:
        """
        Close an open position.
        
        Args:
            symbol: Trading pair
            exit_price: Price at exit
            exit_fee: Fee paid at exit
            exit_time: Unix timestamp
        
        Returns:
            (success, position_stats)
        """
        if exit_time is None:
            exit_time = int(time.time())
        
        if symbol not in self.positions:
            logger.warning(f"No open position for {symbol}")
            return False, {}
        
        position = self.positions[symbol]
        position.exit_price = exit_price
        position.exit_time = exit_time
        position.current_price = exit_price
        
        # Calculate P&L
        exit_value = exit_price * position.entry_size
        gross_pnl = exit_value - (position.entry_price * position.entry_size)
        net_pnl = gross_pnl - position.entry_fee - exit_fee
        pnl_pct = (net_pnl / position.entry_value) * 100 if position.entry_value > 0 else 0
        
        # Determine status
        if net_pnl > 0:
            position.status = 'CLOSED_PROFIT'
        elif net_pnl < 0:
            position.status = 'CLOSED_LOSS'
        else:
            position.status = 'CLOSED'
        
        # Update cash
        self.cash += (exit_value - exit_fee)
        
        # Move to history, but skip junk trades with entry_value ≈ 0
        del self.positions[symbol]
        if position.entry_value > 1e-3:
            self.closed_positions.append(position)
        else:
            logger.info(f"[CLEANUP] Ignored closed trade with entry_value ≈ 0 for {symbol}")
        
        # Update balance
        self.current_balance = self.cash + sum(p.current_value for p in self.positions.values())
        
        # Update peak balance for max drawdown calculation
        if self.current_balance > self.peak_balance:
            self.peak_balance = self.current_balance
        
        stats = {
            'symbol': symbol,
            'entry_price': position.entry_price,
            'exit_price': exit_price,
            'gross_pnl': gross_pnl,
            'net_pnl': net_pnl,
            'pnl_pct': pnl_pct,
            'duration': exit_time - position.entry_time,
        }
        
        logger.info(f"Closed {symbol}: PnL={net_pnl:.4f} ({pnl_pct:.2f}%), "
                   f"Cash returned: {exit_value:.2f} EUR")
        
        return True, stats
    
    def update_position_price(self, symbol: str, current_price: float):
        """Update current price for a position (for P&L tracking)"""
        if symbol in self.positions:
            self.positions[symbol].current_price = current_price
            self.current_balance = self.cash + sum(p.current_value for p in self.positions.values())
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get an open position"""
        return self.positions.get(symbol)
    
    def get_all_positions(self) -> Dict[str, Position]:
        """Get all open positions"""
        return self.positions.copy()
    
    def get_account_stats(self) -> Dict:
        """Get comprehensive account statistics, filtering out junk trades with entry_value ≈ 0"""
        MIN_ENTRY_VALUE = 1e-3  # Ignore trades with entry_value below this
        filtered_positions = [p for p in self.positions.values() if p.entry_value > MIN_ENTRY_VALUE]
        filtered_closed = [p for p in self.closed_positions if p.entry_value > MIN_ENTRY_VALUE]

        total_open_value = sum(p.current_value for p in filtered_positions)
        total_unrealized_pnl = sum(p.unrealized_pnl for p in filtered_positions)

        closed_pnl = sum(p.unrealized_pnl for p in filtered_closed)
        total_realized_pnl = closed_pnl  # Closing P&L

        total_pnl = total_unrealized_pnl + total_realized_pnl

        # Maximum drawdown from peak
        max_drawdown = (self.current_balance - self.peak_balance) / self.peak_balance if self.peak_balance > 0 else 0

        stats = {
            'initial_balance': self.initial_balance,
            'current_balance': self.current_balance,
            'cash': self.cash,
            'cash_available_for_trade': self.cash,  # Full cash available
            'open_positions_value': total_open_value,
            'num_open_positions': len(filtered_positions),
            'unrealized_pnl': total_unrealized_pnl,
            'realized_pnl': total_realized_pnl,
            'total_pnl': total_pnl,
            'total_pnl_pct': (total_pnl / self.initial_balance) * 100 if self.initial_balance > 0 else 0,
            'max_drawdown': max_drawdown,
            'max_drawdown_pct': max_drawdown * 100,
            'peak_balance': self.peak_balance,
            'num_closed_trades': len(filtered_closed),
        }

        return stats
    
    def is_below_critical_balance(self) -> bool:
        """Check if balance has fallen below critical level (50% of initial)"""
        return self.initial_balance > 0 and self.current_balance < self.initial_balance * 0.5

    def is_below_warning_balance(self) -> bool:
        """Check if balance has fallen below warning level (95% of initial)"""
        return self.initial_balance > 0 and self.current_balance < self.initial_balance * 0.95
    
    def validate_hard_limits(self) -> tuple:
        """
        Validate that all limits are respected.
        
        Returns:
            (all_valid, list of violations)
        """
        violations = []
        
        if self.cash < 0:
            violations.append(f"Negative cash: {self.cash}")
        
        if len(self.positions) > self.MAX_OPEN_POSITIONS:
            violations.append(f"Too many positions: {len(self.positions)} > {self.MAX_OPEN_POSITIONS}")
        
        for symbol, position in self.positions.items():
            if position.entry_value > self.MAX_POSITION_SIZE * 1.01:
                violations.append(f"Position too large: {symbol} = {position.entry_value:.2f} > {self.MAX_POSITION_SIZE}")
        
        return len(violations) == 0, violations
    
    def __repr__(self) -> str:
        stats = self.get_account_stats()
        return f"""PositionManager(
    Balance: {stats['current_balance']:.2f} / {stats['initial_balance']:.2f} EUR
    Positions: {stats['num_open_positions']}/{self.MAX_OPEN_POSITIONS}
    PnL: {stats['total_pnl']:.4f} ({stats['total_pnl_pct']:.2f}%)
    Cash: {stats['cash']:.2f}
)"""
