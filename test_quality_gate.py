"""
Unit and Integration Tests for Quality Gate Grounding and Accuracy Evaluation.
"""
from pipeline.quality_gate import QualityGate
from pipeline.vector_store import VectorStore
from pipeline.chunking import chunk_document

def test_quality_gate_approves_valid_knowledge_base():
    qg = QualityGate()

    # Valid comprehensive document covering golden queries
    comprehensive_text = """
    Return Policy: Customers can return unused items within 30 days of purchase with the original receipt for a full refund.
    Account Security: To reset your password, navigate to the login page, click Forgot Password, enter your email and follow the link.
    Customer Support: Customer support is available 24/7 via live chat and email, and phone support runs Monday through Friday.
    International Shipping: Yes, we ship to over 65 countries worldwide. International shipping typically takes 7 to 14 business days.
    Billing FAQ: We accept major credit cards (Visa, MasterCard, American Express), PayPal, Apple Pay, and Google Pay.
    """

    chunks = chunk_document(doc_name="comprehensive.md", raw_content=comprehensive_text, doc_hash="h1")
    store = VectorStore()
    store.build_index(chunks)

    report = qg.evaluate_candidate(candidate_store=store)

    assert report.decision == "APPROVED"
    assert report.accuracy_score >= 0.85
    assert report.grounding_score >= 0.80
    assert len(report.rejection_reasons) == 0

def test_quality_gate_rejects_insufficient_or_noisy_knowledge_base():
    qg = QualityGate()

    # Incomplete knowledge base missing return policy and support hours
    poor_text = "General store information. We love our customers. Welcome to our website."
    chunks = chunk_document(doc_name="noisy.md", raw_content=poor_text, doc_hash="h2")
    store = VectorStore()
    store.build_index(chunks)

    report = qg.evaluate_candidate(candidate_store=store)

    assert report.decision == "REJECTED"
    assert len(report.rejection_reasons) > 0
    # Grounding or accuracy failed
    assert report.accuracy_score < 0.85 or report.grounding_score < 0.80

def test_quality_gate_rejects_regression_against_baseline():
    qg = QualityGate()

    # Baseline: High quality
    base_text = """
    Return Policy: Customers can return unused items within 30 days with receipt for a full refund.
    Password: Reset password on login page via email link.
    Support: Customer support available 24/7.
    Shipping: International shipping to 65 countries.
    Payment: Major credit cards, PayPal, Apple Pay.
    """
    base_chunks = chunk_document("base.md", base_text, "h_base")
    base_store = VectorStore()
    base_store.build_index(base_chunks)

    # Candidate: Omitted shipping and returns
    cand_text = """
    Password: Reset password on login page via email link.
    Support: Customer support available 24/7.
    """
    cand_chunks = chunk_document("cand.md", cand_text, "h_cand")
    cand_store = VectorStore()
    cand_store.build_index(cand_chunks)

    report = qg.evaluate_candidate(candidate_store=cand_store, baseline_store=base_store)

    assert report.decision == "REJECTED"
    # Should explicitly flag regression
    assert any("regression" in r.lower() or "accuracy" in r.lower() for r in report.rejection_reasons)
