import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tools.web_fetch import WebFetchTool
from app.tools.web_search import WebSearchTool


def test_run_task_and_get_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(self, tool_input: str, **kwargs: object) -> str:
        return json.dumps(
            {
                "query": tool_input,
                "provider": "duckduckgo",
                "results": [
                    {
                        "title": "Example result",
                        "url": "https://example.com/article",
                        "paragraphs": [{"paragraph_id": "snippet-1", "text": "Example snippet for testing."}],
                        "score": 0.9,
                        "provider": "duckduckgo",
                    }
                ],
            }
        )

    async def fake_fetch(self, tool_input: str, **kwargs: object) -> str:
        return json.dumps(
            {
                "url": tool_input,
                "title": "Example article",
                "paragraphs": [
                    {"paragraph_id": "p1", "text": "A first long paragraph that supports the downstream report."},
                    {"paragraph_id": "p2", "text": "A second long paragraph that gives critic enough material."},
                ],
                "provider": "http_fetch",
            }
        )

    monkeypatch.setattr(WebSearchTool, "run", fake_search)
    monkeypatch.setattr(WebFetchTool, "run", fake_fetch)

    app = create_app()
    with TestClient(app) as client:
        response = client.post("/run_task", json={"task": "Write a short platform summary"})
        assert response.status_code == 202
        task_id = response.json()["task_id"]

        state = {}
        for _ in range(20):
            state = client.get(f"/task/{task_id}").json()
            if state["status"] in {"success", "failed"}:
                break

        assert state["status"] == "success"
        assert state["trace_id"]
        assert len(state["steps"]) >= 2
        evidence = client.get(f"/task/{task_id}/evidence").json()
        assert "cards" in evidence
        assert "critic_assessments" in evidence

        artifacts = client.post(f"/task/{task_id}/artifacts").json()
        assert artifacts["task_id"] == task_id
        assert "report.md" in artifacts["files"]

        metrics = client.get("/metrics").json()
        assert metrics["task_count"] >= 1
        assert "queue" in metrics

        tasks_page = client.get("/tasks?page=1&page_size=5").json()
        assert tasks_page["page"] == 1
        assert tasks_page["page_size"] == 5
        assert "total" in tasks_page
        assert "total_pages" in tasks_page

        providers = client.get("/search/providers").json()
        assert "providers" in providers
        assert "mock" not in providers["providers"]

        logs = client.get(f"/logs?task_id={task_id}").json()
        assert logs["logs"]

        deleted = client.delete(f"/task/{task_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"task_id": task_id, "deleted": True}

        missing = client.get(f"/task/{task_id}")
        assert missing.status_code == 404
