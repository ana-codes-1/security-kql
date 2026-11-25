import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from security_kql.app import create_app
from security_kql.azure_clients import AzureClients, Intent
from security_kql.config import Settings
from security_kql.evaluation import exact_score, reference_info, run_comparison
from security_kql.fixtures import expected_count, load_fixtures
from security_kql.public_demo import PublicRequestBudget
from security_kql.queries import QueryKind, query_text, reference_fingerprint
from security_kql.service import DemoService
from security_kql.state import StateStore
from security_kql.telemetry import Telemetry


@pytest.fixture
def service(tmp_path):
    settings = Settings(
        project_endpoint="https://example.services.ai.azure.com/api/projects/test",
        model_name="test-model",
        agent_name="security-kql-test",
        tenant_id="test-tenant",
        cluster_uri="https://example.eastus2.kusto.windows.net",
        database="SecurityDemo",
        insights_connection="",
        insights_resource_id="",
        project_resource_id="",
        state_dir=tmp_path,
    )
    store = StateStore(tmp_path)
    telemetry = Telemetry(settings, store)
    clients = SimpleNamespace(settings=settings, kusto=Mock())
    svc = DemoService(settings, clients, telemetry, store)
    svc.generator = Mock()
    yield svc
    telemetry.close()


def test_fixture_counts_handle_duplicates_latest_state_and_unknown_users():
    fixtures = load_fixtures()
    assert expected_count("DEMO-2026-0001") == 4
    assert expected_count("DEMO-2026-0002") == 2
    assert expected_count("DEMO-2026-0003") == 0
    changed = copy.deepcopy(fixtures)
    changed["findings"].append(
        {
            "DeviceId": "device-05",
            "VulnerabilityId": "DEMO-2026-0001",
            "Status": "Resolved",
            "ObservedAt": "2026-11-24T11:59:59Z",
        }
    )
    assert expected_count("DEMO-2026-0001", changed) == 3


def test_runtime_clients_use_supported_kusto_credential_builder(service, monkeypatch):
    kusto = Mock()
    monkeypatch.setattr("security_kql.azure_clients.KustoClient", kusto)
    clients = AzureClients(service.settings)
    try:
        kusto.assert_called_once()
    finally:
        clients.close()


def test_hosted_runtime_uses_managed_identity_instead_of_local_cli(service, monkeypatch):
    managed = Mock()
    cli = Mock()
    monkeypatch.setattr("security_kql.azure_clients.ManagedIdentityCredential", managed)
    monkeypatch.setattr("security_kql.azure_clients.AzureCliCredential", cli)
    monkeypatch.setattr("security_kql.azure_clients.KustoClient", Mock())
    clients = AzureClients(
        replace(service.settings, managed_identity=True, managed_identity_client_id="hosted-client")
    )
    try:
        assert clients.credential is managed.return_value
        managed.assert_called_once_with(client_id="hosted-client")
        cli.assert_not_called()
    finally:
        clients.close()


@pytest.mark.parametrize(
    ("actual", "expected", "score"),
    [
        ({"AffectedUsers": 4}, {"AffectedUsers": 4}, 1.0),
        ({"AffectedUsers": 8}, {"AffectedUsers": 4}, 0.0),
        ({"AffectedUsers": "4"}, {"AffectedUsers": 4}, 0.0),
        ({"AffectedUsers": True}, {"AffectedUsers": 1}, 0.0),
        ({"AffectedUsers": 0}, {"AffectedUsers": 0}, 1.0),
    ],
)
def test_exact_grader_does_not_confuse_execution_success_with_correctness(actual, expected, score):
    assert exact_score(actual, expected) == score


def test_executor_binds_parameters_and_enforces_readonly(service):
    result = Mock()
    result.get_exceptions.return_value = []
    result.primary_results = [[{"AffectedUsers": 4}]]
    service.clients.kusto.execute_query.return_value = result
    assert service.executor.execute(QueryKind.CANDIDATE, "DEMO-2026-0001")["AffectedUsers"] == 4
    database, query, props = service.clients.kusto.execute_query.call_args.args
    assert database == "SecurityDemo"
    assert query == query_text(QueryKind.CANDIDATE)
    assert "DEMO-2026-0001" not in query
    serialized = json.loads(props.to_json())
    assert serialized["Parameters"]["vulnerability_id"] == "DEMO-2026-0001"
    assert serialized["Options"]["request_readonly_hardline"] is True
    assert serialized["Options"]["request_remote_entities_disabled"] is True


def test_executor_rejects_raw_kql_and_injected_parameters(service):
    with pytest.raises(ValueError):
        service.executor.execute(".drop table DeviceUsers", "DEMO-2026-0001")
    with pytest.raises(ValueError):
        service.executor.execute(QueryKind.CANDIDATE, 'DEMO-2026-0001"; .drop table X')
    service.clients.kusto.execute_query.assert_not_called()


def test_executor_rejects_partial_results(service):
    service.clients.kusto.execute_query.return_value.get_exceptions.return_value = ["partial"]
    with pytest.raises(ValueError, match="partial"):
        service.executor.execute(QueryKind.CANDIDATE, "DEMO-2026-0001")


@pytest.mark.parametrize(
    "question", ["How many users are vulnerable?", "Compare DEMO-2026-0001 and DEMO-2026-0002"]
)
def test_model_cannot_invent_or_silently_choose_the_vulnerability(service, question):
    service.generator.generate.return_value = Intent(
        action="affected_users", vulnerability_id="DEMO-2026-0001", message="Count users"
    )
    service.executor = Mock()
    response = service.chat(question)
    assert response["status"] == "clarify"
    service.executor.execute.assert_not_called()
    assert service.store.trace(response["trace_id"])


def test_chat_uses_database_count_and_records_correlated_spans(service):
    service.generator.generate.return_value = Intent(
        action="affected_users", vulnerability_id="DEMO-2026-0001", message="Count users"
    )
    service.executor = Mock()
    service.executor.execute.return_value = {"AffectedUsers": 4, "request_id": "test-request"}
    response = service.chat("How many users are affected by DEMO-2026-0001?")
    assert response["affected_users"] == 4
    assert "4 users" in response["message"]
    spans = service.store.trace(response["trace_id"])
    root = next(span for span in spans if span["parent_id"] is None)
    assert root["attributes"]["gen_ai.agent.id"] == "security-kql-test"
    assert root["attributes"]["demo.result.affected_users"] == 4


def test_agent_invocations_export_as_dependencies_for_foundry_discovery(service):
    exporter = InMemorySpanExporter()
    service.telemetry.provider.add_span_processor(SimpleSpanProcessor(exporter))
    with service.agent_span("chat"):
        pass
    invocation = exporter.get_finished_spans()[0]
    assert invocation.kind == SpanKind.INTERNAL
    assert invocation.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert invocation.attributes["gen_ai.agent.id"] == service.settings.agent_name


def test_comparison_executes_reference_and_labels_known_bad_baseline(service):
    executor = Mock()
    executor.execute.side_effect = [
        {"AffectedUsers": 4},
        {"AffectedUsers": 4},
        {"AffectedUsers": 8},
    ]
    result = run_comparison(executor, "DEMO-2026-0001", "test-trace", service.store)
    assert [row["passed"] for row in result["variants"]] == [True, False]
    assert result["variants"][1]["label"] == "Deliberately incorrect saved baseline"
    assert result["reference_approved"] is False


def test_reference_must_be_reviewed_and_fingerprint_must_match(service):
    assert not reference_info(service.store)["approved"]
    service.approve_reference()
    assert reference_info(service.store)["approved"]
    service.store.set("reference_approval", {"fingerprint": "obsolete"})
    assert not reference_info(service.store)["approved"]
    assert reference_fingerprint()


def test_http_rejects_cross_origin_posts_and_gates_unapproved_cloud_evaluation(service):
    with TestClient(create_app(service), base_url="http://localhost") as client:
        assert client.get("/health").status_code == 200
        assert client.post("/api/reference/approve").status_code == 403
        response = client.post(
            "/api/evaluations",
            json={"vulnerability_id": "DEMO-2026-0001", "publish": True},
            headers={"X-Demo-Request": "1"},
        )
        assert response.status_code == 409
        assert client.get("/", headers={"host": "attacker.example"}).status_code == 400


def test_portal_link_targets_the_registered_agent_in_the_configured_project(service):
    with TestClient(create_app(service), base_url="http://localhost") as client:
        assert client.get("/api/config").json()["foundry_traces_url"] is None
        service.settings = replace(
            service.settings,
            project_resource_id=(
                "/subscriptions/00000000-0000-0000-0000-000000000001"
                "/resourceGroups/demo(rg)/providers/Microsoft.CognitiveServices"
                "/accounts/demo-foundry/projects/security-kql"
            ),
        )
        assert client.get("/api/config").json()["foundry_traces_url"] == (
            "https://ai.azure.com/nextgen/r/AAAAAAAAAAAAAAAAAAAAAQ,demo%28rg%29,"
            ",demo-foundry,security-kql/build/agents/external/security-kql-test/traces"
        )


def test_public_budget_expires_requests_and_releases_slots_after_failure():
    clock = Mock(return_value=100)
    budget = PublicRequestBudget(limit=2, concurrent=1, clock=clock)
    with pytest.raises(RuntimeError), budget.slot():
        with pytest.raises(HTTPException) as busy, budget.slot():
            pass
        assert busy.value.status_code == 429
        raise RuntimeError("deliberate test failure")
    with budget.slot():
        pass
    with pytest.raises(HTTPException) as exhausted, budget.slot():
        pass
    assert exhausted.value.status_code == 429
    assert exhausted.value.headers["Retry-After"] == "3600"
    clock.return_value = 3700
    with budget.slot():
        pass


def test_public_host_allows_chat_but_bounds_azure_requests(service):
    service.settings = replace(service.settings, public_demo=True, host_name="security.example")
    service.generator.generate.return_value = Intent(
        action="affected_users", vulnerability_id="DEMO-2026-0001", message="Count users"
    )
    service.executor = Mock()
    service.executor.execute.return_value = {"AffectedUsers": 4, "request_id": "test-request"}
    app = create_app(service)
    app.state.public_budget = PublicRequestBudget(limit=1)
    with TestClient(app, base_url="https://security.example") as client:
        assert client.get("/api/config").json()["public_demo"] is True
        args = {
            "json": {"question": "How many users are affected by DEMO-2026-0001?"},
            "headers": {"X-Demo-Request": "1", "Origin": "https://security.example"},
        }
        assert client.post("/api/chat", **args).json()["affected_users"] == 4
        assert client.post("/api/chat", **args).status_code == 429
    service.generator.generate.assert_called_once()


def test_public_host_locks_reference_and_rejects_cross_origin_and_large_bodies(service):
    service.settings = replace(service.settings, public_demo=True, host_name="security.example")
    with TestClient(create_app(service), base_url="https://security.example") as client:
        headers = {"X-Demo-Request": "1"}
        assert client.post("/api/reference/approve", json={}, headers=headers).status_code == 403
        assert not reference_info(service.store)["approved"]
        response = client.post(
            "/api/chat",
            json={"question": "Count users"},
            headers={**headers, "Origin": "https://another.example"},
        )
        assert response.status_code == 403
        assert client.post("/api/chat", content=b"x" * 8193, headers=headers).status_code == 413
        assert client.post("/api/chat", content=iter([b"{}"]), headers=headers).status_code == 411
    service.generator.generate.assert_not_called()


def test_database_failure_never_becomes_a_successful_answer(service):
    service.generator.generate.return_value = Intent(
        action="affected_users", vulnerability_id="DEMO-2026-0001", message="Count users"
    )
    service.executor = Mock()
    service.executor.execute.side_effect = RuntimeError("deliberate test outage")
    with TestClient(create_app(service), base_url="http://localhost") as client:
        response = client.post(
            "/api/chat",
            json={"question": "How many users are affected by DEMO-2026-0001?"},
            headers={"X-Demo-Request": "1"},
        )
    assert response.status_code == 502
    assert "no answer was fabricated" in response.json()["detail"]
