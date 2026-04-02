"""
Logging and Monitoring Utilities
Configures logging and provides dashboard/reporting utilities.
"""

import logging
import logging.handlers
from pathlib import Path
from config.settings import LOGS_DIR, LOG_LEVEL, LOG_FORMAT


def setup_logging():
    """Configure logging for the application"""
    
    # Create logs directory
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, LOG_LEVEL))

    # Only add handlers if none exist (prevents duplicate log entries)
    if not root_logger.handlers:
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(getattr(logging, LOG_LEVEL))
        console_formatter = logging.Formatter(LOG_FORMAT)
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)

        # File handler (rotating)
        file_handler = logging.handlers.RotatingFileHandler(
            LOGS_DIR / "bot.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5
        )
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(LOG_FORMAT)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # Trade log (separate file)
    trade_logger = logging.getLogger('trades')
    if not trade_logger.handlers:
        trade_handler = logging.handlers.RotatingFileHandler(
            LOGS_DIR / "trades.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=10
        )
        trade_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        trade_logger.addHandler(trade_handler)
        trade_logger.setLevel(logging.INFO)

    return root_logger
