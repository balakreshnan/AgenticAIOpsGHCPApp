"""Reusable Streamlit UI components shared across tabs."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Sequence

import streamlit as st


def app_header() -> None:
    """Render the branded application header."""
    st.markdown(
        """
        <div class="aiops-header">
            <div class="logo">AI</div>
            <div>
                <h1>Agentic AIOps Studio</h1>
                <p>Microsoft Foundry agents &middot; Agent Framework orchestration</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_chip(ok: bool, text: str) -> None:
    """Render a small status pill (green/red)."""
    cls = "ok" if ok else "err"
    st.markdown(
        f'<span class="status-chip {cls}"><span class="dot"></span>{html.escape(text)}</span>',
        unsafe_allow_html=True,
    )


def _bubble(role: str, content: str, ts: float | None) -> str:
    side = "user" if role == "user" else "assistant"
    who = "You" if role == "user" else "Agent"
    when = ""
    if ts:
        when = datetime.fromtimestamp(ts).strftime("%H:%M")
    meta = f"{who} &middot; {when}" if when else who
    safe = html.escape(content)
    return (
        f'<div class="chat-row {side}"><div>'
        f'<div class="chat-meta">{meta}</div>'
        f'<div class="chat-bubble">{safe}</div>'
        f"</div></div>"
    )


def render_history(messages: Sequence[dict[str, Any]]) -> None:
    """Render the conversation history as chat bubbles."""
    if not messages:
        st.markdown(
            '<div class="placeholder" style="padding:1.6rem 1rem;">'
            "Start the conversation by asking a question below.</div>",
            unsafe_allow_html=True,
        )
        return
    html_parts = [
        _bubble(m["role"], m["content"], m.get("ts")) for m in messages
    ]
    st.markdown("".join(html_parts), unsafe_allow_html=True)


def _usage_pills(usage: dict[str, Any]) -> None:
    if not usage:
        st.caption("No token usage reported for this turn.")
        return
    pills = "".join(
        f'<div class="usage-pill"><div class="label">{label}</div>'
        f'<div class="value">{usage.get(key, 0) or 0:,}</div></div>'
        for label, key in (("Input", "input"), ("Output", "output"), ("Total", "total"))
    )
    st.markdown(f'<div class="usage-grid">{pills}</div>', unsafe_allow_html=True)


def render_agent_panel(result: Any) -> None:
    """Render the right-side expanders: agent output, token usage, debug."""
    if result is None:
        st.markdown(
            '<div class="placeholder" style="padding:1.6rem 1rem;">'
            "Agent output, token usage and debug details will appear here.</div>",
            unsafe_allow_html=True,
        )
        return

    if getattr(result, "error", None):
        with st.expander("⚠️ Agent Error", expanded=True):
            st.error(result.error)

    with st.expander("🧠 Agent Output", expanded=True):
        if getattr(result, "text", ""):
            st.markdown(result.text)
        else:
            st.caption("No textual output.")

    with st.expander("🔢 Token Usage", expanded=True):
        _usage_pills(getattr(result, "usage", {}) or {})

    with st.expander("🐛 Debug Information", expanded=False):
        st.json(getattr(result, "debug", {}) or {})


def placeholder(title: str, description: str, features: Sequence[str]) -> None:
    """Render a scaffolded placeholder panel for not-yet-built tabs."""
    items = "".join(f"<li>{html.escape(f)}</li>" for f in features)
    st.markdown(
        f"""
        <div class="placeholder">
            <span class="pill">Coming soon</span>
            <h3>{html.escape(title)}</h3>
            <p>{html.escape(description)}</p>
            <ul style="text-align:left; display:inline-block; margin-top:0.4rem;">
                {items}
            </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )


def config_error(message: str) -> None:
    """Render a friendly configuration / connection error panel."""
    st.error(
        "**Azure connection not available.**\n\n"
        f"{message}\n\n"
        "Set the required values in your `.env` file (see `.env.example`) and "
        "ensure you are signed in (`az login`) with access to the Foundry project."
    )


def _pretty_metric(key: str) -> str:
    """Turn an ``evaluator.metric`` key into a readable label."""
    name = key.split(".", 1)[-1] if "." in key else key
    return name.replace("_", " ").title()


def _primary_metrics(metrics: dict[str, Any]) -> dict[str, float]:
    """Pick one headline numeric score per evaluator.

    Prefers ``evaluator.evaluator`` (e.g. ``relevance.relevance``); when that is
    non-numeric (safety evaluators report a label there) falls back to
    ``evaluator.evaluator_score``.
    """
    def _num(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    primary: dict[str, float] = {}
    prefixes = {k.split(".", 1)[0] for k in metrics if "." in k}
    for prefix in prefixes:
        canonical = metrics.get(f"{prefix}.{prefix}")
        score = metrics.get(f"{prefix}.{prefix}_score")
        if _num(canonical):
            primary[prefix] = float(canonical)
        elif _num(score):
            primary[prefix] = float(score)
    return primary


def render_metric_cards(metrics: dict[str, Any], per_row: int = 4) -> None:
    """Render one headline score per evaluator as a grid of cards."""
    primary = _primary_metrics(metrics)
    if not primary:
        primary = {
            k: float(v)
            for k, v in metrics.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
    if not primary:
        st.caption("No numeric aggregate metrics were produced.")
        return
    items = sorted(primary.items())
    for start in range(0, len(items), per_row):
        chunk = items[start : start + per_row]
        cols = st.columns(len(chunk))
        for col, (key, value) in zip(cols, chunk):
            with col:
                st.metric(_pretty_metric(key), f"{value:.3f}")

