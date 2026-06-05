#!/usr/bin/env python
"""Headless runner for the Agentic AIOps lifecycle (used by CI/CD).

The Streamlit app has no command line, but all of its real logic lives in the
``common/`` package and returns plain dataclasses (never raising into the UI).
This script exposes those same orchestrators as subcommands so a GitHub Actions
workflow can exercise the full agent lifecycle:

    run-agent   Run the hosted agent against a question (smoke / CD re-run)
    model-eval  Model evaluation over the Evaluations-tab dataset
    agent-eval  Agent evaluation (intent / tool-use / task adherence)
    assert      ASSERT behavioural test suite
    redteam     Single-turn adversarial red-team scan
    governance  Agent governance attestation + policy checks

Every command:
  * forces UTF-8 streams (red-team / ASSERT emit emoji),
  * enables OpenTelemetry tracing exported to the Foundry-connected
    Application Insights (``--no-tracing`` to skip),
  * prints a readable summary,
  * writes a JSON artifact under ``pipeline-artifacts/`` for the workflow,
  * exits non-zero when the step genuinely fails so CI/CD gates work.

Authentication is ``DefaultAzureCredential`` only. In CI the service principal
from the ``AZURE_CREDENTIALS`` secret is exported as ``AZURE_CLIENT_ID`` /
``AZURE_TENANT_ID`` / ``AZURE_CLIENT_SECRET`` env vars, which
``DefaultAzureCredential``'s ``EnvironmentCredential`` picks up automatically.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make the repository root importable when run as ``python scripts/aiops_pipeline.py``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common.config import ensure_utf8_streams  # noqa: E402

DEFAULT_QUESTION = "Summarize RFP for Virgnia Railway express project"
_ARTIFACT_DIR = _REPO_ROOT / "pipeline-artifacts"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _log(msg: str) -> None:
    print(msg, flush=True)


def _rule(title: str) -> None:
    _log("")
    _log("=" * 72)
    _log(f"  {title}")
    _log("=" * 72)


def _write_artifact(name: str, payload: dict[str, Any]) -> None:
    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), **payload}
    path = _ARTIFACT_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    _log(f"[artifact] {path.relative_to(_REPO_ROOT)}")


def _as_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj


def _enable_tracing(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "no_tracing", False):
        _log("[tracing] disabled via --no-tracing")
        return {"enabled": False, "detail": "disabled via --no-tracing"}
    from common.observability import setup_tracing

    status = setup_tracing()
    flag = "ENABLED" if status.enabled else "not active"
    _log(f"[tracing] {flag} ({status.exporter}) - {status.detail}")
    if getattr(args, "require_tracing", False) and not status.enabled:
        # The pipeline mandates Foundry-logged tracing; fail loudly rather than
        # run a step whose telemetry never reaches Application Insights.
        raise SystemExit(
            "Tracing is required (--require-tracing) but could not be enabled: "
            f"{status.detail}"
        )
    return {"enabled": status.enabled, "exporter": status.exporter, "detail": status.detail}


def _resolve_agent(agent_name: str):
    from common.agents import get_agent

    agent = get_agent(agent_name)
    if agent is None:
        raise SystemExit(f"Agent '{agent_name}' was not found in the Foundry project.")
    return agent


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_run_agent(args: argparse.Namespace) -> int:
    _rule(f"RUN AGENT - {args.agent}")
    tracing = _enable_tracing(args)
    from common.agents import run_agent

    agent = _resolve_agent(args.agent)
    _log(f"Question: {args.question}")
    result = run_agent(agent, [], args.question)

    text = (result.text or "").strip()
    if result.error:
        _log(f"ERROR: {result.error}")
    else:
        _log("\n--- Agent response ---")
        _log(text if text else "(empty response)")
        _log("--- end response ---")
        if result.usage:
            _log(f"Token usage: {result.usage}")

    _write_artifact(
        "run-agent",
        {
            "agent": agent.name,
            "model": agent.model,
            "question": args.question,
            "response": text,
            "usage": result.usage,
            "error": result.error,
            "tracing": tracing,
        },
    )

    if result.error:
        return 1
    if not text:
        _log("FAIL: agent returned an empty response.")
        return 1
    _log("OK: agent produced a response.")
    return 0


def cmd_model_eval(args: argparse.Namespace) -> int:
    _rule("MODEL EVALUATION")
    tracing = _enable_tracing(args)
    from common import evaluation as ev

    run = ev.run_evaluation(ev.ALL_CATEGORIES)
    _log(f"Dataset rows: {run.row_count}")
    if run.metrics:
        _log("Metrics:")
        for key, value in sorted(run.metrics.items()):
            _log(f"  {key}: {value}")
    if run.studio_url:
        _log(f"Foundry run: {run.studio_url}")
    if run.error:
        _log(f"ERROR: {run.error}")

    _write_artifact(
        "model-eval",
        {
            "row_count": run.row_count,
            "metrics": run.metrics,
            "studio_url": run.studio_url,
            "error": run.error,
            "tracing": tracing,
        },
    )
    if run.error:
        return 1
    _log("OK: model evaluation completed and uploaded to Foundry.")
    return 0


def cmd_agent_eval(args: argparse.Namespace) -> int:
    _rule("AGENT EVALUATION")
    tracing = _enable_tracing(args)
    from common import evaluation as ev

    run = ev.run_agent_evaluation()
    _log(f"Dataset rows: {run.row_count}")
    if run.metrics:
        _log("Metrics:")
        for key, value in sorted(run.metrics.items()):
            _log(f"  {key}: {value}")
    if run.studio_url:
        _log(f"Foundry run: {run.studio_url}")
    if run.error:
        _log(f"ERROR: {run.error}")

    _write_artifact(
        "agent-eval",
        {
            "row_count": run.row_count,
            "metrics": run.metrics,
            "studio_url": run.studio_url,
            "error": run.error,
            "tracing": tracing,
        },
    )
    if run.error:
        return 1
    _log("OK: agent evaluation completed and uploaded to Foundry.")
    return 0


def cmd_assert(args: argparse.Namespace) -> int:
    _rule("ASSERT BEHAVIOURAL SUITE")
    tracing = _enable_tracing(args)
    from common import assert_eval as ae

    run = ae.run_assert(
        behavior_name="rfp_summarization_quality",
        behavior_description=(
            "The agent should produce accurate, grounded and well-structured "
            "summaries of RFP documents without hallucinating requirements."
        ),
        context=(
            "rfpagent answers questions and produces summaries about RFP "
            "(Request for Proposal) documents for infrastructure projects."
        ),
        category_count=args.categories,
        prompt_sample_size=args.prompts,
    )
    _log(f"Target: {run.target}  Judge: {run.judge_model}")
    _log(f"Cases: {run.case_count}  Passed: {run.pass_count}  Pass rate: {run.pass_rate:.0%}")
    if run.dimension_rates:
        _log("Dimension pass rates:")
        for dim, rates in run.dimension_rates.items():
            _log(f"  {dim}: {rates}")
    if run.error:
        _log(f"ERROR: {run.error}")
        if run.log_tail:
            _log("--- log tail ---")
            _log(run.log_tail)

    _write_artifact(
        "assert",
        {
            "target": run.target,
            "judge_model": run.judge_model,
            "case_count": run.case_count,
            "pass_count": run.pass_count,
            "pass_rate": run.pass_rate,
            "dimension_rates": run.dimension_rates,
            "error": run.error,
            "tracing": tracing,
        },
    )
    if run.error:
        return 1
    _log("OK: ASSERT suite executed.")
    return 0


def cmd_redteam(args: argparse.Namespace) -> int:
    _rule("RED TEAM SCAN")
    tracing = _enable_tracing(args)
    from common import redteam as rt

    run = rt.run_redteam(num_objectives=args.objectives)
    _log(f"Scan: {run.scan_name}")
    _log(f"Strategies: {run.strategies}")
    _log(f"Risk categories: {run.risk_categories}")
    _log(f"Total attacks: {run.total_attacks}  Successful: {run.successful_attacks}")
    if run.overall_asr is not None:
        _log(f"Overall attack success rate (ASR): {run.overall_asr:.1%}")
    if run.category_asr:
        _log(f"ASR by category: {run.category_asr}")
    if run.studio_url:
        _log(f"Foundry run: {run.studio_url}")
    if run.warning:
        _log(f"WARNING: {run.warning}")
    if run.error:
        _log(f"ERROR: {run.error}")

    _write_artifact(
        "redteam",
        {
            "scan_name": run.scan_name,
            "strategies": run.strategies,
            "risk_categories": run.risk_categories,
            "total_attacks": run.total_attacks,
            "successful_attacks": run.successful_attacks,
            "overall_asr": run.overall_asr,
            "category_asr": run.category_asr,
            "studio_url": run.studio_url,
            "warning": run.warning,
            "error": run.error,
            "tracing": tracing,
        },
    )
    if run.error:
        return 1
    # ASR is reported as an informational gate, not a hard build failure.
    _log("OK: red-team scan completed and uploaded to Foundry.")
    return 0


def cmd_governance(args: argparse.Namespace) -> int:
    _rule("AGENT GOVERNANCE")
    tracing = _enable_tracing(args)
    from common import governance as gov
    from common.config import get_settings

    sponsor = args.sponsor.strip()
    if not sponsor:
        sponsor = get_settings().governance_sponsor_email

    report = gov.run_governance(
        agent_name=args.agent,
        sponsor_email=sponsor,
        capabilities=list(gov.DEFAULT_CAPABILITIES),
    )
    att = report.attestation
    ident = report.identity
    pd = report.prompt_defense
    pol = report.policy

    _log(f"Agent: {report.agent_name}  Model: {report.agent_model}")
    _log(f"Attestation grade: {att.grade or 'n/a'}  Coverage: {att.coverage_pct:.0f}%")
    _log(f"Identity DID: {ident.did or 'n/a'}  Status: {ident.status or 'n/a'}")
    _log(f"Prompt-defense grade: {pd.grade or 'n/a'}  Defended {pd.defended}/{pd.total}")
    _log(f"Policy checks: {pol.allowed_count} allowed / {len(pol.checks)} total")
    for sub_name, sub in (
        ("attestation", att),
        ("identity", ident),
        ("prompt_defense", pd),
        ("policy", pol),
    ):
        if getattr(sub, "error", None):
            _log(f"  {sub_name} error: {sub.error}")

    _write_artifact("governance", {**_as_dict(report), "tracing": tracing})

    # Governance is the CD release gate: fail only if every check errored (nothing ran).
    errors = [s.error for s in (att, ident, pd, pol) if getattr(s, "error", None)]
    if len(errors) == 4:
        _log("FAIL: all governance checks errored.")
        return 1
    _log("OK: governance attestation produced.")
    return 0


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def _add_tracing_flag(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--no-tracing",
        action="store_true",
        help="Skip OpenTelemetry/Foundry tracing setup for this step.",
    )
    p.add_argument(
        "--require-tracing",
        action="store_true",
        help="Fail the step if tracing to Foundry/App Insights cannot be enabled.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agentic AIOps lifecycle runner.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run-agent", help="Run the hosted agent against a question.")
    p_run.add_argument("--agent", default=os.getenv("ASSERT_TARGET_AGENT", "rfpagent"))
    p_run.add_argument("--question", default=DEFAULT_QUESTION)
    _add_tracing_flag(p_run)
    p_run.set_defaults(func=cmd_run_agent)

    p_me = sub.add_parser("model-eval", help="Run model evaluation over the dataset.")
    _add_tracing_flag(p_me)
    p_me.set_defaults(func=cmd_model_eval)

    p_ae = sub.add_parser("agent-eval", help="Run agent evaluation over the dataset.")
    _add_tracing_flag(p_ae)
    p_ae.set_defaults(func=cmd_agent_eval)

    p_as = sub.add_parser("assert", help="Run the ASSERT behavioural suite.")
    p_as.add_argument("--categories", type=int, default=3)
    p_as.add_argument("--prompts", type=int, default=2)
    _add_tracing_flag(p_as)
    p_as.set_defaults(func=cmd_assert)

    p_rt = sub.add_parser("redteam", help="Run a single-turn red-team scan.")
    p_rt.add_argument("--objectives", type=int, default=1)
    _add_tracing_flag(p_rt)
    p_rt.set_defaults(func=cmd_redteam)

    p_gov = sub.add_parser("governance", help="Run agent governance attestation.")
    p_gov.add_argument("--agent", default=os.getenv("ASSERT_TARGET_AGENT", "rfpagent"))
    p_gov.add_argument("--sponsor", default=os.getenv("GOVERNANCE_SPONSOR_EMAIL", ""))
    _add_tracing_flag(p_gov)
    p_gov.set_defaults(func=cmd_governance)

    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
