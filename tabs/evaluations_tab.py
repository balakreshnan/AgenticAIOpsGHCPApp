"""Evaluations tab.

Runs the **Azure AI Evaluation** SDK against the Foundry ``rfpagent`` agent's
responses (``evaldata/datarfp.jsonl``) across all applicable metrics — NLP,
AI-assisted quality and risk & safety — and renders a detailed breakdown with a
button to (re)run on demand.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from common import evaluation as ev
from common.config import ConfigError, require_settings
from common.ui import config_error, render_metric_cards

_STATE_KEY = "eval_run"


def _row_to_record(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten an evaluate() row into a compact table record."""
    record: dict[str, Any] = {}
    for key, value in row.items():
        if key.startswith("inputs."):
            short = key.split("inputs.", 1)[1]
            if short in ("query", "ground_truth"):
                record[short] = value
        elif key.startswith("outputs."):
            short = key.split("outputs.", 1)[1]
            # Keep only numeric scores in the table (skip reason strings).
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                record[short] = round(float(value), 3)
    return record


def _render_results(run: "ev.EvalRun") -> None:
    if run.error:
        st.error(f"**Evaluation failed.**\n\n{run.error}")

    if run.skipped:
        with st.expander(f"⚠️ Skipped evaluators ({len(run.skipped)})", expanded=False):
            for name, reason in run.skipped:
                st.markdown(f"- **{name}** — {reason}")

    if not run.metrics and not run.rows:
        return

    meta_cols = st.columns(4)
    meta_cols[0].metric("Rows", run.row_count)
    meta_cols[1].metric("Evaluators", len(run.evaluators))
    meta_cols[2].metric("Judge model", run.judge_model)
    meta_cols[3].metric("Duration", f"{run.duration_s:.1f}s")

    if run.studio_url:
        st.markdown(f"[🔗 View run in Azure AI Foundry]({run.studio_url})")

    st.markdown("#### Aggregate metrics")
    render_metric_cards(run.metrics)

    with st.expander("📊 All aggregate metrics", expanded=False):
        st.json(
            {k: round(v, 4) for k, v in sorted(run.metrics.items())
             if isinstance(v, (int, float)) and not isinstance(v, bool)},
            expanded=True,
        )

    if run.rows:
        st.markdown("#### Per-row results")
        records = [_row_to_record(r) for r in run.rows]
        st.dataframe(records, use_container_width=True, hide_index=False)

        with st.expander("🔍 Row detail (inputs, outputs & reasons)", expanded=False):
            for i, row in enumerate(run.rows, start=1):
                st.markdown(f"**Row {i}**")
                st.json(row, expanded=False)


def render() -> None:
    # ---- Configuration -----------------------------------------------------
    try:
        settings = require_settings()
    except ConfigError as exc:
        config_error(str(exc))
        return

    safety_ready = bool(settings.project_endpoint)

    # ---- Header / context --------------------------------------------------
    dataset_name = settings.eval_dataset_path.replace("\\", "/").split("/")[-1]
    info_cols = st.columns([2, 2, 3])
    info_cols[0].markdown(f"**Agent**\n\n`{ev.AGENT_NAME}`")
    info_cols[1].markdown(f"**Judge model**\n\n`{settings.judge_model}`")
    info_cols[2].markdown(f"**Dataset**\n\n`{dataset_name}`")

    st.caption(
        "Evaluates the responses captured for the **rfpagent** Foundry agent using "
        "the Azure AI Evaluation SDK. Authentication uses DefaultAzureCredential."
    )

    # ---- Controls ----------------------------------------------------------
    default_categories = list(ev.ALL_CATEGORIES)
    if not safety_ready:
        default_categories = [c for c in default_categories if c != ev.CATEGORY_SAFETY]

    ctrl_cols = st.columns([4, 1.4])
    with ctrl_cols[0]:
        categories = st.multiselect(
            "Metric categories",
            options=list(ev.ALL_CATEGORIES),
            default=default_categories,
            help="\n".join(f"{c}: {ev.CATEGORY_HELP[c]}" for c in ev.ALL_CATEGORIES),
        )
    with ctrl_cols[1]:
        st.write("")
        st.write("")
        has_result = _STATE_KEY in st.session_state
        label = "🔄 Rerun" if has_result else "▶ Run Evaluation"
        run_clicked = st.button(
            label, type="primary", use_container_width=True, disabled=not categories
        )

    if not safety_ready:
        st.caption(
            "ℹ️ Risk & Safety metrics require a configured AZURE_AI_PROJECT_ENDPOINT."
        )

    if run_clicked and categories:
        with st.spinner("Running Azure AI evaluations… this can take a minute."):
            st.session_state[_STATE_KEY] = ev.run_evaluation(tuple(categories))

    # ---- Results -----------------------------------------------------------
    run = st.session_state.get(_STATE_KEY)
    if run is None:
        st.info(
            "Select one or more metric categories and click **Run Evaluation** to "
            "score the rfpagent dataset across all available metrics."
        )
        return

    _render_results(run)

