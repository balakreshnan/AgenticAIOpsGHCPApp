"""Tracing / observability utilities.

Foundry agents emit OpenTelemetry gen-ai spans to the **Application Insights**
resource connected to the project. This module reads those spans back so the
Tracing tab can let the user pick one or more agents and inspect their recent
trace logs (e.g. the last week, or the top *N* spans).

Access path (no extra SDK, ``DefaultAzureCredential`` only):

1. ``AIProjectClient.telemetry.get_application_insights_connection_string()``
   returns a connection string that carries the ``ApplicationId`` of the App
   Insights resource.
2. We query the Application Insights REST API
   (``https://api.applicationinsights.io/v1/apps/{ApplicationId}/query``) with an
   AAD bearer token for the ``https://api.applicationinsights.io/.default``
   scope, obtained from ``DefaultAzureCredential``.

Spans land in the ``dependencies`` table. Each agent invocation, model ``chat``
call and ``execute_tool`` call is a span; they share an ``operation_Id`` (one
conversation / run). Gen-ai attributes live in ``customDimensions`` —
``gen_ai.agent.name``, ``span_type``, ``gen_ai.usage.input_tokens`` /
``output_tokens`` etc.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import require_settings

# App Insights REST query endpoint + the AAD scope used to call it.
_AI_API = "https://api.applicationinsights.io/v1/apps/{app_id}/query"
_AI_SCOPE = "https://api.applicationinsights.io/.default"

# Allow-listed time windows (label -> KQL ``ago()`` literal). Restricting to a
# fixed set keeps the literal out of user control (no KQL injection via range).
TIME_RANGES: dict[str, str] = {
    "Last hour": "1h",
    "Last 24 hours": "24h",
    "Last 7 days": "7d",
    "Last 30 days": "30d",
}
DEFAULT_TIME_RANGE = "Last 7 days"

# Allow-listed result caps for the "top N logs" control.
LIMIT_OPTIONS: list[int] = [50, 100, 200, 500]
DEFAULT_LIMIT = 50


@dataclass
class TraceSpan:
    """A single telemetry span (agent invocation, model chat or tool call)."""

    timestamp: str = ""
    agent: str = ""
    span_type: str = ""
    name: str = ""
    operation: str = ""
    duration_ms: float = 0.0
    success: bool | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    operation_id: str = ""
    span_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0)


@dataclass
class TraceQuery:
    """Result of a trace-log query for the UI."""

    spans: list[TraceSpan] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    time_range: str = ""
    limit: int = DEFAULT_LIMIT
    total_spans: int = 0
    agent_invocations: int = 0
    tool_calls: int = 0
    model_calls: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    avg_duration_ms: float = 0.0
    duration_s: float = 0.0
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def _app_insights_context() -> tuple[str, str]:
    """Return ``(application_id, bearer_token)`` for the connected App Insights.

    Raises a ``RuntimeError`` (surfaced to the UI) when the project has no
    Application Insights connection or the token cannot be acquired.
    """
    from azure.identity import DefaultAzureCredential

    from .azure_clients import get_project_client

    require_settings()  # ensures project endpoint is configured
    client = get_project_client()
    telemetry = getattr(client, "telemetry", None)
    if telemetry is None:
        raise RuntimeError(
            "The installed azure-ai-projects SDK does not expose telemetry "
            "operations; cannot resolve the Application Insights resource."
        )
    conn = telemetry.get_application_insights_connection_string()
    if not conn:
        raise RuntimeError(
            "No Application Insights resource is connected to this Foundry "
            "project. Connect one in the project's *Tracing* settings so agent "
            "telemetry is captured."
        )
    parts = dict(kv.split("=", 1) for kv in conn.split(";") if "=" in kv)
    app_id = parts.get("ApplicationId")
    if not app_id:
        raise RuntimeError(
            "The Application Insights connection string did not include an "
            "ApplicationId, so trace logs cannot be queried."
        )
    token = DefaultAzureCredential().get_token(_AI_SCOPE).token
    return app_id, token


def _run_kql(
    app_id: str, token: str, kql: str, timeout: int = 60
) -> tuple[list[str], list[list[Any]]]:
    """Execute a KQL query against App Insights, returning ``(columns, rows)``."""
    body = json.dumps({"query": kql}).encode("utf-8")
    req = urllib.request.Request(
        _AI_API.format(app_id=app_id),
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:400]
        raise RuntimeError(
            f"Application Insights query failed (HTTP {exc.code}): {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach Application Insights: {exc.reason}"
        ) from exc

    tables = payload.get("tables") or []
    if not tables:
        return [], []
    table = tables[0]
    columns = [c["name"] for c in table.get("columns", [])]
    rows = table.get("rows", []) or []
    return columns, rows


def list_trace_agents(days: int = 30) -> list[tuple[str, int]]:
    """Return ``(agent_name, span_count)`` pairs that have recent telemetry.

    Sorted by count descending. Used to populate the agent selector with the
    agents that actually have trace logs in the window.
    """
    app_id, token = _app_insights_context()
    days = max(1, min(int(days), 90))
    kql = (
        "dependencies "
        f"| where timestamp > ago({days}d) "
        "| extend agent = tostring(customDimensions['gen_ai.agent.name']) "
        "| where isnotempty(agent) "
        "| summarize c = count() by agent "
        "| order by c desc"
    )
    cols, rows = _run_kql(app_id, token, kql)
    idx_agent = cols.index("agent") if "agent" in cols else 0
    idx_count = cols.index("c") if "c" in cols else 1
    out: list[tuple[str, int]] = []
    for row in rows:
        try:
            out.append((str(row[idx_agent]), int(row[idx_count])))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def _coerce_details(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _infer_span_type(span_type: str, name: str) -> str:
    """Classify a span as agent / tool / chat (model) / other.

    The span *name* prefix follows the gen-ai semantic conventions and is more
    reliable than the Foundry ``span_type`` attribute (which tags every span in
    an agent run as ``agent``), so name detection takes precedence.
    """
    lname = (name or "").lower()
    if lname.startswith("invoke_agent"):
        return "agent"
    if lname.startswith("execute_tool"):
        return "tool"
    if lname.startswith("chat") or lname.startswith("text_completion"):
        return "chat"
    return span_type or "span"


def fetch_traces(
    agents: list[str],
    time_range: str = DEFAULT_TIME_RANGE,
    limit: int = DEFAULT_LIMIT,
) -> TraceQuery:
    """Fetch the most recent trace spans for the selected ``agents``.

    All spans belonging to a run/conversation that *any* selected agent
    participated in are returned (so model ``chat`` and ``execute_tool`` child
    spans, which don't carry the agent name, are included), capped at ``limit``
    and ordered newest-first.
    """
    ago = TIME_RANGES.get(time_range, TIME_RANGES[DEFAULT_TIME_RANGE])
    limit = int(limit) if int(limit) in LIMIT_OPTIONS else DEFAULT_LIMIT
    result = TraceQuery(
        agents=list(agents), time_range=time_range, limit=limit
    )

    selected = [a for a in agents if a]
    if not selected:
        result.error = "Select at least one agent to load its trace logs."
        return result

    started = time.perf_counter()
    try:
        app_id, token = _app_insights_context()
        # Agent names are JSON-encoded into a KQL dynamic literal; the values
        # come from a controlled selector, and JSON quoting prevents injection.
        sel = json.dumps(selected)
        kql = (
            f"let sel = dynamic({sel});\n"
            "let ids = dependencies\n"
            f"  | where timestamp > ago({ago})\n"
            "  | where tostring(customDimensions['gen_ai.agent.name']) in (sel)\n"
            "  | distinct operation_Id;\n"
            "dependencies\n"
            f"| where timestamp > ago({ago})\n"
            "| where operation_Id in (ids)\n"
            f"| top {limit} by timestamp desc\n"
            "| project timestamp, operation_Id, id, name, duration, success,\n"
            "          span_type = tostring(customDimensions['span_type']),\n"
            "          agent = tostring(customDimensions['gen_ai.agent.name']),\n"
            "          operation = tostring(customDimensions['gen_ai.operation.name']),\n"
            "          in_tok = toint(customDimensions['gen_ai.usage.input_tokens']),\n"
            "          out_tok = toint(customDimensions['gen_ai.usage.output_tokens']),\n"
            "          customDimensions"
        )
        cols, rows = _run_kql(app_id, token, kql)
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        result.duration_s = round(time.perf_counter() - started, 2)
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    col = {name: i for i, name in enumerate(cols)}

    def get(row: list[Any], name: str, default: Any = None) -> Any:
        i = col.get(name)
        return row[i] if i is not None and i < len(row) else default

    spans: list[TraceSpan] = []
    for row in rows:
        details = _coerce_details(get(row, "customDimensions"))
        success_val = get(row, "success")
        if isinstance(success_val, str):
            success: bool | None = success_val.lower() == "true"
        elif isinstance(success_val, bool):
            success = success_val
        else:
            success = None
        spans.append(
            TraceSpan(
                timestamp=str(get(row, "timestamp", "") or ""),
                agent=str(get(row, "agent", "") or ""),
                span_type=_infer_span_type(
                    str(get(row, "span_type", "") or ""), str(get(row, "name", "") or "")
                ),
                name=str(get(row, "name", "") or ""),
                operation=str(get(row, "operation", "") or ""),
                duration_ms=float(get(row, "duration", 0) or 0),
                success=success,
                input_tokens=int(get(row, "in_tok", 0) or 0),
                output_tokens=int(get(row, "out_tok", 0) or 0),
                operation_id=str(get(row, "operation_Id", "") or ""),
                span_id=str(get(row, "id", "") or ""),
                details=details,
            )
        )

    # Child spans (chat / tool) don't carry the agent name; fill it down from
    # the agent span sharing the same operation_Id so every row is attributable.
    agent_by_trace: dict[str, str] = {}
    for span in spans:
        if span.agent and span.operation_id:
            agent_by_trace.setdefault(span.operation_id, span.agent)
    for span in spans:
        if not span.agent and span.operation_id in agent_by_trace:
            span.agent = agent_by_trace[span.operation_id]

    result.spans = spans
    result.total_spans = len(spans)
    result.agent_invocations = sum(1 for s in spans if s.span_type == "agent")
    result.tool_calls = sum(1 for s in spans if s.span_type == "tool")
    result.model_calls = sum(1 for s in spans if s.span_type == "chat")
    result.errors = sum(1 for s in spans if s.success is False)
    result.input_tokens = sum(s.input_tokens for s in spans)
    result.output_tokens = sum(s.output_tokens for s in spans)
    if spans:
        result.avg_duration_ms = round(
            sum(s.duration_ms for s in spans) / len(spans), 1
        )
    result.duration_s = round(time.perf_counter() - started, 2)
    return result
