"""
Command-Line Interface (CLI) for Knowledge Base Pipeline Operations.
"""
import argparse
import json
import sys
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.chatbot import ChatbotEngine
from pipeline.monitoring import METRICS

def main():
    parser = argparse.ArgumentParser(description="Production Knowledge Base Pipeline CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Update command
    update_p = subparsers.add_parser("update", help="Run ingestion, quality evaluation, and update")
    update_p.add_argument("--force", action="store_true", help="Force rebuild even if no changes detected")
    update_p.add_argument("--sim-window", choices=["true", "false"], help="Simulate maintenance window override")
    update_p.add_argument("--sim-canary-fail", action="store_true", help="Simulate canary health probe failure")
    update_p.add_argument("--actor", default="cli_operator", help="Actor name for audit logging")

    # Rollback command
    rb_p = subparsers.add_parser("rollback", help="Revert to previous active version")
    rb_p.add_argument("--reason", default="CLI operator triggered rollback", help="Reason for rollback")

    # Status command
    subparsers.add_parser("status", help="Show pipeline, versioning, canary, and maintenance status")

    # Ask command (Query Chatbot)
    ask_p = subparsers.add_parser("ask", help="Query the chatbot with guardrails and PII masking")
    ask_p.add_argument("query", help="User question string")

    # Health check command
    health_p = subparsers.add_parser("healthcheck", help="Run canary health check probes")
    health_p.add_argument("--fail", action="store_true", help="Force failure to test auto-rollback")

    args = parser.parse_args()
    orchestrator = PipelineOrchestrator()

    if args.command == "update":
        sim_win = None
        if args.sim_window == "true":
            sim_win = True
        elif args.sim_window == "false":
            sim_win = False

        res = orchestrator.run_update(
            force_all=args.force,
            simulated_maintenance_window=sim_win,
            simulate_canary_failure=args.sim_canary_fail,
            actor=args.actor
        )
        print(json.dumps(res, indent=2))

    elif args.command == "rollback":
        res = orchestrator.rollback(reason=args.reason)
        print(json.dumps(res, indent=2))

    elif args.command == "status":
        in_win, win_status = orchestrator.maintenance_gate.is_window_active()
        status_payload = {
            "versions": orchestrator.version_mgr.manifest,
            "maintenance_window": {
                "in_window": in_win,
                "details": win_status
            },
            "retry_state": orchestrator.retry_mgr.state,
            "canary_state": orchestrator.canary_monitor.state,
            "metrics": METRICS.get_summary()
        }
        print(json.dumps(status_payload, indent=2))

    elif args.command == "ask":
        engine = ChatbotEngine(orchestrator.version_mgr)
        resp = engine.ask(args.query)
        print(json.dumps(resp.to_dict(), indent=2))

    elif args.command == "healthcheck":
        res = orchestrator.canary_monitor.run_health_probes(
            version_manager=orchestrator.version_mgr,
            simulated_failure=args.fail
        )
        print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
