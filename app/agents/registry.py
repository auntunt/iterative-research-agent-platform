from __future__ import annotations

from app.agents.base_agent import BaseAgent
from app.core.config import Settings


class AgentRegistry:
    def __init__(self, settings: Settings, agents: dict[str, BaseAgent]) -> None:
        self.settings = settings
        self._agents = agents

    def get(self, name: str) -> BaseAgent:
        if not self.is_enabled(name):
            raise KeyError(f"Agent disabled: {name}")
        return self._agents[name]

    def is_enabled(self, name: str) -> bool:
        return self.settings.enabled_agents.get(name, True) and name in self._agents

    def select_for_goal(self, goal: str) -> str:
        lowered = goal.lower()
        if any(token in lowered for token in ["research", "source", "evidence", "collect"]):
            return "researcher"
        if any(token in lowered for token in ["critic", "sufficiency", "faithfulness", "coverage", "validate"]):
            return "critic"
        if any(token in lowered for token in ["review", "quality", "gap"]):
            return "critic"
        if any(token in lowered for token in ["write", "compose", "report", "final"]):
            return "writer"
        return "writer"

    def enabled_agent_names(self) -> list[str]:
        return [name for name in self._agents if self.is_enabled(name)]
