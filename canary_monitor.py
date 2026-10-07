"""
Post-Activation 5-Minute Canary Health Check and Auto-Rollback Monitor.
"""
import time
import json
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

from config import (
    STATE_DIR,
    CANARY_WINDOW_SECONDS,
    CANARY_MAX_ERROR_RATE,
    CANARY_MAX_LATENCY_P95_MS
)
from pipeline.versioning import VersionManager
from pipeline.security import log_security_event

CANARY_STATE_FILE = STATE_DIR / "canary_state.json"

CANARY_PROBE_QUERIES = [
    "What is the company return policy?",
    "How do I reset my password?",
    "What are your customer support working hours?"
]

class CanaryHealthMonitor:
    def __init__(self, canary_window_seconds: int = CANARY_WINDOW_SECONDS):
        self.canary_window = canary_window_seconds
        self.state_file = CANARY_STATE_FILE
        self._load_state()

    def _load_state(self):
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    self.state = json.load(f)
            except Exception:
                self.state = self._default_state()
        else:
            self.state = self._default_state()

    def _default_state(self) -> Dict[str, Any]:
        return {
            "is_canary_active": False,
            "version_under_test": None,
            "activation_timestamp": 0,
            "expiry_timestamp": 0,
            "probe_runs": 0,
            "probe_failures": 0,
            "status": "IDLE", # "MONITORING", "HEALTHY_PASSED", "AUTO_ROLLED_BACK"
            "last_check_timestamp": 0,
            "probe_logs": []
        }

    def _save_state(self):
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def start_canary(self, activated_version: str):
        """Starts 5-minute health check canary on activated version."""
        now = time.time()
        self.state = {
            "is_canary_active": True,
            "version_under_test": activated_version,
            "activation_timestamp": now,
            "expiry_timestamp": now + self.canary_window,
            "probe_runs": 0,
            "probe_failures": 0,
            "status": "MONITORING",
            "last_check_timestamp": now,
            "probe_logs": []
        }
        self._save_state()
        log_security_event(
            event_type="CANARY_MONITOR_STARTED",
            actor="canary_monitor",
            status="INFO",
            details={"version": activated_version, "duration_seconds": self.canary_window}
        )

    def run_health_probes(
        self,
        version_manager: VersionManager,
        simulated_failure: bool = False,
        current_time: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Executes canary health checks against active vector store.
        If checks fail, automatically triggers immediate rollback.
        """
        now = current_time or time.time()
        if not self.state.get("is_canary_active"):
            return {
                "is_canary_active": False,
                "message": "No canary monitoring currently active."
            }

        version = self.state.get("version_under_test")
        active_store = version_manager.load_active_store()

        self.state["probe_runs"] += 1
        self.state["last_check_timestamp"] = now

        probe_success = True
        probe_details = []

        if simulated_failure or active_store is None:
            probe_success = False
            probe_details.append("Forced failure or Active Store unavailable.")
        else:
            # Run synthetic queries
            for q in CANARY_PROBE_QUERIES:
                start = time.perf_counter()
                results = active_store.search(q, top_k=2)
                latency_ms = (time.perf_counter() - start) * 1000

                # Must return results with top score >= 0.12
                if not results or results[0][1] < 0.12 or latency_ms > CANARY_MAX_LATENCY_P95_MS:
                    probe_success = False
                    probe_details.append(
                        f"Probe '{q}' failed: hits={len(results)}, top_score={results[0][1] if results else 0:.2f}, latency={latency_ms:.1f}ms"
                    )
                else:
                    probe_details.append(
                        f"Probe '{q}' healthy: top_score={results[0][1]:.2f}, latency={latency_ms:.1f}ms"
                    )

        if not probe_success:
            self.state["probe_failures"] += 1

        self.state["probe_logs"].append({
            "timestamp": now,
            "success": probe_success,
            "details": probe_details
        })

        # Failure condition: any probe failure during canary triggers auto-rollback
        if not probe_success:
            rollback_reason = f"Canary health check failed within 5 minutes on version {version}: {'; '.join(probe_details)}"
            success, msg, new_active = version_manager.rollback(reason=rollback_reason)
            self.state["is_canary_active"] = False
            self.state["status"] = "AUTO_ROLLED_BACK"
            self._save_state()

            log_security_event(
                event_type="AUTO_ROLLBACK_TRIGGERED",
                actor="canary_monitor",
                status="CRITICAL",
                details={"version": version, "rolled_back_to": new_active, "reason": rollback_reason}
            )
            return {
                "health_status": "FAILED",
                "action": "AUTO_ROLLED_BACK",
                "reason": rollback_reason,
                "current_active_version": new_active
            }

        # Check if 5 minutes have elapsed
        if now >= self.state.get("expiry_timestamp", 0):
            self.state["is_canary_active"] = False
            self.state["status"] = "HEALTHY_PASSED"
            self._save_state()

            log_security_event(
                event_type="CANARY_MONITOR_COMPLETED",
                actor="canary_monitor",
                status="SUCCESS",
                details={"version": version, "probes_run": self.state["probe_runs"]}
            )
            return {
                "health_status": "PASSED",
                "action": "PROMOTED_STABLE",
                "message": "5-minute canary window completed successfully without errors."
            }

        self._save_state()
        remaining_seconds = max(0, int(self.state.get("expiry_timestamp", 0) - now))
        return {
            "health_status": "HEALTHY",
            "action": "CONTINUE_MONITORING",
            "remaining_seconds": remaining_seconds,
            "probe_runs": self.state["probe_runs"]
        }
