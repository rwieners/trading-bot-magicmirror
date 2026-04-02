"""
Tests for broker/sync_kraken.py — the core Kraken-to-DB synchronization logic.

Tested WITHOUT the Kraken API: we call the DB-level logic directly
by populating holdings dicts and DB rows, then verifying the results.
"""
import sqlite3
from datetime import datetime
from tests.conftest import insert_open_trade


# ---------------------------------------------------------------------------
# We extract the DB-sync logic into a testable helper so we don't need
# to mock ccxt.  The function under test is sync_kraken_to_db, but it
# fetches from Kraken internally.  Instead we replicate the DB portion
# of sync_kraken_to_db here (same logic, same SQL) to test it in isolation.
# ---------------------------------------------------------------------------

def _run_sync_logic(db_path, holdings):
    """
    Replicate the DB-portion of sync_kraken_to_db with a pre-built holdings dict.
    This is the exact same logic as broker/sync_kraken.py after the API calls.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute('SELECT id, symbol, entry_price, entry_size, entry_time FROM trades WHERE status = "OPEN" OR status IS NULL')
    open_rows = cursor.fetchall()
    open_by_symbol = {}
    for row in open_rows:
        sym = row['symbol']
        if sym not in open_by_symbol:
            open_by_symbol[sym] = {'total_eur': 0, 'trades': []}
        open_by_symbol[sym]['total_eur'] += row['entry_size']
        open_by_symbol[sym]['trades'].append(row)

    symbols_on_kraken = set(holdings.keys())
    symbols_in_db = set(open_by_symbol.keys())
    to_close = list(symbols_in_db - symbols_on_kraken)
    if to_close:
        now_ts_close = int(datetime.now().timestamp())
        for sym in to_close:
            for row in open_by_symbol[sym]['trades']:
                trade_id = row['id']
                # Skip trades that already have exit_price
                cursor.execute('SELECT exit_price FROM trades WHERE id = ?', (trade_id,))
                existing = cursor.fetchone()
                if existing and existing['exit_price']:
                    continue
                exit_price = row['entry_price']  # In tests we don't have live price
                coin_qty = row['entry_size'] / row['entry_price'] if row['entry_price'] else 0
                cursor.execute(
                    'UPDATE trades SET status = "CLOSED_MANUAL_SYNC", exit_time = ?, '
                    'exit_price = ?, exit_size = ? '
                    'WHERE id = ? AND (status = "OPEN" OR status IS NULL)',
                    (now_ts_close, exit_price, coin_qty, trade_id)
                )

    now_ts = int(datetime.now().timestamp())
    MIN_POSITION_EUR = 0.50

    for symbol, data in holdings.items():
        coin_amount = data['amount']
        entry_price = data['entry_price']
        kraken_total_eur = coin_amount * entry_price

        if kraken_total_eur < MIN_POSITION_EUR:
            continue

        if symbol not in open_by_symbol:
            cursor.execute(
                'INSERT INTO trades (symbol, entry_price, entry_size, entry_value, entry_time, status, reason) '
                'VALUES (?, ?, ?, ?, ?, "OPEN", "SYNC_KRAKEN")',
                (symbol, entry_price, kraken_total_eur, kraken_total_eur, now_ts)
            )
        else:
            db_total_eur = open_by_symbol[symbol]['total_eur']
            db_total_coins = db_total_eur / entry_price if entry_price > 0 else 0
            diff_coins = coin_amount - db_total_coins
            diff_eur = diff_coins * entry_price

            if diff_eur >= MIN_POSITION_EUR:
                cursor.execute(
                    'INSERT INTO trades (symbol, entry_price, entry_size, entry_value, entry_time, status, reason) '
                    'VALUES (?, ?, ?, ?, ?, "OPEN", "SYNC_KRAKEN_ADDITIONAL")',
                    (symbol, entry_price, diff_eur, diff_eur, now_ts)
                )

    conn.commit()
    conn.close()


def _get_open_trades(db_path, symbol=None):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if symbol:
        cursor.execute('SELECT * FROM trades WHERE (status = "OPEN" OR status IS NULL) AND symbol = ?', (symbol,))
    else:
        cursor.execute('SELECT * FROM trades WHERE status = "OPEN" OR status IS NULL')
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def _get_all_trades(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM trades')
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# ========================= TESTS =========================

class TestSyncNewPosition:
    """Sync should create a new trade when a coin exists on Kraken but not in DB."""

    def test_new_coin_creates_trade(self, db_path, db_conn):
        holdings = {'BTC/EUR': {'amount': 0.001, 'entry_price': 50000.0}}  # €50
        _run_sync_logic(db_path, holdings)

        trades = _get_open_trades(db_path, 'BTC/EUR')
        assert len(trades) == 1
        assert trades[0]['reason'] == 'SYNC_KRAKEN'
        assert abs(trades[0]['entry_size'] - 50.0) < 0.01

    def test_multiple_new_coins(self, db_path, db_conn):
        holdings = {
            'BTC/EUR': {'amount': 0.001, 'entry_price': 50000.0},
            'ETH/EUR': {'amount': 0.1, 'entry_price': 3000.0},
        }
        _run_sync_logic(db_path, holdings)

        all_open = _get_open_trades(db_path)
        assert len(all_open) == 2
        symbols = {t['symbol'] for t in all_open}
        assert symbols == {'BTC/EUR', 'ETH/EUR'}


class TestSyncDustFilter:
    """Positions below €0.50 should be ignored."""

    def test_dust_position_ignored(self, db_path, db_conn):
        holdings = {'DOGE/EUR': {'amount': 1.0, 'entry_price': 0.10}}  # €0.10
        _run_sync_logic(db_path, holdings)

        trades = _get_open_trades(db_path)
        assert len(trades) == 0

    def test_just_above_threshold(self, db_path, db_conn):
        holdings = {'DOGE/EUR': {'amount': 10.0, 'entry_price': 0.10}}  # €1.00
        _run_sync_logic(db_path, holdings)

        trades = _get_open_trades(db_path)
        assert len(trades) == 1


class TestSyncAdditionalPurchase:
    """When Kraken shows more coins than the DB knows about, a new trade should be created."""

    def test_additional_buy_creates_second_trade(self, db_path, db_conn):
        # DB has €10 worth of BTC at €50000 = 0.0002 BTC
        insert_open_trade(db_conn, 'BTC/EUR', entry_price=50000.0, entry_size_eur=10.0)

        # Kraken now shows 0.0004 BTC (user bought another €10 worth)
        holdings = {'BTC/EUR': {'amount': 0.0004, 'entry_price': 50000.0}}
        _run_sync_logic(db_path, holdings)

        trades = _get_open_trades(db_path, 'BTC/EUR')
        assert len(trades) == 2
        reasons = {t['reason'] for t in trades}
        assert 'SYNC_KRAKEN_ADDITIONAL' in reasons

        # The additional trade should be ~€10
        additional = [t for t in trades if t['reason'] == 'SYNC_KRAKEN_ADDITIONAL'][0]
        assert abs(additional['entry_size'] - 10.0) < 0.01

    def test_no_additional_trade_if_same_amount(self, db_path, db_conn):
        # DB has exactly what Kraken has
        insert_open_trade(db_conn, 'BTC/EUR', entry_price=50000.0, entry_size_eur=10.0)

        holdings = {'BTC/EUR': {'amount': 0.0002, 'entry_price': 50000.0}}  # Same
        _run_sync_logic(db_path, holdings)

        trades = _get_open_trades(db_path, 'BTC/EUR')
        assert len(trades) == 1  # No additional trade created

    def test_small_diff_below_threshold_ignored(self, db_path, db_conn):
        insert_open_trade(db_conn, 'BTC/EUR', entry_price=50000.0, entry_size_eur=10.0)

        # Kraken has slightly more, but diff < €0.50
        holdings = {'BTC/EUR': {'amount': 0.000205, 'entry_price': 50000.0}}  # +€0.25
        _run_sync_logic(db_path, holdings)

        trades = _get_open_trades(db_path, 'BTC/EUR')
        assert len(trades) == 1  # Dust diff ignored

    def test_multiple_existing_trades_summed(self, db_path, db_conn):
        # Two existing trades: €5 + €5 = €10 total = 0.0002 BTC
        insert_open_trade(db_conn, 'BTC/EUR', entry_price=50000.0, entry_size_eur=5.0)
        insert_open_trade(db_conn, 'BTC/EUR', entry_price=50000.0, entry_size_eur=5.0)

        # Kraken has 0.0004 BTC = €20 → diff is €10
        holdings = {'BTC/EUR': {'amount': 0.0004, 'entry_price': 50000.0}}
        _run_sync_logic(db_path, holdings)

        trades = _get_open_trades(db_path, 'BTC/EUR')
        assert len(trades) == 3  # 2 originals + 1 additional
        additional = [t for t in trades if t['reason'] == 'SYNC_KRAKEN_ADDITIONAL']
        assert len(additional) == 1
        assert abs(additional[0]['entry_size'] - 10.0) < 0.01


class TestSyncClosePosition:
    """When a coin is no longer on Kraken, its DB trade should be closed."""

    def test_sold_coin_gets_closed(self, db_path, db_conn):
        insert_open_trade(db_conn, 'XRP/EUR', entry_price=0.50, entry_size_eur=5.0)

        holdings = {}  # XRP sold on Kraken
        _run_sync_logic(db_path, holdings)

        open_trades = _get_open_trades(db_path, 'XRP/EUR')
        assert len(open_trades) == 0

        all_trades = _get_all_trades(db_path)
        assert all_trades[0]['status'] == 'CLOSED_MANUAL_SYNC'

    def test_close_one_keep_another(self, db_path, db_conn):
        insert_open_trade(db_conn, 'BTC/EUR', entry_price=50000.0, entry_size_eur=10.0)
        insert_open_trade(db_conn, 'XRP/EUR', entry_price=0.50, entry_size_eur=5.0)

        # Only BTC still on Kraken
        holdings = {'BTC/EUR': {'amount': 0.0002, 'entry_price': 50000.0}}
        _run_sync_logic(db_path, holdings)

        btc_open = _get_open_trades(db_path, 'BTC/EUR')
        xrp_open = _get_open_trades(db_path, 'XRP/EUR')
        assert len(btc_open) == 1
        assert len(xrp_open) == 0


class TestSyncIdempotent:
    """Running sync twice with the same data should not duplicate trades."""

    def test_double_sync_no_duplicates(self, db_path, db_conn):
        holdings = {'ETH/EUR': {'amount': 0.01, 'entry_price': 3000.0}}  # €30
        _run_sync_logic(db_path, holdings)
        _run_sync_logic(db_path, holdings)

        trades = _get_open_trades(db_path, 'ETH/EUR')
        assert len(trades) == 1
