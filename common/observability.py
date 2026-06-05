"""Observability / tracing setup for headless (CI/CD) runs.

In the Streamlit app, agent telemetry is *read back* from Application Insights by
the Tracing tab. In a CI/CD pipeline there is no UI, so this module turns the
other direction on: it **emits** OpenTelemetry spans for agent / model / tool
calls and exports them to the Application Insights resource connected to the
Foundry project — i.e. tracing is "enabled and logged in Foundry" for the run.

How it works (all best-effort, never raises):

1. Ask the Foundry project for its connected Application Insights connection
   string (``AIProjectClient.telemetry``).
2. Wire that into the OpenTelemetry pipeline with Azure Monitor
   (``configure_azure_monitor``) so spans flow to that App Insights resource.
3. Turn on the **Microsoft Agent Framework** instrumentation
   (``agent_framework.observability.enable_instrumentation``) so each
   ``FoundryAgent`` run produces gen-ai spans.

Authentication is unchanged — ``DefaultAzureCredential`` only (which, in CI,
resolves to the service principal exported from ``AZURE_CREDENTIALS``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_CONFIGURED = False


@dataclass
class TracingStatus:
    """Outcome of a tracing-setup attempt (for logging in the pipeline)."""

    enabled: bool = False
    exporter: str = "none"
    detail: str = ""


def _app_insights_connection_string() -> str:
    """Return the project's connected App Insights connection string (or "")."""
    from .azure_clients import get_project_client

    client = get_project_client()
    telemetry = getattr(client, "telemetry", None)
    if telemetry is None:
        return ""
    getter = getattr(telemetry, "get_application_insights_connection_string", None)
    if not callable(getter):
        return ""
    try:
        return getter() or ""
    except Exception:  # noqa: BLE001 - no telemetry resource / no access
        return ""


def setup_tracing(service_name: str = "agentic-aiops") -> TracingStatus:
    """Enable OpenTelemetry tracing and export spans to the project's App Insights.

    Idempotent and fail-safe: returns a :class:`TracingStatus` describing what
    happened and never raises, so a tracing gap can be logged without failing the
    pipeline step.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return TracingStatus(True, "azure-monitor", "Already configured.")

    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)

    conn = _app_insights_connection_string()
    if not conn:
        return TracingStatus(
            False,
            "none",
            "No Application Insights resource is connected to the Foundry "
            "project; agent spans will not be exported.",
        )

    # 1) Route OpenTelemetry to the project's App Insights via Azure Monitor.
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=conn)
    except ImportError:
        return TracingStatus(
            False,
            "none",
            "azure-monitor-opentelemetry is not installed; cannot export traces "
            "to Foundry. Add it to requirements.txt.",
        )
    except Exception as exc:  # noqa: BLE001 - never break the run
        return TracingStatus(False, "none", f"Azure Monitor setup failed: {exc}")

    # 2) Turn on Agent Framework instrumentation so agent runs emit spans.
    detail = "Spans export to the Foundry-connected Application Insights."
    try:
        from agent_framework.observability import enable_instrumentation

        enable_instrumentation()
    except Exception as exc:  # noqa: BLE001 - Azure Monitor still active
        detail += f" (Agent Framework instrumentation not enabled: {exc})"

    _CONFIGURED = True
    return TracingStatus(True, "azure-monitor", detail)
