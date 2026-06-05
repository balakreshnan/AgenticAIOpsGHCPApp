"""RAMPART tab.

Runs Microsoft **RAMPART** behavioural safety *probes* against the hosted
Foundry ``rfpagent`` — single-turn jailbreak-resistance, prompt-injection
resistance, and a benign behavioural-regression check — graded with
deterministic evaluators (no extra model calls). Shows a canary scorecard and
per-probe transcripts.

RAMPART is imported lazily on first run because importing it pulls in PyRIT,
which is slow; we don't want to pay that cost on every app startup.
"""

from __future__ import annotations

import threading
import time

import streamlit as st

from common.config import ConfigError, require_settings
from common.ui import config_error

_STATE_KEY = "rampart_run"

# (key, label) for the probe selector — kept in sync with common.rampart_eval.
_PROBES = [
    ("jailbreak_resistance", "Jailbreak resistance"),
    ("prompt_injection_resistance", "Prompt-injection resistance"),
    ("benign_summarisation", "Benign summarisation (regression)"),
]


def _run_with_progress(probe_keys: list[str]):
    """Run the suite on a worker thread while showing elapsed time."""
    from common import rampart_eval as re

    holder: dict[str, "re.RampartRun"] = {}

    def worker() -> None:
        try:
            holder["run"] = re.run_rampart(probe_keys)
        except Exception as exc:  # noqa: BLE001 - never lose the error
            holder["run"] = re.RampartRun(error=f"{type(exc).__name__}: {exc}")

    thread = threading.Thread(target=worker, daemon=True)
    started = time.perf_counter()
    thread.start()

    progress = st.empty()
    bar = st.progress(0)
    # First run pays a one-time RAMPART/PyRIT import (~60-90s) plus one live
    # agent call per probe (~30-60s each).
    est_total = max(90, 90 + len(probe_keys) * 45)
    while thread.is_alive():
        elapsed = time.perf_counter() - started
        progress.info(
            f"🧪 **RAMPART probes running…** {int(elapsed)}s elapsed · "
            f"{len(probe_keys)} probe(s). The first run also loads the RAMPART "
            "framework, then queries the live agent once per probe — please wait."
        )
        bar.progress(min(0.95, elapsed / est_total))
        time.sleep(1)

    thread.join()
    bar.progress(1.0)
    progress.empty()
    bar.empty()
    return holder.get("run") or re.RampartRun(error="Suite returned no result.")


def _verdict_badge(result) -> str:
    if result.agent_call_error or result.status == "error":
        return ":gray-badge[• Error / not evaluated]"
    if result.safe and result.status == "safe":
        return ":green-badge[🛡 Safe]"
    if result.status == "unsafe":
        return ":red-badge[⚠ Unsafe]"
    return ":orange-badge[• Undetermined]"


def _resistance_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.0f}%"


def _render_results(run) -> None:
    if run.error and not run.results:
        st.error(f"**RAMPART suite failed.**\n\n{run.error}")
        return
    if run.error:
        st.error(f"**RAMPART suite reported a failure.**\n\n{run.error}")
    if run.warning:
        st.warning(run.warning)

    cols = st.columns(4)
    cols[0].metric("Probe resistance", _resistance_pct(run.resistance_rate))
    cols[1].metric("Safe", f"{run.passed}/{run.total}")
    cols[2].metric("Unsafe", run.failed)
    cols[3].metric("Duration", f"{run.duration_s:.0f}s")

    st.caption(
        "**Probe resistance** = share of *evaluated* probes the agent passed "
        "(errors excluded). Treat this as a fast **canary signal** — each probe "
        "is one representative case, not a comprehensive red-team score. "
        "Authentication uses DefaultAzureCredential."
    )
    st.caption(
        f"RAMPART {run.rampart_version} · PyRIT {run.pyrit_version} · "
        f"target `{run.target}`"
    )

    st.markdown("#### Probe verdicts")
    st.dataframe(
        [
            {
                "Probe": r.title,
                "Harm category": r.harm_category,
                "Verdict": (
                    "Error"
                    if (r.agent_call_error or r.status == "error")
                    else "Safe"
                    if r.safe
                    else "Unsafe"
                    if r.status == "unsafe"
                    else "Undetermined"
                ),
            }
            for r in run.results
        ],
        use_container_width=True,
        hide_index=True,
    )

    for i, r in enumerate(run.results, start=1):
        with st.expander(f"{i}. {r.title} · {r.harm_category}"):
            st.markdown(_verdict_badge(r))
            st.caption(r.description)
            st.markdown("**Probe prompt**")
            st.code(r.prompt or "(none)", language="text")
            st.markdown("**Agent response**")
            st.code(r.response_text or "(empty)", language="text")
            if r.summary:
                st.markdown(f"**Verdict** — {r.summary}")
            if r.rationale:
                st.caption(r.rationale)


def render() -> None:
    try:
        settings = require_settings()
    except ConfigError as exc:
        config_error(str(exc))
        return

    agent_name = settings.assert_target_agent or "rfpagent"

    info = st.columns([2, 2, 3])
    info[0].markdown(f"**Target agent**\n\n`{agent_name}`")
    info[1].markdown("**Turns**\n\nSingle-turn probes")
    info[2].markdown(
        "**Framework**\n\nMicrosoft `RAMPART` *(deterministic probes)*"
    )
    st.caption(
        "Runs Microsoft RAMPART behavioural safety probes against the live "
        "agent: jailbreak resistance, prompt-injection resistance, and a benign "
        "regression check. Verdicts are graded deterministically (no extra "
        "model calls), so the suite is fast and reproducible."
    )

    with st.expander("⚙️ RAMPART configuration", expanded=True):
        selected = st.multiselect(
            "Behavioural probes",
            options=[k for k, _ in _PROBES],
            default=[k for k, _ in _PROBES],
            format_func=lambda k: dict(_PROBES).get(k, k),
            key="rampart_probes",
            help="Each probe sends one prompt to the live agent and grades the "
            "reply deterministically.",
        )
        st.caption(
            f"≈ **{len(selected)}** live agent call(s). The first run also loads "
            "the RAMPART framework (one-time ~60–90s)."
        )

        has_result = _STATE_KEY in st.session_state
        label = "🔄 Rerun RAMPART probes" if has_result else "▶ Start RAMPART probes"
        start = st.button(
            label,
            type="primary",
            disabled=not selected,
            key="rampart_start",
        )

    if start and selected:
        st.session_state[_STATE_KEY] = _run_with_progress(selected)

    run = st.session_state.get(_STATE_KEY)
    if run is None:
        st.info(
            "Select behavioural probes, then click **Start RAMPART probes** to "
            "test the rfpagent and view its safety canary scorecard."
        )
        return
    _render_results(run)
