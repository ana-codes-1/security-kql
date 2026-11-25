from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)


class LocalSpanExporter(SpanExporter):
    def __init__(self, store):
        self.store = store

    def export(self, spans):
        self.store.save_spans(spans)
        return SpanExportResult.SUCCESS


class Telemetry:
    def __init__(self, settings, store):
        self.provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": settings.agent_name,
                    "service.version": "0.1.0",
                    "deployment.environment.name": "synthetic-demo",
                }
            )
        )
        self.provider.add_span_processor(SimpleSpanProcessor(LocalSpanExporter(store)))
        if settings.insights_connection:
            from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

            # The Azure exporter reads service metadata from the process-wide provider.
            trace.set_tracer_provider(self.provider)
            exporter = AzureMonitorTraceExporter(
                connection_string=settings.insights_connection,
                disable_offline_storage=True,
            )
            self.provider.add_span_processor(
                BatchSpanProcessor(exporter, schedule_delay_millis=1000)
            )
        self.tracer = self.provider.get_tracer("security_kql", "0.1.0")

    def close(self):
        self.provider.force_flush(timeout_millis=15000)
        self.provider.shutdown()
