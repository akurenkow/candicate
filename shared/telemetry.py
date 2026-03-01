"""
OpenTelemetry instrumentation bootstrap.
Enabled only when OTEL_ENABLED=true. Disabled when ENVIRONMENT=test.
"""

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

_SAMPLING_RATIO = {"production": 0.1, "staging": 0.5, "test": 0.0}


def bootstrap(app: "FastAPI | None" = None) -> None:
    if os.getenv("ENVIRONMENT") == "test":
        os.environ["OTEL_ENABLED"] = "false"
        return
    if os.getenv("OTEL_ENABLED", "").lower() != "true":
        return

    deployment_env = os.getenv("DEPLOYMENT_ENVIRONMENT", os.getenv("ENVIRONMENT", "development"))
    traces_enabled = os.getenv("OTEL_TRACES_ENABLED", "true").lower() == "true"
    metrics_enabled = os.getenv("OTEL_METRICS_ENABLED", "true").lower() == "true"

    from opentelemetry import metrics, trace
    from opentelemetry.sdk.resources import Resource

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    service_name = os.getenv("OTEL_SERVICE_NAME", "unknown-service")
    service_version = os.getenv("SERVICE_VERSION", "0.1.0")

    resource = Resource(attributes={
        "service.name": service_name,
        "service.version": service_version,
        "deployment.environment": deployment_env,
    })

    if traces_enabled:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ParentBased, TraceIdRatioBased

        ratio = _SAMPLING_RATIO.get(deployment_env, 0.1)
        sampler = ALWAYS_OFF if ratio <= 0 else ParentBased(root=TraceIdRatioBased(ratio))
        span_exporter = OTLPSpanExporter(endpoint=endpoint)
        trace_provider = TracerProvider(resource=resource, sampler=sampler)
        trace_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(trace_provider)

    if metrics_enabled:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint),
            export_interval_millis=60000,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)

    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor

    RequestsInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    Psycopg2Instrumentor().instrument()
    AsyncPGInstrumentor().instrument()
    if app is not None:
        FastAPIInstrumentor().instrument_app(app)


def _should_record() -> bool:
    if os.getenv("ENVIRONMENT") == "test":
        return False
    if os.getenv("OTEL_ENABLED", "").lower() != "true":
        return False
    return os.getenv("OTEL_METRICS_ENABLED", "true").lower() == "true"


def _get_histogram(name: str):
    if not _should_record():
        return None
    from opentelemetry import metrics
    meter = metrics.get_meter_provider().get_meter("shared.telemetry", "0.1.0")
    return meter.create_histogram(name, "seconds", "Duration in seconds")


def record_rag_pipeline_duration(seconds: float, flow: str, service: str) -> None:
    h = _get_histogram("rag_pipeline_duration_seconds")
    if h is not None:
        h.record(seconds, {"flow": flow, "service": service})


def record_retrieval_duration(seconds: float, flow: str, service: str) -> None:
    h = _get_histogram("retrieval_duration_seconds")
    if h is not None:
        h.record(seconds, {"flow": flow, "service": service})


def record_generation_duration(seconds: float, flow: str, service: str) -> None:
    h = _get_histogram("generation_duration_seconds")
    if h is not None:
        h.record(seconds, {"flow": flow, "service": service})
