"""
Master Pipeline Orchestrator:
Coordinates Ingestion, Deduplication, Quarantine, Chunking, Staging,
Quality Gate, Maintenance Window Enforcement, Activation, Canary Monitoring, and Rollback.
"""
from typing import Dict, Any, Optional

from pipeline.ingestion import IngestionManager
from pipeline.chunking import chunk_document
from pipeline.vector_store import VectorStore
from pipeline.versioning import VersionManager
from pipeline.quality_gate import QualityGate
from pipeline.scheduler import MaintenanceGatekeeper, RetryManager
from pipeline.canary_monitor import CanaryHealthMonitor
from pipeline.security import log_security_event

class PipelineOrchestrator:
    def __init__(self):
        self.ingestion_mgr = IngestionManager()
        self.version_mgr = VersionManager()
        self.quality_gate = QualityGate()
        self.maintenance_gate = MaintenanceGatekeeper()
        self.retry_mgr = RetryManager()
        self.canary_monitor = CanaryHealthMonitor()

    def run_update(
        self,
        force_all: bool = False,
        simulated_maintenance_window: Optional[bool] = None,
        simulate_canary_failure: bool = False,
        actor: str = "system_scheduler"
    ) -> Dict[str, Any]:
        """
        Executes complete production update pipeline flow.
        """
        report: Dict[str, Any] = {
            "stage": "STARTED",
            "status": "IN_PROGRESS",
            "actor": actor
        }

        # 1. Incremental Ingestion, Deduplication & Quarantine
        ingest_res = self.ingestion_mgr.scan_and_ingest(force_all=force_all)
        report["ingestion"] = ingest_res.to_dict()

        # If no documents are active
        if not ingest_res.processed_docs:
            report["status"] = "ABORTED"
            report["message"] = "No valid documents found in documents repository."
            return report

        # If no new or modified documents and not force_all and active version already exists
        active_ver = self.version_mgr.manifest.get("active_version")
        if not force_all and active_ver and not ingest_res.new_files and not ingest_res.modified_files:
            report["status"] = "SKIPPED_NO_CHANGES"
            report["message"] = f"No new or modified documents detected. Active version '{active_ver}' is up to date."
            report["active_version"] = active_ver
            return report

        # 2. Chunking & PII Redaction
        all_chunks = []
        for doc_name, content in ingest_res.processed_docs.items():
            doc_hash = ingest_res.doc_hashes.get(doc_name, "")
            chunks = chunk_document(doc_name=doc_name, raw_content=content, doc_hash=doc_hash)
            all_chunks.extend(chunks)

        report["chunk_count"] = len(all_chunks)

        # 3. Vector Index Creation
        candidate_store = VectorStore()
        candidate_store.build_index(all_chunks)

        # 4. Stage Candidate Version
        candidate_id = self.version_mgr.stage_candidate_version(
            chunks=all_chunks,
            vector_store=candidate_store,
            metadata={"new_files": ingest_res.new_files, "modified_files": ingest_res.modified_files}
        )
        report["staged_candidate_id"] = candidate_id

        # 5. Quality Gate Pre-Activation Test
        baseline_store = self.version_mgr.load_active_store()
        quality_eval = self.quality_gate.evaluate_candidate(
            candidate_store=candidate_store,
            baseline_store=baseline_store
        )
        report["quality_gate"] = quality_eval.to_dict()

        if quality_eval.decision != "APPROVED":
            report["status"] = "REJECTED_QUALITY_GATE"
            report["message"] = f"Update candidate {candidate_id} rejected due to quality check failures."
            
            # Record failure in Retry Manager (15m, 30m, 60m backoff)
            failure_reason = "; ".join(quality_eval.rejection_reasons)
            retry_state = self.retry_mgr.record_failure(
                reason=failure_reason,
                candidate_id=candidate_id
            )
            report["retry_schedule"] = retry_state
            
            log_security_event(
                event_type="PIPELINE_UPDATE_REJECTED",
                actor=actor,
                status="FAILED",
                details={"candidate_id": candidate_id, "reasons": quality_eval.rejection_reasons}
            )
            return report

        # 6. Maintenance Window Enforcement
        in_window, window_status = self.maintenance_gate.is_window_active()
        if simulated_maintenance_window is not None:
            in_window = simulated_maintenance_window
            window_status = f"Simulated maintenance window: {in_window}"

        report["maintenance_window"] = {
            "in_window": in_window,
            "status_message": window_status
        }

        if not in_window:
            report["status"] = "APPROVED_PENDING_MAINTENANCE_WINDOW"
            report["message"] = f"Quality gate passed, but current time is outside maintenance window. Update queued for next maintenance window."
            return report

        # 7. Activation: Promote Candidate to Active
        active_version = self.version_mgr.promote_candidate(
            version_id=candidate_id,
            quality_metrics=quality_eval.to_dict()
        )
        self.retry_mgr.reset()
        report["status"] = "ACTIVATED"
        report["active_version"] = active_version

        # 8. Post-Activation 5-Minute Canary Health Check
        self.canary_monitor.start_canary(activated_version=active_version)
        canary_res = self.canary_monitor.run_health_probes(
            version_manager=self.version_mgr,
            simulated_failure=simulate_canary_failure
        )
        report["canary_health_check"] = canary_res

        if canary_res.get("action") == "AUTO_ROLLED_BACK":
            report["status"] = "AUTO_ROLLED_BACK"
            report["active_version"] = canary_res.get("current_active_version")
            report["message"] = "Health checks failed within 5 minutes of activation. Automated rollback executed successfully."

        return report

    def rollback(self, reason: str = "Manual rollback triggered") -> Dict[str, Any]:
        """Triggers rollback to previous version."""
        success, msg, active_version = self.version_mgr.rollback(reason=reason)
        return {
            "success": success,
            "message": msg,
            "current_active_version": active_version
        }
