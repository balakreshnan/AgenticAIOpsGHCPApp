"""Assert tab.

Runs the **ASSERT** framework (``assert_ai``) against the hosted Foundry
``rfpagent``. ASSERT takes a plain-English behaviour spec, generates a test set,
runs each case against the live agent, and uses an LLM judge to validate every
response. This tab exposes the spec controls and renders the generated taxonomy,
per-case agent responses, and judge verdicts so the user can validate the agent's
behaviour and catch regressions.

Authentication is DefaultAzureCredential only (no keys / service principals).
"""

from __future__ import annotations

import streamlit as st

from common import assert_eval as ae
from common.config import ConfigError, require_settings
from common.errors import CONTENT_FILTER_MARKER
from common.ui import config_error

_STATE_KEY = "assert_run"


def _verdict_badge(passed: bool) -> str:
    if passed:
        return ":green-badge[✓ Pass]"
    return ":red-badge[✗ Fail]"


def _render_dimension_cards(run: "ae.AssertRun") -> None:
    """Headline pass rate plus a failure-rate card per judged dimension."""
    rates = run.dimension_rates
    cards: list[tuple[str, str]] = [
        ("Pass rate", f"{run.pass_rate * 100:.0f}%"),
    ]
    for name, info in rates.items():
        label = name.replace("_", " ").title() + " rate"
        cards.append((label, f"{info['failure_rate'] * 100:.0f}%"))

    for start in range(0, len(cards), 4):
        chunk = cards[start : start + 4]
        cols = st.columns(len(chunk))
        for col, (label, value) in zip(cols, chunk):
            help_text = None
            key = label.replace(" rate", "").lower().replace(" ", "_")
            if key in ae.DIMENSION_HELP:
                help_text = ae.DIMENSION_HELP[key]
            col.metric(label, value, help=help_text)


def _render_records(run: "ae.AssertRun") -> None:
    st.markdown("#### Test cases & judge verdicts")
    st.caption(
        "Each case was generated from the behaviour spec, run against the live "
        "rfpagent, then scored by the judge. Expand a case to validate the "
        "agent's response."
    )
    for i, rec in enumerate(run.records, start=1):
        title = rec.title or rec.test_case_id or f"Case {i}"
        flags = [n for n, v in rec.dimensions.items() if v]
        summary = f"{_verdict_badge(rec.passed)} &nbsp; **{i}. {title}**"
        if rec.response.startswith(CONTENT_FILTER_MARKER):
            summary += " &nbsp; :orange-badge[🛡 content filtered]"
        if flags:
            summary += " &nbsp; — " + ", ".join(f.replace("_", " ") for f in flags)
        st.markdown(summary, unsafe_allow_html=True)
        with st.expander("Details", expanded=False):
            if rec.category:
                st.caption(f"Category: {rec.category}")
            st.markdown("**Prompt**")
            st.markdown(f"> {rec.prompt or '_(none)_'}")
            st.markdown("**Agent response**")
            if rec.response and rec.response.startswith(CONTENT_FILTER_MARKER):
                detail = rec.response[len(CONTENT_FILTER_MARKER):].strip()
                st.markdown(":orange-badge[🛡 Blocked by content safety filter]")
                st.info(
                    detail
                    or "The agent's safety system refused this prompt before "
                    "it reached the model."
                )
            elif rec.response:
                st.markdown(rec.response)
            else:
                st.caption("No response captured.")
            st.markdown("**Judge verdict**")
            if rec.judge_status and rec.judge_status != "ok":
                st.warning(f"Judge status: {rec.judge_status} — {rec.judge_error or ''}")
            if rec.dimensions:
                st.json(rec.dimensions, expanded=False)
            if rec.justification:
                st.markdown(rec.justification)


def _render_results(run: "ae.AssertRun") -> None:
    if run.error:
        st.error(f"**ASSERT run failed.**\n\n{run.error}")
        if run.log_tail:
            with st.expander("Pipeline log", expanded=False):
                st.code(run.log_tail)
        return

    meta = st.columns(4)
    meta[0].metric("Test cases", run.case_count)
    meta[1].metric("Passed", f"{run.pass_count}/{run.case_count}")
    meta[2].metric("Judge model", run.judge_model)
    meta[3].metric("Duration", f"{run.duration_s:.1f}s")

    st.markdown("#### Scorecard")
    _render_dimension_cards(run)

    if run.categories:
        with st.expander(
            f"🗂️ Behaviour taxonomy ({len(run.categories)} categories)",
            expanded=False,
        ):
            for cat in run.categories:
                st.markdown(f"**{cat['name']}**")
                st.caption(cat["definition"])

    if run.records:
        _render_records(run)

    tok = run.token_totals or {}
    if tok:
        with st.expander("🔢 Token usage & artifacts", expanded=False):
            tcols = st.columns(3)
            tcols[0].metric("Input tokens", f"{tok.get('input_tokens', 0):,}")
            tcols[1].metric("Output tokens", f"{tok.get('output_tokens', 0):,}")
            tcols[2].metric("Model calls", f"{tok.get('calls', 0):,}")
            st.caption(f"Artifacts: `{run.run_dir}`")


def render() -> None:
    try:
        settings = require_settings()
    except ConfigError as exc:
        config_error(str(exc))
        return

    info = st.columns([2, 2, 3])
    info[0].markdown(f"**Target agent**\n\n`{settings.assert_target_agent}`")
    info[1].markdown(f"**Judge model**\n\n`{settings.judge_model}`")
    info[2].markdown(
        "**Framework**\n\nASSERT — *Adaptive Spec-driven Scoring for Evaluation "
        "and Regression Testing*"
    )
    st.caption(
        "ASSERT generates a spec-driven test set, runs it against the live agent, "
        "and validates every response with an LLM judge. Authentication uses "
        "DefaultAzureCredential. Each test case calls the live agent, so keep "
        "sample sizes small."
    )

    with st.expander("⚙️ Behaviour specification", expanded=True):
        behavior_name = st.text_input(
            "Behaviour name",
            value=ae.DEFAULT_BEHAVIOR_NAME,
            key="assert_behavior_name",
        )
        behavior_description = st.text_area(
            "Behaviour description (what the agent should / shouldn't do)",
            value=ae.DEFAULT_BEHAVIOR_DESCRIPTION,
            height=200,
            key="assert_behavior_desc",
        )
        context = st.text_area(
            "Context (what the target is)",
            value=ae.DEFAULT_CONTEXT,
            height=110,
            key="assert_context",
        )
        sliders = st.columns(3)
        category_count = sliders[0].slider(
            "Behaviour categories", 1, 6, 3, key="assert_cat_count",
            help="How many sub-behaviours ASSERT derives from the spec.",
        )
        prompt_sample_size = sliders[1].slider(
            "Prompts per category", 1, 6, 2, key="assert_prompt_size",
            help="Single-turn test prompts generated per category.",
        )
        max_turns = sliders[2].slider(
            "Max turns", 1, 5, 3, key="assert_max_turns",
            help="Maximum conversation turns per test case.",
        )

    has_result = _STATE_KEY in st.session_state
    label = "🔄 Rerun ASSERT" if has_result else "▶ Run ASSERT"
    run_clicked = st.button(
        label,
        type="primary",
        key="assert_run_btn",
        disabled=not behavior_description.strip(),
    )

    if run_clicked:
        with st.spinner(
            "Running ASSERT pipeline — generating tests, querying the agent and "
            "judging responses. This can take several minutes."
        ):
            st.session_state[_STATE_KEY] = ae.run_assert(
                behavior_name=behavior_name.strip() or ae.DEFAULT_BEHAVIOR_NAME,
                behavior_description=behavior_description.strip(),
                context=context.strip() or ae.DEFAULT_CONTEXT,
                category_count=category_count,
                prompt_sample_size=prompt_sample_size,
                max_turns=max_turns,
            )

    run = st.session_state.get(_STATE_KEY)
    if run is None:
        st.info(
            "Configure the behaviour specification above and click **Run ASSERT** "
            "to generate a test set, run it against the rfpagent, and validate the "
            "responses with an LLM judge."
        )
        return
    _render_results(run)
