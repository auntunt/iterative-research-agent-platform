from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from statistics import mean
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import Settings
from app.models.schemas import AgentOutput
from app.services import MemoryStore, Orchestrator, TaskManager
from app.services.artifacts import ResearchArtifactExporter


def _load_questions(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_cards_and_assessments(task) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cards: dict[str, dict[str, Any]] = {}
    assessments: list[dict[str, Any]] = []
    for step in task.steps:
        if not step.output:
            continue
        try:
            output = AgentOutput.model_validate_json(step.output)
        except Exception:
            continue
        for card in output.metadata.get("evidence_cards", []):
            cards[card["card_id"]] = card
        if step.agent == "critic":
            assessments.append(output.metadata)
    return list(cards.values()), assessments


def _score_result(question: dict[str, Any], task, cards: list[dict[str, Any]], assessments: list[dict[str, Any]]) -> dict[str, Any]:
    urls = {card["citation"]["url"] for card in cards if card.get("citation", {}).get("url")}
    topics = {card.get("topic_id", "") for card in cards}
    latest = assessments[-1] if assessments else {}
    expected = question.get("expected_topics", [])
    joined_text = f"{task.result}\n{json.dumps(cards, ensure_ascii=False)}"
    keyword_hits = sum(1 for keyword in expected if keyword.lower() in joined_text.lower())
    citation_count = task.result.count("](")
    return {
        "id": question["id"],
        "task_id": task.task_id,
        "status": task.status.value,
        "evidence_cards": len(cards),
        "unique_sources": len(urls),
        "topic_count": len(topics),
        "citation_count": citation_count,
        "keyword_recall": round(keyword_hits / max(1, len(expected)), 3),
        "coverage_score": latest.get("coverage_score", 0.0),
        "faithfulness_score": latest.get("faithfulness_score", 0.0),
        "answer_relevancy_score": latest.get("answer_relevancy_score", 0.0),
        "latency": round(task.metrics.latency, 4),
        "llm_calls": task.metrics.llm_calls,
        "estimated_cost": round(task.metrics.estimated_cost, 6),
    }


async def _run_one(question: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    settings = Settings(
        simple_task_provider="local",
        complex_task_provider="local",
        fallback_providers=["local"],
        web_search_provider="duckduckgo",
        max_retries=1,
    )
    task_manager = TaskManager(database_url="sqlite:///:memory:")
    memory = MemoryStore(settings)
    orchestrator = Orchestrator(settings, task_manager, memory)
    task = await task_manager.create_task(
        question["question"],
        metadata={
            "research_depth": "deep",
            "max_topics": 5,
            "max_research_rounds": 2,
            "min_evidence_per_topic": 2,
            "max_sources_per_query": 3,
            "web_search_provider": "duckduckgo",
        },
    )
    result = await orchestrator.execute_task(task.task_id)
    cards, assessments = _extract_cards_and_assessments(result)
    logs = await task_manager.get_logs(task_id=result.task_id, limit=500)
    exporter = ResearchArtifactExporter(str(output_dir / "runs"))
    artifact = exporter.export(result, logs)
    scored = _score_result(question, result, cards, assessments)
    scored["artifact_dir"] = artifact["output_dir"]
    return scored


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline deep research evaluation set.")
    parser.add_argument("--questions", default="evals/questions.json")
    parser.add_argument("--output", default="evals/results")
    args = parser.parse_args()

    questions_path = Path(args.questions)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    questions = _load_questions(questions_path)

    rows = []
    for question in questions:
        rows.append(await _run_one(question, output_dir))

    summary = {
        "count": len(rows),
        "avg_evidence_cards": round(mean(row["evidence_cards"] for row in rows), 3),
        "avg_unique_sources": round(mean(row["unique_sources"] for row in rows), 3),
        "avg_keyword_recall": round(mean(row["keyword_recall"] for row in rows), 3),
        "avg_coverage_score": round(mean(row["coverage_score"] for row in rows), 3),
        "avg_faithfulness_score": round(mean(row["faithfulness_score"] for row in rows), 3),
        "avg_answer_relevancy_score": round(mean(row["answer_relevancy_score"] for row in rows), 3),
        "avg_llm_calls": round(mean(row["llm_calls"] for row in rows), 3),
        "total_estimated_cost": round(sum(row["estimated_cost"] for row in rows), 6),
    }
    payload = {"summary": summary, "results": rows}
    (output_dir / "eval_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
