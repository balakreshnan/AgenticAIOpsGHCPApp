"""Tracing tab.

Lets the user pick one or more agents and inspect their recent OpenTelemetry
trace logs (the last hour … 30 days, top *N* spans) pulled from the Application
Insights resource connected to the Foundry project. Authentication uses
DefaultAzureCredential.
"""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from common import tracing as tr
from common.config import ConfigError, require_settings
from common.ui import config_error

_STATE_KEY = "tracing_result"


@st.cache_data(show_spinner=False, ttl=300)
def _discover_agents(days: int) -> list[tuple[str, int]]:
    """Cached discovery of agents that have telemetry in the window."""
    return tr.list_trace_agents(days=days)


_TYPE_BADGE = {
    "agent": ":blue-badge[agent]",
    "chat": ":violet-badge[model]",
    "tool": ":orange-badge[tool]",
}


def _fmt_ts(ts: str) -> str:
    """Render an ISO timestamp compactly in UTC."""
    if not ts:
        return ""
    try:
        cleaned = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned).astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts[:19].replace("T", " ")


def _short_id(value: str) -> str:
    return (value or "")[:8]


def _render_results(result: "tr.TraceQuery") -> None:
    if result.error:
        st.error(f"**Could not load traces.**\n\n{result.error}")
        if not result.spans:
            return

    if not result.spans:
        st.info(
            "No trace spans were found for the selected agents in this window. "
            "Run the agent (e.g. from the Chat tab) to generate telemetry, then "
            "reload — ingestion can lag a minute or two."
        )
        return

    cols = st.columns(5)
    cols[0].metric("Spans", result.total_spans)
    cols[1].metric("Agent runs", result.agent_invocations)
    cols[2].metric("Tool calls", result.tool_calls)
    cols[3].metric("Errors", result.errors)
    cols[4].metric("Total tokens", f"{result.total_tokens:,}")

    sub = st.columns(3)
    sub[0].metric("Model calls", result.model_calls)
    sub[1].metric("Avg span latency", f"{result.avg_duration_ms:,.0f} ms")
    sub[2].metric("Query time", f"{result.duration_s:.1f}s")
    st.caption(
        f"Showing the top {result.limit} spans · {result.time_range.lower()} · "
        f"{result.input_tokens:,} input / {result.output_tokens:,} output tokens."
    )

    flat_tab, trace_tab = st.tabs(["📜 Log table", "🧵 By conversation"])

    with flat_tab:
        st.dataframe(
            [
                {
                    "Time (UTC)": _fmt_ts(s.timestamp),
                    "Agent": s.agent,
                    "Type": s.span_type,
                    "Name": s.name,
                    "Duration (ms)": round(s.duration_ms, 1),
                    "OK": "✓" if s.success else ("✗" if s.success is False else "—"),
                    "In tok": s.input_tokens or "",
                    "Out tok": s.output_tokens or "",
                    "Trace": _short_id(s.operation_id),
                }
                for s in result.spans
            ],
            use_container_width=True,
            hide_index=True,
            height=380,
        )

    with trace_tab:
        # Group spans by conversation (operation_Id), newest trace first.
        order: list[str] = []
        groups: dict[str, list[tr.TraceSpan]] = {}
        for span in result.spans:
            key = span.operation_id or span.span_id
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(span)

        st.caption(f"{len(order)} conversation(s) in this result set.")
        for key in order:
            spans = groups[key]
            agent = next((s.agent for s in spans if s.agent), "unknown")
            newest = _fmt_ts(spans[0].timestamp)
            toks = sum(s.total_tokens for s in spans)
            errs = sum(1 for s in spans if s.success is False)
            flag = " · ⚠ errors" if errs else ""
            header = (
                f"{newest} · {agent} · {len(spans)} span(s) · "
                f"{toks:,} tok · trace {_short_id(key)}{flag}"
            )
            with st.expander(header):
                for s in sorted(spans, key=lambda x: x.timestamp):
                    badge = _TYPE_BADGE.get(s.span_type, f":gray-badge[{s.span_type}]")
                    status = (
                        "✓" if s.success else ("✗" if s.success is False else "—")
                    )
                    line = (
                        f"{badge} **{s.name}** · {s.duration_ms:,.0f} ms · {status}"
                    )
                    if s.total_tokens:
                        line += (
                            f" · {s.input_tokens:,}→{s.output_tokens:,} tok"
                        )
                    st.markdown(f"`{_fmt_ts(s.timestamp)}`  {line}")
                    if s.details:
                        with st.popover("attributes"):
                            st.json(s.details)


def render() -> None:
    try:
        settings = require_settings()
    except ConfigError as exc:
        config_error(str(exc))
        return

    st.caption(
        "Inspect OpenTelemetry trace logs for your Foundry agents — agent runs, "
        "model calls and tool calls — read live from the project's connected "
        "Application Insights. Pick agents, a time window and how many spans to "
        "pull. Authentication uses DefaultAzureCredential."
    )

    # ---- Controls ---------------------------------------------------------
    ctrl = st.columns([4, 2, 1.6, 1.4])
    with ctrl[0]:
        try:
            discovered = _discover_agents(30)
        except ConfigError as exc:
            config_error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not list agents with telemetry: {exc}")
            discovered = []

        names = [name for name, _ in discovered]
        counts = {name: c for name, c in discovered}
        if not names:
            st.warning(
                "No agents have emitted telemetry in the last 30 days. Run an "
                "agent (e.g. from the Chat tab) and try again."
            )
        default = (
            [settings.assert_target_agent]
            if settings.assert_target_agent in names
            else names[:1]
        )
        agents = st.multiselect(
            "Agents",
            options=names,
            default=default,
            format_func=lambda n: f"{n} ({counts.get(n, 0)} spans)",
            key="trace_agents",
            help="Agents discovered from the last 30 days of telemetry.",
        )
    with ctrl[1]:
        time_range = st.selectbox(
            "Time window",
            options=list(tr.TIME_RANGES.keys()),
            index=list(tr.TIME_RANGES.keys()).index(tr.DEFAULT_TIME_RANGE),
            key="trace_range",
        )
    with ctrl[2]:
        limit = st.selectbox(
            "Max spans",
            options=tr.LIMIT_OPTIONS,
            index=tr.LIMIT_OPTIONS.index(tr.DEFAULT_LIMIT),
            key="trace_limit",
            help="Top N most-recent spans to pull.",
        )
    with ctrl[3]:
        st.write("")
        st.write("")
        load = st.button(
            "🔄 Load logs",
            type="primary",
            use_container_width=True,
            disabled=not agents,
            key="trace_load",
        )

    if load and agents:
        with st.spinner("Querying Application Insights…"):
            st.session_state[_STATE_KEY] = tr.fetch_traces(
                agents, time_range, limit
            )

    result = st.session_state.get(_STATE_KEY)
    if result is None:
        st.info(
            "Select one or more agents and click **Load logs** to view their "
            "recent trace spans (agent runs, model calls and tool calls)."
        )
        return
    _render_results(result)
