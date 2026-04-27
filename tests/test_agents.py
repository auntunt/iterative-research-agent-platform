import pytest

from app.agents.planner import PlannerAgent
from app.core.config import Settings
from app.core.llm_router import LLMRouter


@pytest.mark.asyncio
async def test_planner_emits_dynamic_task_graph() -> None:
    router = LLMRouter(Settings(simple_task_provider="local", complex_task_provider="local"))
    planner = PlannerAgent(router)

    run = await planner.run("Compare multi-agent orchestration strategies for production systems", {})

    graph = run.output.metadata["task_graph"]
    agents = [step["agent"] for step in graph["steps"]]
    assert 3 <= agents.count("researcher") <= 5
    assert "critic" in agents
    assert "writer" in agents
    assert len(run.output.metadata["research_topics"]) == agents.count("researcher")
