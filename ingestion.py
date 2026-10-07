"""
Incremental Document Ingestion, Duplicate Detection, and Quarantine System.
"""
import os
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Dict, Any, List, Tuple, Set, Optional

from config import (
    DOCUMENTS_DIR,
    QUARANTINE_DIR,
    STATE_DIR,
    NEAR_DUPLICATE_SIMILARITY_THRESHOLD
)
from pipeline.security import log_security_event

STATE_FILE = STATE_DIR / "ingestion_state.json"
QUARANTINE_MANIFEST = QUARANTINE_DIR / "quarantine_manifest.json"

class IngestionResult:
    def __init__(self):
        self.new_files: List[str] = []
        self.modified_files: List[str] = []
        self.unchanged_files: List[str] = []
        self.duplicate_files: List[Dict[str, Any]] = []
        self.quarantined_files: List[Dict[str, Any]] = []
        self.processed_docs: Dict[str, str] = {} # doc_name -> text_content
        self.doc_hashes: Dict[str, str] = {}     # doc_name -> sha256

    def to_dict(self) -> Dict[str, Any]:
        return {
            "new_count": len(self.new_files),
            "modified_count": len(self.modified_files),
            "unchanged_count": len(self.unchanged_files),
            "duplicate_count": len(self.duplicate_files),
            "quarantined_count": len(self.quarantined_files),
            "new_files": self.new_files,
            "modified_files": self.modified_files,
            "unchanged_files": self.unchanged_files,
            "duplicates": self.duplicate_files,
            "quarantined": self.quarantined_files,
            "total_active_docs": len(self.processed_docs)
        }

def compute_sha256(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def compute_text_shingles(text: str) -> Set[str]:
    import re
    words = [w.lower() for w in re.findall(r"\b\w+\b", text) if len(w) > 1]
    if not words:
        return set()
    unigrams = set(words)
    bigrams = set(f"{words[i]}_{words[i+1]}" for i in range(len(words) - 1))
    return unigrams.union(bigrams)

def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a.intersection(set_b))
    union = len(set_a.union(set_b))
    return intersection / union if union > 0 else 0.0

class IngestionManager:
    def __init__(self, docs_dir: Path = DOCUMENTS_DIR, quarantine_dir: Path = QUARANTINE_DIR):
        self.docs_dir = docs_dir
        self.quarantine_dir = quarantine_dir
        self.state_file = STATE_FILE
        self.quarantine_manifest_file = QUARANTINE_MANIFEST
        self._load_state()

    def _load_state(self):
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    self.state = json.load(f)
            except Exception:
                self.state = {"files": {}, "last_run": 0}
        else:
            self.state = {"files": {}, "last_run": 0}

    def _save_state(self):
        self.state["last_run"] = time.time()
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def _quarantine_file(self, file_path: Path, reason: str, details: str) -> Dict[str, Any]:
        """Moves corrupted, empty or invalid file to quarantine folder with audit record."""
        target_name = f"{int(time.time())}_{file_path.name}"
        quarantined_path = self.quarantine_dir / target_name
        
        try:
            shutil.copy2(file_path, quarantined_path)
            # Remove original from incoming docs directory to prevent infinite retry on corrupted input
            if file_path.exists():
                file_path.unlink()
        except Exception as e:
            details += f" (Copy failed: {e})"

        record = {
            "timestamp": time.time(),
            "original_filename": file_path.name,
            "quarantined_file": target_name,
            "reason": reason,
            "details": details
        }

        # Update quarantine manifest
        manifest = []
        if self.quarantine_manifest_file.exists():
            try:
                with open(self.quarantine_manifest_file, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception:
                manifest = []
        manifest.append(record)
        with open(self.quarantine_manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        log_security_event(
            event_type="FILE_QUARANTINED",
            actor="pipeline_ingestor",
            status="WARNING",
            details=record
        )
        return record

    def parse_file_content(self, file_path: Path) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Parses document contents based on extension.
        Returns: (success, text_content, error_message)
        """
        if not file_path.exists():
            return False, None, "File does not exist."

        # Check empty file
        if file_path.stat().st_size == 0:
            return False, None, "File is empty (0 bytes)."

        suffix = file_path.suffix.lower()

        # Text and Markdown
        if suffix in [".txt", ".md"]:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if not content.strip():
                    return False, None, "File contains only whitespace."
                return True, content, None
            except UnicodeDecodeError:
                # Try fallback latin-1 or report invalid encoding
                try:
                    with open(file_path, "r", encoding="latin-1") as f:
                        content = f.read()
                    return True, content, None
                except Exception as e:
                    return False, None, f"Encoding error: {e}"

        # JSON Document
        elif suffix == ".json":
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # Convert json data into searchable text
                if isinstance(data, dict):
                    lines = []
                    for k, v in data.items():
                        lines.append(f"{k}: {json.dumps(v) if isinstance(v, (dict, list)) else str(v)}")
                    text = "\n".join(lines)
                elif isinstance(data, list):
                    text = "\n".join(json.dumps(item) for item in data)
                else:
                    text = str(data)

                if not text.strip():
                    return False, None, "JSON parsed to empty string."
                return True, text, None
            except json.JSONDecodeError as jde:
                return False, None, f"Malformed JSON: {jde}"
            except Exception as e:
                return False, None, f"JSON parse error: {e}"

        # CSV Document
        elif suffix == ".csv":
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f.readlines() if line.strip()]
                if not lines:
                    return False, None, "Empty CSV file."
                return True, "\n".join(lines), None
            except Exception as e:
                return False, None, f"CSV parse error: {e}"

        # PDF Document
        elif suffix == ".pdf":
            try:
                import PyPDF2
                reader = PyPDF2.PdfReader(str(file_path))
                if len(reader.pages) == 0:
                    return False, None, "PDF has 0 pages."
                extracted = []
                for p in reader.pages:
                    extracted.append(p.extract_text() or "")
                text = "\n".join(extracted).strip()
                if not text:
                    return False, None, "PDF parsed but contained no readable text."
                return True, text, None
            except Exception as e:
                return False, None, f"Corrupted or invalid PDF: {e}"

        else:
            return False, None, f"Unsupported file format: {suffix}"

    def scan_and_ingest(self, force_all: bool = False) -> IngestionResult:
        """
        Executes incremental scan, duplicate check, and quarantine.
        Only processes new or modified files.
        """
        result = IngestionResult()
        existing_hashes = {info["sha256"]: name for name, info in self.state.get("files", {}).items()}
        existing_doc_shingles: Dict[str, Set[str]] = {}

        # First pass: parse valid active files already in state or load text for duplicate detection
        current_files = list(self.docs_dir.glob("*.*"))

        # Temporary store for candidate parsed files in this run
        parsed_batch: Dict[str, Tuple[str, str, Path]] = {} # name -> (content, sha256, path)

        for file_path in current_files:
            if file_path.is_dir():
                continue

            file_name = file_path.name
            
            # 1. Parse and validate file (Quarantine if invalid)
            success, content, err = self.parse_file_content(file_path)
            if not success:
                quarantine_record = self._quarantine_file(
                    file_path=file_path,
                    reason="PARSING_FAILURE",
                    details=err or "Invalid file content"
                )
                result.quarantined_files.append(quarantine_record)
                continue

            # 2. Check SHA-256 for modification / new
            file_hash = compute_sha256(file_path)
            prev_info = self.state.get("files", {}).get(file_name)

            if not force_all and prev_info and prev_info.get("sha256") == file_hash:
                result.unchanged_files.append(file_name)
                result.processed_docs[file_name] = content
                result.doc_hashes[file_name] = file_hash
                existing_doc_shingles[file_name] = compute_text_shingles(content)
                continue

            parsed_batch[file_name] = (content, file_hash, file_path)

        # 3. Duplicate Detection on parsed new/modified files
        for file_name, (content, file_hash, file_path) in parsed_batch.items():
            # Exact Hash Duplicate Check
            if file_hash in existing_hashes and existing_hashes[file_hash] != file_name:
                orig_file = existing_hashes[file_hash]
                dup_record = {
                    "filename": file_name,
                    "duplicate_of": orig_file,
                    "type": "EXACT_HASH_MATCH",
                    "hash": file_hash
                }
                result.duplicate_files.append(dup_record)
                log_security_event("DUPLICATE_FILE_DETECTED", "ingestor", "INFO", dup_record)
                continue

            # Near-Duplicate / Semantic Duplicate Check
            candidate_shingles = compute_text_shingles(content)
            is_near_dup = False
            for active_name, active_shingles in existing_doc_shingles.items():
                sim = jaccard_similarity(candidate_shingles, active_shingles)
                if sim >= NEAR_DUPLICATE_SIMILARITY_THRESHOLD:
                    dup_record = {
                        "filename": file_name,
                        "duplicate_of": active_name,
                        "type": "NEAR_DUPLICATE_SEMANTIC",
                        "similarity": round(sim, 3)
                    }
                    result.duplicate_files.append(dup_record)
                    log_security_event("NEAR_DUPLICATE_DETECTED", "ingestor", "INFO", dup_record)
                    is_near_dup = True
                    break

            if is_near_dup:
                continue

            # It's a valid new or modified document
            prev_info = self.state.get("files", {}).get(file_name)
            if prev_info:
                result.modified_files.append(file_name)
            else:
                result.new_files.append(file_name)

            result.processed_docs[file_name] = content
            result.doc_hashes[file_name] = file_hash
            existing_hashes[file_hash] = file_name
            existing_doc_shingles[file_name] = candidate_shingles

            # Record in state
            self.state["files"][file_name] = {
                "sha256": file_hash,
                "size_bytes": file_path.stat().st_size,
                "last_modified": file_path.stat().st_mtime
            }

        self._save_state()
        return result
