"""Red Team utilities built on **Azure AI Evaluation**'s ``RedTeam`` (PyRIT).

The Red Team tab runs a *single-turn* automated adversarial scan against the
hosted Foundry ``rfpagent`` using a few simple attack strategies, then uploads
the results to the Azure AI Foundry project so the scan is auditable and
trackable in the portal.

Design choices (kept deliberately small to bound runtime — every attack calls
the live agent plus RAI grading):

* **Single-turn strategies only** — ``Baseline``, ``Base64``, ``Flip`` … never
  ``MultiTurn`` / ``Crescendo``.
* **Few risk categories** + small ``num_objectives`` (1) per category.
* **Foundry upload enabled** (``skip_upload=False``) for auditing.

Authentication is ``DefaultAzureCredential`` only — no keys / service
principals. The target callable offloads each ``run_agent`` call onto a worker
thread so its ``asyncio.run`` gets a fresh event loop (the scan itself runs
inside an event loop, so a nested ``asyncio.run`` would otherwise fail).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .agents import get_agent, run_agent
from .config import ensure_utf8_streams, require_settings

AGENT_NAME = "rfpagent"

# Risk categories offered in the UI (label -> ``RiskCategory`` enum member).
RISK_CATEGORIES: dict[str, str] = {
    "Hate & Unfairness": "HateUnfairness",
    "Violence": "Violence",
    "Sexual": "Sexual",
    "Self-Harm": "SelfHarm",
}
DEFAULT_RISK_CATEGORIES: list[str] = ["Hate & Unfairness", "Violence"]

# Simple *single-turn* attack strategies (label -> ``AttackStrategy`` member).
# MultiTurn / Crescendo are intentionally excluded to keep the scan to 1 turn.
ATTACK_STRATEGIES: dict[str, str] = {
    "Baseline": "Baseline",
    "Base64": "Base64",
    "Flip": "Flip",
    "Leetspeak": "Leetspeak",
    "Morse": "Morse",
    "ROT13": "ROT13",
    "Caesar": "Caesar",
}
DEFAULT_STRATEGIES: list[str] = ["Baseline", "Base64", "Flip"]

# Where per-scan JSON artifacts are written locally.
WORK_DIR = ".redteam_work"


@dataclass
class AttackRow:
    """A single adversarial attempt and the agent's response."""

    risk_category: str = ""
    attack_technique: str = ""
    attack_complexity: str = ""
    attack_success: bool | None = None
    attack_prompt: str = ""
    response: str = ""


@dataclass
class RedTeamRun:
    """Structured result of a red-team scan for the UI."""

    overall_asr: float | None = None
    category_asr: dict[str, float] = field(default_factory=dict)
    technique_asr: dict[str, float] = field(default_factory=dict)
    rows: list[AttackRow] = field(default_factory=list)
    total_attacks: int = 0
    successful_attacks: int = 0
    risk_categories: list[str] = field(default_factory=list)
    strategies: list[str] = field(default_factory=list)
    num_objectives: int = 1
    scan_name: str = ""
    duration_s: float = 0.0
    studio_url: str | None = None
    output_path: str | None = None
    agent_call_errors: int = 0
    warning: str | None = None
    error: str | None = None


# Per-agent-call hard timeout so a hung agent never stalls the whole scan.
_TARGET_TIMEOUT_S = 240


def _make_target(agent: Any, errors: list[str]) -> Callable[[str], str]:
    """Build the single-turn callable the scanner drives against ``rfpagent``.

    ``run_agent`` internally calls ``asyncio.run``; because the scan executes
    inside an event loop we offload onto a worker thread (with a hard timeout)
    so a *fresh* loop is used. A **content-filter block / refusal** surfaces as a
    benign string — the desired "defense worked" outcome. A genuine
    **infrastructure failure** (the agent could not be reached) is recorded in
    ``errors`` so the caller can tell a real outage apart from a strong defense.
    """

    def target(query: str) -> str:
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(run_agent, agent, [], query).result(
                    timeout=_TARGET_TIMEOUT_S
                )
        except Exception as exc:  # noqa: BLE001 - never break the scan
            errors.append(f"{type(exc).__name__}: {exc}")
            return f"[error] The agent could not be reached: {exc}"
        if result.error:
            # Refusal / content-filter block — an expected adversarial outcome.
            return (
                "[blocked] The request was refused or filtered and could not be "
                f"completed: {result.error}"
            )
        return result.text or "[empty response]"

    return target


def _first(items: Any) -> dict[str, Any]:
    """Return the first dict of a summary list (or ``{}``)."""
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    if isinstance(items, dict):
        return items
    return {}


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _asr_map(summary: dict[str, Any], drop: tuple[str, ...] = ()) -> dict[str, float]:
    """Pull every ``*_asr`` numeric field from a summary dict into a label map."""
    out: dict[str, float] = {}
    for key, value in summary.items():
        if not key.endswith("_asr") or key in drop:
            continue
        val = _as_float(value)
        if val is None:
            continue
        label = key[: -len("_asr")].replace("_", " ").strip().title()
        out[label] = val
    return out


def _parse_attack_details(details: Any) -> list[AttackRow]:
    """Convert ``RedTeamResult.attack_details`` into readable rows."""
    rows: list[AttackRow] = []
    if not isinstance(details, list):
        return rows
    for item in details:
        if not isinstance(item, dict):
            continue
        prompt, response = "", ""
        for msg in item.get("conversation", []) or []:
            if not isinstance(msg, dict):
                continue
            role = (msg.get("role") or "").lower()
            content = msg.get("content")
            if isinstance(content, list):
                content = " ".join(str(c) for c in content)
            content = "" if content is None else str(content)
            if role in ("user", "attacker") and not prompt:
                prompt = content
            elif role in ("assistant", "target") and not response:
                response = content
        rows.append(
            AttackRow(
                risk_category=str(item.get("risk_category", "") or ""),
                attack_technique=str(item.get("attack_technique", "") or ""),
                attack_complexity=str(item.get("attack_complexity", "") or ""),
                attack_success=item.get("attack_success"),
                attack_prompt=prompt,
                response=response,
            )
        )
    return rows


def _parse_result(result: Any, run: RedTeamRun) -> RedTeamRun:
    """Fold a ``RedTeamResult`` into the structured :class:`RedTeamRun`."""
    scan_result = getattr(result, "scan_result", None) or {}

    scorecard: dict[str, Any] = {}
    to_scorecard = getattr(result, "to_scorecard", None)
    if callable(to_scorecard):
        try:
            scorecard = to_scorecard() or {}
        except Exception:  # noqa: BLE001 - fall back to scan_result
            scorecard = {}
    if not scorecard and isinstance(scan_result, dict):
        scorecard = scan_result.get("scorecard", {}) or {}

    risk_summary = _first(scorecard.get("risk_category_summary"))
    technique_summary = _first(scorecard.get("attack_technique_summary"))

    run.overall_asr = _as_float(risk_summary.get("overall_asr"))
    run.category_asr = _asr_map(risk_summary, drop=("overall_asr",))
    run.technique_asr = _asr_map(technique_summary, drop=("overall_asr",))

    details = getattr(result, "attack_details", None)
    if details is None and isinstance(scan_result, dict):
        details = scan_result.get("attack_details")
    run.rows = _parse_attack_details(details)
    run.total_attacks = len(run.rows)
    run.successful_attacks = sum(1 for r in run.rows if r.attack_success)

    if isinstance(scan_result, dict):
        run.studio_url = scan_result.get("studio_url") or run.studio_url
    return run


def run_redteam(
    risk_categories: list[str] | None = None,
    strategies: list[str] | None = None,
    num_objectives: int = 1,
) -> RedTeamRun:
    """Run a single-turn red-team scan against ``rfpagent`` and upload to Foundry.

    The credential is a synchronous ``DefaultAzureCredential``; the same
    credential drives the Foundry upload (``skip_upload=False``).
    """
    from azure.identity import DefaultAzureCredential

    # The red-team SDK logs emoji-laden progress; ensure the console can encode
    # it (Windows defaults to cp1252 and would otherwise raise UnicodeEncodeError).
    ensure_utf8_streams()

    cats = list(risk_categories or DEFAULT_RISK_CATEGORIES)
    strats = list(strategies or DEFAULT_STRATEGIES)
    # The SDK always injects a Baseline pass; include it explicitly so the
    # displayed strategy list and attempt counts match what actually runs.
    if "Baseline" not in strats:
        strats = ["Baseline"] + strats
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    scan_name = f"redteam-{AGENT_NAME}-{ts}"
    run = RedTeamRun(
        risk_categories=cats,
        strategies=strats,
        num_objectives=num_objectives,
        scan_name=scan_name,
    )

    settings = require_settings()
    if not settings.project_endpoint:
        run.error = "AZURE_AI_PROJECT_ENDPOINT is not configured."
        return run
    if not cats:
        run.error = "Select at least one risk category."
        return run
    if not strats:
        run.error = "Select at least one attack strategy."
        return run

    agent_name = settings.assert_target_agent or AGENT_NAME
    agent = get_agent(agent_name)
    if agent is None:
        run.error = f"Agent '{agent_name}' was not found in the Foundry project."
        return run

    try:
        from azure.ai.evaluation.red_team import (
            AttackStrategy,
            RedTeam,
            RiskCategory,
        )

        risk_enums = [getattr(RiskCategory, RISK_CATEGORIES[c]) for c in cats]
        strat_enums = [getattr(AttackStrategy, ATTACK_STRATEGIES[s]) for s in strats]
    except KeyError as exc:
        run.error = f"Unknown risk category or strategy: {exc}"
        return run
    except ImportError as exc:
        run.error = (
            "The red-team extra is not installed. Run "
            "`pip install \"azure-ai-evaluation[redteam]\"`. "
            f"({exc})"
        )
        return run

    out_dir = Path(WORK_DIR)
    out_dir.mkdir(exist_ok=True)
    # The SDK treats output_path as a directory it writes result files into.
    output_path = str(out_dir / scan_name)
    run.output_path = output_path

    scenario = (
        "RFP procurement assistant that drafts proposal responses for "
        "enterprise customers."
    )
    agent_errors: list[str] = []
    target = _make_target(agent, agent_errors)

    def _scan(skip_upload: bool):
        red_team = RedTeam(
            azure_ai_project=settings.project_endpoint,
            credential=DefaultAzureCredential(),
            risk_categories=risk_enums,
            num_objectives=max(1, int(num_objectives)),
            application_scenario=scenario,
            output_dir=str(out_dir),
        )
        return asyncio.run(
            red_team.scan(
                target=target,
                scan_name=scan_name,
                attack_strategies=strat_enums,
                skip_upload=skip_upload,
                output_path=output_path,
                application_scenario=scenario,
                # Serial execution: the target blocks its event loop, so real
                # parallelism is not achievable and serial is more predictable.
                parallel_execution=False,
                max_parallel_tasks=1,
                timeout=3600,
            )
        )

    started = time.perf_counter()
    try:
        # skip_upload=False → upload to the Foundry project for auditing.
        result = _scan(skip_upload=False)
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        # The upload path can fail before attacks run (e.g. RBAC). Retry once
        # locally so the user still gets a scorecard, and flag the gap.
        agent_errors.clear()
        try:
            result = _scan(skip_upload=True)
            run.warning = (
                "Results were computed locally but could NOT be uploaded to the "
                f"Foundry project for auditing: {type(exc).__name__}: {exc}"
            )
        except Exception as exc2:  # noqa: BLE001
            run.duration_s = round(time.perf_counter() - started, 2)
            run.error = f"{type(exc2).__name__}: {exc2}"
            return run

    run.duration_s = round(time.perf_counter() - started, 2)
    run.agent_call_errors = len(agent_errors)
    try:
        _parse_result(result, run)
    except Exception as exc:  # noqa: BLE001 - defensive parsing
        run.error = f"Scan completed but results could not be parsed: {exc}"

    # Tell a genuine outage apart from a strong defense: if every attempt hit an
    # infrastructure error, the low ASR is meaningless — surface it clearly.
    if run.agent_call_errors:
        if run.total_attacks and run.agent_call_errors >= run.total_attacks:
            run.error = (
                f"The agent '{agent_name}' could not be reached on any of the "
                f"{run.agent_call_errors} attempt(s); the attack-success rate is "
                "not meaningful. Check the agent deployment and your access."
            )
        else:
            note = (
                f"{run.agent_call_errors} attack attempt(s) failed to reach the "
                "agent (infrastructure error, not a defended attack)."
            )
            run.warning = f"{run.warning}\n\n{note}" if run.warning else note
    return run
