"""
Unit Tests for Version Management, Lineage, and Instant Rollback.
"""
from pipeline.versioning import VersionManager
from pipeline.vector_store import VectorStore
from pipeline.chunking import chunk_document

def test_versioning_lifecycle_and_instant_rollback(temp_workspace):
    versions_dir = temp_workspace["versions"]
    vm = VersionManager(versions_dir=versions_dir)

    # 1. First Version (v1.0.0)
    chunks_v1 = chunk_document("doc1.txt", "Initial base content", "h1")
    store_v1 = VectorStore()
    store_v1.build_index(chunks_v1)

    cand_id_1 = vm.stage_candidate_version(chunks_v1, store_v1)
    assert cand_id_1 == "v1.0.0"

    active_1 = vm.promote_candidate(cand_id_1, quality_metrics={"accuracy": 1.0})
    assert active_1 == "v1.0.0"
    assert vm.manifest["active_version"] == "v1.0.0"
    assert vm.manifest["previous_version"] is None

    # 2. Second Version (v1.1.0)
    chunks_v2 = chunk_document("doc2.txt", "Updated content with additions", "h2")
    store_v2 = VectorStore()
    store_v2.build_index(chunks_v2)

    cand_id_2 = vm.stage_candidate_version(chunks_v2, store_v2)
    assert cand_id_2 == "v1.1.0"

    active_2 = vm.promote_candidate(cand_id_2, quality_metrics={"accuracy": 1.0})
    assert active_2 == "v1.1.0"
    assert vm.manifest["active_version"] == "v1.1.0"
    assert vm.manifest["previous_version"] == "v1.0.0"

    # 3. Test Instant Rollback
    success, msg, new_active = vm.rollback(reason="Test rollback invocation")
    assert success is True
    assert new_active == "v1.0.0"
    assert vm.manifest["active_version"] == "v1.0.0"
    assert vm.manifest["previous_version"] == "v1.1.0"

    # Verify loaded active store matches v1.0.0
    loaded_store = vm.load_active_store()
    assert loaded_store is not None
    assert len(loaded_store.chunks) == 1
    assert loaded_store.chunks[0].doc_name == "doc1.txt"
