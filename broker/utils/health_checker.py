"""
Self-Monitoring Health Checker
Tracks errors in-memory and reacts to recurring problems.
"""

import json
import logging
import time
from collections import defaultdict
from pathlib import Path

from config.settings import LOGS_DIR

logger = logging.getLogger(__name__)


class HealthChecker:
    """
    Monitors bot health by tracking errors in a rolling time window.
    When error thresholds are exceeded, triggers corrective actions
    like pausing trading or forcing a re-sync.
    """

    # Error categories
    API_ERROR = "api_error"
    SYNC_ERROR = "sync_error"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    ORDER_FAILED = "order_failed"
    DB_ERROR = "db_error"
    GHOST_POSITION = "ghost_position"
    ITERATION_CRASH = "iteration_crash"

    # Default thresholds: (max_errors_in_window, window_seconds, action)
    DEFAULT_THRESHOLDS = {
        API_ERROR: (5, 600, "pause"),           # 5 API errors in 10 min → pause
        SYNC_ERROR: (3, 600, "pause"),           # 3 sync errors in 10 min → pause
        INSUFFICIENT_FUNDS: (3, 300, "pause"),   # 3 insufficient funds in 5 min → pause
        ORDER_FAILED: (5, 600, "pause"),         # 5 order failures in 10 min → pause
        DB_ERROR: (3, 300, "pause"),             # 3 DB errors in 5 min → pause
        GHOST_POSITION: (5, 3600, "log"),        # 5 ghost closes in 1h → log warning
        ITERATION_CRASH: (3, 300, "pause"),      # 3 iteration crashes in 5 min → pause
    }

    def __init__(self, check_interval: int = 300):
        """
        Args:
            check_interval: Seconds between health evaluations (default: 5 min).
        """
        self.check_interval = check_interval
        self._last_check = 0
        self._errors: dict[str, list[float]] = defaultdict(list)
        self._thresholds = dict(self.DEFAULT_THRESHOLDS)
        self._pause_until = 0.0
        self._pause_reason = ""
        self._stats = {
            "total_errors": 0,
            "pauses_triggered": 0,
            "last_pause_reason": "",
        }

    # ------------------------------------------------------------------
    # Error recording
    # ------------------------------------------------------------------

    def record_error(self, category: str, detail: str = ""):
        """Record an error occurrence with current timestamp."""
        now = time.time()
        self._errors[category].append(now)
        self._stats["total_errors"] += 1
        logger.debug(f"[HEALTH] Error recorded: {category} — {detail}")

    # ------------------------------------------------------------------
    # Health evaluation
    # ------------------------------------------------------------------

    def check(self) -> bool:
        """
        Evaluate accumulated errors against thresholds.
        Called once per iteration; actual evaluation runs every check_interval.

        Returns:
            True if trading should continue, False if paused.
        """
        now = time.time()

        # Already paused?
        if self._pause_until > now:
            remaining = int(self._pause_until - now)
            logger.warning(
                f"[HEALTH] Trading paused ({self._pause_reason}). "
                f"Resuming in {remaining}s."
            )
            return False

        # Only evaluate every check_interval seconds
        if now - self._last_check < self.check_interval:
            return True
        self._last_check = now

        triggered = False

        for category, (max_errors, window, action) in self._thresholds.items():
            # Prune old entries outside the window
            cutoff = now - window
            self._errors[category] = [
                t for t in self._errors[category] if t > cutoff
            ]
            count = len(self._errors[category])

            if count >= max_errors:
                logger.error(
                    f"[HEALTH] Threshold exceeded: {category} — "
                    f"{count} errors in last {window}s (limit {max_errors})"
                )
                if action == "pause":
                    self._trigger_pause(category, window)
                    triggered = True
                # Reset counter so we don't keep re-triggering immediately
                self._errors[category].clear()

        if not triggered:
            logger.info(
                f"[HEALTH] OK — {self._error_summary()}"
            )

        self.write_status_file()
        return not triggered

    # ------------------------------------------------------------------
    # Pause management
    # ------------------------------------------------------------------

    @property
    def is_paused(self) -> bool:
        return time.time() < self._pause_until

    def _trigger_pause(self, category: str, duration: int = 300):
        """Pause trading for *duration* seconds."""
        self._pause_until = time.time() + duration
        self._pause_reason = category
        self._stats["pauses_triggered"] += 1
        self._stats["last_pause_reason"] = category
        logger.critical(
            f"[HEALTH] ⛔ TRADING PAUSED for {duration}s due to: {category}"
        )

    def force_resume(self):
        """Manually resume trading (e.g. from web UI)."""
        if self._pause_until > time.time():
            logger.info("[HEALTH] Trading manually resumed")
        self._pause_until = 0
        self._pause_reason = ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _error_summary(self) -> str:
        parts = []
        for cat in self._thresholds:
            count = len(self._errors[cat])
            if count > 0:
                parts.append(f"{cat}={count}")
        return ", ".join(parts) if parts else "no errors"

    def get_status(self) -> dict:
        """Return health status dict (for web UI / diagnostics)."""
        now = time.time()
        error_counts = {}
        for cat, (_, window, _) in self._thresholds.items():
            cutoff = now - window
            error_counts[cat] = len(
                [t for t in self._errors[cat] if t > cutoff]
            )
        return {
            "healthy": not self.is_paused,
            "paused": self.is_paused,
            "pause_reason": self._pause_reason if self.is_paused else "",
            "pause_remaining_s": max(0, int(self._pause_until - now)),
            "error_counts": error_counts,
            **self._stats,
        }

    def write_status_file(self):
        """Write health status to JSON file for the web UI to read."""
        status_path = LOGS_DIR / "health_status.json"
        try:
            status = self.get_status()
            status["timestamp"] = time.time()
            status_path.write_text(json.dumps(status))
        except Exception:
            pass  # Non-critical — don't let status writing crash the bot
