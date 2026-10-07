"""
Unit Tests for Post-Activation 5-Minute Canary Health Monitoring and Automated Rollback.
"""
import time
from pipeline.canary_monitor import CanaryHealthMonitor
from pipeline.versioning import VersionManager
from pipeline.vector_store import VectorStore
from pipeline.chunking import chunk_document

def test_canary_auto_rollback_on_health_check_failure(temp_workspace):
    versions_dir = temp_workspace["versions"]
    vm = VersionManager(versions_dir=versions_dir)

    # Establish stable v1.0.0
    c1 = chunk_document("doc1.txt", "Return policy allows 30 days refund with receipt.", "h1")
    s1 = VectorStore()
    s1.build_index(c1)
    cand_1 = vm.stage_candidate_version(c1, s1)
    vm.promote_candidate(cand_1, {"accuracy": 1.0})

    # Activate candidate v1.1.0
    c2 = chunk_document("doc2.txt", "Some new document content.", "h2")
    s2 = VectorStore()
    s2.build_index(c2)
    cand_2 = vm.stage_candidate_version(c2, s2)
    v1_1 = vm.promote_candidate(cand_2, {"accuracy": 1.0})

    assert vm.manifest["active_version"] == "v1.1.0"
    assert vm.manifest["previous_version"] == "v1.0.0"

    # Start 5-minute canary
    canary = CanaryHealthMonitor(canary_window_seconds=300)
    canary.state_file = temp_workspace["state"] / "canary_test.json"
    canary.start_canary(v1_1)
    assert canary.state["is_canary_active"] is True
    assert canary.state["version_under_test"] == "v1.1.0"

    # Simulate health probe failure within the 5-minute window
    res = canary.run_health_probes(version_manager=vm, simulated_failure=True)

    # Auto-rollback must be triggered!
    assert res["health_status"] == "FAILED"
    assert res["action"] == "AUTO_ROLLED_BACK"
    assert res["current_active_version"] == "v1.0.0"

    # Verify version manager active version is back to v1.0.0
    assert vm.manifest["active_version"] == "v1.0.0"
    assert canary.state["is_canary_active"] is False
    assert canary.state["status"] == "AUTO_ROLLED_BACK"

def test_canary_passes_after_5_minutes(temp_workspace):
    versions_dir = temp_workspace["versions"]
    vm = VersionManager(versions_dir=versions_dir)

    # Comprehensive document satisfying canary probe queries
    content = """
    What is the company return policy? Customers can return items within 30 days for full refund with receipt.
    How do I reset my password? Navigate to login page, click forgot password, enter email.
    What are your customer support working hours? Support is available 24/7.
    """
    c = chunk_document("comprehensive.txt", content, "hc")
    s = VectorStore()
    s.build_index(c)
    cand = vm.stage_candidate_version(c, s)
    active_ver = vm.promote_candidate(cand, {"accuracy": 1.0})

    canary = CanaryHealthMonitor(canary_window_seconds=300)
    canary.state_file = temp_workspace["state"] / "canary_pass_test.json"
    canary.start_canary(active_ver)

    # Run healthy probe at t=10s
    res1 = canary.run_health_probes(version_manager=vm)
    assert res1["health_status"] == "HEALTHY"
    assert res1["action"] == "CONTINUE_MONITORING"
    assert canary.state["is_canary_active"] is True

    # Fast forward past 5 minutes (301 seconds)
    t_after_5m = canary.state["activation_timestamp"] + 305
    res2 = canary.run_health_probes(version_manager=vm, current_time=t_after_5m)
    assert res2["health_status"] == "PASSED"
    assert res2["action"] == "PROMOTED_STABLE"
    assert canary.state["is_canary_active"] is False
    assert vm.manifest["active_version"] == active_ver
