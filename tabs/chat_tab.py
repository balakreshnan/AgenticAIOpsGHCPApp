"""Chat tab: select a Foundry agent and converse with it.

Layout:
* Top row — agent selector, refresh, clear, and a connection status chip.
* Two ``gap="medium"`` containers (height 500) — left scrollable history, right
  expanders for agent output / token usage / debug.
* ``st.chat_input`` at the bottom for the user's question.
"""

from __future__ import annotations

import streamlit as st

from common import chat
from common.agents import AgentInfo, list_foundry_agents, run_agent
from common.config import ConfigError
from common.ui import config_error, render_agent_panel, render_history, status_chip


@st.cache_data(show_spinner=False, ttl=300)
def _load_agents() -> list[dict]:
    """Cache the agent list (as plain dicts) for a few minutes."""
    return [a.__dict__ for a in list_foundry_agents()]


def _agent_from_dict(d: dict) -> AgentInfo:
    return AgentInfo(**d)


def render() -> None:
    chat.init_state()

    # ---- Load agents (live Azure) -----------------------------------------
    agents: list[AgentInfo] = []
    load_error: str | None = None
    try:
        agents = [_agent_from_dict(d) for d in _load_agents()]
    except ConfigError as exc:
        load_error = str(exc)
    except Exception as exc:  # auth / network / SDK errors
        load_error = f"{type(exc).__name__}: {exc}"

    # ---- Top control row ---------------------------------------------------
    sel_col, refresh_col, clear_col, status_col = st.columns([6, 1.4, 1.4, 2.2])

    selected: AgentInfo | None = None
    with sel_col:
        if agents:
            labels = {a.label: a for a in agents}
            current_id = chat.get_selected_agent_id()
            default_index = next(
                (i for i, a in enumerate(agents) if a.id == current_id), 0
            )
            choice = st.selectbox(
                "Agent",
                options=list(labels.keys()),
                index=default_index,
                label_visibility="collapsed",
            )
            selected = labels[choice]
            chat.set_selected_agent_id(selected.id)
        else:
            st.selectbox(
                "Agent",
                options=["No agents available"],
                disabled=True,
                label_visibility="collapsed",
            )

    with refresh_col:
        if st.button("🔄 Refresh", use_container_width=True):
            _load_agents.clear()
            st.rerun()

    with clear_col:
        if st.button("🗑️ Clear", use_container_width=True):
            chat.clear_conversation()
            st.rerun()

    with status_col:
        if load_error:
            status_chip(False, "Disconnected")
        else:
            status_chip(True, f"{len(agents)} agent(s)")

    if load_error:
        config_error(load_error)
        return

    # ---- Two-container body ------------------------------------------------
    left, right = st.columns([1.45, 1], gap="medium")

    with left:
        history_box = st.container(height=500)
        with history_box:
            render_history(chat.get_history())

    with right:
        panel_box = st.container(height=500)
        with panel_box:
            render_agent_panel(chat.get_last_result())

    # ---- Chat input --------------------------------------------------------
    question = st.chat_input(
        "Ask the selected agent a question…",
        disabled=selected is None,
    )
    if question and selected is not None:
        chat.append_turn("user", question)
        history = chat.history_for_run()[:-1]  # exclude the just-added question
        with st.spinner(f"Asking {selected.name}…"):
            result = run_agent(selected, history, question)
        chat.set_last_result(result)
        if result.error:
            chat.append_turn("assistant", f"⚠️ {result.error}")
        else:
            chat.append_turn("assistant", result.text or "(no response)")
        st.rerun()
