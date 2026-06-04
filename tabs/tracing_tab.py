"""Tracing tab (scaffolded placeholder)."""

from __future__ import annotations

from common.ui import placeholder


def render() -> None:
    placeholder(
        "Tracing & Observability",
        "Inspect end-to-end traces of agent runs — tool calls, token usage and "
        "latency — via OpenTelemetry and Azure Monitor / Application Insights.",
        [
            "Span timeline for each agent run",
            "Tool / function call inputs and outputs",
            "Token usage and latency per step",
            "Filter and search across recent conversations",
        ],
    )
