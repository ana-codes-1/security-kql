import argparse
import json
import time
from datetime import UTC, datetime

from security_kql.azure_clients import (
    AdxExecutor,
    AzureClients,
    register_external_agent,
)
from security_kql.config import Settings
from security_kql.evaluation import reference_info
from security_kql.fixtures import expected_count, load_fixtures
from security_kql.queries import QueryKind
from security_kql.seed import seed
from security_kql.service import DemoService
from security_kql.state import StateStore
from security_kql.telemetry import Telemetry


def main():
    parser = argparse.ArgumentParser(description="Security KQL demo operations")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="Replace only the two demo tables with versioned fixtures.")
    commands.add_parser("register", help="Register the external agent and exact-result evaluator.")
    commands.add_parser("verify", help="Query real ADX and verify the fixture counts.")
    commands.add_parser("reference", help="Print the reference definition and query for review.")
    approve = commands.add_parser(
        "approve-reference", help="Record a human's explicit demo approval."
    )
    approve.add_argument("--reviewed", action="store_true", required=True)
    chat = commands.add_parser("chat", help="Run the real Azure-backed question-to-query path.")
    chat.add_argument("question")
    compare = commands.add_parser("evaluate", help="Compare candidate and known-bad baseline.")
    compare.add_argument("--vulnerability", default="DEMO-2026-0001")
    compare.add_argument("--publish", action="store_true")
    compare.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    store = StateStore(settings.state_dir)
    if args.command == "reference":
        print(json.dumps(reference_info(store), indent=2))
        return
    if args.command == "approve-reference":
        from security_kql.queries import reference_fingerprint

        store.set(
            "reference_approval",
            {
                "fingerprint": reference_fingerprint(),
                "approved_at": datetime.now(UTC).isoformat(),
                "reviewer": "explicit interactive approval via CLI",
                "scope": "Synthetic demo only; not Forescout domain approval.",
            },
        )
        print("Recorded explicit approval of the current demo reference and fixture version.")
        return
    settings.validate()
    clients = AzureClients(settings)
    telemetry = Telemetry(settings, store)
    service = DemoService(settings, clients, telemetry, store)
    try:
        if args.command == "seed":
            seed(clients)
            print("Replaced the two demo tables with synthetic fixture data.")
        elif args.command == "register":
            connected = clients.project.telemetry.get_application_insights_connection_string()
            if connected != settings.insights_connection:
                raise ValueError("The Foundry project is connected to different telemetry.")
            registration = register_external_agent(clients)
            store.set("agent_registration", registration)
            service.publisher.ensure_evaluator()
            print(json.dumps(registration, indent=2))
        elif args.command == "verify":
            executor = AdxExecutor(clients, telemetry.tracer)
            for row in load_fixtures()["vulnerabilities"]:
                reference = executor.execute(QueryKind.REFERENCE, row["id"])["AffectedUsers"]
                candidate = executor.execute(QueryKind.CANDIDATE, row["id"])["AffectedUsers"]
                expected = expected_count(row["id"])
                if reference != expected or candidate != expected:
                    raise ValueError(f"Fixture verification failed for {row['id']}.")
                print(
                    f"{row['id']}: reference={reference}, "
                    f"candidate={candidate}, expected={expected}"
                )
        elif args.command == "chat":
            print(json.dumps(service.chat(args.question), indent=2))
        elif args.command == "evaluate":
            result = service.compare(args.vulnerability, args.publish)
            if args.wait and result["cloud"]:
                deadline = time.monotonic() + 600
                while result["cloud"]["status"] not in ("completed", "failed", "canceled"):
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Foundry evaluation is still running; its IDs are saved."
                        )
                    time.sleep(10)
                    result["cloud"] = service.publisher.refresh(result["cloud"])
                    store.save_evaluation(result)
            print(json.dumps(result, indent=2))
    finally:
        telemetry.close()
        clients.close()


if __name__ == "__main__":
    main()
