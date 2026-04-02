"""Tests for the HealthChecker self-monitoring module."""

import time
import json
import pytest
from unittest.mock import patch

from broker.utils.health_checker import HealthChecker


class TestHealthChecker:
    """Unit tests for HealthChecker."""

    def test_initial_state_healthy(self):
        hc = HealthChecker(check_interval=0)
        assert hc.check() is True
        assert hc.is_paused is False

    def test_record_error_increments(self):
        hc = HealthChecker(check_interval=0)
        hc.record_error(HealthChecker.API_ERROR, "test")
        status = hc.get_status()
        assert status["error_counts"]["api_error"] == 1
        assert status["total_errors"] == 1

    def test_threshold_triggers_pause(self):
        hc = HealthChecker(check_interval=0)
        # Default threshold for API_ERROR is 5 in 600s
        for _ in range(5):
            hc.record_error(HealthChecker.API_ERROR)
        # check() should detect threshold exceeded and pause
        result = hc.check()
        assert result is False
        assert hc.is_paused is True
        status = hc.get_status()
        assert status["paused"] is True
        assert status["pause_reason"] == HealthChecker.API_ERROR
        assert status["pauses_triggered"] == 1

    def test_below_threshold_no_pause(self):
        hc = HealthChecker(check_interval=0)
        # 4 errors — below the 5-error threshold
        for _ in range(4):
            hc.record_error(HealthChecker.API_ERROR)
        result = hc.check()
        assert result is True
        assert hc.is_paused is False

    def test_pause_blocks_check(self):
        hc = HealthChecker(check_interval=0)
        for _ in range(5):
            hc.record_error(HealthChecker.API_ERROR)
        hc.check()  # triggers pause
        # Subsequent check should return False while paused
        assert hc.check() is False

    def test_force_resume(self):
        hc = HealthChecker(check_interval=0)
        for _ in range(5):
            hc.record_error(HealthChecker.API_ERROR)
        hc.check()
        assert hc.is_paused is True
        hc.force_resume()
        assert hc.is_paused is False
        assert hc.check() is True

    def test_errors_expire_outside_window(self):
        hc = HealthChecker(check_interval=0)
        # Manually inject old timestamps (older than the 600s window)
        old_time = time.time() - 700
        hc._errors[HealthChecker.API_ERROR] = [old_time] * 5
        # check() should prune them and NOT trigger pause
        result = hc.check()
        assert result is True
        assert len(hc._errors[HealthChecker.API_ERROR]) == 0

    def test_multiple_categories_independent(self):
        hc = HealthChecker(check_interval=0)
        # 4 API + 2 sync — neither hits threshold
        for _ in range(4):
            hc.record_error(HealthChecker.API_ERROR)
        for _ in range(2):
            hc.record_error(HealthChecker.SYNC_ERROR)
        result = hc.check()
        assert result is True

    def test_insufficient_funds_triggers_pause(self):
        hc = HealthChecker(check_interval=0)
        # Threshold is 3 in 300s
        for _ in range(3):
            hc.record_error(HealthChecker.INSUFFICIENT_FUNDS)
        result = hc.check()
        assert result is False
        assert hc.get_status()["pause_reason"] == HealthChecker.INSUFFICIENT_FUNDS

    def test_ghost_position_only_logs(self):
        """Ghost position threshold action is 'log', not 'pause'."""
        hc = HealthChecker(check_interval=0)
        for _ in range(5):
            hc.record_error(HealthChecker.GHOST_POSITION)
        result = hc.check()
        # Should still be True — ghost positions use "log" action, not "pause"
        assert result is True
        assert hc.is_paused is False

    def test_get_status_structure(self):
        hc = HealthChecker(check_interval=0)
        status = hc.get_status()
        assert "healthy" in status
        assert "paused" in status
        assert "error_counts" in status
        assert "total_errors" in status
        assert "pauses_triggered" in status
        assert isinstance(status["error_counts"], dict)

    def test_check_interval_throttles(self):
        hc = HealthChecker(check_interval=300)
        # First call evaluates (check_interval=300, _last_check=0)
        assert hc.check() is True
        # Record enough errors to trigger
        for _ in range(5):
            hc.record_error(HealthChecker.API_ERROR)
        # Second call should be throttled — returns True (no evaluation)
        assert hc.check() is True
        assert hc.is_paused is False

    def test_write_status_file(self, tmp_path):
        """Status file is written correctly."""
        hc = HealthChecker(check_interval=0)
        hc.record_error(HealthChecker.API_ERROR, "test")
        with patch("broker.utils.health_checker.LOGS_DIR", tmp_path):
            hc.write_status_file()
        status_file = tmp_path / "health_status.json"
        assert status_file.exists()
        data = json.loads(status_file.read_text())
        assert data["healthy"] is True
        assert data["error_counts"]["api_error"] == 1
        assert "timestamp" in data

    def test_counter_resets_after_pause(self):
        """After triggering a pause, the error counter for that category is cleared."""
        hc = HealthChecker(check_interval=0)
        for _ in range(5):
            hc.record_error(HealthChecker.API_ERROR)
        hc.check()
        hc.force_resume()
        # Counter should have been cleared by check()
        status = hc.get_status()
        assert status["error_counts"]["api_error"] == 0
