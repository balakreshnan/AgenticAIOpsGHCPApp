"""ASSERT callable target — bridges the Foundry ``rfpagent`` to ASSERT.

The **ASSERT** framework (``assert_ai``) drives an evaluation by repeatedly
calling a plain Python *callable target* with a user message (and optional
conversation history) and judging whatever the callable returns.

This module exposes :func:`chat_sync`, the integration boundary ASSERT imports
via ``common.assert_target:chat_sync`` (configured under
``pipeline.inference.target.callable``). It resolves the hosted Foundry agent
(default ``rfpagent``) once and forwards each turn through the existing
Microsoft Agent Framework path in :mod:`common.agents`.

Authentication is ``DefaultAzureCredential`` throughout — the same as the rest
of the app. No keys or service principals.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from .agents import AgentInfo, list_foundry_agents, run_agent
from .errors import (
    CONTENT_FILTER_MARKER,
    is_content_filter_error,
    parse_content_filter,
)

# Name of the hosted Foundry agent ASSERT should exercise. Overridable so the
# same target works for other agents without code changes.
TARGET_AGENT_NAME = os.getenv("ASSERT_TARGET_AGENT", "rfpagent")

_AGENT: AgentInfo | None = None
_AGENT_LOCK = threading.Lock()


def _resolve_agent() -> AgentInfo:
    """Return the target :class:`AgentInfo`, discovered once and cached."""
    global _AGENT
    if _AGENT is not None:
        return _AGENT
    with _AGENT_LOCK:
        if _AGENT is not None:
            return _AGENT
        agents = list_foundry_agents()
        target = TARGET_AGENT_NAME.strip().lower()
        match = next(
            (a for a in agents if (a.name or "").strip().lower() == target), None
        )
        if match is None:
            available = ", ".join(sorted(a.name for a in agents)) or "(none)"
            raise RuntimeError(
                f"ASSERT target agent '{TARGET_AGENT_NAME}' not found in the "
                f"Foundry project. Available agents: {available}"
            )
        _AGENT = match
        return _AGENT


def _to_prior_history(history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    """Drop the trailing user turn (it is passed separately as ``message``)."""
    if not history:
        return []
    prior = list(history)
    if prior and prior[-1].get("role") == "user":
        prior = prior[:-1]
    return [
        {"role": t.get("role", "user"), "content": t.get("content", "")}
        for t in prior
        if t.get("content")
    ]


def _run_in_thread(agent: AgentInfo, prior: list[dict[str, str]], message: str) -> Any:
    """Execute the (blocking, asyncio-based) agent call on a dedicated thread.

    ASSERT may invoke this callable from within a running event loop. Because
    :func:`common.agents.run_agent` calls ``asyncio.run`` internally, we always
    run it on a fresh thread so there is never a running loop in scope.
    """
    box: dict[str, Any] = {}

    def _worker() -> None:
        box["result"] = run_agent(agent, prior, message)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    return box.get("result")


def chat_sync(message: str, history: list[dict[str, str]] | None = None) -> str:
    """ASSERT entry point: answer ``message`` with the target Foundry agent.

    ``history`` follows the OpenAI/LiteLLM chat-messages shape (``user`` /
    ``assistant`` only); the current user turn is ``history[-1]`` and is also
    provided as ``message``. Returns the agent's final response text.
    """
    agent = _resolve_agent()
    prior = _to_prior_history(history)
    result = _run_in_thread(agent, prior, message)
    if result is None:
        raise RuntimeError("Agent returned no result.")
    error = getattr(result, "error", None)
    if error:
        # A content-filter block is an *expected* outcome for adversarial /
        # jailbreak test prompts: the agent's safety system refused the request.
        # Rather than crash the whole ASSERT pipeline, return a readable, benign
        # response so the case is recorded and the judge can score the refusal.
        if is_content_filter_error(error):
            info = parse_content_filter(error)
            where = "prompt"
            cats = "content policy"
            if info is not None:
                where = "prompt" if "prompt" in (info.source or "") else (
                    info.source or "request"
                )
                cats = info.category_label
            return (
                f"{CONTENT_FILTER_MARKER} The request was blocked by Azure "
                f"OpenAI's content safety filter on the {where} ({cats}); the "
                "agent's safety system refused to answer this prompt."
            )
        raise RuntimeError(error)
    return getattr(result, "text", "") or ""
