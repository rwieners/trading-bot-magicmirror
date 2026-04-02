#!/usr/bin/env python3
"""
Run Backtest on Historical Data
Tests trading strategy on historical data with walk-forward validation.
"""

import sys
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import *
from broker.data.live_feed import LiveDataFeed
from broker.strategies.profit_gate_strategy import ProfitGateStrategy
from backtesting.backtest import Backtester
from broker.utils.logger import setup_logging
import numpy as np

logger = logging.getLogger(__name__)


def run_backtest(symbol: str = 'BTC/EUR', days: int = 90):
    """
    Run backtest on historical data.
    
    Args:
        symbol: Trading pair to backtest
        days: Number of days of history
    """
    logger.info(f"Starting backtest for {symbol} ({days} days)")
    
    # Fetch historical data
    feed = LiveDataFeed()
    logger.info(f"Fetching {days} days of historical data...")
    
    num_candles = days * 96  # 96 candles per day @ 15min
    ohlcv = feed.fetch_ohlcv(symbol, timeframe='15m', limit=num_candles)
    
    if not ohlcv:
        logger.error("Failed to fetch data")
        return
    
    logger.info(f"Fetched {len(ohlcv)} candles")
    
    # Convert to numpy array
    ohlcv_array = np.array(ohlcv, dtype=np.float32)
    
    # Create strategy
    strategy = ProfitGateStrategy(
        profit_gate_threshold=PROFIT_GATE_THRESHOLD,
        min_profit_target=MIN_PROFIT_TARGET,
        max_loss_cutoff=MAX_LOSS_CUTOFF,
        portfolio_drawdown_limit=PORTFOLIO_DRAWDOWN_LIMIT,
        position_size_limit=MAX_POSITION_SIZE,
        max_positions=MAX_OPEN_POSITIONS
    )
    
    # Run backtest
    logger.info("Running backtest...")
    backtester = Backtester(strategy=strategy)
    results = backtester.run_walk_forward(symbol, ohlcv_array)
    
    # Print results
    results.print_summary()
    
    logger.info(f"✓ Backtest completed for {symbol}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Run backtest")
    parser.add_argument('--symbol', default='BTC/EUR', help='Trading pair')
    parser.add_argument('--days', type=int, default=90, help='Days of history')
    
    args = parser.parse_args()
    
    setup_logging()
    run_backtest(symbol=args.symbol, days=args.days)
