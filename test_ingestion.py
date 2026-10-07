"""
Unit and Integration Tests for Ingestion, Deduplication, and Quarantine.
"""
from pathlib import Path
import json
import time

from pipeline.ingestion import IngestionManager, compute_sha256

def test_incremental_ingestion_skips_unchanged_files(temp_workspace):
    docs_dir = temp_workspace["docs"]
    quarantine_dir = temp_workspace["quarantine"]

    # Create initial file
    f1 = docs_dir / "faq.txt"
    f1.write_text("This is the original customer FAQ content.", encoding="utf-8")

    mgr = IngestionManager(docs_dir=docs_dir, quarantine_dir=quarantine_dir)
    mgr.state_file = temp_workspace["state"] / "ingest_state.json"
    mgr.quarantine_manifest_file = quarantine_dir / "quarantine_manifest.json"

    # Run 1: Should be processed as new
    res1 = mgr.scan_and_ingest()
    assert "faq.txt" in res1.new_files
    assert len(res1.unchanged_files) == 0

    # Run 2 without changes: Should be skipped as unchanged
    res2 = mgr.scan_and_ingest()
    assert len(res2.new_files) == 0
    assert "faq.txt" in res2.unchanged_files

    # Modify file
    f1.write_text("This is the UPDATED customer FAQ content with new policies.", encoding="utf-8")
    res3 = mgr.scan_and_ingest()
    assert "faq.txt" in res3.modified_files
    assert len(res3.unchanged_files) == 0

def test_exact_hash_duplicate_detection(temp_workspace):
    docs_dir = temp_workspace["docs"]
    quarantine_dir = temp_workspace["quarantine"]

    content = "Detailed warranty specifications and coverage terms for all electronic units."
    (docs_dir / "warranty_v1.txt").write_text(content, encoding="utf-8")
    (docs_dir / "warranty_copy.txt").write_text(content, encoding="utf-8")

    mgr = IngestionManager(docs_dir=docs_dir, quarantine_dir=quarantine_dir)
    mgr.state_file = temp_workspace["state"] / "ingest_state.json"
    mgr.quarantine_manifest_file = quarantine_dir / "quarantine_manifest.json"

    res = mgr.scan_and_ingest(force_all=True)
    # One should be ingested, the second must be flagged as exact duplicate
    assert len(res.duplicate_files) >= 1
    dup = res.duplicate_files[0]
    assert dup["type"] == "EXACT_HASH_MATCH"
    assert dup["duplicate_of"] in ["warranty_v1.txt", "warranty_copy.txt"]

def test_semantic_near_duplicate_detection(temp_workspace):
    docs_dir = temp_workspace["docs"]
    quarantine_dir = temp_workspace["quarantine"]

    doc1_content = "Our standard company return policy allows customers to return items within thirty days of delivery for a full refund."
    # Near identical copy with minor trivial wording alteration
    doc2_content = "Our standard company return policy allows customers to return products within thirty days of delivery for a full refund."

    (docs_dir / "return_original.txt").write_text(doc1_content, encoding="utf-8")

    mgr = IngestionManager(docs_dir=docs_dir, quarantine_dir=quarantine_dir)
    mgr.state_file = temp_workspace["state"] / "ingest_state.json"
    mgr.quarantine_manifest_file = quarantine_dir / "quarantine_manifest.json"

    mgr.scan_and_ingest()

    # Now add near-duplicate
    (docs_dir / "return_rephrase.txt").write_text(doc2_content, encoding="utf-8")
    res = mgr.scan_and_ingest()

    assert any(d["type"] == "NEAR_DUPLICATE_SEMANTIC" for d in res.duplicate_files)

def test_quarantine_corrupted_and_empty_files(temp_workspace):
    docs_dir = temp_workspace["docs"]
    quarantine_dir = temp_workspace["quarantine"]

    # 1. Empty file (0 bytes)
    empty_file = docs_dir / "empty.txt"
    empty_file.write_text("", encoding="utf-8")

    # 2. Corrupted JSON file (syntax error)
    bad_json = docs_dir / "broken.json"
    bad_json.write_text("{ unclosed_json: true, missing: ", encoding="utf-8")

    mgr = IngestionManager(docs_dir=docs_dir, quarantine_dir=quarantine_dir)
    mgr.state_file = temp_workspace["state"] / "ingest_state.json"
    mgr.quarantine_manifest_file = quarantine_dir / "quarantine_manifest.json"

    res = mgr.scan_and_ingest()

    # Both must be quarantined
    assert len(res.quarantined_files) == 2
    quarantined_names = [q["original_filename"] for q in res.quarantined_files]
    assert "empty.txt" in quarantined_names
    assert "broken.json" in quarantined_names

    # Check that original invalid files were safely removed from incoming docs dir
    assert not empty_file.exists()
    assert not bad_json.exists()

    # Check quarantine manifest was created
    assert mgr.quarantine_manifest_file.exists()
    with open(mgr.quarantine_manifest_file, "r") as f:
        manifest = json.load(f)
    assert len(manifest) == 2
