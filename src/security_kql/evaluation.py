import json
import uuid

from security_kql.fixtures import expected_count, load_fixtures
from security_kql.queries import (
    DEFINITION,
    QueryKind,
    query_text,
    reference_fingerprint,
    validate_identifier,
)

EVALUATOR_NAME = "security_affected_users_exact"
GRADER_CODE = """def grade(sample: dict, item: dict) -> float:
    import json
    try:
        actual = json.loads(item["response"])
        expected = json.loads(item["ground_truth"])
        if set(actual) != {"AffectedUsers"} or set(expected) != {"AffectedUsers"}:
            return 0.0
        if type(actual["AffectedUsers"]) is not int:
            return 0.0
        if type(expected["AffectedUsers"]) is not int:
            return 0.0
        return 1.0 if actual["AffectedUsers"] == expected["AffectedUsers"] else 0.0
    except (ValueError, KeyError, TypeError):
        return 0.0
"""


def exact_score(actual, expected):
    namespace = {}
    exec(GRADER_CODE, namespace)
    return namespace["grade"](
        {},
        {"response": json.dumps(actual), "ground_truth": json.dumps(expected)},
    )


def reference_info(store):
    fingerprint = reference_fingerprint()
    approval = store.get("reference_approval", {})
    return {
        "definition": DEFINITION,
        "query": query_text(QueryKind.REFERENCE),
        "fingerprint": fingerprint,
        "approved": approval.get("fingerprint") == fingerprint,
        "approval": approval if approval.get("fingerprint") == fingerprint else None,
        "fixture_version": load_fixtures()["version"],
        "as_of": load_fixtures()["as_of"],
    }


class EvaluationPublisher:
    def __init__(self, clients, store):
        self.clients = clients
        self.store = store

    def ensure_evaluator(self):
        registered = self.store.get("registered_evaluator")
        if registered and registered.get("project") == self.clients.settings.project_endpoint:
            return
        evaluator = self.clients.project.evaluators.create_version(
            name=EVALUATOR_NAME,
            evaluator_version={
                "name": EVALUATOR_NAME,
                "display_name": "Affected users: exact result match",
                "description": "Compares captured ADX aggregates, not KQL text or prose.",
                "categories": ["quality"],
                "definition": {
                    "type": "code",
                    "code_text": GRADER_CODE,
                    "init_parameters": {
                        "type": "object",
                        "properties": {
                            "deployment_name": {"type": "string"},
                            "pass_threshold": {"type": "number"},
                        },
                        "required": ["deployment_name", "pass_threshold"],
                    },
                    "data_schema": {
                        "type": "object",
                        "properties": {
                            "item": {
                                "type": "object",
                                "properties": {
                                    "response": {"type": "string"},
                                    "ground_truth": {"type": "string"},
                                },
                            }
                        },
                        "required": ["item"],
                    },
                    "metrics": {
                        "result": {
                            "type": "continuous",
                            "desirable_direction": "increase",
                            "min_value": 0.0,
                            "max_value": 1.0,
                        }
                    },
                },
            },
            headers={"Foundry-Features": "Evaluations=V1Preview"},
        )
        self.store.set(
            "registered_evaluator",
            {
                "project": self.clients.settings.project_endpoint,
                "name": EVALUATOR_NAME,
                "id": evaluator.id,
            },
        )

    def publish(self, comparison):
        if not reference_info(self.store)["approved"]:
            raise ValueError("Review and approve the demo reference before publishing.")
        self.ensure_evaluator()
        client = self.clients.openai
        evaluation = client.evals.create(
            name="Security KQL - correct query versus known-bad baseline",
            data_source_config={
                "type": "custom",
                "item_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "response": {"type": "string"},
                        "ground_truth": {"type": "string"},
                        "variant": {"type": "string"},
                        "kql": {"type": "string"},
                        "trace_id": {"type": "string"},
                    },
                    "required": ["query", "response", "ground_truth", "variant"],
                },
            },
            testing_criteria=[
                {
                    "type": "azure_ai_evaluator",
                    "name": "affected_users_exact",
                    "evaluator_name": EVALUATOR_NAME,
                    "initialization_parameters": {
                        "deployment_name": self.clients.settings.model_name,
                        "pass_threshold": 1.0,
                    },
                }
            ],
        )
        content = [
            {
                "item": {
                    "query": comparison["question"],
                    "response": json.dumps({"AffectedUsers": row["actual"]}),
                    "ground_truth": json.dumps({"AffectedUsers": comparison["expected"]}),
                    "variant": row["variant"],
                    "kql": row["kql"],
                    "trace_id": comparison["trace_id"],
                }
            }
            for row in comparison["variants"]
        ]
        run = client.evals.runs.create(
            eval_id=evaluation.id,
            name=f"Correct-versus-known-bad {comparison['id'][:8]}",
            data_source={
                "type": "jsonl",
                "source": {"type": "file_content", "content": content},
            },
        )
        return {
            "eval_id": evaluation.id,
            "run_id": run.id,
            "status": run.status,
            "report_url": run.report_url,
        }

    def refresh(self, cloud):
        run = self.clients.openai.evals.runs.retrieve(
            run_id=cloud["run_id"], eval_id=cloud["eval_id"]
        )
        result = {
            **cloud,
            "status": run.status,
            "report_url": run.report_url,
            "result_counts": run.result_counts.model_dump() if run.result_counts else None,
        }
        if run.status in ("completed", "failed", "canceled"):
            result["outputs"] = [
                row.model_dump(mode="json")
                for row in self.clients.openai.evals.runs.output_items.list(
                    run_id=cloud["run_id"], eval_id=cloud["eval_id"]
                )
            ]
        return result


def run_comparison(executor, vulnerability_id, trace_id, store):
    validate_identifier(vulnerability_id)
    expected = executor.execute(QueryKind.REFERENCE, vulnerability_id)["AffectedUsers"]
    fixture_expected = expected_count(vulnerability_id)
    if expected != fixture_expected:
        raise ValueError("ADX reference results differ from the versioned fixture snapshot.")
    variants = []
    for kind, label in (
        (QueryKind.CANDIDATE, "Guarded candidate"),
        (QueryKind.KNOWN_BAD, "Deliberately incorrect saved baseline"),
    ):
        actual = executor.execute(kind, vulnerability_id)["AffectedUsers"]
        variants.append(
            {
                "variant": kind.value,
                "label": label,
                "actual": actual,
                "passed": exact_score({"AffectedUsers": actual}, {"AffectedUsers": expected})
                == 1.0,
                "kql": query_text(kind),
            }
        )
    return {
        "id": str(uuid.uuid4()),
        "question": f"How many users are affected by {vulnerability_id}?",
        "vulnerability_id": vulnerability_id,
        "trace_id": trace_id,
        "expected": expected,
        "variants": variants,
        "reference_approved": reference_info(store)["approved"],
        "reference_fingerprint": reference_fingerprint(),
        "cloud": None,
    }
