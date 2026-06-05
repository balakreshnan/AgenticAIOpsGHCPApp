"""RAMPART adapter — bridges the Foundry ``rfpagent`` to Microsoft RAMPART.

`RAMPART <https://github.com/microsoft/RAMPART>`_ is a safety-testing framework
for agentic AI. Product teams connect their agent by implementing two protocols
(:class:`rampart.AgentAdapter` and :class:`rampart.Session`); RAMPART then drives
probes/attacks through them and judges the responses.

This module exposes :class:`RfpAgentAdapter`, which forwards every RAMPART
``Request`` to the existing Microsoft Agent Framework path in
:mod:`common.agents` (the same hosted ``rfpagent`` used everywhere else in the
app). Authentication is ``DefaultAzureCredential`` throughout — no keys or
service principals.

Robustness notes
----------------
* :func:`common.agents.run_agent` is blocking and calls ``asyncio.run`` itself,
  so it is invoked via ``asyncio.to_thread`` (never inside the running loop) and
  wrapped in ``asyncio.wait_for`` so a hung Foundry call cannot stall a probe.
* A **content-filter block** is returned as a readable *refusal* string — for an
  adversarial probe that is the desired (safe) behaviour.
* A **transient / hung** agent call is returned as a benign
  ``[agent-call-error]`` marker instead of raising, so a single infrastructure
  blip never crashes the suite. The orchestrator treats that marker as an
  ``ERROR`` outcome (never a false ``SAFE``).
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import TYPE_CHECKING, Any

from .agents import AgentInfo, list_foundry_agents, run_agent
from .errors import (
    is_content_filter_error,
    parse_content_filter,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import types

    from rampart import AppManifest, ObservabilityLevel, Request, Response

# Name of the hosted Foundry agent RAMPART should exercise. Overridable so the
# same adapter works for other agents without code changes.
TARGET_AGENT_NAME = os.getenv("ASSERT_TARGET_AGENT", "rfpagent")

# Marker prefixed to a benign response when the agent call fails for a transient
# reason (timeout / 5xx / throttling) or hangs. The orchestrator detects this
# marker and records the probe as an ERROR rather than letting an absent attack
# token masquerade as a "safe" pass.
TARGET_ERROR_MARKER = "[agent-call-error]"

# Hard ceiling (seconds) for a single agent call. A hung Foundry/Agent Framework
# request must not stall the whole RAMPART run.
_CALL_TIMEOUT = float(os.getenv("RAMPART_CALL_TIMEOUT", "240"))

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
                f"RAMPART target agent '{TARGET_AGENT_NAME}' not found in the "
                f"Foundry project. Available agents: {available}"
            )
        _AGENT = match
        return _AGENT


def _refusal_text(error: str) -> str:
    """Turn a content-filter error into a readable refusal sentence."""
    info = parse_content_filter(error)
    where = "prompt"
    cats = "content policy"
    if info is not None:
        where = "prompt" if "prompt" in (info.source or "") else (
            info.source or "request"
        )
        cats = info.category_label
    return (
        f"The request was blocked by Azure OpenAI's content safety filter on the "
        f"{where} ({cats}); the agent's safety system refused to answer this "
        "prompt."
    )


class RfpAgentSession:
    """A single RAMPART interaction session with the hosted ``rfpagent``.

    The agent is stateless per session here — each probe creates a fresh
    session, matching RAMPART's "fresh state = fresh session" contract.
    """

    def __init__(self, agent: AgentInfo) -> None:
        self._agent = agent

    async def send_async(self, request: "Request") -> "Response":
        """Forward ``request`` to the agent and wrap the reply as a Response."""
        from rampart import Response  # local import: heavy (pulls PyRIT)

        prompt = (getattr(request, "prompt", None) or "").strip()
        if not prompt:
            # Attachment-only requests are not meaningful for this text agent.
            return Response(
                text=f"{TARGET_ERROR_MARKER} empty prompt (no text to summarise).",
                metadata={"agent_call_error": True},
            )

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(run_agent, self._agent, [], prompt),
                timeout=_CALL_TIMEOUT,
            )
        except (asyncio.TimeoutError, TimeoutError):
            return Response(
                text=(
                    f"{TARGET_ERROR_MARKER} the agent did not respond within "
                    f"{int(_CALL_TIMEOUT)}s (timeout)."
                ),
                metadata={"agent_call_error": True},
            )
        except Exception as exc:  # noqa: BLE001 - never raise into RAMPART
            return Response(
                text=f"{TARGET_ERROR_MARKER} {type(exc).__name__}: {exc}",
                metadata={"agent_call_error": True},
            )

        if result is None:
            return Response(
                text=f"{TARGET_ERROR_MARKER} agent returned no result.",
                metadata={"agent_call_error": True},
            )

        error = getattr(result, "error", None)
        if error:
            if is_content_filter_error(error):
                # A safety refusal is the *desired* outcome for adversarial
                # probes — return it as plain text the evaluator can detect.
                return Response(
                    text=_refusal_text(error),
                    metadata={"content_filter": True},
                )
            summary = (error or "unknown error").splitlines()[0]
            return Response(
                text=f"{TARGET_ERROR_MARKER} {summary}",
                metadata={"agent_call_error": True},
            )

        return Response(
            text=getattr(result, "text", "") or "",
            metadata={"usage": getattr(result, "usage", {}) or {}},
        )

    async def __aenter__(self) -> "RfpAgentSession":
        return self

    async def __aexit__(
        self,
        exc_type: "type[BaseException] | None",
        exc_val: "BaseException | None",
        exc_tb: "types.TracebackType | None",
    ) -> None:
        # Stateless hosted agent — nothing to clean up. Must be idempotent.
        return None


class RfpAgentAdapter:
    """RAMPART :class:`~rampart.AgentAdapter` for the hosted ``rfpagent``.

    Declares the agent's (tool-free) capabilities via an :class:`AppManifest`
    and creates fresh :class:`RfpAgentSession` instances on demand.
    """

    def __init__(self, agent: AgentInfo | None = None) -> None:
        self._agent = agent or _resolve_agent()
        self._manifest: Any | None = None

    async def create_session_async(self) -> RfpAgentSession:
        return RfpAgentSession(self._agent)

    @property
    def manifest(self) -> "AppManifest":
        from rampart import AppManifest

        if self._manifest is None:
            self._manifest = AppManifest(
                name=self._agent.name or TARGET_AGENT_NAME,
                description=(
                    "Hosted Azure AI Foundry agent that answers questions about "
                    "and produces summaries of RFP (Request for Proposal) "
                    "documents for infrastructure projects. It has no tools and "
                    "no external data sources."
                ),
                metadata={"model": self._agent.model or "", "id": self._agent.id},
            )
        return self._manifest

    @property
    def observability_profile(self) -> "ObservabilityLevel":
        from rampart import ObservabilityLevel

        # The agent exposes only its text response (no tool calls / side effects).
        return ObservabilityLevel.RESPONSE_ONLY


def build_adapter(agent: AgentInfo | None = None) -> RfpAgentAdapter:
    """Convenience factory used by the orchestrator and tab."""
    return RfpAgentAdapter(agent)
