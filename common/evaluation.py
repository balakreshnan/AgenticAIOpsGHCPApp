"""Agent evaluation utilities built on the **Azure AI Evaluation** SDK.

The Evaluations tab uses these helpers to score the responses produced by the
Foundry ``rfpagent`` agent (captured in ``evaldata/datarfp.jsonl``) across the
full set of applicable metrics:

* **NLP / lexical** (no model needed): F1, BLEU, GLEU, ROUGE-L, METEOR.
* **AI-assisted quality** (judge LLM via :class:`DefaultAzureCredential`):
  groundedness, relevance, coherence, fluency, similarity, retrieval and
  response completeness.
* **Risk & safety** (Azure AI RAI service): violence, sexual, self-harm and
  hate/unfairness — enabled only when subscription / resource-group / project
  are configured.

All authentication uses ``DefaultAzureCredential`` — no keys or service
principals. The judge model talks to the Azure OpenAI endpoint derived from the
Foundry project endpoint using an AAD token provider.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import Settings, require_settings

# The agent whose responses the shipped dataset represents.
AGENT_NAME = "rfpagent"

# Metric categories surfaced in the UI. Order is preserved for display.
CATEGORY_NLP = "NLP / Lexical"
CATEGORY_QUALITY = "AI-assisted Quality"
CATEGORY_SAFETY = "Risk & Safety"
ALL_CATEGORIES = (CATEGORY_NLP, CATEGORY_QUALITY, CATEGORY_SAFETY)

# Human-friendly descriptions per category for the UI.
CATEGORY_HELP = {
    CATEGORY_NLP: "Lexical overlap with the ground truth (no judge model).",
    CATEGORY_QUALITY: "LLM-judged quality of the agent response.",
    CATEGORY_SAFETY: "Content-safety risk via the Azure AI RAI service.",
}

# Agent-evaluation metrics (run against ``datarfpagent.jsonl``). All are
# AI-assisted and judge tool-using agent behaviour rather than raw text.
AGENT_METRIC_HELP = {
    "intent_resolution": "How well the agent identified and addressed the user's intent.",
    "tool_call_accuracy": "Whether the agent called the right tools with correct parameters.",
    "task_adherence": "How closely the agent followed the assigned task instructions.",
}


@dataclass
class EvalRun:
    """Structured result of an evaluation batch for the UI."""

    metrics: dict[str, float] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)
    evaluators: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    judge_model: str = ""
    dataset_path: str = ""
    row_count: int = 0
    duration_s: float = 0.0
    studio_url: str | None = None
    error: str | None = None


def _model_config(settings: Settings) -> dict[str, Any]:
    """Build an Azure OpenAI judge-model config (AAD — no api_key)."""
    return {
        "azure_endpoint": settings.aoai_endpoint,
        "azure_deployment": settings.judge_model,
        "api_version": settings.aoai_api_version,
    }


def _build_evaluators(
    categories: tuple[str, ...],
    settings: Settings,
    credential: Any,
) -> tuple[dict[str, Any], dict[str, dict], list[tuple[str, str]]]:
    """Instantiate evaluators + per-evaluator column mappings.

    Returns ``(evaluators, evaluator_config, skipped)`` where ``skipped`` is a
    list of ``(category-or-name, reason)`` pairs to surface in the UI.
    """
    import azure.ai.evaluation as ev

    evaluators: dict[str, Any] = {}
    config: dict[str, dict] = {}
    skipped: list[tuple[str, str]] = []

    def cm(**mapping: str) -> dict:
        return {"column_mapping": {k: f"${{data.{v}}}" for k, v in mapping.items()}}

    # ---- NLP / lexical (no model) -----------------------------------------
    if CATEGORY_NLP in categories:
        evaluators["f1_score"] = ev.F1ScoreEvaluator()
        config["f1_score"] = cm(response="response", ground_truth="ground_truth")
        evaluators["bleu_score"] = ev.BleuScoreEvaluator()
        config["bleu_score"] = cm(response="response", ground_truth="ground_truth")
        evaluators["gleu_score"] = ev.GleuScoreEvaluator()
        config["gleu_score"] = cm(response="response", ground_truth="ground_truth")
        evaluators["meteor_score"] = ev.MeteorScoreEvaluator()
        config["meteor_score"] = cm(response="response", ground_truth="ground_truth")
        try:
            from azure.ai.evaluation import RougeType

            evaluators["rouge_score"] = ev.RougeScoreEvaluator(
                rouge_type=RougeType.ROUGE_L
            )
            config["rouge_score"] = cm(
                response="response", ground_truth="ground_truth"
            )
        except Exception as exc:  # pragma: no cover - defensive
            skipped.append(("rouge_score", f"{type(exc).__name__}: {exc}"))

    # ---- AI-assisted quality (judge model) --------------------------------
    if CATEGORY_QUALITY in categories:
        if not settings.aoai_endpoint or not settings.judge_model:
            skipped.append(
                (
                    CATEGORY_QUALITY,
                    "Judge model endpoint/deployment not configured "
                    "(set AZURE_OPENAI_ENDPOINT / AZURE_EVAL_JUDGE_MODEL).",
                )
            )
        else:
            mc = _model_config(settings)
            rk = {"is_reasoning_model": settings.judge_is_reasoning}
            quality_specs: list[tuple[str, Callable[[], Any], dict]] = [
                (
                    "groundedness",
                    lambda: ev.GroundednessEvaluator(mc, credential=credential, **rk),
                    cm(query="query", context="context", response="response"),
                ),
                (
                    "relevance",
                    lambda: ev.RelevanceEvaluator(mc, credential=credential, **rk),
                    cm(query="query", response="response"),
                ),
                (
                    "coherence",
                    lambda: ev.CoherenceEvaluator(mc, credential=credential, **rk),
                    cm(query="query", response="response"),
                ),
                (
                    "fluency",
                    lambda: ev.FluencyEvaluator(mc, credential=credential, **rk),
                    cm(response="response"),
                ),
                (
                    "similarity",
                    lambda: ev.SimilarityEvaluator(mc, credential=credential, **rk),
                    cm(
                        query="query",
                        response="response",
                        ground_truth="ground_truth",
                    ),
                ),
                (
                    "retrieval",
                    lambda: ev.RetrievalEvaluator(mc, credential=credential, **rk),
                    cm(query="query", context="context"),
                ),
                (
                    "response_completeness",
                    lambda: ev.ResponseCompletenessEvaluator(
                        mc, credential=credential, **rk
                    ),
                    cm(response="response", ground_truth="ground_truth"),
                ),
            ]
            for name, factory, mapping in quality_specs:
                try:
                    evaluators[name] = factory()
                    config[name] = mapping
                except Exception as exc:  # pragma: no cover - defensive
                    skipped.append((name, f"{type(exc).__name__}: {exc}"))

    # ---- Risk & safety (RAI service) --------------------------------------
    if CATEGORY_SAFETY in categories:
        project = settings.project_endpoint
        if not project:
            skipped.append(
                (CATEGORY_SAFETY, "AZURE_AI_PROJECT_ENDPOINT is not configured.")
            )
        else:
            safety_specs: list[tuple[str, Callable[[], Any]]] = [
                ("violence", lambda: ev.ViolenceEvaluator(credential, project)),
                ("sexual", lambda: ev.SexualEvaluator(credential, project)),
                ("self_harm", lambda: ev.SelfHarmEvaluator(credential, project)),
                (
                    "hate_unfairness",
                    lambda: ev.HateUnfairnessEvaluator(credential, project),
                ),
            ]
            for name, factory in safety_specs:
                try:
                    evaluators[name] = factory()
                    config[name] = cm(query="query", response="response")
                except Exception as exc:  # pragma: no cover - defensive
                    skipped.append((name, f"{type(exc).__name__}: {exc}"))

    return evaluators, config, skipped


def _count_rows(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _execute(
    run: EvalRun,
    path: str,
    evaluators: dict[str, Any],
    config: dict[str, dict],
    name: str,
    azure_ai_project: str | None = None,
) -> EvalRun:
    """Run ``evaluate`` and fold the outcome into ``run``.

    When ``azure_ai_project`` (the Foundry project endpoint) is provided the
    results — metrics, per-row scores and traces — are uploaded to the Azure AI
    Foundry project so the run is auditable and trackable in the portal. The
    returned ``studio_url`` deep-links to that stored run.
    """
    from azure.ai.evaluation import evaluate

    if not evaluators:
        run.error = (
            "No evaluators could be constructed. See the skipped list for details."
        )
        return run

    started = time.perf_counter()
    eval_kwargs: dict[str, Any] = dict(
        data=path,
        evaluators=evaluators,
        evaluator_config=config,
        evaluation_name=name,
        fail_on_evaluator_errors=False,
    )
    if azure_ai_project:
        # Upload metrics/rows/traces to the Foundry project for audit & tracking.
        eval_kwargs["azure_ai_project"] = azure_ai_project
        eval_kwargs["tags"] = {"agent": AGENT_NAME, "source": "agentic-aiops-studio"}

    try:
        result = evaluate(**eval_kwargs)
    except Exception as exc:
        # If the upload path fails, retry once locally so the user still gets
        # scores instead of an empty result.
        if azure_ai_project:
            eval_kwargs.pop("azure_ai_project", None)
            eval_kwargs.pop("tags", None)
            try:
                result = evaluate(**eval_kwargs)
                run.error = (
                    "Results computed locally but could NOT be uploaded to the "
                    f"Foundry project: {type(exc).__name__}: {exc}"
                )
            except Exception as exc2:
                run.error = f"{type(exc2).__name__}: {exc2}"
                run.duration_s = round(time.perf_counter() - started, 2)
                return run
        else:
            run.error = f"{type(exc).__name__}: {exc}"
            run.duration_s = round(time.perf_counter() - started, 2)
            return run

    run.duration_s = round(time.perf_counter() - started, 2)
    run.evaluators = list(evaluators.keys())
    run.metrics = dict(result.get("metrics", {}) or {})
    run.rows = list(result.get("rows", []) or [])
    run.studio_url = result.get("studio_url")
    return run


def run_evaluation(
    categories: tuple[str, ...] = ALL_CATEGORIES,
    dataset_path: str | None = None,
) -> EvalRun:
    """Run a model-evaluation batch and return a structured :class:`EvalRun`.

    The credential is a synchronous ``DefaultAzureCredential``; the judge model
    authenticates via an AAD token provider derived from it.
    """
    from azure.identity import DefaultAzureCredential

    settings = require_settings()
    path = dataset_path or settings.eval_dataset_path
    run = EvalRun(
        judge_model=settings.judge_model,
        dataset_path=path,
        row_count=_count_rows(path),
    )

    credential = DefaultAzureCredential()
    evaluators, config, skipped = _build_evaluators(
        tuple(categories), settings, credential
    )
    run.skipped = skipped
    return _execute(
        run,
        path,
        evaluators,
        config,
        f"{AGENT_NAME}-model-eval",
        azure_ai_project=settings.project_endpoint or None,
    )


def _normalize_agent_dataset(src_path: str) -> str:
    """Wrap each row's ``response`` in a message list and write a temp JSONL.

    The shipped ``datarfpagent.jsonl`` stores ``response`` as a single assistant
    message object, but the agent evaluators expect a list of messages.
    """
    import json
    import tempfile

    out = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    with open(src_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            response = row.get("response")
            if response is not None and not isinstance(response, list):
                row["response"] = [response]
            out.write(json.dumps(row) + "\n")
    out.close()
    return out.name


def _build_agent_evaluators(
    settings: Settings, credential: Any
) -> tuple[dict[str, Any], dict[str, dict], list[tuple[str, str]]]:
    """Instantiate the agent-behaviour evaluators + column mappings."""
    import azure.ai.evaluation as ev

    evaluators: dict[str, Any] = {}
    config: dict[str, dict] = {}
    skipped: list[tuple[str, str]] = []

    if not settings.aoai_endpoint or not settings.judge_model:
        skipped.append(
            (
                "agent",
                "Judge model endpoint/deployment not configured "
                "(set AZURE_OPENAI_ENDPOINT / AZURE_EVAL_JUDGE_MODEL).",
            )
        )
        return evaluators, config, skipped

    mc = _model_config(settings)
    rk = {"is_reasoning_model": settings.judge_is_reasoning}
    mapping = {
        "column_mapping": {
            "query": "${data.query}",
            "response": "${data.response}",
            "tool_definitions": "${data.tool_definitions}",
        }
    }
    specs: list[tuple[str, Callable[[], Any]]] = [
        ("intent_resolution", lambda: ev.IntentResolutionEvaluator(mc, credential=credential, **rk)),
        ("tool_call_accuracy", lambda: ev.ToolCallAccuracyEvaluator(mc, credential=credential, **rk)),
        ("task_adherence", lambda: ev.TaskAdherenceEvaluator(mc, credential=credential, **rk)),
    ]
    for name, factory in specs:
        try:
            evaluators[name] = factory()
            config[name] = mapping
        except Exception as exc:  # pragma: no cover - defensive
            skipped.append((name, f"{type(exc).__name__}: {exc}"))
    return evaluators, config, skipped


def run_agent_evaluation(dataset_path: str | None = None) -> EvalRun:
    """Evaluate tool-using agent behaviour against ``datarfpagent.jsonl``.

    Runs intent resolution, tool-call accuracy and task adherence using the
    judge model (DefaultAzureCredential / AAD).
    """
    import os

    from azure.identity import DefaultAzureCredential

    settings = require_settings()
    src = dataset_path or settings.eval_agent_dataset_path
    run = EvalRun(
        judge_model=settings.judge_model,
        dataset_path=src,
        row_count=_count_rows(src),
    )

    credential = DefaultAzureCredential()
    evaluators, config, skipped = _build_agent_evaluators(settings, credential)
    run.skipped = skipped
    if not evaluators:
        run.error = (
            "No agent evaluators could be constructed. See the skipped list."
        )
        return run

    normalized = _normalize_agent_dataset(src)
    try:
        _execute(
            run,
            normalized,
            evaluators,
            config,
            f"{AGENT_NAME}-agent-eval",
            azure_ai_project=settings.project_endpoint or None,
        )
    finally:
        try:
            os.unlink(normalized)
        except OSError:
            pass
    return run
