"""
Flask REST API and Web Dashboard for Knowledge Base Update & Monitoring Pipeline.
"""
import os
import json
import time
from pathlib import Path
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS

from config import (
    DOCUMENTS_DIR,
    QUARANTINE_DIR,
    BASE_DIR,
    API_KEYS,
    ROLE_PERMISSIONS
)
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.chatbot import ChatbotEngine
from pipeline.security import authenticate_key, enforce_permission, log_security_event
from pipeline.monitoring import METRICS

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static")
)
CORS(app)

orchestrator = PipelineOrchestrator()
chatbot = ChatbotEngine(orchestrator.version_mgr)

# Helper for authentication
def check_auth(permission: str):
    api_key = request.headers.get("X-API-Key") or request.headers.get("Authorization")
    sec_ctx = authenticate_key(api_key)
    authorized, msg = enforce_permission(sec_ctx, permission)
    return authorized, msg, sec_ctx

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "UP",
        "timestamp": time.time(),
        "active_version": orchestrator.version_mgr.manifest.get("active_version")
    })

@app.route("/api/chat", methods=["POST"])
def chat():
    # Role: VIEWER or higher
    authorized, msg, sec_ctx = check_auth("query")
    if not authorized:
        log_security_event("UNAUTHORIZED_ACCESS", "anonymous", "DENIED", {"endpoint": "/api/chat", "msg": msg})
        return jsonify({"error": msg}), 401 if "401" in msg else 403

    data = request.get_json(silent=True) or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Query string is required"}), 400

    resp = chatbot.ask(query)
    return jsonify(resp.to_dict())

@app.route("/api/pipeline/update", methods=["POST"])
def trigger_update():
    # Requires OPERATOR or ADMIN
    authorized, msg, sec_ctx = check_auth("ingest")
    if not authorized:
        log_security_event("UNAUTHORIZED_PIPELINE_UPDATE", "anonymous", "DENIED", {"msg": msg})
        return jsonify({"error": msg}), 401 if "401" in msg else 403

    data = request.get_json(silent=True) or {}
    force_all = bool(data.get("force_all", False))
    simulated_window = data.get("simulated_maintenance_window")
    simulate_canary_failure = bool(data.get("simulate_canary_failure", False))

    actor = sec_ctx.user if sec_ctx else "unknown"
    report = orchestrator.run_update(
        force_all=force_all,
        simulated_maintenance_window=simulated_window,
        simulate_canary_failure=simulate_canary_failure,
        actor=actor
    )
    return jsonify(report)

@app.route("/api/pipeline/rollback", methods=["POST"])
def rollback():
    authorized, msg, sec_ctx = check_auth("rollback")
    if not authorized:
        return jsonify({"error": msg}), 401 if "401" in msg else 403

    data = request.get_json(silent=True) or {}
    reason = data.get("reason", f"Manual rollback by {sec_ctx.user}")
    res = orchestrator.rollback(reason=reason)
    return jsonify(res)

@app.route("/api/pipeline/status", methods=["GET"])
def pipeline_status():
    authorized, msg, _ = check_auth("metrics_read")
    if not authorized:
        return jsonify({"error": msg}), 401 if "401" in msg else 403

    in_window, window_status = orchestrator.maintenance_gate.is_window_active()
    return jsonify({
        "versions": orchestrator.version_mgr.manifest,
        "maintenance_window": {
            "is_active": in_window,
            "status": window_status,
            "simulated_override": orchestrator.maintenance_gate._simulated_override
        },
        "retry_state": orchestrator.retry_mgr.state,
        "canary_state": orchestrator.canary_monitor.state
    })

@app.route("/api/metrics", methods=["GET"])
def get_metrics():
    authorized, msg, _ = check_auth("metrics_read")
    if not authorized:
        return jsonify({"error": msg}), 401 if "401" in msg else 403

    return jsonify(METRICS.get_summary())

@app.route("/api/quarantine", methods=["GET"])
def get_quarantine():
    authorized, msg, _ = check_auth("audit_read")
    if not authorized:
        return jsonify({"error": msg}), 401 if "401" in msg else 403

    manifest_file = QUARANTINE_DIR / "quarantine_manifest.json"
    items = []
    if manifest_file.exists():
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            items = []
    return jsonify({"quarantined_files": items})

@app.route("/api/audit", methods=["GET"])
def get_audit():
    authorized, msg, _ = check_auth("audit_read")
    if not authorized:
        return jsonify({"error": msg}), 401 if "401" in msg else 403

    audit_file = BASE_DIR / "data" / "logs" / "security_audit.jsonl"
    events = []
    if audit_file.exists():
        try:
            with open(audit_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        events.append(json.loads(line.strip()))
        except Exception:
            events = []
    return jsonify({"events": events[-50:]}) # Last 50 events

@app.route("/api/simulate/outage", methods=["POST"])
def toggle_outage():
    authorized, msg, _ = check_auth("simulate")
    if not authorized:
        return jsonify({"error": msg}), 401 if "401" in msg else 403

    data = request.get_json(silent=True) or {}
    active = bool(data.get("active", False))
    METRICS.circuit_breaker.set_simulated_outage(active)
    return jsonify({
        "simulated_outage": active,
        "circuit_breaker_state": METRICS.circuit_breaker.state
    })

@app.route("/api/simulate/maintenance-window", methods=["POST"])
def toggle_window():
    authorized, msg, _ = check_auth("simulate")
    if not authorized:
        return jsonify({"error": msg}), 401 if "401" in msg else 403

    data = request.get_json(silent=True) or {}
    override = data.get("override") # True, False, or None
    orchestrator.maintenance_gate.set_simulated_override(override)
    in_win, status = orchestrator.maintenance_gate.is_window_active()
    return jsonify({
        "override": override,
        "is_window_active": in_win,
        "status": status
    })

@app.route("/api/simulate/canary-probe", methods=["POST"])
def trigger_canary_probe():
    authorized, msg, _ = check_auth("simulate")
    if not authorized:
        return jsonify({"error": msg}), 401 if "401" in msg else 403

    data = request.get_json(silent=True) or {}
    fail = bool(data.get("fail", False))
    res = orchestrator.canary_monitor.run_health_probes(
        version_manager=orchestrator.version_mgr,
        simulated_failure=fail
    )
    return jsonify(res)

@app.route("/api/upload", methods=["POST"])
def upload_document():
    authorized, msg, sec_ctx = check_auth("ingest")
    if not authorized:
        return jsonify({"error": msg}), 401 if "401" in msg else 403

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    uploaded = request.files["file"]
    if uploaded.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    target_path = DOCUMENTS_DIR / uploaded.filename
    uploaded.save(target_path)

    log_security_event(
        event_type="FILE_UPLOADED",
        actor=sec_ctx.user if sec_ctx else "unknown",
        status="SUCCESS",
        details={"filename": uploaded.filename, "size": target_path.stat().st_size}
    )
    return jsonify({
        "message": f"File '{uploaded.filename}' saved to documents staging.",
        "filename": uploaded.filename,
        "size_bytes": target_path.stat().st_size
    })

if __name__ == "__main__":
    # Ensure initial pipeline version is ready if empty
    if not orchestrator.version_mgr.manifest.get("active_version"):
        print("[Startup] Initializing base knowledge base version v1.0.0...")
        init_res = orchestrator.run_update(force_all=True, simulated_maintenance_window=True, actor="system_init")
        print(f"[Startup] Initial KB status: {init_res.get('status')}")

    print("[Server] Starting Knowledge Base Pipeline Server on port 5000...")
    app.run(host="0.0.0.0", port=5000, debug=False)
