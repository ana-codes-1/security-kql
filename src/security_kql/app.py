import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from security_kql.azure_clients import AzureClients
from security_kql.config import PACKAGE, Settings
from security_kql.evaluation import reference_info
from security_kql.fixtures import load_fixtures
from security_kql.public_demo import PublicRequestBudget
from security_kql.service import DemoService
from security_kql.state import StateStore
from security_kql.telemetry import Telemetry

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=1500)


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vulnerability_id: str = Field(max_length=32)
    publish: bool = True


def create_app(service=None):
    @asynccontextmanager
    async def lifespan(app):
        if service:
            app.state.service = service
            yield
            return
        settings = Settings.from_env()
        settings.validate()
        store = StateStore(settings.state_dir)
        if settings.reference_approval:
            store.set("reference_approval", settings.reference_approval)
        telemetry = Telemetry(settings, store)
        clients = AzureClients(settings)
        app.state.service = DemoService(settings, clients, telemetry, store)
        try:
            yield
        finally:
            telemetry.close()
            clients.close()

    app = FastAPI(title="Security KQL - Foundry observability", lifespan=lifespan)
    host_name = (
        service.settings.host_name
        if service
        else os.getenv("DEMO_HOSTNAME", os.getenv("WEBSITE_HOSTNAME", ""))
    )
    allowed_hosts = ["localhost", "127.0.0.1"]
    if host_name:
        allowed_hosts.append(host_name)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.state.public_budget = PublicRequestBudget()

    @app.middleware("http")
    async def protect_public_demo(request: Request, call_next):
        svc = request.app.state.service
        if not svc.settings.public_demo or request.method != "POST":
            return await call_next(request)
        if request.url.path == "/api/reference/approve":
            return JSONResponse({"detail": "Reference approval is locked on the public demo."}, 403)
        if request.url.path not in ("/api/chat", "/api/evaluations"):
            return await call_next(request)
        origin = request.headers.get("origin")
        if origin and origin != f"https://{svc.settings.host_name}":
            return JSONResponse({"detail": "Use the hosted demo interface."}, 403)
        length = request.headers.get("content-length")
        if length is None:
            return JSONResponse({"detail": "A Content-Length header is required."}, 411)
        try:
            size = int(length)
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length."}, 400)
        if not 0 <= size <= 8192:
            return JSONResponse({"detail": "The demo accepts at most 8 KB per request."}, 413)
        if request.headers.get("x-demo-request") != "1":
            return JSONResponse({"detail": "Use the hosted demo interface."}, 403)
        try:
            with request.app.state.public_budget.slot():
                return await call_next(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, exc.status_code, headers=exc.headers)

    def require_local_request(x_demo_request: Annotated[str | None, Header()] = None):
        if x_demo_request != "1":
            raise HTTPException(403, "Use the local demo interface.")

    @app.get("/")
    def index():
        return FileResponse(PACKAGE / "static" / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "azure", "synthetic_data": True}

    @app.get("/api/config")
    def config(request: Request):
        svc = request.app.state.service
        return {
            "model": svc.settings.model_name,
            "agent": svc.settings.agent_name,
            "project_endpoint": svc.settings.project_endpoint,
            "foundry_traces_url": svc.settings.foundry_traces_url,
            "cluster": svc.settings.cluster_uri,
            "database": svc.settings.database,
            "telemetry_configured": bool(svc.settings.insights_connection),
            "public_demo": svc.settings.public_demo,
            "insights_url": (
                f"https://portal.azure.com/#resource{svc.settings.insights_resource_id}/overview"
                if svc.settings.insights_resource_id
                else None
            ),
            "fixtures": load_fixtures(),
            "reference": reference_info(svc.store),
        }

    @app.post("/api/chat", dependencies=[Depends(require_local_request)])
    def chat(body: ChatRequest, request: Request):
        try:
            return request.app.state.service.chat(body.question.strip())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            logger.exception("Azure-backed chat request failed")
            raise HTTPException(
                502,
                "The Azure request failed. Check the trace and logs; no answer was fabricated.",
            ) from exc

    @app.post(
        "/api/reference/approve",
        dependencies=[Depends(require_local_request)],
    )
    def approve(request: Request):
        svc = request.app.state.service
        svc.approve_reference()
        return reference_info(svc.store)

    @app.post("/api/evaluations", dependencies=[Depends(require_local_request)])
    def compare(body: ComparisonRequest, request: Request):
        svc = request.app.state.service
        if body.publish and not reference_info(svc.store)["approved"]:
            raise HTTPException(409, "Review and approve the demo reference first.")
        try:
            return svc.compare(body.vulnerability_id, body.publish)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            logger.exception("Comparison or Foundry publishing failed")
            raise HTTPException(
                502,
                "Evaluation failed. Inspect the trace and logs; no cloud result was fabricated.",
            ) from exc

    @app.get("/api/evaluations/{evaluation_id}")
    def evaluation(evaluation_id: str, request: Request):
        svc = request.app.state.service
        payload = svc.store.evaluation(evaluation_id)
        if not payload:
            raise HTTPException(404, "Evaluation not found.")
        if payload["cloud"] and payload["cloud"]["status"] not in (
            "completed",
            "failed",
            "canceled",
        ):
            payload["cloud"] = svc.publisher.refresh(payload["cloud"])
            svc.store.save_evaluation(payload)
        return payload

    @app.get("/api/traces/{trace_id}")
    def trace(trace_id: str, request: Request):
        spans = request.app.state.service.store.trace(trace_id)
        if not spans:
            raise HTTPException(404, "Trace not found.")
        return {"trace_id": trace_id, "spans": spans}

    return app


app = create_app()
