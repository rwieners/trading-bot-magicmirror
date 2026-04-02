"""
Tests for broker/data/storage.py — TradeDatabase CRUD operations.
Uses a temporary SQLite file (no Kraken connection needed).
"""
import pytest
import time
from broker.data.storage import TradeDatabase


@pytest.fixture
def db(tmp_path):
    """Provide a fresh TradeDatabase with an in-file SQLite."""
    d = TradeDatabase(str(tmp_path / "test_trades.db"))
    yield d
    d.close()


class TestRecordTradeEntry:
    def test_creates_trade_and_returns_id(self, db):
        trade_id = db.record_trade_entry(
            symbol='BTC/EUR', entry_time=1000000, entry_price=50000.0,
            entry_size=10.0, entry_fee=0.03, model_confidence=0.85
        )
        assert trade_id is not None
        assert trade_id > 0

    def test_trade_is_open(self, db):
        trade_id = db.record_trade_entry(
            symbol='ETH/EUR', entry_time=1000000, entry_price=3000.0,
            entry_size=10.0, entry_fee=0.02, model_confidence=0.75
        )
        open_trades = db.get_open_trades()
        assert len(open_trades) == 1
        assert open_trades[0]['symbol'] == 'ETH/EUR'
        assert open_trades[0]['status'] == 'OPEN'

    def test_entry_value_equals_entry_size(self, db):
        """entry_size is EUR invested, entry_value should equal entry_size."""
        db.record_trade_entry(
            symbol='BTC/EUR', entry_time=1000000, entry_price=50000.0,
            entry_size=10.0, entry_fee=0.0, model_confidence=0.9
        )
        trade = db.get_open_trades()[0]
        assert trade['entry_value'] == pytest.approx(trade['entry_size'])


class TestRecordTradeExit:
    def test_exit_closes_trade(self, db):
        trade_id = db.record_trade_entry(
            symbol='BTC/EUR', entry_time=1000000, entry_price=50000.0,
            entry_size=10.0, entry_fee=0.03, model_confidence=0.85
        )
        coin_qty = 10.0 / 50000.0
        db.record_trade_exit(
            trade_id=trade_id, exit_time=1001000,
            exit_price=55000.0, exit_size=coin_qty,
            exit_fee=0.03, reason='PROFIT_TARGET'
        )
        open_trades = db.get_open_trades()
        assert len(open_trades) == 0

        closed = db.get_closed_trades()
        assert len(closed) == 1
        assert closed[0]['pnl'] > 0
        assert 'PROFIT' in closed[0]['status']

    def test_exit_loss_status(self, db):
        trade_id = db.record_trade_entry(
            symbol='BTC/EUR', entry_time=1000000, entry_price=50000.0,
            entry_size=10.0, entry_fee=0.03, model_confidence=0.85
        )
        coin_qty = 10.0 / 50000.0
        db.record_trade_exit(
            trade_id=trade_id, exit_time=1001000,
            exit_price=40000.0, exit_size=coin_qty,
            exit_fee=0.03, reason='STOP_LOSS'
        )
        closed = db.get_closed_trades()
        assert closed[0]['pnl'] < 0
        assert 'LOSS' in closed[0]['status']


class TestTradeStats:
    def test_empty_stats(self, db):
        stats = db.get_trade_stats()
        assert stats['total_trades'] == 0
        assert stats['win_rate'] == 0

    def test_stats_after_trades(self, db):
        # One winner
        t1 = db.record_trade_entry('BTC/EUR', 1000000, 50000.0, 10.0, 0.0, 0.9)
        db.record_trade_exit(t1, 1001000, 55000.0, 10.0 / 50000.0, 0.0, 'PROFIT_TARGET')

        # One loser
        t2 = db.record_trade_entry('ETH/EUR', 1000000, 3000.0, 10.0, 0.0, 0.8)
        db.record_trade_exit(t2, 1001000, 2500.0, 10.0 / 3000.0, 0.0, 'STOP_LOSS')

        stats = db.get_trade_stats()
        assert stats['total_trades'] == 2
        assert stats['winning_trades'] == 1
        assert stats['win_rate'] == 50.0
        assert stats['total_pnl'] != 0


class TestAccountSnapshot:
    def test_record_and_retrieve(self, db):
        db.record_account_snapshot(
            timestamp=int(time.time()), balance=100.0, available=80.0,
            open_positions=2, open_value=20.0, total_pnl=1.5
        )
        latest = db.get_latest_account_balance()
        assert latest is not None
        assert latest['balance'] == 100.0
        assert latest['open_positions'] == 2

    def test_latest_returns_most_recent(self, db):
        db.record_account_snapshot(1000, 90.0, 70.0, 1, 20.0, -1.0)
        db.record_account_snapshot(2000, 110.0, 100.0, 0, 0.0, 5.0)
        latest = db.get_latest_account_balance()
        assert latest['balance'] == 110.0
