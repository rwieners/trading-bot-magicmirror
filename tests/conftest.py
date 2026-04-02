"""
Shared test fixtures for the Broker test suite.
Uses in-memory SQLite databases — no external services needed.
"""
import os
import sys
import pytest
import sqlite3
import tempfile

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def db_path(tmp_path):
    """Provide a temporary SQLite database path."""
    return str(tmp_path / "test_trades.db")


@pytest.fixture
def db_conn(db_path):
    """Create a fresh trades database with the correct schema and return the connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            entry_time INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            entry_size REAL NOT NULL,
            entry_value REAL NOT NULL,
            exit_time INTEGER,
            exit_price REAL,
            exit_size REAL,
            exit_value REAL,
            pnl REAL,
            pnl_pct REAL,
            entry_fee REAL,
            exit_fee REAL,
            reason TEXT,
            status TEXT,
            model_confidence REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS account_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            balance REAL NOT NULL,
            available REAL NOT NULL,
            open_positions INTEGER,
            open_value REAL,
            total_pnl REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    yield conn
    conn.close()


def insert_open_trade(conn, symbol, entry_price, entry_size_eur, entry_time=1000000):
    """Helper: insert an open trade into the test DB."""
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO trades (symbol, entry_price, entry_size, entry_value, entry_time, status, reason) '
        'VALUES (?, ?, ?, ?, ?, "OPEN", "TEST")',
        (symbol, entry_price, entry_size_eur, entry_size_eur, entry_time)
    )
    conn.commit()
    return cursor.lastrowid
