"""
Unit Tests for Scheduler, Maintenance Window Gatekeeper, and 15/30/60-Minute Retry Policy.
"""
import time
from pipeline.scheduler import MaintenanceGatekeeper, RetryManager

def test_maintenance_window_gatekeeper():
    gate = MaintenanceGatekeeper(start_hour=2, end_hour=4)

    # Simulated outside window
    gate.set_simulated_override(False)
    in_win, status = gate.is_window_active()
    assert in_win is False

    # Simulated inside window
    gate.set_simulated_override(True)
    in_win, status = gate.is_window_active()
    assert in_win is True

    # Reset simulation and test with explicit timestamp
    gate.set_simulated_override(None)
    # Timestamp for 03:00 UTC (inside 02:00-04:00)
    # 2026-10-05 03:00:00 UTC = 1791169200 approx
    t_inside = 1791169200 - (1791169200 % 86400) + (3 * 3600)
    in_win, _ = gate.is_window_active(at_time=t_inside)
    assert in_win is True

    # Timestamp for 10:00 UTC (outside)
    t_outside = 1791169200 - (1791169200 % 86400) + (10 * 3600)
    in_win, _ = gate.is_window_active(at_time=t_outside)
    assert in_win is False

def test_retry_policy_15_30_60_minute_intervals(temp_workspace):
    retry_mgr = RetryManager(intervals_minutes=[15, 30, 60])
    retry_mgr.state_file = temp_workspace["state"] / "retry_test.json"
    retry_mgr.reset()

    # Attempt 1: Should schedule retry 15 minutes (900 seconds) later
    now = time.time()
    s1 = retry_mgr.record_failure("Quality gate failed attempt 1")
    assert s1["pending_retry"] is True
    assert s1["attempt_count"] == 1
    assert "15M" in s1["status"]
    # Approx 900 seconds delay
    expected_delay_1 = s1["next_retry_timestamp"] - now
    assert 895 <= expected_delay_1 <= 905
    assert not retry_mgr.is_retry_due()

    # Fast-forward 15 minutes: Should now be due
    retry_mgr.fast_forward_time(901)
    assert retry_mgr.is_retry_due()

    # Attempt 2: Should schedule retry 30 minutes (1800 seconds) later
    now2 = time.time()
    s2 = retry_mgr.record_failure("Transient error attempt 2")
    assert s2["attempt_count"] == 2
    assert "30M" in s2["status"]
    expected_delay_2 = s2["next_retry_timestamp"] - now2
    assert 1795 <= expected_delay_2 <= 1805

    # Fast-forward 30 minutes
    retry_mgr.fast_forward_time(1801)
    assert retry_mgr.is_retry_due()

    # Attempt 3: Should schedule retry 60 minutes (3600 seconds) later
    now3 = time.time()
    s3 = retry_mgr.record_failure("Service unavailable attempt 3")
    assert s3["attempt_count"] == 3
    assert "60M" in s3["status"]
    expected_delay_3 = s3["next_retry_timestamp"] - now3
    assert 3595 <= expected_delay_3 <= 3605

    # Attempt 4: Max attempts reached -> RETRIES_EXHAUSTED
    s4 = retry_mgr.record_failure("Exhausted")
    assert s4["pending_retry"] is False
    assert s4["status"] == "RETRIES_EXHAUSTED"
