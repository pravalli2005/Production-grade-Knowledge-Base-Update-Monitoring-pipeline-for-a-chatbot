"""
Real-Time Telemetry & Monitoring: Latency, Failures, Confidence, Escalations, and Circuit Breaker.
"""
import time
import math
from typing import Dict, Any, List, Optional
import numpy as np

from config import (
    CONFIDENCE_ESCALATION_THRESHOLD,
    CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    CIRCUIT_BREAKER_RESET_TIMEOUT
)

class CircuitBreaker:
    """
    Protects chatbot and pipeline during service outages.
    States: CLOSED (normal), OPEN (outage fallback), HALF_OPEN (recovery testing).
    """
    def __init__(
        self,
        failure_threshold: int = CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        reset_timeout: float = CIRCUIT_BREAKER_RESET_TIMEOUT
    ):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.state = "CLOSED" # CLOSED, OPEN, HALF_OPEN
        self.consecutive_failures = 0
        self.last_failure_time = 0.0
        self.simulated_outage = False

    def set_simulated_outage(self, active: bool):
        """Toggles simulated external outage for evaluation testing."""
        self.simulated_outage = active
        if active:
            self.state = "OPEN"
            self.last_failure_time = time.time()
        else:
            self.state = "CLOSED"
            self.consecutive_failures = 0

    def can_execute(self) -> bool:
        if self.simulated_outage:
            return False

        if self.state == "CLOSED":
            return True

        now = time.time()
        if self.state == "OPEN":
            if now - self.last_failure_time > self.reset_timeout:
                self.state = "HALF_OPEN"
                return True
            return False

        if self.state == "HALF_OPEN":
            return True

        return True

    def record_success(self):
        if self.state == "HALF_OPEN":
            self.state = "CLOSED"
        self.consecutive_failures = 0

    def record_failure(self):
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        if self.consecutive_failures >= self.failure_threshold:
            self.state = "OPEN"


class MetricsCollector:
    """
    In-memory and windowed metrics collector for latency, failures, confidence, and escalations.
    """
    def __init__(self, window_size: int = 1000):
        self.window_size = window_size
        self.latencies_ms: List[float] = []
        self.confidences: List[float] = []
        self.total_queries: int = 0
        self.total_errors: int = 0
        self.total_escalations: int = 0
        self.escalation_reasons: Dict[str, int] = {
            "LOW_CONFIDENCE": 0,
            "PROMPT_INJECTION": 0,
            "SERVICE_OUTAGE": 0,
            "UNHANDLED_EXCEPTION": 0
        }
        self.circuit_breaker = CircuitBreaker()

    def record_query(
        self,
        latency_ms: float,
        confidence: float,
        is_error: bool = False,
        escalated: bool = False,
        escalation_reason: Optional[str] = None
    ):
        self.total_queries += 1
        self.latencies_ms.append(latency_ms)
        if len(self.latencies_ms) > self.window_size:
            self.latencies_ms.pop(0)

        self.confidences.append(confidence)
        if len(self.confidences) > self.window_size:
            self.confidences.pop(0)

        if is_error:
            self.total_errors += 1

        if escalated:
            self.total_escalations += 1
            if escalation_reason:
                self.escalation_reasons[escalation_reason] = self.escalation_reasons.get(escalation_reason, 0) + 1

    def get_latency_percentiles(self) -> Dict[str, float]:
        if not self.latencies_ms:
            return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "avg": 0.0}
        
        arr = np.array(self.latencies_ms)
        return {
            "p50": round(float(np.percentile(arr, 50)), 2),
            "p90": round(float(np.percentile(arr, 90)), 2),
            "p95": round(float(np.percentile(arr, 95)), 2),
            "p99": round(float(np.percentile(arr, 99)), 2),
            "avg": round(float(np.mean(arr)), 2)
        }

    def get_confidence_stats(self) -> Dict[str, float]:
        if not self.confidences:
            return {"avg": 0.0, "min": 0.0, "max": 0.0}
        arr = np.array(self.confidences)
        return {
            "avg": round(float(np.mean(arr)), 3),
            "min": round(float(np.min(arr)), 3),
            "max": round(float(np.max(arr)), 3)
        }

    def get_summary(self) -> Dict[str, Any]:
        total = max(1, self.total_queries)
        return {
            "total_queries": self.total_queries,
            "total_errors": self.total_errors,
            "error_rate": round(self.total_errors / total, 4),
            "total_escalations": self.total_escalations,
            "escalation_rate": round(self.total_escalations / total, 4),
            "escalation_breakdown": self.escalation_reasons,
            "latency_ms": self.get_latency_percentiles(),
            "confidence": self.get_confidence_stats(),
            "circuit_breaker": {
                "state": self.circuit_breaker.state,
                "simulated_outage": self.circuit_breaker.simulated_outage,
                "consecutive_failures": self.circuit_breaker.consecutive_failures
            }
        }

# Global metrics collector instance
METRICS = MetricsCollector()
