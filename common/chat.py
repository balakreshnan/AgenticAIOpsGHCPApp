"""Conversation state helpers backed by ``st.session_state``."""

from __future__ import annotations

import time
from typing import Any

import streamlit as st

_HISTORY_KEY = "chat_history"
_LAST_RESULT_KEY = "last_result"
_SELECTED_AGENT_KEY = "selected_agent_id"


def init_state() -> None:
    """Ensure the chat-related session-state keys exist."""
    st.session_state.setdefault(_HISTORY_KEY, [])
    st.session_state.setdefault(_LAST_RESULT_KEY, None)
    st.session_state.setdefault(_SELECTED_AGENT_KEY, None)


def get_history() -> list[dict[str, Any]]:
    """Return the full stored conversation (role/content/ts dicts)."""
    return st.session_state.get(_HISTORY_KEY, [])


def history_for_run() -> list[dict[str, str]]:
    """Return history trimmed to role/content pairs for the agent call."""
    return [
        {"role": turn["role"], "content": turn["content"]}
        for turn in get_history()
    ]


def append_turn(role: str, content: str) -> None:
    """Append a message to the conversation history."""
    get_history().append({"role": role, "content": content, "ts": time.time()})


def set_last_result(result: Any) -> None:
    st.session_state[_LAST_RESULT_KEY] = result


def get_last_result() -> Any:
    return st.session_state.get(_LAST_RESULT_KEY)


def clear_conversation() -> None:
    """Reset the conversation and last result."""
    st.session_state[_HISTORY_KEY] = []
    st.session_state[_LAST_RESULT_KEY] = None


def get_selected_agent_id() -> str | None:
    return st.session_state.get(_SELECTED_AGENT_KEY)


def set_selected_agent_id(agent_id: str | None) -> None:
    st.session_state[_SELECTED_AGENT_KEY] = agent_id
