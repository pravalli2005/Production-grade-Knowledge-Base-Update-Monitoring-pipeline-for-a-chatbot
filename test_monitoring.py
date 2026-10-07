"""
Unit Tests for Telemetry, Latency Metrics, Escalations, and Outage Circuit Breaker.
"""
from pipeline.monitoring import MetricsCollector, CircuitBreaker
from pipeline.chatbot import ChatbotEngine
from pipeline.versioning import VersionManager
from pipeline.vector_store import VectorStore
from pipeline.chunking import chunk_document

def test_metrics_collector_latency_and_percentiles():
    collector = MetricsCollector()

    latencies = [10.0, 15.0, 20.0, 25.0, 30.0, 50.0, 100.0, 200.0]
    for lat in latencies:
        collector.record_query(latency_ms=lat, confidence=0.85)

    p = collector.get_latency_percentiles()
    assert p["p50"] > 0
    assert p["p95"] >= p["p50"]
    assert p["p99"] >= p["p95"]
    assert collector.total_queries == 8
    assert collector.total_errors == 0

def test_circuit_breaker_and_outage_fallback(temp_workspace):
    vm = VersionManager(versions_dir=temp_workspace["versions"])
    # Seed knowledge base
    c = chunk_document("doc.txt", "Standard customer support info.", "h1")
    s = VectorStore()
    s.build_index(c)
    cand = vm.stage_candidate_version(c, s)
    vm.promote_candidate(cand, {"accuracy": 1.0})

    engine = ChatbotEngine(version_manager=vm)

    # 1. Normal state: CLOSED
    resp1 = engine.ask("What is customer support?")
    assert not resp1.escalated or resp1.escalation_reason != "SERVICE_OUTAGE"

    # 2. Simulate Upstream Outage
    from pipeline.monitoring import METRICS
    METRICS.circuit_breaker.set_simulated_outage(True)
    assert METRICS.circuit_breaker.state == "OPEN"

    # Query during outage: Should return graceful fallback without raising uncaught exception
    resp2 = engine.ask("What is customer support?")
    assert resp2.escalated is True
    assert resp2.escalation_reason == "SERVICE_OUTAGE"
    assert "service degradation" in resp2.answer.lower() or "offline queue" in resp2.answer.lower()

    # 3. Restore service
    METRICS.circuit_breaker.set_simulated_outage(False)
    assert METRICS.circuit_breaker.state == "CLOSED"

    resp3 = engine.ask("What is customer support?")
    assert resp3.escalation_reason != "SERVICE_OUTAGE"

def test_low_confidence_escalation(temp_workspace):
    vm = VersionManager(versions_dir=temp_workspace["versions"])
    c = chunk_document("doc.txt", "We sell widgets and gadgets.", "h1")
    s = VectorStore()
    s.build_index(c)
    cand = vm.stage_candidate_version(c, s)
    vm.promote_candidate(cand, {"accuracy": 1.0})

    engine = ChatbotEngine(version_manager=vm)

    # Query with completely unrelated intent -> low confidence
    resp = engine.ask("What is the quantum mechanics formula for photon momentum?")
    assert resp.escalated is True
    assert resp.escalation_reason in [
        "LOW_CONFIDENCE",
        "NO_GROUNDED_MATCH",
        "AI_PROVIDER_NOT_CONFIGURED"
    ]
