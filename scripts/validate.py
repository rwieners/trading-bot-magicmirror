#!/usr/bin/env python3
"""
System Validation and Health Check
Validates all prerequisites before running the bot.
"""

import sys
import logging
from pathlib import Path
import os

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import KRAKEN_API_KEY, KRAKEN_API_SECRET, PROJECT_ROOT, LOGS_DIR, ALLOWED_COINS
from broker.data.live_feed import LiveDataFeed
from broker.data.coin_analyzer import CoinAnalyzer
from broker.exchange.kraken_trader import KrakenTrader
from broker.models.lstm_model import ModelManager
from broker.utils.logger import setup_logging

logger = logging.getLogger(__name__)


def check_environment():
    """Check Python and package versions"""
    print("\n" + "="*60)
    print("ENVIRONMENT CHECK")
    print("="*60)
    
    print(f"Python: {sys.version.split()[0]}")
    
    try:
        import torch
        print(f"PyTorch: {torch.__version__}")
    except:
        print("PyTorch: ✗ NOT INSTALLED")
    
    try:
        import ccxt
        print(f"CCXT: {ccxt.__version__}")
    except:
        print("CCXT: ✗ NOT INSTALLED")
    
    try:
        import pandas
        print(f"Pandas: {pandas.__version__}")
    except:
        print("Pandas: ✗ NOT INSTALLED")
    
    return True


def check_api_credentials():
    """Check Kraken API credentials"""
    print("\n" + "="*60)
    print("API CREDENTIALS CHECK")
    print("="*60)
    
    if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
        print("✗ KRAKEN_API_KEY or KRAKEN_API_SECRET not set in environment")
        print("  Set them in .env file:")
        print("    KRAKEN_API_KEY=your_key")
        print("    KRAKEN_API_SECRET=your_secret")
        return False
    
    print("✓ API credentials found")
    
    # Test connection
    try:
        exchange = KrakenTrader(api_key=KRAKEN_API_KEY, api_secret=KRAKEN_API_SECRET)
        ticker = exchange.fetch_ticker('BTC/EUR')
        if ticker:
            print(f"✓ Kraken connectivity OK (BTC price: {ticker['last']:.2f})")
        return True
    except Exception as e:
        print(f"✗ Kraken connectivity failed: {e}")
        return False


def check_data_feeds():
    """Check coin availability and trading readiness"""
    print("\n" + "="*60)
    print("DATA FEEDS & COINS CHECK")
    print("="*60)
    
    try:
        feed = LiveDataFeed()
        analyzer = CoinAnalyzer(allowed_coins=ALLOWED_COINS)
        
        valid_coins = 0
        for symbol in ALLOWED_COINS:
            is_valid, reason = analyzer.validate_coin(symbol, 10)
            status = "✓" if is_valid else "✗"
            print(f"{status} {symbol}: {reason}")
            if is_valid:
                valid_coins += 1
        
        print(f"\nValid coins: {valid_coins}/{len(ALLOWED_COINS)}")
        return valid_coins > 0
    
    except Exception as e:
        print(f"✗ Data feed check failed: {e}")
        return False


def check_model():
    """Check if trained model exists"""
    print("\n" + "="*60)
    print("MODEL CHECK")
    print("="*60)
    
    manager = ModelManager(model_dir=str(PROJECT_ROOT / "models"))
    
    if manager.load_model("lstm_model"):
        print("✓ Model found and loaded")
        print(manager.model_summary())
        return True
    else:
        print("✗ Model not found")
        print("  Run: python3 scripts/train_model.py")
        return False


def check_directories():
    """Check required directories"""
    print("\n" + "="*60)
    print("DIRECTORY STRUCTURE")
    print("="*60)
    
    dirs_to_check = [
        PROJECT_ROOT / "logs",
        PROJECT_ROOT / "models",
        PROJECT_ROOT / "broker",
        PROJECT_ROOT / "config",
    ]
    
    all_exist = True
    for dir_path in dirs_to_check:
        exists = "✓" if dir_path.exists() else "✗"
        print(f"{exists} {dir_path}")
        if not dir_path.exists():
            all_exist = False
    
    # Create logs dir if missing
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    return all_exist


def check_hard_limits():
    """Validate limits are enforced"""
    from broker.risk.position_manager import PositionManager
    
    print("\n" + "="*60)
    print("LIMITS VALIDATION")
    print("="*60)
    
    pm = PositionManager(initial_balance=100.0)  # Test with 100€
    is_valid, violations = pm.validate_hard_limits()
    
    print(f"Test Budget: 100.0 EUR")
    print(f"Max Position: {pm.MAX_POSITION_SIZE} EUR")
    print(f"Max Positions: {pm.MAX_OPEN_POSITIONS}")
    
    if is_valid:
        print("✓ All hard limits validated")
    else:
        print("✗ Hard limit violations:")
        for v in violations:
            print(f"  - {v}")
    
    return is_valid


def main():
    """Run all checks"""
    setup_logging()
    
    logger.info("Starting system health check...")
    
    print("\n" + "█"*60)
    print("TRADING BOT SYSTEM VALIDATION".center(60))
    print("█"*60)
    
    checks = [
        ("Environment", check_environment),
        ("Directories", check_directories),
        ("Hard Limits", check_hard_limits),
        ("API Credentials", check_api_credentials),
        ("Data Feeds", check_data_feeds),
        ("Model", check_model),
    ]
    
    results = {}
    for name, check_fn in checks:
        try:
            result = check_fn()
            results[name] = result
        except Exception as e:
            logger.error(f"Check '{name}' failed: {e}")
            results[name] = False
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for name, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
    
    print(f"\nTotal: {passed}/{total} checks passed")
    
    if passed == total:
        print("\n✓ System is ready for trading!")
        print("\nStart bot with: python3 -m broker.bot")
        return 0
    else:
        print("\n✗ Please fix above issues before trading")
        return 1


if __name__ == "__main__":
    sys.exit(main())
