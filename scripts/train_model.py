#!/usr/bin/env python3
"""
Train LSTM Model on Historical Data
Fetches data from Kraken for ALL configured coins and trains model
with walk-forward validation on the combined dataset.
"""

import sys
import logging
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import *
from broker.data.live_feed import LiveDataFeed
from broker.models.features import FeatureEngineer
from broker.models.model_trainer import ModelTrainer
from broker.models.lstm_model import LSTMPricePredictor, ModelManager
from broker.utils.logger import setup_logging
import numpy as np

logger = logging.getLogger(__name__)


def fetch_coin_features(feed: LiveDataFeed, symbol: str, days: int,
                        feature_engineer: FeatureEngineer) -> tuple:
    """
    Fetch OHLCV data for a single coin and compute features as 2D array.
    Also returns raw close prices for accurate target computation.
    
    Returns:
        (features_2d, raw_closes) or (empty_array, empty_array)
    """
    num_candles = days * 96  # 96 candles per day @ 15min
    
    logger.info(f"  Fetching {symbol} ({days} days, ~{num_candles} candles)...")
    ohlcv = feed.fetch_ohlcv(symbol, timeframe='15m', limit=num_candles)
    
    if not ohlcv or len(ohlcv) < LOOKBACK_PERIODS + PREDICTION_HORIZON + 10:
        logger.warning(f"  {symbol}: insufficient data ({len(ohlcv) if ohlcv else 0} candles)")
        return np.array([]), np.array([])
    
    logger.info(f"  {symbol}: got {len(ohlcv)} candles")
    ohlcv_array = np.array(ohlcv, dtype=np.float32)
    
    # Extract raw close prices BEFORE normalization (for target computation)
    raw_closes = ohlcv_array[:, 4].copy()
    
    # Compute normalized features on the FULL array → (N, 12) 2D array
    features = feature_engineer.compute_features(ohlcv_array)
    if features is None or len(features) == 0:
        logger.warning(f"  {symbol}: no features computed")
        return np.array([]), np.array([])
    
    logger.info(f"  {symbol}: {features.shape[0]} feature vectors")
    return features, raw_closes


def create_sequences(features_2d: np.ndarray, raw_closes: np.ndarray,
                     lookback: int, prediction_horizon: int) -> tuple:
    """
    Create LSTM input sequences (X) and targets (y) from 2D features.
    Targets are computed from RAW prices to avoid normalization artifacts.
    
    Returns:
        (X, y) where X is (N, lookback, num_features) and y is (N,)
    """
    n = len(features_2d)
    if n < lookback + prediction_horizon:
        return np.array([]), np.array([])
    
    X = []
    y = []
    
    for i in range(n - lookback - prediction_horizon + 1):
        # Input: sliding window of normalized features
        window = features_2d[i:i + lookback]
        X.append(window)
        
        # Target: price change from RAW (un-normalized) close prices
        current_price = raw_closes[i + lookback - 1]
        future_price = raw_closes[i + lookback + prediction_horizon - 1]
        
        if current_price > 0:
            price_change = (future_price - current_price) / current_price
        else:
            price_change = 0.0
        
        y.append(price_change)
    
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


def train_model(symbols: list = None, days: int = 180):
    """
    Train LSTM model on historical data from multiple coins.
    
    Training on multiple coins improves generalization: the model learns
    universal price patterns instead of overfitting to one coin's history.
    
    Args:
        symbols: List of trading pairs (default: all ALLOWED_COINS)
        days: Number of days of history to fetch per coin
    """
    if symbols is None:
        symbols = list(ALLOWED_COINS.keys())
    
    logger.info(f"=" * 60)
    logger.info(f"Multi-Coin LSTM Training")
    logger.info(f"Coins: {', '.join(symbols)}")
    logger.info(f"History: {days} days per coin")
    logger.info(f"=" * 60)
    
    feed = LiveDataFeed()
    feature_engineer = FeatureEngineer(lookback_periods=LOOKBACK_PERIODS)
    
    # Fetch data and create (X, y) sequences for each coin
    all_X = []
    all_y = []
    coins_ok = 0
    
    for symbol in symbols:
        features, raw_closes = fetch_coin_features(feed, symbol, days, feature_engineer)
        if len(features) == 0:
            continue
        
        # Create sequences with targets from raw prices
        X, y = create_sequences(features, raw_closes, LOOKBACK_PERIODS, PREDICTION_HORIZON)
        if len(X) > 0:
            all_X.append(X)
            all_y.append(y)
            coins_ok += 1
            logger.info(f"  {symbol}: {len(X)} training sequences created")
        
        # Rate limit: avoid hammering Kraken API
        time.sleep(1)
    
    if not all_X:
        logger.error("No training data from any coin — aborting training")
        return
    
    # Combine all coins' sequences
    X_combined = np.concatenate(all_X, axis=0)
    y_combined = np.concatenate(all_y, axis=0)
    
    # Shuffle to mix coins (prevents model from memorizing order)
    np.random.seed(42)
    shuffle_idx = np.random.permutation(len(X_combined))
    X_combined = X_combined[shuffle_idx]
    y_combined = y_combined[shuffle_idx]
    
    # Log target distribution
    logger.info(f"\nCombined dataset: {len(X_combined)} sequences from {coins_ok} coins")
    logger.info(f"  X shape: {X_combined.shape}")
    logger.info(f"  Target stats: mean={y_combined.mean():.6f}, std={y_combined.std():.6f}, "
                f"min={y_combined.min():.6f}, max={y_combined.max():.6f}")
    
    # Split: 70% train, 15% val, 15% test
    n = len(X_combined)
    train_end = int(n * 0.7)
    val_end = train_end + int(n * 0.15)
    
    X_train, y_train = X_combined[:train_end], y_combined[:train_end]
    X_val, y_val = X_combined[train_end:val_end], y_combined[train_end:val_end]
    X_test, y_test = X_combined[val_end:], y_combined[val_end:]
    
    logger.info(f"  Split: Train={len(X_train)}, Val={len(X_val)}, Test={len(X_test)}")
    
    # Create and train model
    logger.info("\nCreating model...")
    model = LSTMPricePredictor(
        input_size=NUM_FEATURES,
        hidden_size=LSTM_HIDDEN_SIZE,
        num_layers=2,
        dropout=LSTM_DROPOUT,
        output_size=1
    )
    
    trainer = ModelTrainer(model, learning_rate=0.001)
    
    logger.info("Training model...")
    history = trainer.train(
        X_train, y_train,
        X_val, y_val,
        epochs=80,
        batch_size=64,
        early_stopping_patience=15
    )
    
    # Evaluate on test set
    import torch
    from torch.utils.data import DataLoader, TensorDataset
    test_dataset = TensorDataset(torch.FloatTensor(X_test), torch.FloatTensor(y_test))
    test_loader = DataLoader(test_dataset, batch_size=64)
    test_loss, test_mae = trainer.validate(test_loader)
    history['test_loss'] = test_loss
    history['test_mae'] = test_mae
    logger.info(f"Test Results - Loss: {test_loss:.6f}, MAE: {test_mae:.6f}")
    
    # Save model
    logger.info("\nSaving model...")
    model_manager = ModelManager(model_dir=str(PROJECT_ROOT / "models"))
    model_manager.model = model
    model_manager.save_model("lstm_model")
    
    logger.info(f"\n{'=' * 60}")
    logger.info(f"✓ Multi-Coin Model Training Complete")
    logger.info(f"  Coins trained on: {coins_ok}/{len(symbols)}")
    logger.info(f"  Total sequences: {len(X_combined)}")
    logger.info(f"  Best epoch: {history.get('best_epoch', 0) + 1}")
    logger.info(f"  Test MAE: {test_mae:.6f}")
    logger.info(f"  Test Loss: {test_loss:.6f}")
    logger.info(f"  Model saved to: {PROJECT_ROOT / 'models' / 'lstm_model.pt'}")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Train LSTM model on multiple coins")
    parser.add_argument('--symbols', nargs='+', default=None,
                        help='Trading pairs (default: all configured coins)')
    parser.add_argument('--days', type=int, default=180, 
                        help='Days of history per coin (default: 180)')
    
    args = parser.parse_args()
    
    setup_logging()
    train_model(symbols=args.symbols, days=args.days)
