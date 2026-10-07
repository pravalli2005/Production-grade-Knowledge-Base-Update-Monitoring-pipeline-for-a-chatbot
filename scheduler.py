"""
Scheduler, Maintenance Window Gatekeeper, and 15/30/60-Minute Retry Engine.
"""
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
import json

from config import (
    STATE_DIR,
    MAINTENANCE_WINDOW_START_HOUR,
    MAINTENANCE_WINDOW_END_HOUR,
    RETRY_INTERVALS_MINUTES,
    MAX_RETRY_ATTEMPTS
)
from pipeline.security import log_security_event

SCHEDULER_STATE_FILE = STATE_DIR / "scheduler_state.json"

class MaintenanceGatekeeper:
    def __init__(
        self,
        start_hour: int = MAINTENANCE_WINDOW_START_HOUR,
        end_hour: int = MAINTENANCE_WINDOW_END_HOUR
    ):
        self.start_hour = start_hour
        self.end_hour = end_hour
        self._simulated_override: Optional[bool] = None

    def set_simulated_override(self, is_inside_window: Optional[bool]):
        """Sets simulation override for evaluation testing and demo."""
        self._simulated_override = is_inside_window

    def is_window_active(self, at_time: Optional[float] = None) -> Tuple[bool, str]:
        """
        Checks if current time is within maintenance window.
        Returns: (is_active, status_message)
        """
        if self._simulated_override is not None:
            return self._simulated_override, f"Simulated maintenance window override: {self._simulated_override}"

        dt = datetime.fromtimestamp(at_time or time.time(), tz=timezone.utc)
        current_hour = dt.hour

        if self.start_hour <= self.end_hour:
            in_window = self.start_hour <= current_hour < self.end_hour
        else:
            # Wraps around midnight (e.g., 22:00 to 04:00)
            in_window = current_hour >= self.start_hour or current_hour < self.end_hour

        status = f"UTC current hour: {current_hour}:00. Window is {self.start_hour}:00 to {self.end_hour}:00 UTC."
        return in_window, status


class RetryManager:
    """
    Manages retry state with intervals: 15 min (attempt 1), 30 min (attempt 2), 60 min (attempt 3).
    """
    def __init__(self, intervals_minutes: List[int] = RETRY_INTERVALS_MINUTES):
        self.intervals_seconds = [m * 60 for m in intervals_minutes]
        self.max_attempts = len(self.intervals_seconds)
        self.state_file = SCHEDULER_STATE_FILE
        self._load_state()

    def _load_state(self):
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    self.state = json.load(f)
            except Exception:
                self.state = self._init_state()
        else:
            self.state = self._init_state()

    def _init_state(self) -> Dict[str, Any]:
        return {
            "pending_retry": False,
            "attempt_count": 0,
            "next_retry_timestamp": 0,
            "last_failure_reason": None,
            "staged_candidate_id": None,
            "history": []
        }

    def _save_state(self):
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def record_failure(self, reason: str, candidate_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Records failure and computes next retry time according to [15m, 30m, 60m] policy.
        """
        attempt = self.state.get("attempt_count", 0) + 1
        now = time.time()

        if attempt <= self.max_attempts:
            delay = self.intervals_seconds[attempt - 1]
            delay_minutes = delay // 60
            next_retry = now + delay
            self.state["pending_retry"] = True
            self.state["attempt_count"] = attempt
            self.state["next_retry_timestamp"] = next_retry
            self.state["last_failure_reason"] = reason
            self.state["staged_candidate_id"] = candidate_id
            self.state["status"] = f"RETRY_SCHEDULED_ATTEMPT_{attempt}_IN_{delay_minutes}M"
        else:
            self.state["pending_retry"] = False
            self.state["status"] = "RETRIES_EXHAUSTED"
            self.state["next_retry_timestamp"] = 0

        self.state["history"].append({
            "timestamp": now,
            "attempt": attempt,
            "reason": reason,
            "status": self.state["status"]
        })
        self._save_state()

        log_security_event(
            event_type="PIPELINE_RETRY_SCHEDULED",
            actor="scheduler",
            status="WARNING",
            details={
                "attempt": attempt,
                "reason": reason,
                "status": self.state["status"]
            }
        )
        return self.state

    def reset(self):
        """Resets retry state on successful update."""
        self.state = self._init_state()
        self._save_state()

    def is_retry_due(self, current_time: Optional[float] = None) -> bool:
        if not self.state.get("pending_retry"):
            return False
        now = current_time or time.time()
        return now >= self.state.get("next_retry_timestamp", 0)

    def fast_forward_time(self, seconds: float):
        """Simulates time passing for evaluation testing."""
        if self.state.get("pending_retry") and self.state.get("next_retry_timestamp", 0) > 0:
            self.state["next_retry_timestamp"] -= seconds
            self._save_state()
