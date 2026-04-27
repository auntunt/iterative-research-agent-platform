from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models.schemas import AgentOutput, CriticAssessment, EvidenceCard, LogEntry, TaskState


class ResearchArtifactExporter:
    def __init__(self, output_root: str = "artifacts/runs") -> None:
        self.output_root = Path(output_root)

    def collect_evidence(self, task: TaskState) -> tuple[list[EvidenceCard], list[CriticAssessment]]:
        cards_by_id: dict[str, EvidenceCard] = {}
        assessments: list[CriticAssessment] = []
        for step in task.steps:
            if not step.output:
                continue
            try:
                output = AgentOutput.model_validate_json(step.output)
            except Exception:
                continue
            for raw_card in output.metadata.get("evidence_cards", []):
                card = EvidenceCard.model_validate(raw_card)
                cards_by_id[card.card_id] = card
            if step.agent == "critic":
                assessments.append(CriticAssessment.model_validate(output.metadata))
        return list(cards_by_id.values()), assessments

    def build_manifest(
        self,
        task: TaskState,
        cards: list[EvidenceCard],
        assessments: list[CriticAssessment],
        logs: list[LogEntry],
    ) -> dict[str, Any]:
        latest_assessment = assessments[-1] if assessments else CriticAssessment()
        source_urls = {card.citation.url for card in cards if card.citation.url}
        return {
            "task_id": task.task_id,
            "trace_id": task.trace_id,
            "task": task.task,
            "status": task.status.value,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
            "report_file": "report.md",
            "evidence_file": "evidence_cards.json",
            "citations_file": "citations.json",
            "trace_file": "trace.json",
            "metrics_file": "metrics.json",
            "evidence_card_count": len(cards),
            "unique_source_count": len(source_urls),
            "critic_assessment": latest_assessment.model_dump(mode="json"),
            "step_count": len(task.steps),
            "log_count": len(logs),
        }

    def export(self, task: TaskState, logs: list[LogEntry]) -> dict[str, Any]:
        cards, assessments = self.collect_evidence(task)
        output_dir = self.output_root / task.task_id
        output_dir.mkdir(parents=True, exist_ok=True)
        citations = [
            {
                "card_id": card.card_id,
                "topic_id": card.topic_id,
                "url": card.citation.url,
                "title": card.citation.title,
                "paragraph_id": card.citation.paragraph_id,
                "snippet": card.citation.snippet,
            }
            for card in cards
        ]
        manifest = self.build_manifest(task, cards, assessments, logs)
        files: dict[str, str] = {
            "report.md": task.result or "",
            "evidence_cards.json": json.dumps([card.model_dump(mode="json") for card in cards], ensure_ascii=False, indent=2),
            "citations.json": json.dumps(citations, ensure_ascii=False, indent=2),
            "trace.json": json.dumps([log.model_dump(mode="json") for log in logs], ensure_ascii=False, indent=2, default=str),
            "metrics.json": task.metrics.model_dump_json(indent=2),
            "run_manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2),
        }
        for name, content in files.items():
            (output_dir / name).write_text(content, encoding="utf-8")
        return {
            "task_id": task.task_id,
            "output_dir": str(output_dir),
            "files": sorted(files),
            "manifest": manifest,
        }
