"""
Global Configuration Settings
"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Base paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
MODELS_DIR = PROJECT_ROOT / "models"
USER_SETTINGS_FILE = PROJECT_ROOT / "config" / "user_settings.json"

def get_user_settings():
    """Load user settings from JSON file"""
    default_settings = {
        'max_position_size': 10.0,
        'max_loss_cutoff': -40.0,      # Stop-loss in % (negative)
        'min_profit_target': 5.0       # Profit target in %
    }
    try:
        if USER_SETTINGS_FILE.exists():
            with open(USER_SETTINGS_FILE, 'r') as f:
                saved = json.load(f)
                return {**default_settings, **saved}
    except Exception:
        pass
    return default_settings

# Budget Configuration
# Note: These limits apply to the automated bot only.
# The web dashboard uses your actual Kraken balance.
_user_settings = get_user_settings()
MAX_POSITION_SIZE = _user_settings.get('max_position_size', 10.0)  # EUR per position (dynamic from UI)

# Trading Mode: 'conservative' or 'aggressive' (dynamic from UI)
TRADING_MODE = _user_settings.get('trading_mode', 'conservative')

# Mode-dependent parameters
if TRADING_MODE == 'scalping':
    MAX_OPEN_POSITIONS = 20
    MAX_POSITIONS_PER_SYMBOL = 6    # Up to 6 positions per coin in scalping
    PROFIT_GATE_THRESHOLD = 0.005   # 0.5% (just above 0.52% round-trip fees) - Scalping
    MAX_NEW_TRADES_PER_CYCLE = 5
elif TRADING_MODE == 'aggressive':
    MAX_OPEN_POSITIONS = 6
    MAX_POSITIONS_PER_SYMBOL = 1    # 1 position per coin
    PROFIT_GATE_THRESHOLD = 0.007   # 0.7% (0.52% fees + 0.18% margin) - Microtrading
    MAX_NEW_TRADES_PER_CYCLE = 3
else:
    MAX_OPEN_POSITIONS = 3
    MAX_POSITIONS_PER_SYMBOL = 1    # 1 position per coin
    PROFIT_GATE_THRESHOLD = 0.0142  # 1.42% (0.52% fees + 0.9% safety margin) - Conservative
    MAX_NEW_TRADES_PER_CYCLE = 1

# Trading Configuration (dynamic from UI)
MIN_PROFIT_TARGET = _user_settings.get('min_profit_target', 5.0) / 100.0  # Convert % to decimal
MAX_LOSS_CUTOFF = _user_settings.get('max_loss_cutoff', -40.0) / 100.0  # Convert % to decimal
PORTFOLIO_DRAWDOWN_LIMIT = _user_settings.get('portfolio_drawdown_limit', -10.0) / 100.0  # Dynamic from UI

# Fee Configuration (Kraken Spot Trading)
MAKER_FEE = 0.0016  # 0.16%
TAKER_FEE = 0.0026  # 0.26%

# Coins (Whitelist)
# Note: Using EUR instead of USDT due to Kraken EU restrictions on USDT trading for DE accounts
# withdrawal_fee: Only applies when transferring coins OFF Kraken to an external wallet (not for trading!)
#                 Trading fees are defined above (MAKER_FEE/TAKER_FEE) and apply to every buy/sell.
# min_volume: Minimum 24h trading volume in EUR for liquidity filtering
ALLOWED_COINS = {
    'BTC/EUR': {'withdrawal_fee': 0.0005, 'min_volume': 100000},  # 0.0005 BTC
    'ETH/EUR': {'withdrawal_fee': 0.015, 'min_volume': 100000},   # 0.015 ETH
    'SOL/EUR': {'withdrawal_fee': 0.00025, 'min_volume': 50000},  # 0.00025 SOL
    'XRP/EUR': {'withdrawal_fee': 0.10, 'min_volume': 50000},     # 0.10 XRP
    'ADA/EUR': {'withdrawal_fee': 0.70, 'min_volume': 50000},     # 0.70 ADA
    # 'DOGE/EUR': {'withdrawal_fee': 1.00, 'min_volume': 50000},    # 1.00 DOGE (entfernt: nicht mehr kaufen)
}

# Data Configuration
DATA_CHECK_INTERVAL = _user_settings.get('check_interval', 60)  # Dynamic from UI
LOOKBACK_MINUTES = 15  # 15-minute candles
LOOKBACK_PERIODS = 60  # Use 60 × 15-min = 15 hours for model input
PREDICTION_HORIZON = 4  # Predict 4 × 15-min = 1 hour ahead

# ML Model Configuration
LSTM_HIDDEN_SIZE = 128
LSTM_NUM_LAYERS = 2
LSTM_DROPOUT = 0.2
NUM_FEATURES = 12  # Number of engineered features

# Backtesting Configuration
BACKTEST_START_DATE = None  # Will be set dynamically (6-12 months back)
BACKTEST_END_DATE = None
BACKTEST_INITIAL_CASH = 100.0  # EUR - starting cash for simulations

# Kraken API (load from environment)
KRAKEN_API_KEY = os.getenv('KRAKEN_API_KEY', '')
KRAKEN_API_SECRET = os.getenv('KRAKEN_API_SECRET', '')

# Logging Configuration
LOG_LEVEL = 'INFO'
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# Auto-Retrain Configuration
RETRAIN_INTERVAL_HOURS = 24      # Retrain model every 24 hours
RETRAIN_DAYS_OF_DATA = 180       # Use 180 days of historical data for training
RETRAIN_ENABLED = True           # Enable/disable automatic retraining

# Alert thresholds
CRITICAL_BALANCE_LEVEL = 5.0   # EUR - Emergency stop below this
WARNING_BALANCE_LEVEL = 20.0   # EUR - Warning below this
