"""Observability / tracing setup for headless (CI/CD) runs.

Hosted Foundry agents are traced by the **Foundry project's own tracing
feature**: when an agent runs, the project records the run (and its child
model / tool spans) to the project's connected tracing backend server-side —
the same traces the app's Tracing tab reads back. So for CI/CD "tracing enabled
and logged in Foundry" we do **not** need a separate Azure Monitor exporter.

This module simply turns on the **Microsoft Agent Framework** OpenTelemetry
instrumentation (``agent_framework.observability.enable_instrumentation``) so
each ``FoundryAgent`` run participates in that tracing. It is best-effort and
never raises.

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


def setup_tracing(service_name: str = "agentic-aiops") -> TracingStatus:
    """Enable Foundry/Agent-Framework tracing for agent runs.

    Idempotent and fail-safe: returns a :class:`TracingStatus` describing what
    happened and never raises, so a tracing gap can be logged without crashing
    the pipeline step.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return TracingStatus(True, "foundry", "Already configured.")

    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)

    try:
        from agent_framework.observability import enable_instrumentation

        enable_instrumentation()
    except Exception as exc:  # noqa: BLE001 - never break the run
        return TracingStatus(
            False,
            "none",
            f"Agent Framework instrumentation could not be enabled: {exc}. "
            "Foundry still records hosted-agent runs server-side.",
        )

    _CONFIGURED = True
    return TracingStatus(
        True,
        "foundry",
        "Agent Framework instrumentation enabled; hosted-agent runs are traced "
        "and logged by the Foundry project.",
    )
