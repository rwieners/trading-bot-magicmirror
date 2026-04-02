"""
Feature Engineering for ML Model Input
Computes technical indicators and features from OHLCV data.
"""

import logging
import numpy as np
import pandas as pd
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class FeatureEngineer:
    """
    Computes technical indicators and engineered features from price data.
    Output: Normalized feature vector for LSTM model input.
    """
    
    def __init__(self, lookback_periods: int = 60):
        """
        Initialize feature engineer.
        
        Args:
            lookback_periods: Number of historical periods to use
        """
        self.lookback_periods = lookback_periods
    
    def _compute_rsi(self, closes: np.ndarray, period: int = 14) -> np.ndarray:
        """
        Compute Relative Strength Index.
        
        Args:
            closes: Array of close prices
            period: RSI period (default 14)
        
        Returns:
            Array of RSI values [0-100]
        """
        deltas = np.diff(closes)
        seed = deltas[:period + 1]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period
        rs = up / down if down != 0 else 0
        rsi = np.zeros_like(closes)
        rsi[:period] = 100.0 - 100.0 / (1.0 + rs)
        
        for i in range(period, len(closes)):
            delta = deltas[i - 1]
            if delta > 0:
                upval = delta
                downval = 0.0
            else:
                upval = 0.0
                downval = -delta
            
            up = (up * (period - 1) + upval) / period
            down = (down * (period - 1) + downval) / period
            rs = up / down if down != 0 else 0
            rsi[i] = 100.0 - 100.0 / (1.0 + rs)
        
        return rsi
    
    def _compute_macd(self, closes: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Compute MACD (Moving Average Convergence Divergence).
        
        Returns:
            (macd_line, signal_line, histogram)
        """
        exp1 = pd.Series(closes).ewm(span=12, adjust=False).mean().values
        exp2 = pd.Series(closes).ewm(span=26, adjust=False).mean().values
        macd_line = exp1 - exp2
        signal_line = pd.Series(macd_line).ewm(span=9, adjust=False).mean().values
        histogram = macd_line - signal_line
        
        return macd_line, signal_line, histogram
    
    def _compute_sma(self, closes: np.ndarray, period: int) -> np.ndarray:
        """Compute Simple Moving Average"""
        return pd.Series(closes).rolling(window=period).mean().values
    
    def _compute_bollinger_bands(self, closes: np.ndarray, period: int = 20, 
                                 std_dev: float = 2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Compute Bollinger Bands.
        
        Returns:
            (upper_band, middle_band, lower_band)
        """
        series = pd.Series(closes)
        middle = series.rolling(window=period).mean().values
        std = series.rolling(window=period).std().values
        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)
        
        return upper, middle, lower
    
    def _compute_volatility(self, closes: np.ndarray, period: int = 20) -> np.ndarray:
        """Compute rolling volatility (std dev of returns)"""
        returns = np.diff(closes) / closes[:-1]
        volatility = pd.Series(returns).rolling(window=period).std().values
        
        # Pad first value
        volatility = np.concatenate([[volatility[0]], volatility])
        
        return volatility
    
    def _compute_volume_change(self, volumes: np.ndarray, period: int = 14) -> np.ndarray:
        """Compute rate of change of volume"""
        roc = (volumes / pd.Series(volumes).rolling(window=period).mean().values - 1) * 100
        return np.nan_to_num(roc, 0)
    
    def compute_features(self, candle_array: np.ndarray) -> Optional[np.ndarray]:
        """
        Compute all features from OHLCV candle array.
        
        Args:
            candle_array: (N, 6) array with [timestamp, o, h, l, c, v]
        
        Returns:
            (N, num_features) normalized feature array, or None on error
        """
        if candle_array is None or len(candle_array) < 30:
            logger.warning("Insufficient candle data for feature computation")
            return None
        
        try:
            # Extract OHLCV
            timestamps = candle_array[:, 0]
            opens = candle_array[:, 1]
            highs = candle_array[:, 2]
            lows = candle_array[:, 3]
            closes = candle_array[:, 4]
            volumes = candle_array[:, 5]
            
            # Compute indicators
            rsi = self._compute_rsi(closes)
            macd, macd_signal, macd_hist = self._compute_macd(closes)
            sma_20 = self._compute_sma(closes, 20)
            sma_50 = self._compute_sma(closes, 50)
            upper_bb, middle_bb, lower_bb = self._compute_bollinger_bands(closes)
            volatility = self._compute_volatility(closes)
            volume_change = self._compute_volume_change(volumes)
            
            # Price features
            returns = np.diff(closes) / closes[:-1]
            returns = np.concatenate([[0], returns])
            
            high_low_range = (highs - lows) / closes
            
            # Construct feature matrix (12 features)
            features = np.column_stack([
                closes,                    # 0: Close price (normalized later)
                rsi / 100.0,              # 1: RSI normalized [0-1]
                macd / np.std(macd[~np.isnan(macd)]) if np.std(macd[~np.isnan(macd)]) > 0 else macd,  # 2: MACD
                macd_hist / np.std(macd_hist[~np.isnan(macd_hist)]) if np.std(macd_hist[~np.isnan(macd_hist)]) > 0 else macd_hist,  # 3: MACD histogram
                (closes - sma_20) / sma_20,  # 4: Distance to SMA20 (%)
                (closes - sma_50) / sma_50,  # 5: Distance to SMA50 (%)
                (closes - lower_bb) / (upper_bb - lower_bb),  # 6: Position in BB [0-1]
                volatility,               # 7: Volatility
                returns,                  # 8: Returns
                volume_change / 100.0,    # 9: Volume change rate
                high_low_range,           # 10: High-Low range ratio
                volumes / np.mean(volumes[volumes > 0]),  # 11: Volume ratio to average
            ])
            
            # Handle NaN values
            features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)
            
            # Normalize to [-1, 1] range using Min-Max scaling (scale-invariant)
            # This works regardless of the absolute price range
            for i in range(features.shape[1]):
                col = features[:, i]
                col_min = np.min(col)
                col_max = np.max(col)
                col_range = col_max - col_min
                
                if col_range > 1e-8:
                    # Min-Max scale to [-1, 1]
                    features[:, i] = 2.0 * (col - col_min) / col_range - 1.0
                else:
                    # If no range (all same values), center at 0
                    features[:, i] = col - np.mean(col)
            
            logger.debug(f"Computed {features.shape[1]} features from {len(candle_array)} candles")
            return features
        
        except Exception as e:
            logger.error(f"Error computing features: {e}")
            return None
    
    def get_latest_feature_vector(self, candle_array: np.ndarray) -> Optional[np.ndarray]:
        """
        Get the latest feature vector (last row).
        
        Returns:
            (num_features,) array or None
        """
        features = self.compute_features(candle_array)
        if features is None:
            return None
        return features[-1]
    
    def get_feature_names(self) -> list:
        """Return list of feature names"""
        return [
            'close_price',
            'rsi',
            'macd',
            'macd_histogram',
            'distance_to_sma20',
            'distance_to_sma50',
            'bb_position',
            'volatility',
            'returns',
            'volume_change',
            'high_low_range',
            'volume_ratio',
        ]
    
    def compute_statistics(self, candle_array: np.ndarray) -> dict:
        """
        Compute useful statistics from candles.
        
        Returns:
            Dict with price stats, trend info, etc.
        """
        if candle_array is None or len(candle_array) == 0:
            return {}
        
        closes = candle_array[:, 4]
        volumes = candle_array[:, 5]
        
        stats = {
            'price_current': closes[-1],
            'price_min': np.min(closes),
            'price_max': np.max(closes),
            'price_avg': np.mean(closes),
            'price_std': np.std(closes),
            'change_pct': ((closes[-1] - closes[0]) / closes[0] * 100) if closes[0] != 0 else 0,
            'volume_total': np.sum(volumes),
            'volume_avg': np.mean(volumes),
            'trend': 'up' if closes[-1] > closes[-20] else 'down',
        }
        
        return stats
