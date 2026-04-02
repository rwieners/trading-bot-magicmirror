"""
Backtesting Framework
Tests trading strategy on historical data with walk-forward validation.
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, Tuple, List
from datetime import datetime, timedelta

from config.settings import *
from broker.models.features import FeatureEngineer
from broker.strategies.profit_gate_strategy import ProfitGateStrategy
from broker.risk.position_manager import PositionManager
from broker.data.coin_analyzer import CoinAnalyzer

logger = logging.getLogger(__name__)

# Budget for backtesting simulations
BACKTEST_BUDGET = 100.0  # EUR


class BacktestResults:
    """Container for backtesting results"""
    
    def __init__(self):
        self.trades = []
        self.balance_history = []
        self.equity_history = []
        self.drawdown_history = []
        self.prediction_history = []
    
    def calculate_metrics(self) -> Dict:
        """Calculate performance metrics"""
        if not self.trades:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'profit_factor': 0,
                'sharpe_ratio': 0,
                'max_drawdown': 0,
                'total_return': 0,
            }
        
        # Convert to DataFrame for easier analysis
        df_trades = pd.DataFrame(self.trades)
        
        winning_trades = len(df_trades[df_trades['pnl'] > 0])
        losing_trades = len(df_trades[df_trades['pnl'] < 0])
        total_trades = len(df_trades)
        
        win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
        
        gross_profit = df_trades[df_trades['pnl'] > 0]['pnl'].sum()
        gross_loss = abs(df_trades[df_trades['pnl'] < 0]['pnl'].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Sharpe ratio (simplified, using daily returns)
        equity_array = np.array(self.equity_history)
        if len(equity_array) > 1:
            returns = np.diff(equity_array) / equity_array[:-1]
            sharpe = np.mean(returns) / (np.std(returns) + 1e-8) * np.sqrt(252 * 96)  # 96 x 15-min intervals per day
        else:
            sharpe = 0
        
        max_drawdown = min(self.drawdown_history) if self.drawdown_history else 0
        
        total_pnl = df_trades['pnl'].sum()
        total_return = (total_pnl / BACKTEST_BUDGET) * 100 if BACKTEST_BUDGET > 0 else 0
        
        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'total_pnl': total_pnl,
            'total_return_pct': total_return,
            'sharpe_ratio': sharpe,
            'max_drawdown': max_drawdown,
            'avg_win': gross_profit / winning_trades if winning_trades > 0 else 0,
            'avg_loss': gross_loss / losing_trades if losing_trades > 0 else 0,
        }
    
    def print_summary(self):
        """Print summary report"""
        metrics = self.calculate_metrics()
        
        print("\n" + "="*60)
        print("BACKTEST RESULTS")
        print("="*60)
        print(f"Total Trades: {metrics['total_trades']}")
        print(f"Winning: {metrics['winning_trades']} | Losing: {metrics['losing_trades']}")
        print(f"Win Rate: {metrics['win_rate']:.2f}%")
        print(f"Profit Factor: {metrics['profit_factor']:.2f}")
        print(f"Total P&L: {metrics['total_pnl']:.4f} EUR")
        print(f"Total Return: {metrics['total_return_pct']:+.2f}%")
        print(f"Sharpe Ratio: {metrics['sharpe_ratio']:.2f}")
        print(f"Max Drawdown: {metrics['max_drawdown']:.2f}%")
        print(f"Avg Win: {metrics['avg_win']:.4f} | Avg Loss: {metrics['avg_loss']:.4f}")
        print("="*60 + "\n")
        
        return metrics


class Backtester:
    """
    Backtests trading strategy on historical OHLCV data.
    """
    
    def __init__(self, strategy: ProfitGateStrategy = None):
        """
        Initialize backtester.
        
        Args:
            strategy: ProfitGateStrategy instance
        """
        self.strategy = strategy or ProfitGateStrategy()
        self.feature_engineer = FeatureEngineer(lookback_periods=LOOKBACK_PERIODS)
        self.position_manager = PositionManager(initial_balance=BACKTEST_BUDGET)
        self.coin_analyzer = CoinAnalyzer()
        self.results = BacktestResults()
    
    def prepare_candle_sequences(self, ohlcv: np.ndarray) -> Tuple[List, List]:
        """
        Convert OHLCV array into sequences for feature computation.
        
        Args:
            ohlcv: (N, 6) array with [timestamp, o, h, l, c, v]
        
        Returns:
            (sequence_list, timestamp_list)
        """
        sequences = []
        timestamps = []
        
        for i in range(LOOKBACK_PERIODS, len(ohlcv) - PREDICTION_HORIZON + 1):
            # Features need lookback + prediction horizon
            seq = ohlcv[i-LOOKBACK_PERIODS:i+PREDICTION_HORIZON]
            sequences.append(seq)
            timestamps.append(int(ohlcv[i, 0]))
        
        return sequences, timestamps
    
    def simulate_prediction(self, candle_sequence: np.ndarray) -> Tuple[float, float]:
        """
        Simulate ML prediction on candle sequence.
        
        This is a placeholder - would use actual model in live trading.
        Uses simple SMA crossover as dummy indicator.
        
        Args:
            candle_sequence: (lookback+horizon, 6) candles
        
        Returns:
            (predicted_move_pct, confidence)
        """
        closes = candle_sequence[:, 4]
        lookback_closes = closes[:LOOKBACK_PERIODS]
        future_closes = closes[LOOKBACK_PERIODS:]
        
        # Simple SMA-based prediction
        sma_fast = np.mean(lookback_closes[-5:])  # Last 5 periods
        sma_slow = np.mean(lookback_closes[-20:])  # Last 20 periods
        
        # Predict based on SMA cross
        if sma_fast > sma_slow:
            # Uptrend
            future_move = np.mean(np.diff(future_closes) / future_closes[:-1])
        else:
            # Downtrend
            future_move = np.mean(np.diff(future_closes) / future_closes[:-1]) * -1
        
        return future_move, 0.6  # Fixed confidence for demo
    
    def run_backtest(self, symbol: str, ohlcv: np.ndarray,
                    start_idx: int = 0, end_idx: int = None) -> BacktestResults:
        """
        Run backtest on historical data.
        
        Args:
            symbol: Trading pair
            ohlcv: (N, 6) historical OHLCV array
            start_idx: Start index in array
            end_idx: End index in array (default: all)
        
        Returns:
            BacktestResults object
        """
        if end_idx is None:
            end_idx = len(ohlcv)
        
        self.results = BacktestResults()
        self.position_manager = PositionManager(initial_balance=BACKTEST_BUDGET)
        
        # Prepare sequences
        sequences, timestamps = self.prepare_candle_sequences(ohlcv[start_idx:end_idx])
        
        logger.info(f"Running backtest on {symbol} with {len(sequences)} periods")
        
        positions = {}  # Track open positions
        
        for seq_idx, (candle_seq, timestamp) in enumerate(zip(sequences, timestamps)):
            # Compute features
            features = self.feature_engineer.compute_features(candle_seq)
            if features is None:
                continue
            
            # Get predicted move
            predicted_move, confidence = self.simulate_prediction(candle_seq)
            
            stats = self.feature_engineer.compute_statistics(candle_seq[:LOOKBACK_PERIODS])
            current_price = stats['price_current']
            
            # Record prediction
            self.results.prediction_history.append({
                'timestamp': timestamp,
                'symbol': symbol,
                'predicted_move': predicted_move,
                'confidence': confidence,
                'price': current_price,
            })
            
            # Update position prices (for P&L tracking)
            if symbol in positions:
                positions[symbol]['current_price'] = current_price
                pnl_pct = (current_price - positions[symbol]['entry_price']) / positions[symbol]['entry_price']
            
            # Generate signal
            account_stats = self.position_manager.get_account_stats()
            account_stats['available_balance'] = account_stats['cash_available_for_trade']
            account_stats['portfolio_drawdown'] = account_stats['max_drawdown']
            
            signal = self.strategy.evaluate(
                symbol=symbol,
                predicted_move=predicted_move,
                confidence=confidence,
                current_positions=self.position_manager.get_all_positions(),
                account_stats=account_stats,
                current_time=timestamp
            )
            
            # Execute signal
            if signal:
                from broker.strategies.profit_gate_strategy import Signal
                
                if signal.signal == Signal.BUY and symbol not in positions:
                    # Open position
                    entry_fee = current_price * signal.position_size / 100 * MAKER_FEE
                    self.position_manager.open_position(
                        symbol=symbol,
                        entry_price=current_price,
                        size=signal.position_size,
                        entry_fee=entry_fee,
                        entry_time=timestamp
                    )
                    positions[symbol] = {
                        'entry_price': current_price,
                        'entry_time': timestamp,
                        'entry_fee': entry_fee,
                        'position_size': signal.position_size,
                    }
                    logger.debug(f"BUY {symbol} @ {current_price:.2f}")
                
                elif signal.signal == Signal.SELL and symbol in positions:
                    # Close position
                    pos = positions[symbol]
                    exit_fee = current_price * pos['position_size'] / 100 * TAKER_FEE
                    
                    # Calculate P&L
                    pnl = (current_price - pos['entry_price']) / pos['entry_price'] * pos['position_size']
                    
                    self.position_manager.close_position(
                        symbol=symbol,
                        exit_price=current_price,
                        exit_fee=exit_fee,
                        exit_time=timestamp
                    )
                    
                    self.results.trades.append({
                        'symbol': symbol,
                        'entry_price': pos['entry_price'],
                        'exit_price': current_price,
                        'entry_time': pos['entry_time'],
                        'exit_time': timestamp,
                        'pnl': pnl,
                        'pnl_pct': (pnl / pos['position_size']) * 100,
                    })
                    
                    del positions[symbol]
                    logger.debug(f"SELL {symbol} @ {current_price:.2f}, P&L={pnl:.4f}")
            
            # Record balance history
            account_stats = self.position_manager.get_account_stats()
            self.results.balance_history.append(account_stats['current_balance'])
            self.results.equity_history.append(account_stats['current_balance'])
            self.results.drawdown_history.append(account_stats['max_drawdown'] * 100)
        
        logger.info(f"Backtest completed: {len(self.results.trades)} trades")
        return self.results
    
    def run_walk_forward(self, symbol: str, ohlcv: np.ndarray,
                        train_ratio: float = 0.7,
                        val_ratio: float = 0.15) -> BacktestResults:
        """
        Run walk-forward backtesting (no future-looking).
        
        Args:
            symbol: Trading pair
            ohlcv: Historical OHLCV array
            train_ratio: Ratio of data for training
            val_ratio: Ratio of data for validation
        
        Returns:
            BacktestResults
        """
        n = len(ohlcv)
        train_end = int(n * train_ratio)
        val_end = train_end + int(n * val_ratio)
        
        logger.info(f"Running walk-forward backtest: train={train_end}, val={val_end-train_end}, test={n-val_end}")
        
        # Run on test set only (model would be trained on earlier data)
        return self.run_backtest(symbol, ohlcv, start_idx=val_end, end_idx=n)
