"""Red Team tab.

Runs a single-turn automated adversarial scan against the Foundry ``rfpagent``
using **Azure AI Evaluation**'s ``RedTeam`` (PyRIT-backed), shows live progress
with elapsed time, renders the attack-success scorecard and per-attack
transcripts, and uploads the results to the Foundry project for auditing.
"""

from __future__ import annotations

import math
import threading
import time

import streamlit as st

from common import redteam as rt
from common.config import ConfigError, require_settings
from common.ui import config_error

_STATE_KEY = "redteam_run"


def _asr_pct(value: float | None) -> str:
    """Render an attack-success-rate percentage (the SDK reports 0–100)."""
    if value is None:
        return "—"
    try:
        if math.isnan(value):
            return "—"
    except TypeError:
        return "—"
    return f"{value:.0f}%"


def _run_with_progress(
    cats: list[str], strats: list[str], num_objectives: int
) -> "rt.RedTeamRun":
    """Run the scan on a worker thread while showing elapsed time."""
    holder: dict[str, rt.RedTeamRun] = {}

    def worker() -> None:
        try:
            holder["run"] = rt.run_redteam(cats, strats, num_objectives)
        except Exception as exc:  # noqa: BLE001 - never lose the error
            holder["run"] = rt.RedTeamRun(
                error=f"{type(exc).__name__}: {exc}"
            )

    thread = threading.Thread(target=worker, daemon=True)
    started = time.perf_counter()
    thread.start()

    progress = st.empty()
    bar = st.progress(0)
    attacks = len(cats) * len(strats) * max(1, num_objectives)
    # Rough expectation: each attack calls the live agent (~30–60s) + grading.
    est_total = max(60, attacks * 60)
    while thread.is_alive():
        elapsed = time.perf_counter() - started
        progress.info(
            f"🛡️ **Red-team scan in progress…** {int(elapsed)}s elapsed "
            f"· {attacks} attack attempt(s). Each attempt queries the live "
            "agent and is graded by the Azure AI RAI service — please wait."
        )
        bar.progress(min(0.95, elapsed / est_total))
        time.sleep(1)

    thread.join()
    bar.progress(1.0)
    progress.empty()
    bar.empty()
    return holder.get("run") or rt.RedTeamRun(error="Scan returned no result.")


def _success_badge(success: bool | None) -> str:
    if success is True:
        return ":red-badge[⚠ Attack succeeded]"
    if success is False:
        return ":green-badge[🛡 Defended]"
    return ":gray-badge[• Inconclusive]"


def _render_results(run: "rt.RedTeamRun") -> None:
    if run.error:
        st.error(f"**Red-team scan failed.**\n\n{run.error}")
        if not run.rows:
            return

    if run.warning:
        st.warning(run.warning)

    cols = st.columns(4)
    cols[0].metric("Overall ASR", _asr_pct(run.overall_asr))
    cols[1].metric("Attack attempts", run.total_attacks)
    cols[2].metric("Successful attacks", run.successful_attacks)
    cols[3].metric("Duration", f"{run.duration_s:.0f}s")

    st.caption(
        "**ASR = Attack Success Rate** — the fraction of adversarial attempts "
        "that bypassed the agent's safety. **Lower is better.**"
    )

    if run.studio_url:
        st.markdown(
            f"[🔗 View this scan in Azure AI Foundry]({run.studio_url}) — "
            "uploaded for auditing."
        )
    else:
        st.caption(
            "ℹ️ Results were uploaded to the Foundry project for auditing "
            "(open the project's *Evaluations → Red teaming* view)."
        )

    if run.category_asr:
        st.markdown("#### Attack success by risk category")
        cat_cols = st.columns(max(1, len(run.category_asr)))
        for col, (label, val) in zip(cat_cols, sorted(run.category_asr.items())):
            col.metric(label, _asr_pct(val))

    if run.technique_asr:
        st.markdown("#### Attack success by technique / complexity")
        tech_cols = st.columns(max(1, len(run.technique_asr)))
        for col, (label, val) in zip(tech_cols, sorted(run.technique_asr.items())):
            col.metric(label, _asr_pct(val))

    if run.rows:
        st.markdown("#### Attack transcripts")
        st.dataframe(
            [
                {
                    "Risk category": r.risk_category,
                    "Technique": r.attack_technique,
                    "Complexity": r.attack_complexity,
                    "Outcome": (
                        "Attack succeeded"
                        if r.attack_success
                        else "Defended"
                        if r.attack_success is False
                        else "Inconclusive"
                    ),
                }
                for r in run.rows
            ],
            use_container_width=True,
            hide_index=True,
        )
        for i, r in enumerate(run.rows, start=1):
            header = (
                f"{i}. {r.attack_technique or 'attack'} · "
                f"{r.risk_category or 'risk'}"
            )
            with st.expander(header):
                st.markdown(_success_badge(r.attack_success))
                st.markdown("**Adversarial prompt**")
                st.code(r.attack_prompt or "(not captured)", language="text")
                st.markdown("**Agent response**")
                st.code(r.response or "(empty)", language="text")


def render() -> None:
    try:
        settings = require_settings()
    except ConfigError as exc:
        config_error(str(exc))
        return

    agent_name = settings.assert_target_agent or rt.AGENT_NAME

    info = st.columns([2, 2, 3])
    info[0].markdown(f"**Target agent**\n\n`{agent_name}`")
    info[1].markdown("**Turns**\n\nSingle-turn")
    info[2].markdown(
        "**Framework**\n\nAzure AI Evaluation · `RedTeam` *(PyRIT, preview)*"
    )
    st.caption(
        "Probes the live agent with a few simple single-turn attack strategies "
        "across selected risk categories, scores the attack-success rate, and "
        "uploads the scan to the Foundry project for auditing. Authentication "
        "uses DefaultAzureCredential."
    )

    with st.expander("⚙️ Red-team configuration", expanded=True):
        cfg = st.columns([3, 3, 2])
        with cfg[0]:
            categories = st.multiselect(
                "Risk categories",
                options=list(rt.RISK_CATEGORIES.keys()),
                default=list(rt.DEFAULT_RISK_CATEGORIES),
                key="rt_categories",
                help="Content-safety risk areas the attacks target.",
            )
        with cfg[1]:
            strategies = st.multiselect(
                "Attack strategies (single-turn)",
                options=list(rt.ATTACK_STRATEGIES.keys()),
                default=list(rt.DEFAULT_STRATEGIES),
                key="rt_strategies",
                help="Simple obfuscation strategies. Multi-turn is intentionally "
                "excluded to keep the scan to one turn.",
            )
        with cfg[2]:
            num_objectives = st.slider(
                "Objectives / category",
                min_value=1,
                max_value=3,
                value=1,
                key="rt_num_objectives",
                help="Number of distinct attack objectives per risk category. "
                "Keep small — each one calls the live agent.",
            )

        attacks = len(categories) * len(strategies) * num_objectives
        st.caption(
            f"≈ **{attacks}** attack attempt(s) will be sent to the live agent "
            "(roughly 30–60s each plus grading)."
        )

        has_result = _STATE_KEY in st.session_state
        label = "🔄 Rerun red-team scan" if has_result else "▶ Start red-team scan"
        start = st.button(
            label,
            type="primary",
            disabled=not (categories and strategies),
            key="rt_start",
        )

    if start and categories and strategies:
        st.session_state[_STATE_KEY] = _run_with_progress(
            categories, strategies, num_objectives
        )

    run = st.session_state.get(_STATE_KEY)
    if run is None:
        st.info(
            "Select risk categories and attack strategies, then click "
            "**Start red-team scan** to probe the rfpagent and view its "
            "attack-success scorecard."
        )
        return
    _render_results(run)
