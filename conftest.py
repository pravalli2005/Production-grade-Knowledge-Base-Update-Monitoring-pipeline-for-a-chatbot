"""
Pytest configuration and shared fixtures for Knowledge Base Pipeline tests.
"""
import sys
import shutil
import tempfile
from pathlib import Path
import pytest

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import DATA_DIR
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.versioning import VersionManager
from pipeline.vector_store import VectorStore

@pytest.fixture
def temp_workspace():
    """Creates a temporary workspace with clean directories for tests."""
    temp_dir = Path(tempfile.mkdtemp())
    docs_dir = temp_dir / "documents"
    quarantine_dir = temp_dir / "quarantine"
    versions_dir = temp_dir / "versions"
    state_dir = temp_dir / "state"

    for d in [docs_dir, quarantine_dir, versions_dir, state_dir]:
        d.mkdir(parents=True, exist_ok=True)

    yield {
        "root": temp_dir,
        "docs": docs_dir,
        "quarantine": quarantine_dir,
        "versions": versions_dir,
        "state": state_dir
    }

    shutil.rmtree(temp_dir, ignore_errors=True)

@pytest.fixture
def initialized_orchestrator():
    """Returns an orchestrator with baseline v1.0.0 installed."""
    orch = PipelineOrchestrator()
    orch.run_update(force_all=True, simulated_maintenance_window=True, actor="test_fixture")
    return orch
