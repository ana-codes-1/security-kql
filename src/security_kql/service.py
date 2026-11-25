import json
from datetime import UTC, datetime

from opentelemetry.trace import SpanKind

from security_kql.azure_clients import AdxExecutor, IntentGenerator, query_parameters
from security_kql.evaluation import EvaluationPublisher, run_comparison
from security_kql.fixtures import load_fixtures
from security_kql.queries import (
    DEFINITION,
    IDENTIFIER,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    QueryKind,
    query_text,
    reference_fingerprint,
    validate_identifier,
)


class DemoService:
    def __init__(self, settings, clients, telemetry, store):
        self.settings = settings
        self.clients = clients
        self.telemetry = telemetry
        self.store = store
        self.tracer = telemetry.tracer
        self.generator = IntentGenerator(clients, self.tracer)
        self.executor = AdxExecutor(clients, self.tracer)
        self.publisher = EvaluationPublisher(clients, store)

    def agent_span(self, kind):
        return self.tracer.start_as_current_span(
            f"invoke_agent {self.settings.agent_name}",
            kind=SpanKind.INTERNAL,
            attributes={
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.id": self.settings.agent_name,
                "gen_ai.agent.name": self.settings.agent_name,
                "demo.request.kind": kind,
                "demo.schema.version": SCHEMA_VERSION,
                "demo.prompt.version": PROMPT_VERSION,
                "demo.fixture.version": load_fixtures()["version"],
            },
        )

    def chat(self, question):
        with self.agent_span("chat") as root:
            trace_id = f"{root.get_span_context().trace_id:032x}"
            intent = self.generator.generate(question)
            with self.tracer.start_as_current_span("validate_intent") as validation:
                mentioned = {match.upper() for match in IDENTIFIER.findall(question)}
                if intent.action != "affected_users":
                    root.set_attribute("demo.outcome", intent.action)
                    return {
                        "status": intent.action,
                        "message": intent.message,
                        "trace_id": trace_id,
                    }
                if len(mentioned) != 1 or intent.vulnerability_id not in mentioned:
                    validation.set_attribute("demo.guardrail.passed", False)
                    root.set_attribute("demo.outcome", "clarify")
                    return {
                        "status": "clarify",
                        "message": "Please name one vulnerability from the demo dataset.",
                        "trace_id": trace_id,
                    }
                vulnerability_id = validate_identifier(intent.vulnerability_id)
                validation.set_attributes(
                    {
                        "demo.guardrail.passed": True,
                        "demo.guardrail.query_mode": "registered-parameterized-template",
                        "demo.vulnerability_id": vulnerability_id,
                    }
                )
            with self.tracer.start_as_current_span("compile_kql") as compiler:
                compiler.set_attribute("demo.query.kind", QueryKind.CANDIDATE.value)
                compiler.set_attribute("demo.query.text", query_text(QueryKind.CANDIDATE))
            result = self.executor.execute(QueryKind.CANDIDATE, vulnerability_id)
            answer = f"{result['AffectedUsers']} users are affected by {vulnerability_id}."
            with self.tracer.start_as_current_span("format_answer") as formatter:
                formatter.set_attribute("demo.result.affected_users", result["AffectedUsers"])
            root.set_attributes(
                {
                    "demo.outcome": "answered",
                    "demo.vulnerability_id": vulnerability_id,
                    "demo.result.affected_users": result["AffectedUsers"],
                    "gen_ai.input.messages": json.dumps(
                        [
                            {
                                "role": "user",
                                "parts": [
                                    {
                                        "type": "text",
                                        "content": f"Count affected users for {vulnerability_id}",
                                    }
                                ],
                            }
                        ]
                    ),
                    "gen_ai.output.messages": json.dumps(
                        [{"role": "assistant", "parts": [{"type": "text", "content": answer}]}]
                    ),
                }
            )
            return {
                "status": "answered",
                "message": answer,
                "affected_users": result["AffectedUsers"],
                "vulnerability_id": vulnerability_id,
                "kql": query_text(QueryKind.CANDIDATE),
                "parameters": query_parameters(vulnerability_id),
                "definition": DEFINITION,
                "trace_id": trace_id,
                "database_request_id": result["request_id"],
            }

    def compare(self, vulnerability_id, publish):
        with self.agent_span("evaluation") as root:
            trace_id = f"{root.get_span_context().trace_id:032x}"
            comparison = run_comparison(self.executor, vulnerability_id, trace_id, self.store)
            root.set_attribute("demo.evaluation.id", comparison["id"])
            root.set_attribute(
                "demo.evaluation.reference_approved", comparison["reference_approved"]
            )
            self.store.save_evaluation(comparison)
            if publish:
                with self.tracer.start_as_current_span("publish_foundry_evaluation"):
                    comparison["cloud"] = self.publisher.publish(comparison)
                    self.store.save_evaluation(comparison)
            root.set_attribute("demo.outcome", "comparison_complete")
            return comparison

    def approve_reference(self):
        approval = {
            "fingerprint": reference_fingerprint(),
            "approved_at": datetime.now(UTC).isoformat(),
            "reviewer": "local interactive reviewer",
            "scope": "Synthetic demo only; not Forescout approval or enterprise attestation.",
        }
        self.store.set("reference_approval", approval)
        return approval
