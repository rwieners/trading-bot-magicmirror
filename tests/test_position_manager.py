"""
Tests for broker/risk/position_manager.py — budget limits and position tracking.
"""
import pytest
from broker.risk.position_manager import PositionManager, Position


@pytest.fixture
def pm():
    """A fresh PositionManager with €100 balance, no DB."""
    manager = PositionManager(initial_balance=100.0)
    manager._max_open_positions_override = 3
    manager._max_positions_per_symbol_override = 1
    return manager


class TestCanOpenPosition:
    def test_allows_valid_position(self, pm):
        ok, reason = pm.can_open_position('BTC/EUR', 10.0)
        assert ok
        assert reason == "OK"

    def test_rejects_duplicate_symbol(self, pm):
        pm.open_position('BTC/EUR', entry_price=50000.0, size=10.0, entry_fee=0.03)
        ok, reason = pm.can_open_position('BTC/EUR', 10.0)
        assert not ok
        assert 'already open' in reason.lower() or 'position(s)' in reason.lower()

    def test_rejects_over_max_positions(self, pm):
        pm.open_position('BTC/EUR', entry_price=50000.0, size=10.0, entry_fee=0.03)
        pm.open_position('ETH/EUR', entry_price=3000.0, size=10.0, entry_fee=0.03)
        pm.open_position('SOL/EUR', entry_price=100.0, size=10.0, entry_fee=0.03)
        ok, reason = pm.can_open_position('XRP/EUR', 10.0)
        assert not ok
        assert 'max' in reason.lower()

    def test_rejects_insufficient_cash(self, pm):
        # €200 exceeds both the position size limit (€10) and cash (€100)
        ok, reason = pm.can_open_position('BTC/EUR', 200.0)
        assert not ok


class TestMultiPositionPerSymbol:
    """Tests for multiple positions per symbol (scalping mode)."""

    @pytest.fixture
    def scalping_pm(self):
        """PositionManager configured for scalping: 3 positions per symbol, 8 total."""
        manager = PositionManager(initial_balance=100.0)
        manager._max_open_positions_override = 8
        manager._max_positions_per_symbol_override = 3
        return manager

    def test_allows_multiple_positions_same_symbol(self, scalping_pm):
        ok1, _ = scalping_pm.open_position('BTC/EUR', entry_price=50000.0, size=10.0, entry_fee=0.03)
        assert ok1
        ok2, _ = scalping_pm.open_position('BTC/EUR', entry_price=51000.0, size=10.0, entry_fee=0.03)
        assert ok2
        assert scalping_pm.count_positions_for_symbol('BTC/EUR') == 2

    def test_rejects_at_per_symbol_limit(self, scalping_pm):
        scalping_pm.open_position('BTC/EUR', entry_price=50000.0, size=10.0, entry_fee=0.03)
        scalping_pm.open_position('BTC/EUR', entry_price=51000.0, size=10.0, entry_fee=0.03)
        scalping_pm.open_position('BTC/EUR', entry_price=52000.0, size=10.0, entry_fee=0.03)
        ok, reason = scalping_pm.can_open_position('BTC/EUR', 10.0)
        assert not ok
        assert '3' in reason

    def test_composite_keys_used(self, scalping_pm):
        scalping_pm.open_position('BTC/EUR', entry_price=50000.0, size=10.0, entry_fee=0.03)
        scalping_pm.open_position('BTC/EUR', entry_price=51000.0, size=10.0, entry_fee=0.03)
        keys = list(scalping_pm.positions.keys())
        # First uses plain key, second uses composite
        assert keys[0] == 'BTC/EUR'
        assert keys[1].startswith('BTC/EUR_')

    def test_close_composite_position(self, scalping_pm):
        scalping_pm.open_position('BTC/EUR', entry_price=50000.0, size=10.0, entry_fee=0.0)
        scalping_pm.open_position('BTC/EUR', entry_price=51000.0, size=10.0, entry_fee=0.0)
        composite_key = [k for k in scalping_pm.positions if k != 'BTC/EUR'][0]
        success, stats = scalping_pm.close_position(composite_key, exit_price=53000.0, exit_fee=0.0)
        assert success
        assert scalping_pm.count_positions_for_symbol('BTC/EUR') == 1

    def test_count_with_imported_composite_keys(self, scalping_pm):
        """Composite keys from DB sync (e.g., BTC/EUR_7) are counted correctly."""
        scalping_pm.import_position('BTC/EUR_7', amount=0.001, entry_price=50000.0, current_price=50000.0)
        pos = scalping_pm.positions.get('BTC/EUR_7')
        pos.original_symbol = 'BTC/EUR'
        scalping_pm.import_position('BTC/EUR_12', amount=0.001, entry_price=51000.0, current_price=51000.0)
        pos2 = scalping_pm.positions.get('BTC/EUR_12')
        pos2.original_symbol = 'BTC/EUR'
        assert scalping_pm.count_positions_for_symbol('BTC/EUR') == 2

    def test_default_is_one_per_symbol(self, pm):
        """Default MAX_POSITIONS_PER_SYMBOL is 1 (conservative/aggressive)."""
        pm.open_position('BTC/EUR', entry_price=50000.0, size=10.0, entry_fee=0.03)
        ok, _ = pm.can_open_position('BTC/EUR', 10.0)
        assert not ok


class TestOpenPosition:
    def test_opens_and_deducts_cash(self, pm):
        success, _ = pm.open_position('BTC/EUR', entry_price=50000.0, size=10.0, entry_fee=0.03)
        assert success
        assert 'BTC/EUR' in pm.positions
        assert pm.cash == pytest.approx(100.0 - 10.0 - 0.03, abs=0.01)

    def test_entry_size_is_coins_not_eur(self, pm):
        pm.open_position('BTC/EUR', entry_price=50000.0, size=10.0, entry_fee=0.0)
        pos = pm.positions['BTC/EUR']
        # entry_size should be coin amount: 10 / 50000 = 0.0002
        assert pos.entry_size == pytest.approx(0.0002, rel=1e-6)


class TestImportPosition:
    def test_import_does_not_deduct_cash(self, pm):
        cash_before = pm.cash
        pm.import_position('BTC/EUR', amount=0.001, entry_price=50000.0, current_price=51000.0,
                          entry_fee=0.013)
        # Cash should stay the same (position already on exchange)
        assert pm.cash == cash_before

    def test_import_tracks_position(self, pm):
        pm.import_position('ETH/EUR', amount=0.5, entry_price=3000.0, current_price=3100.0)
        assert 'ETH/EUR' in pm.positions
        pos = pm.positions['ETH/EUR']
        assert pos.entry_price == 3000.0
        assert pos.current_price == 3100.0

    def test_import_rejects_duplicate(self, pm):
        pm.import_position('BTC/EUR', amount=0.001, entry_price=50000.0, current_price=50000.0)
        ok, reason = pm.import_position('BTC/EUR', amount=0.002, entry_price=50000.0, current_price=50000.0)
        assert not ok
        assert 'already tracked' in reason.lower()

    def test_import_not_blocked_by_max_positions(self, pm):
        """Imported positions from Kraken should not be blocked by MAX_OPEN_POSITIONS."""
        pm.import_position('BTC/EUR', amount=0.001, entry_price=50000.0, current_price=50000.0)
        pm.import_position('ETH/EUR', amount=0.5, entry_price=3000.0, current_price=3000.0)
        pm.import_position('SOL/EUR', amount=1.0, entry_price=100.0, current_price=100.0)
        # 4th import should still work (Kraken is data master)
        ok, _ = pm.import_position('XRP/EUR', amount=100.0, entry_price=0.50, current_price=0.50)
        assert ok

    def test_import_with_entry_fee_from_db(self, pm):
        """When entry_fee is provided from DB, it should be used in P&L calculation."""
        # 10€ position with 0.026€ fee (0.26% taker)
        pm.import_position('BTC/EUR', amount=0.0002, entry_price=50000.0,
                          current_price=50000.0, entry_fee=0.026)
        pos = pm.positions['BTC/EUR']
        assert pos.entry_fee == pytest.approx(0.026, abs=0.001)
        # At same price, unrealized_pnl should be negative (fee drag)
        assert pos.unrealized_pnl < 0

    def test_import_without_fee_estimates_conservatively(self, pm):
        """When entry_fee=0 (old trade), it should be estimated with TAKER_FEE."""
        # 10€ position, no fee recorded
        pm.import_position('BTC/EUR', amount=0.0002, entry_price=50000.0,
                          current_price=50000.0, entry_fee=0)
        pos = pm.positions['BTC/EUR']
        # Should have estimated fee: 0.0002 * 50000 * 0.0026 = 0.026€
        assert pos.entry_fee > 0
        assert pos.entry_fee == pytest.approx(0.026, abs=0.005)
        # At same price, unrealized_pnl should be negative
        assert pos.unrealized_pnl < 0

    def test_imported_position_marginal_profit_blocked(self, pm):
        """A 0.4% gain on imported position should not appear profitable after round-trip costs."""
        # Entry: 10€ at 50000, current: 50200 (+0.4%)
        pm.import_position('BTC/EUR', amount=0.0002, entry_price=50000.0,
                          current_price=50200.0, entry_fee=0.026)
        pos = pm.positions['BTC/EUR']
        # unrealized_pnl = current_value - entry_value
        # current_value = 0.0002 * 50200 = 10.04
        # entry_value = 50000 * 0.0002 + 0.026 = 10.026
        # unrealized_pnl = 10.04 - 10.026 = 0.014
        assert pos.unrealized_pnl == pytest.approx(0.014, abs=0.001)
        # After sell fee (0.26%) + spread (0.1%) on 10.04€:
        # sell costs = 10.04 * 0.0036 = 0.036€
        # net = 0.014 - 0.036 = -0.022 → negative = should NOT sell
        sell_costs = pos.current_value * (0.0026 + 0.001)
        net_after_sell = pos.unrealized_pnl - sell_costs
        assert net_after_sell < 0, "0.4% gain should be negative after all costs"


class TestClosePosition:
    def test_close_returns_cash(self, pm):
        pm.open_position('BTC/EUR', entry_price=50000.0, size=10.0, entry_fee=0.0)
        cash_after_open = pm.cash

        success, stats = pm.close_position('BTC/EUR', exit_price=52000.0, exit_fee=0.0)
        assert success
        assert pm.cash > cash_after_open
        assert 'BTC/EUR' not in pm.positions

    def test_close_profit_status(self, pm):
        pm.open_position('BTC/EUR', entry_price=50000.0, size=10.0, entry_fee=0.0)
        _, stats = pm.close_position('BTC/EUR', exit_price=55000.0, exit_fee=0.0)
        assert stats['net_pnl'] > 0

    def test_close_loss_status(self, pm):
        pm.open_position('BTC/EUR', entry_price=50000.0, size=10.0, entry_fee=0.0)
        _, stats = pm.close_position('BTC/EUR', exit_price=45000.0, exit_fee=0.0)
        assert stats['net_pnl'] < 0

    def test_close_nonexistent_fails(self, pm):
        success, _ = pm.close_position('FAKE/EUR', exit_price=100.0, exit_fee=0.0)
        assert not success


class TestPositionPnL:
    def test_unrealized_pnl_positive(self):
        pos = Position(
            symbol='BTC/EUR', entry_price=50000.0, entry_size=0.001,
            entry_time=1000000, entry_fee=0.0, current_price=55000.0
        )
        assert pos.unrealized_pnl > 0
        assert pos.unrealized_pnl_pct > 0

    def test_unrealized_pnl_negative(self):
        pos = Position(
            symbol='BTC/EUR', entry_price=50000.0, entry_size=0.001,
            entry_time=1000000, entry_fee=0.0, current_price=45000.0
        )
        assert pos.unrealized_pnl < 0

    def test_pnl_includes_fee(self):
        pos = Position(
            symbol='BTC/EUR', entry_price=50000.0, entry_size=0.001,
            entry_time=1000000, entry_fee=0.05, current_price=50000.0
        )
        # Price unchanged, but fee means we're in the red
        assert pos.unrealized_pnl < 0


class TestAccountStats:
    def test_stats_with_no_positions(self, pm):
        stats = pm.get_account_stats()
        assert stats['num_open_positions'] == 0
        assert stats['cash_available_for_trade'] == pytest.approx(100.0)

    def test_stats_reflect_open_positions(self, pm):
        pm.open_position('BTC/EUR', entry_price=50000.0, size=10.0, entry_fee=0.0)
        stats = pm.get_account_stats()
        assert stats['num_open_positions'] == 1
        assert stats['open_positions_value'] > 0
