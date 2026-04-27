from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.llm_router import LLMContext, LLMRouter, RoutingDecision
from app.core.token_counter import count_tokens
from app.models.schemas import AgentOutput, LLMUsage, ToolResult
from app.tools.base import BaseTool


@dataclass
class AgentRun:
    output: AgentOutput
    llm_usage: list[LLMUsage] = field(default_factory=list)
    tool_calls: list[ToolResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    routing_decisions: list[RoutingDecision] = field(default_factory=list)


class BaseAgent:
    name = "base"
    role = "generic agent"

    def __init__(self, llm_router: LLMRouter, tools: list[BaseTool] | None = None) -> None:
        self.llm_router = llm_router
        self.tools = {tool.name: tool for tool in tools or []}

    async def run(self, task: str, context: dict[str, Any]) -> AgentRun:
        plan = await self.plan(task, context)
        action = await self.act(task, context, plan)
        observation = await self.observe(task, context, action)
        action.output.metadata.update({"observation": observation, "plan": plan})
        return action

    async def plan(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "objective": task,
            "tools": [],
            "success_criteria": ["structured output", "traceable reasoning", "bounded execution"],
        }

    async def act(self, task: str, context: dict[str, Any], plan: dict[str, Any]) -> AgentRun:
        prompt = self._prompt(task, context, plan)
        text, usage, routing = await self._generate(prompt, task, context)
        return AgentRun(
            output=AgentOutput(
                thought=f"{self.role} processed the task according to its plan.",
                action="llm_generate",
                result=text,
            ),
            llm_usage=[usage],
            routing_decisions=[routing],
        )

    async def observe(self, task: str, context: dict[str, Any], run: AgentRun) -> dict[str, Any]:
        return {
            "complete": bool(run.output.result.strip()),
            "tool_success": all(call.success for call in run.tool_calls),
            "llm_calls": len(run.llm_usage),
        }

    def decide_next_action(self, task: str, context: dict[str, Any], plan: dict[str, Any]) -> str:
        if plan.get("tools"):
            return "tool_chain"
        if context.get("previous_outputs"):
            return "llm_synthesize"
        return "llm_generate"

    async def call_tool(self, name: str, tool_input: str, **kwargs: Any) -> ToolResult:
        tool = self.tools.get(name)
        if not tool:
            raise KeyError(f"Tool not registered for {self.name}: {name}")
        return await tool(tool_input, **kwargs)

    async def _generate(
        self, prompt: str, task: str, context: dict[str, Any]
    ) -> tuple[str, LLMUsage, RoutingDecision]:
        token_count = count_tokens(prompt)
        metadata = {**context, "estimated_prompt_tokens": token_count}
        llm_context = LLMContext(
            task=task,
            agent=self.name,
            complexity=context.get("complexity", "simple"),
            excluded_providers=set(context.get("excluded_providers", set())),
            metadata=metadata,
        )
        return await self.llm_router.generate(prompt, llm_context)

    def _prompt(self, task: str, context: dict[str, Any], plan: dict[str, Any]) -> str:
        return (
            f"Agent: {self.name}\n"
            f"Role: {self.role}\n"
            f"Task: {task}\n"
            f"Context: {context}\n"
            f"Plan: {plan}\n"
            "Return concise enterprise-grade output."
        )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return count_tokens(text)
