"""
Configuration for Knowledge Base Update & Monitoring Pipeline.
"""
import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DOCUMENTS_DIR = DATA_DIR / "documents"
QUARANTINE_DIR = DATA_DIR / "quarantine"
VERSIONS_DIR = DATA_DIR / "versions"
STATE_DIR = DATA_DIR / "state"
LOGS_DIR = DATA_DIR / "logs"

# Ensure runtime directories exist
for directory in [DATA_DIR, DOCUMENTS_DIR, QUARANTINE_DIR, VERSIONS_DIR, STATE_DIR, LOGS_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# Quality Gate Thresholds
MIN_GROUNDING_SCORE = 0.80      # Updates rejected if grounding score is below 80%
MIN_ACCURACY_SCORE = 0.85       # Updates rejected if accuracy is below 85%
MAX_ALLOWED_ACCURACY_DROP = 0.02 # Reject if candidate drops accuracy by > 2% from baseline

# Duplicate Detection Thresholds
EXACT_HASH_DEDUP = True
NEAR_DUPLICATE_SIMILARITY_THRESHOLD = 0.80 # Jaccard/Cosine similarity threshold for near-duplicates

# Maintenance Window Configuration
# E.g., Window 02:00 to 04:00 UTC, or simulated override
MAINTENANCE_WINDOW_START_HOUR = 2   # 02:00
MAINTENANCE_WINDOW_END_HOUR = 4     # 04:00
ALLOW_SIMULATED_WINDOW_OVERRIDE = True

# Retry Policy for Failed Updates
RETRY_INTERVALS_MINUTES = [15, 30, 60] # Retry after 15m, 30m, and 60m
MAX_RETRY_ATTEMPTS = 3

# Post-Activation Health Check Canary
CANARY_WINDOW_SECONDS = 300 # 5 minutes
CANARY_PROBE_INTERVAL_SECONDS = 10
CANARY_MAX_ERROR_RATE = 0.05 # 5% error rate triggers auto-rollback
CANARY_MAX_LATENCY_P95_MS = 2500 # 2.5s p95 latency ceiling

# Security & RBAC
API_KEYS = {
    "admin-key-9988": {"role": "ADMIN", "user": "Admin Alice"},
    "operator-key-4455": {"role": "OPERATOR", "user": "Operator Bob"},
    "auditor-key-1122": {"role": "AUDITOR", "user": "Auditor Charlie"},
    "viewer-key-0011": {"role": "VIEWER", "user": "Viewer Dave"}
}

ROLE_PERMISSIONS = {
    "ADMIN": ["ingest", "activate", "rollback", "quarantine_manage", "simulate", "query", "audit_read", "metrics_read"],
    "OPERATOR": ["ingest", "activate", "rollback", "query", "audit_read", "metrics_read"],
    "AUDITOR": ["audit_read", "metrics_read", "query"],
    "VIEWER": ["query", "metrics_read"]
}

# Monitoring & Escalation Thresholds
CONFIDENCE_ESCALATION_THRESHOLD = 0.65 # Confidence below 65% triggers human escalation
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3  # 3 consecutive upstream failures trip circuit breaker
CIRCUIT_BREAKER_RESET_TIMEOUT = 30     # 30 seconds to attempt half-open recovery
