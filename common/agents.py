"""Agent discovery and execution.

Two responsibilities live here:

* :func:`list_foundry_agents` enumerates the agents in the Foundry project using
  the **Azure AI Projects** SDK (``AIProjectClient.agents.list``).
* :func:`run_agent` talks to a single selected agent through the **Microsoft Agent
  Framework** (``FoundryAgent``), which binds to the existing hosted agent by name
  and returns the reply together with token usage and debug information.

Field/keyword access is written defensively because SDK surface names evolve
across versions.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any

from .config import require_settings


@dataclass(frozen=True)
class AgentInfo:
    """Lightweight description of a Foundry agent for the UI."""

    id: str
    name: str
    version: str = ""
    model: str = ""
    description: str = ""

    @property
    def label(self) -> str:
        base = self.name or self.id
        return f"{base} ({self.model})" if self.model else base


@dataclass
class AgentRunResult:
    """Result of a single agent turn."""

    text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class AgentDefinition:
    """The resolved definition of a hosted agent (instructions + model)."""

    name: str
    instructions: str = ""
    model: str = ""
    version: str = ""


def _first_attr(obj: Any, names: tuple[str, ...], default: Any = "") -> Any:
    for name in names:
        value = getattr(obj, name, None)
        if value:
            return value
    return default


def _nav(obj: Any, key: str) -> Any:
    """Read ``key`` from a mapping-like or attribute-bearing SDK object."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    getter = getattr(obj, "get", None)
    if callable(getter):
        try:
            value = getter(key)
            if value is not None:
                return value
        except Exception:
            pass
    return getattr(obj, key, None)


def _model_from_versions(raw: Any) -> str:
    """Best-effort extraction of the model from an agent's latest version."""
    versions = getattr(raw, "versions", None)
    if versions is None:
        return ""
    latest = (
        getattr(versions, "latest", None)
        or getattr(versions, "current", None)
        or versions
    )
    definition = getattr(latest, "definition", None)
    return str(_first_attr(definition, ("model",), "")) if definition else ""


def list_foundry_agents() -> list[AgentInfo]:
    """Return the agents available in the configured Foundry project."""
    from .azure_clients import get_project_client

    client = get_project_client()
    agents_ops = client.agents

    iterator = None
    for method_name in ("list", "list_agents"):
        method = getattr(agents_ops, method_name, None)
        if callable(method):
            iterator = method()
            break
    if iterator is None:
        raise RuntimeError(
            "The installed azure-ai-projects SDK does not expose an agent listing "
            "method (tried list/list_agents)."
        )

    agents: list[AgentInfo] = []
    seen: set[str] = set()
    for raw in iterator:
        name = str(_first_attr(raw, ("name", "display_name"), ""))
        agent_id = str(_first_attr(raw, ("id", "agent_id"), name))
        key = name or agent_id
        if not key or key in seen:
            continue
        seen.add(key)
        agents.append(
            AgentInfo(
                id=agent_id,
                name=name or agent_id,
                version=str(_first_attr(raw, ("version",), "")),
                model=_model_from_versions(raw),
                description=str(_first_attr(raw, ("description",), "")),
            )
        )
    return agents


def get_agent(agent_name: str) -> AgentInfo | None:
    """Return the :class:`AgentInfo` for ``agent_name`` (case-insensitive)."""
    wanted = (agent_name or "").strip().lower()
    for agent in list_foundry_agents():
        if agent.name.lower() == wanted or agent.id.lower() == wanted:
            return agent
    return None


def _latest_version(raw: Any) -> Any:
    versions = _nav(raw, "versions")
    if versions is None:
        return None
    return (
        _nav(versions, "latest")
        or _nav(versions, "current")
        or versions
    )


def get_agent_instructions(agent_name: str) -> AgentDefinition:
    """Fetch the hosted agent's system instructions, model and version.

    Reads the live definition from the Foundry project (``client.agents.get``
    with a fallback to scanning ``list``). Used by the Governance tab to run a
    static prompt-defense scan against the agent's actual system prompt.
    """
    from .azure_clients import get_project_client

    client = get_project_client()
    raw = None
    getter = getattr(client.agents, "get", None)
    if callable(getter):
        try:
            raw = getter(agent_name)
        except Exception:
            raw = None
    if raw is None:
        for item in client.agents.list():
            name = str(_first_attr(item, ("name", "display_name"), ""))
            if name.lower() == agent_name.lower():
                raw = item
                break
    if raw is None:
        raise RuntimeError(
            f"Agent '{agent_name}' was not found in the Foundry project."
        )

    latest = _latest_version(raw)
    definition = _nav(latest, "definition") or {}
    instructions = str(_nav(definition, "instructions") or "")
    model = str(_nav(definition, "model") or "")
    version = str(_nav(latest, "version") or "")
    name = str(_nav(raw, "name") or agent_name)
    return AgentDefinition(
        name=name, instructions=instructions, model=model, version=version
    )


def _to_messages(history: list[dict[str, str]], question: str) -> list[Any]:
    """Convert stored history + new question into Agent Framework messages."""
    from agent_framework import Message

    messages: list[Any] = []
    for turn in history:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if content:
            messages.append(Message(role, [content]))
    messages.append(Message("user", [question]))
    return messages


def _usage_value(usage: Any, names: tuple[str, ...]) -> int:
    """Read a token count from a UsageDetails dict or object."""
    for name in names:
        if isinstance(usage, dict) and usage.get(name):
            return int(usage[name])
        value = getattr(usage, name, None)
        if value:
            return int(value)
    return 0


def _extract_usage(response: Any) -> dict[str, Any]:
    """Pull token counts from an Agent Framework response, if present."""
    usage = getattr(response, "usage_details", None) or getattr(response, "usage", None)
    if usage is None:
        return {}
    prompt = _usage_value(usage, ("input_token_count", "prompt_tokens", "input_tokens"))
    completion = _usage_value(
        usage, ("output_token_count", "completion_tokens", "output_tokens")
    )
    total = _usage_value(usage, ("total_token_count", "total_tokens")) or (
        prompt + completion
    )
    return {"input": prompt, "output": completion, "total": total}


def _build_foundry_agent(agent: AgentInfo, credential: Any) -> Any:
    """Construct a ``FoundryAgent`` bound to the existing hosted ``agent``."""
    from agent_framework.foundry import FoundryAgent

    settings = require_settings()
    params = inspect.signature(FoundryAgent).parameters

    kwargs: dict[str, Any] = {"credential": credential}
    if "project_endpoint" in params:
        kwargs["project_endpoint"] = settings.project_endpoint
    if "agent_name" in params:
        kwargs["agent_name"] = agent.name
    if agent.version and "agent_version" in params:
        kwargs["agent_version"] = agent.version
    return FoundryAgent(**kwargs)


async def _run_agent_async(
    agent: AgentInfo, history: list[dict[str, str]], question: str
) -> AgentRunResult:
    from azure.identity.aio import DefaultAzureCredential

    started = time.perf_counter()
    messages = _to_messages(history, question)

    async with DefaultAzureCredential() as credential:
        foundry_agent = _build_foundry_agent(agent, credential)
        async with foundry_agent:
            response = await foundry_agent.run(messages)

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    text = _first_attr(response, ("text",), "") or str(response)
    return AgentRunResult(
        text=text,
        usage=_extract_usage(response),
        debug={
            "agent_id": agent.id,
            "agent_name": agent.name,
            "agent_version": agent.version or "latest",
            "model": agent.model or "(hosted)",
            "latency_ms": elapsed_ms,
            "response_id": str(_first_attr(response, ("response_id", "id"), "")),
            "raw_type": type(response).__name__,
        },
    )


def run_agent(
    agent: AgentInfo, history: list[dict[str, str]], question: str
) -> AgentRunResult:
    """Run ``agent`` against the conversation ``history`` plus a new ``question``."""
    try:
        return asyncio.run(_run_agent_async(agent, history, question))
    except Exception as exc:  # surfaced to the UI, never crash the app
        return AgentRunResult(
            error=f"{type(exc).__name__}: {exc}",
            debug={"agent_id": agent.id, "agent_name": agent.name},
        )
