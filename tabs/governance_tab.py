"""Governance tab.

Validates the hosted Foundry ``rfpagent`` using Microsoft's **Agent Governance
Toolkit** (``agent-governance-toolkit``). Surfaces a toolkit governance
attestation, a declared zero-trust identity, a static prompt-defense scan over
the agent's real system instructions, a local capability/policy simulation, and
an optional live response-resistance smoke test.

Authentication uses DefaultAzureCredential only.
"""

from __future__ import annotations

import streamlit as st

from common import governance as gov
from common.agents import AgentDefinition, get_agent_instructions
from common.config import ConfigError, require_settings
from common.ui import config_error

_STATE_KEY = "governance_report"
_STATE_RESIST = "governance_resistance"
_STATE_PROMPT = "governance_prompt_text"

_GRADE_COLORS = {"A": "green", "B": "green", "C": "orange", "D": "red", "F": "red"}


@st.cache_data(ttl=300, show_spinner=False)
def _load_instructions(agent_name: str) -> dict:
    """Cache the live agent definition to avoid a Foundry call on every rerun."""
    defn = get_agent_instructions(agent_name)
    return {
        "name": defn.name,
        "instructions": defn.instructions,
        "model": defn.model,
        "version": defn.version,
    }


def _grade_badge(grade: str) -> str:
    color = _GRADE_COLORS.get((grade or "").upper(), "gray")
    return f":{color}-badge[Grade {grade or '—'}]"


def _render_attestation(att: "gov.AttestationResult") -> None:
    st.markdown("#### 🏛️ Toolkit governance attestation")
    st.caption(
        "Toolkit-level OWASP-ASI 2026 control coverage from the Agent Governance "
        "Toolkit. This attests the governance *stack* posture, not rfpagent itself."
    )
    if att.error:
        st.warning(f"Attestation unavailable: {att.error}")
        return
    cols = st.columns(4)
    cols[0].metric("Grade", att.grade)
    cols[1].metric("OWASP coverage", f"{att.coverage_pct:.0f}%")
    cols[2].metric("Controls passed", f"{att.controls_passed}/{att.controls_total}")
    cols[3].metric("Toolkit", att.toolkit_version or "—")
    if att.summary:
        with st.expander("Attestation summary", expanded=False):
            st.code(att.summary)


def _render_identity(ident: "gov.IdentityResult") -> None:
    st.markdown("#### 🪪 Declared agent identity")
    st.caption(
        "A self-asserted zero-trust identity (DID) minted via AgentMesh with a "
        "human sponsor and capability grants. Declared identity — not an external "
        "trust-fabric registration."
    )
    if ident.error:
        st.warning(f"Identity unavailable: {ident.error}")
        return
    cols = st.columns([3, 1])
    with cols[0]:
        st.markdown(f"**DID** &nbsp; `{ident.did or '—'}`", unsafe_allow_html=True)
        st.markdown(f"**Sponsor** &nbsp; `{ident.sponsor or '—'}`", unsafe_allow_html=True)
    with cols[1]:
        st.metric("Status", (ident.status or "—").title())
    if ident.capabilities:
        st.markdown(
            "**Capabilities** &nbsp; "
            + " ".join(f":blue-badge[{c}]" for c in ident.capabilities)
        )


def _render_prompt_defense(pd: "gov.PromptDefenseResult") -> None:
    st.markdown("#### 🛡️ Static prompt-defense coverage")
    st.caption(
        "Deterministic static analysis of the agent's real system prompt against "
        "12 OWASP-mapped attack vectors. Checks for defensive language — it does "
        "not test runtime behaviour (see the resistance smoke test below)."
    )
    if pd.error:
        st.warning(f"Prompt-defense scan unavailable: {pd.error}")
        return

    cols = st.columns(4)
    cols[0].metric("Grade", pd.grade)
    cols[1].metric("Vectors defended", f"{pd.defended}/{pd.total}")
    cols[2].metric("Score", pd.score)
    cols[3].metric("Prompt source", pd.prompt_source, help=f"{pd.prompt_chars:,} chars")

    st.markdown("**Per-vector findings** " + _grade_badge(pd.grade))
    records = [
        {
            "Defended": "✅" if f.defended else "❌",
            "Vector": f.name,
            "OWASP": f.owasp,
            "Severity": f.severity,
            "Confidence": round(f.confidence, 2),
            "Evidence": f.evidence,
        }
        for f in pd.findings
    ]
    st.dataframe(records, use_container_width=True, hide_index=True)
    if pd.missing:
        st.markdown(
            "**Missing defenses** &nbsp; "
            + " ".join(f":red-badge[{m}]" for m in pd.missing)
        )


def _render_policy(policy: "gov.PolicyResult") -> None:
    st.markdown("#### ⚖️ Local policy simulation")
    st.caption(
        "Declared-capability allow/deny checks for sample actions "
        "(AgentIdentity.has_capability). Local simulation — it does not enforce "
        "runtime authorization on the hosted agent."
    )
    if policy.error:
        st.warning(f"Policy simulation unavailable: {policy.error}")
        return
    cols = st.columns(2)
    cols[0].metric("Allowed", policy.allowed_count)
    cols[1].metric("Denied", policy.denied_count)
    records = [
        {
            "Decision": "🟢 Allow" if c.allowed else "🔴 Deny",
            "Action": c.action,
            "Capability": c.capability,
        }
        for c in policy.checks
    ]
    st.dataframe(records, use_container_width=True, hide_index=True)


def _verdict_badge(verdict: str) -> str:
    mapping = {
        "resisted": ":green-badge[✓ Resisted]",
        "failed": ":red-badge[✗ Failed]",
        "review": ":orange-badge[⚠ Review]",
        "error": ":gray-badge[Error]",
    }
    return mapping.get(verdict, verdict)


def _render_resistance(res: "gov.ResistanceResult") -> None:
    if res.error:
        st.error(f"**Resistance test failed.**\n\n{res.error}")
        return
    cols = st.columns(3)
    cols[0].metric("Probes", len(res.probes))
    cols[1].metric("Resisted", res.resisted_count)
    cols[2].metric("Failed", res.failed_count)
    st.caption(
        "Heuristic verdicts (refusal / leak detection). **Review** means no clear "
        "signal — read the response and judge manually."
    )
    for i, p in enumerate(res.probes, start=1):
        st.markdown(
            f"{_verdict_badge(p.verdict)} &nbsp; **{i}. {p.name}** &nbsp; "
            f"<span style='opacity:0.7'>· {p.category} · {p.latency_ms} ms</span>",
            unsafe_allow_html=True,
        )
        with st.expander("Prompt, response & verdict", expanded=False):
            st.markdown("**Adversarial prompt**")
            st.markdown(f"> {p.prompt}")
            st.markdown("**Agent response**")
            if p.error:
                st.error(p.error)
            elif p.response:
                st.markdown(p.response)
            else:
                st.caption("Empty response.")
            st.markdown(f"**Verdict** — {p.detail}")


def render() -> None:
    try:
        settings = require_settings()
    except ConfigError as exc:
        config_error(str(exc))
        return

    agent_name = settings.assert_target_agent

    info = st.columns([2, 2, 3])
    info[0].markdown(f"**Target agent**\n\n`{agent_name}`")
    info[1].markdown(f"**Judge model**\n\n`{settings.judge_model}`")
    info[2].markdown(
        "**Framework**\n\nMicrosoft Agent Governance Toolkit "
        "*(agent-governance-toolkit)*"
    )
    st.caption(
        "Validates the hosted agent's governance posture: toolkit attestation, "
        "zero-trust identity, static prompt-defense coverage over the agent's real "
        "system prompt, and a local policy simulation. Authentication uses "
        "DefaultAzureCredential."
    )

    # ---- Configuration -----------------------------------------------------
    with st.expander("⚙️ Governance configuration", expanded=True):
        sponsor = st.text_input(
            "Human sponsor email",
            value=settings.governance_sponsor_email,
            key="gov_sponsor",
        )
        capabilities = st.multiselect(
            "Granted capabilities",
            options=gov.DEFAULT_CAPABILITIES
            + ["act:send_email", "admin:delete_database", "exfiltrate:confidential_data"],
            default=gov.DEFAULT_CAPABILITIES,
            key="gov_caps",
            help="Capabilities granted to the agent's declared identity.",
        )

        load_cols = st.columns([1.4, 3])
        with load_cols[0]:
            if st.button("📥 Load live prompt", key="gov_load_prompt"):
                try:
                    with st.spinner("Fetching agent instructions…"):
                        defn = _load_instructions(agent_name)
                    st.session_state[_STATE_PROMPT] = defn["instructions"]
                    st.toast(f"Loaded {len(defn['instructions']):,} chars from Foundry.")
                except Exception as exc:  # noqa: BLE001
                    st.warning(f"Could not load instructions: {exc}")
        with load_cols[1]:
            st.caption(
                "Loads the agent's real system prompt for the defense scan. Leave "
                "the override empty to fetch it automatically at run time."
            )

        prompt_override = st.text_area(
            "System prompt override (optional)",
            value=st.session_state.get(_STATE_PROMPT, ""),
            height=160,
            key="gov_prompt_override",
            help="If empty, the live Foundry instructions are scanned automatically.",
        )

    run_clicked = st.button(
        "🔄 Rerun governance checks" if _STATE_KEY in st.session_state
        else "▶ Run governance checks",
        type="primary",
        key="gov_run",
    )

    if run_clicked:
        with st.spinner("Running governance checks…"):
            st.session_state[_STATE_KEY] = gov.run_governance(
                agent_name=agent_name,
                sponsor_email=sponsor.strip() or settings.governance_sponsor_email,
                capabilities=capabilities,
                prompt_override=prompt_override.strip() or None,
            )

    report = st.session_state.get(_STATE_KEY)
    if report is None:
        st.info(
            "Click **Run governance checks** to validate the rfpagent against the "
            "Agent Governance Toolkit."
        )
    else:
        meta = st.columns(3)
        meta[0].metric("Agent model", report.agent_model or "—")
        meta[1].metric("Agent version", report.agent_version or "—")
        meta[2].metric("Checks duration", f"{report.duration_s:.2f}s")
        _render_attestation(report.attestation)
        _render_identity(report.identity)
        _render_prompt_defense(report.prompt_defense)
        _render_policy(report.policy)

    # ---- Optional live runtime probe --------------------------------------
    st.divider()
    st.markdown("#### 🎯 Response-resistance smoke test (live)")
    st.caption(
        "Sends adversarial prompts to the **live** rfpagent and applies a "
        "refusal/leak heuristic to validate runtime behaviour. Each prompt calls "
        "the real agent, so this takes longer."
    )
    resist_clicked = st.button(
        "🔄 Rerun resistance test" if _STATE_RESIST in st.session_state
        else "🎯 Run resistance test",
        key="gov_resist_run",
    )
    if resist_clicked:
        with st.spinner(
            "Probing the live agent with adversarial prompts… this can take a "
            "few minutes."
        ):
            st.session_state[_STATE_RESIST] = gov.run_resistance_test(
                agent_name=agent_name
            )
    resistance = st.session_state.get(_STATE_RESIST)
    if resistance is not None:
        _render_resistance(resistance)
