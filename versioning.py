"""
Version Management and Instant Rollback Engine for Knowledge Base.
"""
import time
import json
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from config import VERSIONS_DIR
from pipeline.vector_store import VectorStore
from pipeline.chunking import DocumentChunk
from pipeline.security import log_security_event

MANIFEST_FILE = VERSIONS_DIR / "versions_manifest.json"

class VersionManager:
    def __init__(self, versions_dir: Path = VERSIONS_DIR):
        self.versions_dir = versions_dir
        self.manifest_file = self.versions_dir / "versions_manifest.json"
        self._load_manifest()

    def _load_manifest(self):
        if self.manifest_file.exists():
            try:
                with open(self.manifest_file, "r", encoding="utf-8") as f:
                    self.manifest = json.load(f)
            except Exception:
                self.manifest = self._default_manifest()
        else:
            self.manifest = self._default_manifest()

    def _default_manifest(self) -> Dict[str, Any]:
        return {
            "active_version": None,
            "previous_version": None,
            "candidate_version": None,
            "history": []
        }

    def _save_manifest(self):
        with open(self.manifest_file, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, indent=2)

    def get_next_version_str(self) -> str:
        """Computes incremental semantic version v1.0.0 -> v1.1.0 etc."""
        active = self.manifest.get("active_version")
        if not active:
            return "v1.0.0"
        try:
            parts = active.lstrip("v").split(".")
            major = int(parts[0])
            minor = int(parts[1]) + 1
            patch = 0
            return f"v{major}.{minor}.{patch}"
        except Exception:
            return f"v1.{len(self.manifest.get('history', [])) + 1}.0"

    def stage_candidate_version(
        self,
        chunks: List[DocumentChunk],
        vector_store: VectorStore,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Creates candidate version directory and persists vector index before quality testing.
        """
        version_id = self.get_next_version_str()
        candidate_dir = self.versions_dir / f"{version_id}_candidate"
        if candidate_dir.exists():
            shutil.rmtree(candidate_dir)
        candidate_dir.mkdir(parents=True, exist_ok=True)

        vector_store.save(candidate_dir)

        # Write version info
        meta_payload = {
            "version": version_id,
            "created_at": time.time(),
            "created_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "CANDIDATE",
            "chunk_count": len(chunks),
            "doc_count": len(set(c.doc_name for c in chunks)),
            "custom_metadata": metadata or {}
        }
        with open(candidate_dir / "version_meta.json", "w", encoding="utf-8") as f:
            json.dump(meta_payload, f, indent=2)

        self.manifest["candidate_version"] = version_id
        self._save_manifest()
        return version_id

    def promote_candidate(self, version_id: str, quality_metrics: Dict[str, Any]) -> str:
        """
        Promotes candidate version to active version after quality check and maintenance window gates pass.
        """
        candidate_dir = self.versions_dir / f"{version_id}_candidate"
        active_target_dir = self.versions_dir / version_id

        if not candidate_dir.exists():
            raise FileNotFoundError(f"Candidate version directory for {version_id} not found.")

        if active_target_dir.exists():
            shutil.rmtree(active_target_dir)

        # Atomic rename/move
        candidate_dir.rename(active_target_dir)

        # Update metadata
        meta_file = active_target_dir / "version_meta.json"
        meta = {}
        if meta_file.exists():
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
        meta["status"] = "ACTIVE"
        meta["activated_at"] = time.time()
        meta["quality_metrics"] = quality_metrics
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        # Update manifest
        prev = self.manifest.get("active_version")
        self.manifest["previous_version"] = prev
        self.manifest["active_version"] = version_id
        self.manifest["candidate_version"] = None

        self.manifest["history"].append({
            "version": version_id,
            "activated_at": time.time(),
            "chunk_count": meta.get("chunk_count", 0),
            "quality_metrics": quality_metrics,
            "status": "ACTIVE"
        })
        self._save_manifest()

        log_security_event(
            event_type="VERSION_ACTIVATED",
            actor="pipeline_activator",
            status="SUCCESS",
            details={"version": version_id, "previous_version": prev, "metrics": quality_metrics}
        )
        return version_id

    def rollback(self, reason: str = "Manual rollback triggered") -> Tuple[bool, str, Optional[str]]:
        """
        Rolls back instantly to previous active version.
        Returns: (success, message, new_active_version)
        """
        prev = self.manifest.get("previous_version")
        curr = self.manifest.get("active_version")

        if not prev:
            return False, "No previous version available for rollback.", curr

        prev_dir = self.versions_dir / prev
        if not prev_dir.exists():
            return False, f"Target previous version directory '{prev}' does not exist on disk.", curr

        # Swap pointers
        self.manifest["active_version"] = prev
        self.manifest["previous_version"] = curr

        self.manifest["history"].append({
            "version": prev,
            "activated_at": time.time(),
            "status": f"ROLLED_BACK_FROM_{curr}",
            "reason": reason
        })
        self._save_manifest()

        log_security_event(
            event_type="VERSION_ROLLBACK",
            actor="rollback_manager",
            status="WARNING",
            details={"rolled_back_from": curr, "rolled_back_to": prev, "reason": reason}
        )
        return True, f"Successfully rolled back from {curr} to {prev}. Reason: {reason}", prev

    def load_active_store(self) -> Optional[VectorStore]:
        """Loads VectorStore of the active version."""
        active = self.manifest.get("active_version")
        if not active:
            return None
        active_dir = self.versions_dir / active
        if not active_dir.exists():
            return None
        return VectorStore.load(active_dir)

    def load_candidate_store(self, version_id: str) -> Optional[VectorStore]:
        """Loads VectorStore of the candidate version."""
        candidate_dir = self.versions_dir / f"{version_id}_candidate"
        if not candidate_dir.exists():
            return None
        return VectorStore.load(candidate_dir)
