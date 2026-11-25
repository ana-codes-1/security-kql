import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    project_endpoint: str
    model_name: str
    agent_name: str
    tenant_id: str
    cluster_uri: str
    database: str
    insights_connection: str
    insights_resource_id: str
    project_resource_id: str
    state_dir: Path
    host_name: str = ""
    managed_identity: bool = False
    managed_identity_client_id: str | None = None
    public_demo: bool = False
    reference_approval: dict = field(default_factory=dict)

    @property
    def foundry_traces_url(self):
        parts = self.project_resource_id.strip("/").split("/")
        if (
            len(parts) != 10
            or [part.lower() for part in parts[::2]]
            != ["subscriptions", "resourcegroups", "providers", "accounts", "projects"]
            or parts[5].lower() != "microsoft.cognitiveservices"
        ):
            return None
        try:
            # Foundry's portal resource selector uses the subscription UUID's bytes.
            subscription = base64.urlsafe_b64encode(UUID(parts[1]).bytes).decode().rstrip("=")
        except ValueError:
            return None
        resource = ",".join(
            quote(value, safe="") for value in (subscription, parts[3], "", parts[7], parts[9])
        )
        agent = quote(self.agent_name, safe="")
        return f"https://ai.azure.com/nextgen/r/{resource}/build/agents/external/{agent}/traces"

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env", encoding="utf-8-sig")
        state_dir = Path(os.getenv("DEMO_STATE_DIR", ".state"))
        if not state_dir.is_absolute():
            state_dir = ROOT / state_dir
        return cls(
            project_endpoint=os.getenv("FOUNDRY_PROJECT_ENDPOINT", ""),
            model_name=os.getenv("FOUNDRY_MODEL_NAME", "gpt-5-mini"),
            agent_name=os.getenv("FOUNDRY_AGENT_NAME", "security-kql-demo"),
            tenant_id=os.getenv("AZURE_TENANT_ID", ""),
            cluster_uri=os.getenv("ADX_CLUSTER_URI", ""),
            database=os.getenv("ADX_DATABASE", "SecurityDemo"),
            insights_connection=os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", ""),
            insights_resource_id=os.getenv("APPLICATIONINSIGHTS_RESOURCE_ID", ""),
            project_resource_id=os.getenv("FOUNDRY_PROJECT_RESOURCE_ID", ""),
            state_dir=state_dir,
            host_name=os.getenv("DEMO_HOSTNAME", os.getenv("WEBSITE_HOSTNAME", "")),
            managed_identity=os.getenv("DEMO_MANAGED_IDENTITY", "").lower() == "true"
            or bool(os.getenv("WEBSITE_INSTANCE_ID")),
            managed_identity_client_id=os.getenv("AZURE_CLIENT_ID") or None,
            public_demo=os.getenv("DEMO_PUBLIC", "").lower() == "true",
            reference_approval=json.loads(os.getenv("DEMO_REFERENCE_APPROVAL", "{}")),
        )

    def validate(self):
        missing = [
            name
            for name, value in {
                "FOUNDRY_PROJECT_ENDPOINT": self.project_endpoint,
                "ADX_CLUSTER_URI": self.cluster_uri,
                "AZURE_TENANT_ID": self.tenant_id,
                "APPLICATIONINSIGHTS_CONNECTION_STRING": self.insights_connection,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(f"Missing configuration: {', '.join(missing)}")
        for endpoint in (self.project_endpoint, self.cluster_uri):
            if not endpoint.startswith("https://"):
                raise ValueError("Azure endpoints must use HTTPS.")
        if self.public_demo and not self.host_name:
            raise ValueError("Public hosting requires an explicit demo hostname.")
