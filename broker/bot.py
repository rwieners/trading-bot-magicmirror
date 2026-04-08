import atexit
PID_FILE = '/tmp/broker_bot.pid'

def check_single_instance():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            # Check if process is running
            if pid > 0:
                try:
                    os.kill(pid, 0)
                    print(f"Bot läuft bereits mit PID {pid}. Beende Start.")
                    sys.exit(1)
                except OSError:
                    pass
        except Exception:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.path.exists(PID_FILE) and os.remove(PID_FILE))

"""
Main Bot Execution Loop
Orchestrates data collection, prediction, trading, and risk management.
"""

import logging
import time
import signal
import sys
import json
import os
import subprocess
from typing import Dict, Optional
from datetime import datetime

from config.settings import *
from broker.data.live_feed import LiveDataFeed
from broker.data.storage import TradeDatabase
from broker.data.coin_analyzer import CoinAnalyzer
from broker.models.features import FeatureEngineer
from broker.models.lstm_model import ModelManager
from broker.strategies.profit_gate_strategy import ProfitGateStrategy, Signal
from broker.risk.position_manager import PositionManager
from broker.risk.account_monitor import AccountMonitor
from broker.exchange.kraken_trader import KrakenTrader
from broker.utils.logger import setup_logging
from broker.utils.health_checker import HealthChecker

logger = logging.getLogger(__name__)

# Path to user settings file for dynamic check_interval
USER_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'user_settings.json')


class TradingBot:
    """
    Main trading bot orchestrator.
    Runs continuous 24/7 with 1-minute evaluation cycles.
    """
    
    def __init__(self, config_dict: Dict = None):
        """
        Initialize trading bot.
        
        Args:
            config_dict: Override config settings (for testing)
        """
        self.config = config_dict or {
            'symbols': list(ALLOWED_COINS.keys()),
            'check_interval': DATA_CHECK_INTERVAL,
            'api_key': KRAKEN_API_KEY,
            'api_secret': KRAKEN_API_SECRET,
        }
        
        self.running = False
        self.iteration = 0
        self.start_time = None
        self._last_settings_check = 0
        self._cached_check_interval = DATA_CHECK_INTERVAL
        self._cached_trading_mode = TRADING_MODE
        self._retrain_process = None  # Subprocess for auto-retrain
        self._last_retrain_check = 0  # Timestamp of last retrain check
        
        # Initialize components
        logger.info("Initializing trading bot components...")
        
        # Data collection
        self.data_feed = LiveDataFeed(
            exchange_name='kraken',
            lookback_periods=LOOKBACK_PERIODS,
            lookback_minutes=LOOKBACK_MINUTES
        )
        
        # Storage
        self.db = TradeDatabase(str(LOGS_DIR / "trades.db"))
        
        # Coin validation
        self.coin_analyzer = CoinAnalyzer(
            exchange_name='kraken',
            allowed_coins=ALLOWED_COINS
        )
        
        # Feature engineering
        self.feature_engineer = FeatureEngineer(lookback_periods=LOOKBACK_PERIODS)
        
        # ML model
        self.model_manager = ModelManager(model_dir=str(PROJECT_ROOT / "models"))
        
        # Strategy
        self.strategy = ProfitGateStrategy(
            profit_gate_threshold=PROFIT_GATE_THRESHOLD,
            min_profit_target=MIN_PROFIT_TARGET,
            max_loss_cutoff=MAX_LOSS_CUTOFF,
            portfolio_drawdown_limit=PORTFOLIO_DRAWDOWN_LIMIT,
            position_size_limit=MAX_POSITION_SIZE,
            max_positions=MAX_OPEN_POSITIONS
        )
        
        # Load balance thresholds from user settings
        critical_threshold = CRITICAL_BALANCE_LEVEL
        warning_threshold = WARNING_BALANCE_LEVEL
        try:
            if os.path.exists(USER_SETTINGS_PATH):
                with open(USER_SETTINGS_PATH, 'r') as f:
                    user_settings = json.load(f)
                    critical_threshold = user_settings.get('critical_balance_level', CRITICAL_BALANCE_LEVEL)
                    warning_threshold = user_settings.get('warning_balance_level', WARNING_BALANCE_LEVEL)
        except Exception as e:
            logger.warning(f"Could not load balance thresholds from user settings: {e}")
        
        # Risk management (loads actual balance from database)
        self.position_manager = PositionManager(db=self.db)
        self.account_monitor = AccountMonitor(
            db=self.db,
            critical_threshold=critical_threshold,
            warning_threshold=warning_threshold
        )
        
        # Exchange
        self.exchange = KrakenTrader(
            api_key=self.config.get('api_key', ''),
            api_secret=self.config.get('api_secret', ''),
            sandbox=False
        )
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        
        # Apply initial trading mode
        self._max_new_trades = MAX_NEW_TRADES_PER_CYCLE
        self._apply_trading_mode(self._cached_trading_mode)
        
        # Self-monitoring health checker
        self.health = HealthChecker(check_interval=300)
        
        logger.info("Trading bot initialized successfully")
        logger.info(f"Config: {self.strategy}")
    
    @property
    def check_interval(self) -> int:
        """Get check interval from user settings, reload every 60 seconds."""
        now = time.time()
        if now - self._last_settings_check > 60:
            try:
                if os.path.exists(USER_SETTINGS_PATH):
                    with open(USER_SETTINGS_PATH, 'r') as f:
                        settings = json.load(f)
                        new_interval = int(settings.get('check_interval', DATA_CHECK_INTERVAL))
                        if new_interval != self._cached_check_interval:
                            logger.info(f"Check interval changed: {self._cached_check_interval}s -> {new_interval}s")
                            self._cached_check_interval = new_interval
                        # Reload trading mode
                        new_mode = settings.get('trading_mode', 'conservative')
                        if new_mode != self._cached_trading_mode:
                            logger.info(f"Trading mode changed: {self._cached_trading_mode} -> {new_mode}")
                            self._cached_trading_mode = new_mode
                            self._apply_trading_mode(new_mode)
            except Exception as e:
                logger.debug(f"Could not reload check_interval: {e}")
            self._last_settings_check = now
        return self._cached_check_interval
    
    def _apply_trading_mode(self, mode: str):
        """Apply trading mode parameters to strategy and position manager."""
        if mode == 'scalping':
            self.strategy.profit_gate_threshold = 0.005   # 0.5%
            self.strategy.max_positions = 8
            self.strategy.max_positions_per_symbol = 3
            self.strategy.portfolio_drawdown_limit = -0.30  # -30%
            self._max_new_trades = 5
            self.position_manager.MAX_OPEN_POSITIONS = 8
            self.position_manager.MAX_POSITIONS_PER_SYMBOL = 3
            logger.info("Mode SCALPING: profit_gate=0.5%, max_positions=8, per_symbol=3, max_new_trades=5, drawdown_limit=-30%")
        elif mode == 'aggressive':
            self.strategy.profit_gate_threshold = 0.007   # 0.7%
            self.strategy.max_positions = 6
            self.strategy.max_positions_per_symbol = 1
            self.strategy.portfolio_drawdown_limit = -0.25  # -25%
            self._max_new_trades = 3
            self.position_manager.MAX_OPEN_POSITIONS = 6
            self.position_manager.MAX_POSITIONS_PER_SYMBOL = 1
            logger.info("Mode AGGRESSIVE: profit_gate=0.7%, max_positions=6, per_symbol=1, max_new_trades=3, drawdown_limit=-25%")
        else:
            self.strategy.profit_gate_threshold = 0.0142  # 1.42%
            self.strategy.max_positions = 3
            self.strategy.max_positions_per_symbol = 1
            self.strategy.portfolio_drawdown_limit = -0.10  # -10%
            self._max_new_trades = 1
            self.position_manager.MAX_OPEN_POSITIONS = 3
            self.position_manager.MAX_POSITIONS_PER_SYMBOL = 1
            logger.info("Mode CONSERVATIVE: profit_gate=1.42%, max_positions=3, per_symbol=1, max_new_trades=1, drawdown_limit=-10%")
    
    def _handle_shutdown(self, signum, frame):
        """Handle graceful shutdown"""
        logger.info("Shutdown signal received, closing positions...")
        self.running = False
        self._cleanup()
    
    def _cleanup(self):
        """Cleanup resources"""
        # Terminate any running retrain subprocess
        if self._retrain_process is not None and self._retrain_process.poll() is None:
            logger.info("Terminating auto-retrain subprocess...")
            self._retrain_process.terminate()
            try:
                self._retrain_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._retrain_process.kill()
            self._retrain_process = None
        
        try:
            self.db.close()
            logger.info("Database closed")
        except Exception as e:
            logger.error(f"Error closing database: {e}")
    
    def initialize_data_feeds(self):
        """Initialize historical data for all symbols"""
        logger.info("Initializing data feeds...")
        
        for symbol in self.config['symbols']:
            try:
                # Fetch historical data (90 days × 1440 minutes/day = 129600 candles @ 1min)
                # CCXT timeframe for 15-min candles
                ohlcv = self.data_feed.fetch_ohlcv(symbol, timeframe='15m', limit=LOOKBACK_PERIODS * 4)
                
                if ohlcv:
                    self.data_feed.initialize_buffer(symbol, ohlcv)
                    logger.info(f"Initialized buffer for {symbol}")
                else:
                    logger.warning(f"No data available for {symbol}")
            except Exception as e:
                logger.error(f"Failed to initialize data for {symbol}: {e}")
    
    def validate_prerequisites(self) -> bool:
        """Check all prerequisites before trading"""
        logger.info("Validating prerequisites...")
        
        # Check Kraken connectivity
        try:
            ticker = self.exchange.fetch_ticker('BTC/EUR')
            if not ticker:
                logger.error("Failed to fetch Kraken ticker")
                return False
            logger.info("✓ Kraken connectivity OK")
        except Exception as e:
            logger.error(f"Kraken connectivity error: {e}")
            return False
        
        # Check coin availability
        coin_report = self.coin_analyzer.validate_all_whitelisted_coins()
        valid_coins = sum(1 for valid, _ in coin_report.values() if valid)
        logger.info(f"✓ Valid coins: {valid_coins}/{len(self.config['symbols'])}")
        
        if valid_coins == 0:
            logger.error("No valid tradeable coins")
            return False
        
        # Check model is loadable
        if not self.model_manager.load_model("lstm_model"):
            logger.warning("No trained model found - will need to train before trading")
        else:
            logger.info("✓ Model loaded successfully")
        
        # Validate position manager
        is_valid, violations = self.position_manager.validate_hard_limits()
        if not is_valid:
            logger.error(f"Position manager violations: {violations}")
            return False
        logger.info("✓ Position limits validated")
        
        return True
    
    def sync_balance_from_exchange(self):
        """Sync balance from Kraken exchange"""
        try:
            free_eur, total_eur = self.exchange.get_eur_balance()
            if total_eur > 0:
                logger.info(f"EUR balance from Kraken: {total_eur:.2f}€ (free: {free_eur:.2f}€)")
                # Update cash from Kraken, then recalculate current_balance from cash + open positions
                self.position_manager.cash = free_eur
                position_value = sum(p.current_value for p in self.position_manager.positions.values())
                self.position_manager.current_balance = free_eur + position_value
                self.position_manager.peak_balance = max(self.position_manager.peak_balance, self.position_manager.current_balance)
            else:
                logger.warning(f"No EUR balance available from Kraken. Using default 100€.")
                self.position_manager.cash = 100.0
                self.position_manager.current_balance = 100.0 + sum(p.current_value for p in self.position_manager.positions.values())
        except Exception as e:
            logger.error(f"Failed to sync balance from Kraken: {e}")
            self.health.record_error(HealthChecker.SYNC_ERROR, f"Balance sync: {e}")
    
    def sync_positions_from_exchange(self):
        """
        Import existing trades from database as tracked positions.
        Each DB trade becomes a separate position for individual P&L tracking.
        """
        try:
            # Get open trades from database
            open_trades = self._get_open_trades_from_db()
            
            if not open_trades:
                logger.info("No open trades to import")
                return
            
            # Get current prices from exchange
            allowed_symbols = list(ALLOWED_COINS.keys())
            current_prices = {}
            for symbol in allowed_symbols:
                try:
                    ticker = self.exchange.fetch_ticker(symbol)
                    if ticker and 'last' in ticker:
                        current_prices[symbol] = ticker['last']
                except Exception as e:
                    logger.warning(f"Could not fetch price for {symbol}: {e}")
            
            imported_count = 0
            MIN_IMPORT_EUR = 1.00  # Skip dust/broken positions below €1.00
            
            # Get actual Kraken balances to validate positions
            kraken_balances = {}
            try:
                balance = self.exchange.get_balance()
                for key, val in balance.items():
                    if isinstance(val, dict) and 'free' in val:
                        kraken_balances[key] = val.get('free', 0) or val.get('total', 0) or 0
            except Exception as e:
                logger.warning(f"[SYNC] Could not fetch Kraken balances: {e}")
            
            # SAFETY: If balance fetch returned no coin data, skip ghost detection entirely.
            # Closing all positions because of a failed API call would be catastrophic.
            has_valid_balances = any(v > 0 for v in kraken_balances.values())
            if not kraken_balances or not has_valid_balances:
                logger.warning("[SYNC] Kraken balance data is empty or all-zero — skipping ghost position detection this cycle.")
            
            # Track remaining Kraken coins per currency to correctly handle multiple trades
            # of the same coin. Each trade "consumes" its share from the pool.
            remaining_coins = {}
            for key, val in kraken_balances.items():
                remaining_coins[key] = val
            
            for trade in open_trades:
                symbol = trade['symbol']
                trade_id = trade['id']
                entry_price = trade['entry_price']
                entry_size_eur = trade['entry_size']  # EUR value invested (ALWAYS EUR, never coin qty)

                # Validate against actual Kraken balance: if exchange has dust, auto-close
                base_currency = symbol.split('/')[0]
                actual_coins = remaining_coins.get(base_currency, 0)
                expected_coins = entry_size_eur / entry_price if entry_price > 0 else 0
                
                # Only do ghost detection if we have valid balance data
                if has_valid_balances and expected_coins > 0 and actual_coins < expected_coins * 0.05:
                    actual_value = actual_coins * (current_prices.get(symbol, entry_price))
                    if actual_value < MIN_IMPORT_EUR:
                        logger.warning(f"[SYNC] ⚠ Ghost position Trade #{trade_id} {symbol}: "
                                      f"expected {expected_coins:.6f} coins but Kraken has {actual_coins:.8f} "
                                      f"(€{actual_value:.4f}). Auto-closing in DB.")
                        try:
                            self.db.record_trade_exit(
                                trade_id=trade_id,
                                exit_time=int(time.time()),
                                exit_price=current_prices.get(symbol, entry_price),
                                exit_size=expected_coins,
                                exit_fee=0,
                                reason="Ghost position: coins not on exchange"
                            )
                        except Exception as db_err:
                            logger.error(f"[SYNC] Failed to auto-close ghost trade #{trade_id}: {db_err}")
                        continue
                
                # Deduct this trade's expected coins from the remaining pool
                if expected_coins > 0:
                    remaining_coins[base_currency] = max(0, actual_coins - expected_coins)

                # Sanity check: entry_size should be in EUR, not raw coin amount
                # Only correct if entry_size is suspiciously small AND multiplying by price
                # gives a value close to what Kraken actually holds for this trade.
                # Skip this heuristic for SYNC_KRAKEN trades (always stored as EUR).
                trade_reason = trade.get('reason', '')
                if entry_size_eur < MIN_IMPORT_EUR and entry_price > 0 and 'SYNC_KRAKEN' not in trade_reason:
                    possible_eur = entry_size_eur * entry_price
                    # Only correct if the coin interpretation produces a value close to
                    # what Kraken actually holds (within 50% of expected coins)
                    expected_value_eur = actual_coins * current_prices.get(symbol, entry_price)
                    if possible_eur >= MIN_IMPORT_EUR and expected_value_eur > 0 and abs(possible_eur - expected_value_eur) / expected_value_eur < 0.5:
                        logger.warning(f"[SYNC] Trade #{trade_id} {symbol}: entry_size={entry_size_eur} looks like coin qty, not EUR. Correcting to €{possible_eur:.2f}")
                        entry_size_eur = possible_eur
                    else:
                        logger.info(f"[SYNC] Skipping dust position Trade #{trade_id} {symbol}: €{entry_size_eur:.4f}")
                        continue

                # Umwandlung: EUR → Coin-Menge (wird als entry_size im Positionsobjekt gespeichert)
                coin_amount = entry_size_eur / entry_price if entry_price > 0 else 0

                current_price = current_prices.get(symbol, entry_price)
                pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0

                # Use trade_id as position key for individual tracking
                position_key = f"{symbol}_{trade_id}"

                # Wichtig: entry_size in Position = Coin-Menge, NICHT EUR!
                success, reason = self.position_manager.import_position(
                    symbol=position_key,
                    amount=coin_amount,
                    entry_price=entry_price,
                    current_price=current_price,
                    entry_fee=trade.get('entry_fee', 0) or 0
                )
                if success:
                    # Store original symbol and trade_id for later
                    pos = self.position_manager.positions.get(position_key)
                    if pos:
                        pos.trade_id = trade_id
                        pos.original_symbol = symbol
                    imported_count += 1
                    logger.info(f"✓ Imported Trade #{trade_id} {symbol}: €{entry_size_eur:.2f} @ €{entry_price:.2f}, P&L: {pnl_pct:+.2f}%")
            
            if imported_count > 0:
                logger.info(f"Imported {imported_count} trades from DB")
                
        except Exception as e:
            logger.error(f"Failed to sync positions from DB: {e}")
    
    def _get_open_trades_from_db(self):
        """
        Get all open trades from database.
        Each trade is returned individually for separate P&L tracking.
        """
        try:
            import sqlite3
            conn = sqlite3.connect(str(self.db.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, symbol, entry_price, entry_size, entry_time, entry_fee
                FROM trades
                WHERE status = 'OPEN' OR status IS NULL
                ORDER BY entry_time ASC
            ''')
            
            trades = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return trades
            
        except Exception as e:
            logger.warning(f"Could not get open trades from DB: {e}")
            return []

    def _check_auto_retrain(self):
        """
        Check if model needs retraining and launch subprocess if so.
        - Checks model age every 10 minutes (not every iteration)
        - If model is older than RETRAIN_INTERVAL_HOURS, starts retrain in background
        - When retrain subprocess finishes, hot-reloads the new model
        """
        if not RETRAIN_ENABLED:
            return
        
        now = time.time()
        
        # Only check every 10 minutes to avoid overhead
        if now - self._last_retrain_check < 600:
            return
        self._last_retrain_check = now
        
        # Check if a retrain subprocess is already running
        if self._retrain_process is not None:
            retcode = self._retrain_process.poll()
            if retcode is None:
                # Still running
                logger.debug("Auto-retrain subprocess still running...")
                return
            elif retcode == 0:
                logger.info("✅ Auto-retrain completed successfully!")
                # Hot-reload the new model
                reloaded = self.model_manager.reload_if_changed()
                if reloaded:
                    logger.info("🔄 New model loaded into bot")
                else:
                    logger.warning("⚠ Retrain completed but model file unchanged")
            else:
                logger.error(f"❌ Auto-retrain failed with exit code {retcode}")
            self._retrain_process = None
        
        # Check model age
        model_age = self.model_manager.get_model_age_hours()
        if model_age is None:
            logger.warning("No model file found — skipping auto-retrain check")
            return
        
        logger.info(f"Model age: {model_age:.1f}h (retrain threshold: {RETRAIN_INTERVAL_HOURS}h)")
        
        if model_age >= RETRAIN_INTERVAL_HOURS:
            logger.info(f"🔄 Model is {model_age:.1f}h old (>{RETRAIN_INTERVAL_HOURS}h) — starting auto-retrain...")
            try:
                # Get project root directory
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                train_script = os.path.join(project_root, "scripts", "train_model.py")
                venv_python = os.path.join(project_root, "venv", "bin", "python3")
                
                # Use venv python if available, else system python
                python_exe = venv_python if os.path.exists(venv_python) else sys.executable
                
                # Launch retrain as subprocess (non-blocking)
                self._retrain_process = subprocess.Popen(
                    [python_exe, train_script, "--days", str(RETRAIN_DAYS_OF_DATA)],
                    cwd=project_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env={**os.environ}  # Pass current env (includes API keys)
                )
                logger.info(f"Auto-retrain subprocess started (PID: {self._retrain_process.pid})")
            except Exception as e:
                logger.error(f"Failed to start auto-retrain: {e}")
                self._retrain_process = None
    
    def run_iteration(self):
        """Execute one iteration of the bot (runs every minute)"""
        self.iteration += 1
        iteration_start = time.time()

        logger.info(f"[ITERATION {self.iteration}] Starting...")

        # Health check: skip trading if too many errors recently
        if not self.health.check():
            logger.warning(f"[ITERATION {self.iteration}] Skipped — health checker paused trading")
            return

        # Auto-retrain: check model age and launch retraining if needed
        self._check_auto_retrain()
        
        # Hot-reload: pick up model updates from retrain or manual training
        self.model_manager.reload_if_changed()

        # Automatischer Sync: Kraken ist immer der Datenmaster
        try:
            from broker.sync_kraken import sync_kraken_to_db
            db_path = self.db.db_path if hasattr(self, 'db') and hasattr(self.db, 'db_path') else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'trades.db')
            sync_kraken_to_db(db_path)
            logger.info("[SYNC] Datenbank mit Kraken-Beständen aktualisiert.")
        except Exception as e:
            logger.error(f"[SYNC] Fehler beim Sync mit Kraken: {e}")
            self.health.record_error(HealthChecker.SYNC_ERROR, f"Kraken sync: {e}")

        # Always sync balance and positions from Kraken at the start of every iteration
        self.sync_balance_from_exchange()
        self.sync_positions_from_exchange()

        try:
            # 1. Update price data for all symbols
            logger.info(f"[ITERATION {self.iteration}] Updating ticker data...")
            for symbol in self.config['symbols']:
                logger.info(f"[ITERATION {self.iteration}] Fetching ticker for {symbol}...")
                self.data_feed.update_from_ticker(symbol)
                logger.info(f"[ITERATION {self.iteration}] Ticker fetched for {symbol}")

            # 2. Generate predictions
            logger.info(f"[ITERATION {self.iteration}] Generating predictions...")
            predictions = {}
            for symbol in self.config['symbols']:
                candles = self.data_feed.get_current_candles(symbol)
                if not self.data_feed.is_buffer_ready(symbol):
                    logger.debug(f"Buffer not ready for {symbol}")
                    continue

                # Compute features
                candle_array = self.data_feed.get_buffer_as_array(symbol)
                features = self.feature_engineer.compute_features(candle_array)

                if features is None:
                    continue

                # Model prediction
                if self.model_manager.model:
                    predicted_move, confidence = self.model_manager.predict_price_move_1h(features)
                    predictions[symbol] = (predicted_move, confidence)
                    logger.info(f"[ITERATION {self.iteration}] {symbol}: move={predicted_move*100:.3f}%, confidence={confidence:.2f}")

                    # Log prediction
                    self.db.record_model_prediction(
                        symbol=symbol,
                        timestamp=int(time.time()),
                        predicted_move=predicted_move,
                        confidence=confidence,
                        signal='BUY' if predicted_move > PROFIT_GATE_THRESHOLD else 'SELL' if predicted_move < -PROFIT_GATE_THRESHOLD else 'HOLD',
                        executed=False
                    )

            # 3. Update position prices BEFORE evaluating signals
            for position_key, pos in self.position_manager.get_all_positions().items():
                # Use original_symbol for price lookup (position_key may be "BTC/EUR_7")
                actual_symbol = getattr(pos, 'original_symbol', None) or position_key
                price = self.data_feed.get_latest_price(actual_symbol)
                if price:
                    old_price = pos.current_price
                    self.position_manager.update_position_price(position_key, price)
                    if abs(price - old_price) / old_price > 0.001:  # Log if >0.1% change
                        logger.info(f"[ITERATION {self.iteration}] Updated {position_key}: €{old_price:.4f}→€{price:.4f}, P&L: {pos.unrealized_pnl_pct*100:.2f}%")

            # 4. Check exit conditions for ALL individual trades
            logger.info(f"[ITERATION {self.iteration}] Checking exit conditions...")
            exit_signals = self._check_exit_conditions()

            # 5. Generate BUY signals from predictions
            logger.info(f"[ITERATION {self.iteration}] Evaluating BUY signals...")
            account_stats = self.position_manager.get_account_stats()
            account_stats['available_balance'] = account_stats['cash_available_for_trade']
            account_stats['portfolio_drawdown'] = account_stats['max_drawdown']

            buy_signals = self.strategy.evaluate_multiple(
                predictions=predictions,
                current_positions=self.position_manager.get_all_positions(),
                account_stats=account_stats,
                max_new_trades=getattr(self, '_max_new_trades', MAX_NEW_TRADES_PER_CYCLE)
            )

            # Filter to BUY-only (exits handled separately)
            from broker.strategies.profit_gate_strategy import Signal
            buy_signals = [s for s in buy_signals if s.signal == Signal.BUY]

            # Combine exit + buy signals
            signals = exit_signals + buy_signals

            # 6. Execute signals
            logger.info(f"[ITERATION {self.iteration}] Executing {len(signals)} signals ({len(exit_signals)} exits, {len(buy_signals)} buys)...")
            for signal in signals:
                logger.info(f"[ITERATION {self.iteration}] Signal: {signal.signal} {signal.symbol} "
                           f"size={signal.position_size} P&L: +/-")
                self._execute_signal(signal)

            # 7. Monitor account
            self.account_monitor.update(
                current_balance=self.position_manager.current_balance,
                open_positions=len(self.position_manager.get_all_positions()),
                total_pnl=account_stats['total_pnl']
            )

            # Check if should pause
            should_pause, reason = self.account_monitor.should_pause_trading()
            if should_pause:
                logger.critical(f"PAUSING TRADES: {reason}")

            # 8. Log iteration metrics
            elapsed = time.time() - iteration_start
            logger.info(f"[ITERATION {self.iteration}] Complete: {len(signals)} signals, "
                        f"{len(self.position_manager.get_all_positions())} positions, "
                        f"cash={self.position_manager.cash:.2f}€, elapsed={elapsed:.2f}s")

        except Exception as e:
            logger.error(f"Error in iteration {self.iteration}: {e}", exc_info=True)
            self.health.record_error(HealthChecker.ITERATION_CRASH, str(e))
        finally:
            # Write iteration status for the portfolio API
            try:
                status_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'bot_status.json')
                with open(status_path, 'w') as f:
                    json.dump({'iteration': self.iteration, 'timestamp': int(time.time())}, f)
            except Exception:
                pass
    
    def _check_exit_conditions(self):
        """
        Check all positions for exit conditions (profit target or stop loss).
        Returns list of SELL signals for positions that should be closed.
        Fee-aware: profit exits only trigger if net P&L after ALL real trading costs
        is positive (sell fee + estimated spread/slippage).
        Note: Network/withdrawal fees are NOT included — they only apply when moving
        coins OFF Kraken, which the bot never does. Spot trades are internal.
        """
        from broker.strategies.profit_gate_strategy import TradeSignal, Signal
        from config.settings import TAKER_FEE
        
        # Spread/slippage buffer: market sell typically executes ~0.05-0.10% below mid
        SPREAD_BUFFER = 0.001  # 0.1% conservative estimate for bid/ask spread + slippage
        
        exit_signals = []
        profit_target = self.strategy.min_profit_target  # e.g., 0.05 for 5%
        stop_loss = self.strategy.max_loss_cutoff  # e.g., -0.80 for -80%

        # Load scalping absolute profit target from settings
        from config.settings import TRADING_MODE
        scalping_min_abs_profit = None
        if TRADING_MODE == "scalping":
            try:
                import json as _json
                settings_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'user_settings.json')
                with open(settings_path, 'r') as f:
                    _settings = _json.load(f)
                scalping_min_abs_profit = float(_settings.get('scalping_profit_abs', 0.25))
            except Exception:
                scalping_min_abs_profit = 0.25
        
        for position_key, pos in self.position_manager.get_all_positions().items():
            pnl_pct = pos.unrealized_pnl_pct  # Already as decimal (0.05 = 5%)
            trade_id = getattr(pos, 'trade_id', None)
            actual_symbol = getattr(pos, 'original_symbol', None) or position_key

            logger.debug(f"[DEBUG] Trade #{trade_id} {actual_symbol}: entry={pos.entry_price}, current={pos.current_price}, size={pos.entry_size}, P&L={pnl_pct*100:.2f}%, profit_target={profit_target*100:.2f}%, stop_loss={stop_loss*100:.2f}%")

            # Check profit target — but only sell if net P&L after ALL trading costs is positive
            if pnl_pct >= profit_target:
                # Minimum round-trip cost: both buy + sell fee + spread
                # This ensures profitability even if entry_fee was not recorded (e.g. imported positions)
                min_roundtrip_cost = pos.current_value * (TAKER_FEE * 2 + SPREAD_BUFFER)
                # Actual remaining costs: sell fee + spread (buy fee already in unrealized_pnl via entry_value)
                estimated_sell_fee = pos.current_value * TAKER_FEE
                estimated_spread_cost = pos.current_value * SPREAD_BUFFER
                actual_remaining_costs = estimated_sell_fee + estimated_spread_cost
                # Use the higher of: actual remaining costs OR (min_roundtrip - recorded entry_fee)
                # This prevents loss when entry_fee=0 (import bug) without double-counting when entry_fee is correct
                safety_costs = max(actual_remaining_costs, min_roundtrip_cost - pos.entry_fee)
                net_pnl = pos.unrealized_pnl - safety_costs

                # In scalping mode: enforce absolute minimum profit (e.g. 0.50€)
                if scalping_min_abs_profit is not None and net_pnl < scalping_min_abs_profit:
                    logger.debug(f"[SCALPING] Trade #{trade_id} {actual_symbol}: P&L {pnl_pct*100:.2f}% but net €{net_pnl:.4f} < scalping target €{scalping_min_abs_profit:.2f} — holding")
                    continue

                if net_pnl > 0:
                    logger.info(f"💰 Trade #{trade_id} {actual_symbol} reached profit target: {pnl_pct*100:.2f}% >= {profit_target*100:.2f}% (net after costs: €{net_pnl:.4f}, costs=€{safety_costs:.4f})")
                    exit_signals.append(TradeSignal(
                        symbol=position_key,  # Use position_key for lookup
                        signal=Signal.SELL,
                        predicted_move=0,
                        confidence=1.0,
                        timestamp=int(time.time()),
                        reason=f"Profit target reached: {pnl_pct*100:.2f}% (net €{net_pnl:.4f})",
                        position_size=pos.entry_size
                    ))
                else:
                    logger.debug(f"[FEE-CHECK] Trade #{trade_id} {actual_symbol}: P&L {pnl_pct*100:.2f}% meets target but net after costs is €{net_pnl:.4f} (costs=€{safety_costs:.4f}) — holding")
            # Check stop loss — always execute regardless of fees
            elif pnl_pct <= stop_loss:
                logger.info(f"🛑 Trade #{trade_id} {actual_symbol} hit stop loss: {pnl_pct*100:.2f}% <= {stop_loss*100:.2f}%")
                exit_signals.append(TradeSignal(
                    symbol=position_key,
                    signal=Signal.SELL,
                    predicted_move=0,
                    confidence=1.0,
                    timestamp=int(time.time()),
                    reason=f"Stop loss triggered: {pnl_pct*100:.2f}%",
                    position_size=pos.entry_size
                ))
            else:
                logger.debug(f"[DEBUG] Trade #{trade_id} {actual_symbol}: No exit. P&L {pnl_pct*100:.2f}% (target: {profit_target*100:.2f}%, stop: {stop_loss*100:.2f}%)")
        
        return exit_signals

    def _execute_signal(self, signal):
        """Execute a trading signal"""
        try:
            if signal.signal == Signal.BUY:
                self._execute_buy(signal)
            elif signal.signal == Signal.SELL:
                self._execute_sell(signal)
        except Exception as e:
            logger.error(f"Error executing signal for {signal.symbol}: {e}")
            self.health.record_error(HealthChecker.ORDER_FAILED, f"{signal.symbol}: {e}")
    
    def _execute_buy(self, signal):
        """Execute buy signal"""
        ticker = self.exchange.fetch_ticker(signal.symbol)
        if not ticker:
            logger.error(f"Failed to fetch ticker for {signal.symbol}")
            self.health.record_error(HealthChecker.API_ERROR, f"Ticker {signal.symbol}")
            return
        
        current_price = ticker.get('ask', ticker.get('last'))
        if not current_price:
            logger.error(f"No price data for {signal.symbol}")
            return
        
        # Calculate amount
        amount = signal.position_size / current_price
        
        # Estimate fees
        entry_fee = self.exchange.estimate_fees(signal.symbol, 'buy', amount, current_price)
        
        # CRITICAL: Get live balance from Kraken to check actual available funds
        # This prevents orders from failing when other orders/positions lock capital
        try:
            kraken_balance = self.exchange.get_balance()
            available_eur = kraken_balance.get('EUR', {}).get('free', 0)
            if available_eur == 0:
                # Try alternative key names
                available_eur = kraken_balance.get('EUR', {}).get('available', 0)
            logger.info(f"Live Kraken balance: €{available_eur:.2f} available")
        except Exception as e:
            logger.warning(f"Could not fetch live balance from Kraken: {e}")
            available_eur = signal.position_size + entry_fee + 5  # Fallback: assume OK if within margin
        
        # Check vs live balance (with 1€ safety margin for rounding/fees)
        if available_eur < (signal.position_size + entry_fee + 1):
            logger.error(f"Insufficient funds on Kraken: need €{signal.position_size + entry_fee + 1:.2f}, "
                        f"but only €{available_eur:.2f} available. Skipping.")
            self.health.record_error(HealthChecker.INSUFFICIENT_FUNDS, f"{signal.symbol}: need €{signal.position_size + entry_fee + 1:.2f}, have €{available_eur:.2f}")
            return
        
        # Check if position CAN be opened (before placing order)
        can_open, reason = self.position_manager.can_open_position(signal.symbol, signal.position_size)
        if not can_open:
            logger.warning(f"Cannot open position for {signal.symbol}: {reason}")
            return
        
        # Place order (limit order at market price for better fill rate)
        # Use ask price to ensure fills, slightly above market for buy orders
        limit_price = current_price * 1.001  # Buy slightly above market to ensure fill
        logger.info(f"Placing BUY order for {signal.symbol}: {amount:.6f} @ €{limit_price:.2f}")
        
        order = self.exchange.create_limit_order(signal.symbol, 'buy', amount, limit_price)
        
        if not order:
            logger.error(f"Failed to create buy order for {signal.symbol}")
            self.health.record_error(HealthChecker.ORDER_FAILED, f"BUY {signal.symbol}: order creation failed")
            return
        
        order_id = order.get('id')
        logger.info(f"Order {order_id} placed. Waiting for fill...")
        
        # CRITICAL: Wait for order to be filled before registering position
        # Extended timeout to 30 minutes for better fill chances
        filled_order = self.exchange.wait_for_order_fill(order_id, signal.symbol, timeout=1800)
        
        if not filled_order:
            logger.error(f"Order {order_id} not filled within timeout. Cancelling...")
            self.health.record_error(HealthChecker.ORDER_FAILED, f"BUY {signal.symbol}: fill timeout")
            try:
                self.exchange.exchange.cancel_order(order_id, signal.symbol)
            except:
                pass
            return
        
        # Only NOW register position after order is confirmed filled
        filled_price = filled_order.get('average', current_price)
        actual_size = filled_order.get('amount', amount)
        # Use actual fee from Kraken instead of estimate
        actual_entry_fee = self.exchange.get_actual_fee(filled_order)
        if actual_entry_fee > 0:
            entry_fee = actual_entry_fee
        
        success, msg = self.position_manager.open_position(
            symbol=signal.symbol,
            entry_price=filled_price,
            size=signal.position_size,
            entry_fee=entry_fee
        )
        
        if success:
            eur_value = signal.position_size  # position_size is in EUR for EUR pairs
            logger.info(f"✓ BUY {signal.symbol} FILLED: {signal.position_size:.2f}€ @ {filled_price:.2f}€ "
                       f"(size={actual_size:.6f} {signal.symbol.split('/')[0]}, confidence={signal.confidence:.2f}, move={signal.predicted_move*100:.2f}%)")
            
            # Record trade entry
            self.db.record_trade_entry(
                symbol=signal.symbol,
                entry_time=int(time.time()),
                entry_price=filled_price,
                entry_size=signal.position_size,
                entry_fee=entry_fee,
                model_confidence=signal.confidence
            )
    
    def _execute_sell(self, signal):
        """Execute sell signal for individual trade"""
        position_key = signal.symbol  # May be "BTC/EUR_7"
        position = self.position_manager.get_position(position_key)
        if not position:
            logger.warning(f"No position found for {position_key}")
            return
        
        # Get original symbol for exchange operations
        actual_symbol = getattr(position, 'original_symbol', None) or position_key
        trade_id = getattr(position, 'trade_id', None)
        
        ticker = self.exchange.fetch_ticker(actual_symbol)
        if not ticker:
            logger.error(f"Failed to fetch ticker for {actual_symbol}")
            self.health.record_error(HealthChecker.API_ERROR, f"Ticker {actual_symbol}")
            return
        
        current_price = ticker.get('bid', ticker.get('last'))
        if not current_price:
            logger.error(f"No price data for {actual_symbol}")
            return
        
        # Get actual coin balance from Kraken to avoid "insufficient funds" errors
        # (calculated amount may differ slightly from actual holdings due to rounding/fees)
        base_currency = actual_symbol.split('/')[0]
        try:
            kraken_balance = self.exchange.get_balance()
            actual_amount = 0
            for key in [base_currency, base_currency.upper(), 'X' + base_currency]:
                bal = kraken_balance.get(key, {})
                if isinstance(bal, dict):
                    actual_amount = bal.get('free', 0) or bal.get('total', 0) or 0
                    if actual_amount > 0:
                        break
            
            calculated_amount = position.entry_size
            if actual_amount > 0 and actual_amount < calculated_amount:
                logger.warning(f"Kraken balance ({actual_amount:.6f}) < calculated ({calculated_amount:.6f}). Using Kraken balance.")
                amount = actual_amount
            else:
                amount = calculated_amount
        except Exception as e:
            logger.warning(f"Could not verify Kraken balance for {base_currency}: {e}. Using calculated amount.")
            amount = position.entry_size
        
        if amount <= 0:
            logger.error(f"Cannot sell {actual_symbol}: amount is 0")
            return
        
        # Check exchange minimum order size to avoid repeated failed sell attempts
        # (ghost position detection: DB says OPEN but coins are gone from exchange)
        try:
            market = self.exchange.exchange.market(actual_symbol)
            min_amount = market.get('limits', {}).get('amount', {}).get('min', 0) or 0
            min_cost = market.get('limits', {}).get('cost', {}).get('min', 0) or 0
            order_cost = amount * current_price
            
            if amount < min_amount or order_cost < min_cost:
                logger.error(
                    f"⚠ GHOST POSITION: Trade #{trade_id} {actual_symbol} — "
                    f"sell amount {amount:.8f} (€{order_cost:.4f}) is below exchange minimum "
                    f"(min_amount={min_amount}, min_cost=€{min_cost}). "
                    f"Coins are no longer on exchange. Auto-closing in DB."
                )
                self.health.record_error(HealthChecker.GHOST_POSITION, f"Trade #{trade_id} {actual_symbol}")
                # Auto-close ghost position in DB
                self.position_manager.close_position(
                    symbol=position_key,
                    exit_price=current_price,
                    exit_fee=0
                )
                try:
                    if trade_id:
                        self.db.record_trade_exit(
                            trade_id=trade_id,
                            exit_time=int(time.time()),
                            exit_price=current_price,
                            exit_size=position.entry_size,
                            exit_fee=0,
                            reason="Ghost position: coins not on exchange"
                        )
                        logger.info(f"DB updated: Ghost trade #{trade_id} auto-closed")
                except Exception as db_err:
                    logger.error(f"Failed to auto-close ghost trade #{trade_id} in DB: {db_err}")
                return
        except Exception as e:
            logger.debug(f"Could not check min order size for {actual_symbol}: {e}")
        
        logger.info(f"Placing SELL order for Trade #{trade_id} {actual_symbol}: {amount:.6f} @ €{current_price:.2f}")
        
        order = self.exchange.create_market_order(actual_symbol, 'sell', amount)
        
        if not order:
            logger.error(f"Failed to create sell order for {actual_symbol}")
            self.health.record_error(HealthChecker.ORDER_FAILED, f"SELL {actual_symbol}: order creation failed")
            return
        
        order_id = order.get('id')
        logger.info(f"Order {order_id} placed. Waiting for fill...")
        
        # CRITICAL: Wait for order to be filled before closing position
        filled_order = self.exchange.wait_for_order_fill(order_id, actual_symbol, timeout=300)
        
        if not filled_order:
            logger.error(f"Order {order_id} not filled within timeout.")
            self.health.record_error(HealthChecker.ORDER_FAILED, f"SELL {actual_symbol}: fill timeout")
            return
        
        # Only NOW close position after order is confirmed filled
        filled_price = filled_order.get('average', current_price)
        actual_filled_amount = filled_order.get('filled', filled_order.get('amount', amount))
        # Use actual fee from Kraken instead of estimate
        exit_fee = self.exchange.get_actual_fee(filled_order)
        if exit_fee <= 0:
            exit_fee = self.exchange.estimate_fees(actual_symbol, 'sell', actual_filled_amount, filled_price)
        
        # Close position in position manager
        success, stats = self.position_manager.close_position(
            symbol=position_key,
            exit_price=filled_price,
            exit_fee=exit_fee
        )
        
        if success:
            logger.info(f"✓ SELL Trade #{trade_id} {actual_symbol} FILLED @ {filled_price:.2f}: {stats['pnl_pct']:+.2f}% | {signal.reason}")
        
        # ALWAYS try to update DB after a filled sell, even if close_position had issues
        # This prevents the DB from showing OPEN for a trade that was already sold on Kraken
        try:
            if trade_id:
                exit_size = actual_filled_amount  # actual coin quantity sold on exchange
                self.db.record_trade_exit(
                    trade_id=trade_id,
                    exit_time=signal.timestamp,
                    exit_price=filled_price,
                    exit_size=exit_size,
                    exit_fee=exit_fee,
                    reason=signal.reason
                )
                logger.info(f"DB updated: Trade #{trade_id} closed")
            else:
                # Fallback: close all trades for symbol
                self.db.close_trades_by_symbol(
                    symbol=actual_symbol,
                    exit_time=signal.timestamp,
                    exit_price=filled_price,
                    exit_fee=exit_fee,
                    reason=signal.reason
                )
        except Exception as db_error:
            logger.error(f"CRITICAL: Sell FILLED on Kraken but DB update failed for Trade #{trade_id}: {db_error}")
            logger.error(f"Manual fix needed: Trade #{trade_id} {actual_symbol} sold @ {filled_price:.2f}, amount={actual_filled_amount:.6f}")
            self.health.record_error(HealthChecker.DB_ERROR, f"Trade #{trade_id} DB update failed after sell")
    
    def run(self):
        """Main bot execution loop"""
        self.running = True
        self.start_time = time.time()
        
        logger.info("Starting trading bot...")
        
        if not self.validate_prerequisites():
            logger.error("Prerequisites validation failed")
            return
        
        self.initialize_data_feeds()
        
        # Sync balance from Kraken exchange
        self.sync_balance_from_exchange()
        
        # Import existing positions from Kraken (from previous sessions or manual trades)
        self.sync_positions_from_exchange()
        
        # Initialize account monitor with actual portfolio value (cash + positions)
        # so the first iteration doesn't falsely trigger "significant gain" alerts
        portfolio_value = self.position_manager.current_balance
        
        # Also set position_manager's initial_balance if it wasn't loaded from DB
        if self.position_manager.initial_balance <= 0 and portfolio_value > 0:
            self.position_manager.initial_balance = portfolio_value
            self.position_manager.peak_balance = portfolio_value
            logger.info(f"Set initial balance from Kraken sync: {portfolio_value:.2f}€")
        
        self.account_monitor.initial_balance = portfolio_value
        self.account_monitor.previous_balance = portfolio_value
        self.account_monitor.current_balance = portfolio_value
        self.account_monitor.peak_balance = portfolio_value
        self.account_monitor.lowest_balance = portfolio_value
        
        logger.info("Bot running. Press Ctrl+C to stop.\n")
        
        last_execution = time.time()
        
        while self.running:
            try:
                current_time = time.time()
                time_since_last = current_time - last_execution
                
                # Execute every check_interval seconds (dynamically reloaded from user settings)
                if time_since_last >= self.check_interval:
                    self.run_iteration()
                    last_execution = current_time
                    
                    # Sleep a bit to avoid busy waiting
                    time.sleep(0.5)
                else:
                    time.sleep(0.1)  # Small sleep to reduce CPU usage
            
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received")
                break
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
                self.health.record_error(HealthChecker.ITERATION_CRASH, f"Main loop: {e}")
                time.sleep(5)  # Wait before retrying
        
        self._cleanup()
        logger.info("Bot stopped")
        
        # Print final summary
        self._print_summary()
    
    def _print_summary(self):
        """Print trading summary"""
        stats = self.position_manager.get_account_stats()
        perf = self.account_monitor.get_performance_summary()
        
        runtime = time.time() - self.start_time if self.start_time else 0
        hours = runtime / 3600
        
        print("\n" + "="*50)
        print("TRADING SESSION SUMMARY")
        print("="*50)
        print(f"Duration: {hours:.1f} hours")
        print(f"Iterations: {self.iteration}")
        print(f"Final Balance: {stats['current_balance']:.2f}€")
        print(f"Initial Balance: {stats['initial_balance']:.2f}€")
        print(f"Total P&L: {stats['total_pnl']:.4f} ({stats['total_pnl_pct']:+.2f}%)")
        print(f"Realized Trades: {perf['total_trades']}")
        print(f"Win Rate: {perf['win_rate']:.1f}%")
        print(f"Max Drawdown: {stats['max_drawdown_pct']:.2f}%")
        print("="*50 + "\n")


if __name__ == "__main__":
    check_single_instance()
    setup_logging()
    bot = TradingBot()
    bot.run()
