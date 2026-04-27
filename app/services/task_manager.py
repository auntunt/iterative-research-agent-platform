from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from uuid import uuid4

from app.core.config import Settings
from app.core.logger import get_logger, log_context
from app.models.schemas import (
    ErrorCategory,
    LLMUsage,
    LogEntry,
    StepState,
    StepStatus,
    TaskGraph,
    TaskMetrics,
    TaskState,
    TaskStatus,
    ToolResult,
)

logger = get_logger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskNotFoundError(KeyError):
    pass


class TaskManager:
    def __init__(self, settings: Settings | None = None, database_url: str | None = None) -> None:
        self.database_url = database_url or (settings.database_url if settings else "sqlite:///:memory:")
        self._lock = asyncio.Lock()
        self._conn = self._connect(self.database_url)
        self._ensure_schema()

    @contextmanager
    def _transaction(self) -> Generator[None, None, None]:
        """将多个 SQL 写操作合并为单一原子事务。"""
        try:
            yield
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    async def create_task(
        self,
        task: str,
        metadata: dict[str, object] | None = None,
        request_id: str = "",
        trace_id: str | None = None,
    ) -> TaskState:
        state = TaskState(
            task=task,
            metadata=dict(metadata or {}),
            request_id=request_id,
            trace_id=trace_id or str(uuid4()),
        )
        async with self._lock:
            with self._transaction():
                self._upsert_task(state)
                self._insert_log(
                    LogEntry(
                        task_id=state.task_id,
                        trace_id=state.trace_id,
                        request_id=state.request_id,
                        event="task_created",
                        message="Task created",
                        payload={"task": task},
                    )
                )
        logger.info("task_created", extra=log_context(task_id=state.task_id, trace_id=state.trace_id))
        return state.model_copy(deep=True)

    async def get_task(self, task_id: str) -> TaskState:
        async with self._lock:
            return self._load_task(task_id).model_copy(deep=True)

    async def list_tasks(self, limit: int | None = None, offset: int = 0) -> list[TaskState]:
        async with self._lock:
            if limit is None:
                rows = self._conn.execute(
                    "SELECT task_id FROM tasks ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT task_id FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            return [self._load_task(row["task_id"]).model_copy(deep=True) for row in rows]

    async def delete_task(self, task_id: str) -> bool:
        async with self._lock:
            row = self._conn.execute("SELECT task_id FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                return False
            with self._transaction():
                self._conn.execute("DELETE FROM steps WHERE task_id = ?", (task_id,))
                self._conn.execute("DELETE FROM logs WHERE task_id = ?", (task_id,))
                self._conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            return True

    async def set_status(self, task_id: str, status: TaskStatus, error: str | None = None) -> TaskState:
        async with self._lock:
            task = self._load_task(task_id)
            task.status = status
            task.error = error
            task.updated_at = utc_now()
            self._recompute_metrics(task)
            with self._transaction():
                self._upsert_task(task)
                self._insert_log(
                    LogEntry(
                        task_id=task.task_id,
                        trace_id=task.trace_id,
                        request_id=task.request_id,
                        level="ERROR" if status == TaskStatus.FAILED else "INFO",
                        event="task_status_updated",
                        message=f"Task status changed to {status.value}",
                        payload={"status": status.value, "error": error},
                    )
                )
            logger.info(
                "task_status_updated",
                extra=log_context(task_id=task_id, trace_id=task.trace_id, status=status.value),
            )
            return task.model_copy(deep=True)

    async def set_task_graph(self, task_id: str, task_graph: TaskGraph) -> TaskState:
        async with self._lock:
            task = self._load_task(task_id)
            task.task_graph = task_graph
            task.updated_at = utc_now()
            with self._transaction():
                self._upsert_task(task)
                self._insert_log(
                    LogEntry(
                        task_id=task.task_id,
                        trace_id=task.trace_id,
                        request_id=task.request_id,
                        event="task_graph_updated",
                        message="Planner emitted task graph",
                        payload=task_graph.model_dump(mode="json"),
                    )
                )
            return task.model_copy(deep=True)

    async def add_step(
        self,
        task_id: str,
        agent: str,
        step_input: str,
        depends_on: list[int] | None = None,
    ) -> StepState:
        async with self._lock:
            task = self._load_task(task_id)
            next_id = self._next_step_id(task_id)
            step = StepState(
                step_id=next_id,
                agent=agent,
                input=step_input,
                status=StepStatus.PENDING,
                trace_id=task.trace_id,
                depends_on=depends_on or [],
            )
            task.steps.append(step)
            task.updated_at = utc_now()
            self._recompute_metrics(task)
            with self._transaction():
                self._upsert_step(task_id, step)
                self._upsert_task(task)
                self._insert_log(
                    LogEntry(
                        task_id=task.task_id,
                        trace_id=task.trace_id,
                        request_id=task.request_id,
                        event="task_step_added",
                        message=f"Added step {step.step_id} for {agent}",
                        payload={"step_id": step.step_id, "agent": agent, "depends_on": step.depends_on},
                    )
                )
            logger.info(
                "task_step_added",
                extra=log_context(task_id=task_id, trace_id=task.trace_id, step_id=step.step_id, agent=agent),
            )
            return step.model_copy(deep=True)

    async def start_step(self, task_id: str, step_id: int, retries: int) -> StepState:
        async with self._lock:
            task = self._load_task(task_id)
            step = self._require_step(task, step_id)
            step.status = StepStatus.RUNNING
            step.started_at = step.started_at or utc_now()
            step.retries = retries
            step.error = None
            step.error_category = None
            task.updated_at = utc_now()
            with self._transaction():
                self._upsert_step(task_id, step)
                self._upsert_task(task)
                self._insert_log(
                    LogEntry(
                        task_id=task.task_id,
                        trace_id=task.trace_id,
                        request_id=task.request_id,
                        event="task_step_started",
                        message=f"Started step {step_id}",
                        payload={"step_id": step_id, "retries": retries},
                    )
                )
            logger.info(
                "task_step_started",
                extra=log_context(task_id=task_id, trace_id=task.trace_id, step_id=step_id, retries=retries),
            )
            return step.model_copy(deep=True)

    async def finish_step(
        self,
        task_id: str,
        step_id: int,
        output: str,
        latency: float,
        llm_usage: list[LLMUsage],
        tool_calls: list[ToolResult],
    ) -> StepState:
        async with self._lock:
            task = self._load_task(task_id)
            step = self._require_step(task, step_id)
            step.status = StepStatus.SUCCESS
            step.output = output
            step.latency = latency
            step.finished_at = utc_now()
            step.tool_calls = tool_calls
            step.llm_usage = llm_usage
            if llm_usage:
                step.llm_provider = llm_usage[-1].provider
                step.llm_model = llm_usage[-1].model
            task.updated_at = utc_now()
            self._recompute_metrics(task)
            with self._transaction():
                self._upsert_step(task_id, step)
                self._upsert_task(task)
                self._insert_log(
                    LogEntry(
                        task_id=task.task_id,
                        trace_id=task.trace_id,
                        request_id=task.request_id,
                        event="task_step_succeeded",
                        message=f"Step {step_id} succeeded",
                        payload={"step_id": step_id, "latency": latency, "llm_usage": [u.model_dump(mode="json") for u in llm_usage]},
                    )
                )
            logger.info(
                "task_step_succeeded",
                extra=log_context(task_id=task_id, trace_id=task.trace_id, step_id=step_id, latency=latency),
            )
            return step.model_copy(deep=True)

    async def fail_step(
        self,
        task_id: str,
        step_id: int,
        error: str,
        category: ErrorCategory,
        retries: int,
    ) -> StepState:
        async with self._lock:
            task = self._load_task(task_id)
            step = self._require_step(task, step_id)
            step.status = StepStatus.FAILED
            step.error = error
            step.error_category = category
            step.retries = retries
            step.finished_at = utc_now()
            task.updated_at = utc_now()
            self._recompute_metrics(task)
            with self._transaction():
                self._upsert_step(task_id, step)
                self._upsert_task(task)
                self._insert_log(
                    LogEntry(
                        task_id=task.task_id,
                        trace_id=task.trace_id,
                        request_id=task.request_id,
                        level="ERROR",
                        event="task_step_failed",
                        message=f"Step {step_id} failed",
                        payload={"step_id": step_id, "error": error, "category": category.value, "retries": retries},
                    )
                )
            logger.warning(
                "task_step_failed",
                extra=log_context(task_id=task_id, trace_id=task.trace_id, step_id=step_id, category=category.value, error=error),
            )
            return step.model_copy(deep=True)

    async def set_result(self, task_id: str, result: str) -> TaskState:
        async with self._lock:
            task = self._load_task(task_id)
            task.result = result
            task.updated_at = utc_now()
            self._recompute_metrics(task)
            with self._transaction():
                self._upsert_task(task)
            return task.model_copy(deep=True)

    async def append_log(self, entry: LogEntry) -> None:
        async with self._lock:
            with self._transaction():
                self._insert_log(entry)

    async def get_logs(self, task_id: str | None = None, limit: int = 100, offset: int = 0) -> list[LogEntry]:
        async with self._lock:
            params: list[Any] = []
            where = ""
            if task_id:
                where = "WHERE task_id = ?"
                params.append(task_id)
            params.extend([limit, offset])
            rows = self._conn.execute(
                f"SELECT * FROM logs {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
            return [self._row_to_log(row) for row in rows]

    async def platform_metrics(self) -> TaskMetrics:
        async with self._lock:
            metrics = TaskMetrics()
            rows = self._conn.execute("SELECT task_id FROM tasks").fetchall()
            for row in rows:
                task = self._load_task(row["task_id"])
                metrics.total_steps += task.metrics.total_steps
                metrics.successful_steps += task.metrics.successful_steps
                metrics.failed_steps += task.metrics.failed_steps
                metrics.retries += task.metrics.retries
                metrics.latency += task.metrics.latency
                metrics.llm_calls += task.metrics.llm_calls
                metrics.total_tokens += task.metrics.total_tokens
                metrics.estimated_cost += task.metrics.estimated_cost
                for provider, count in task.metrics.provider_usage.items():
                    metrics.provider_usage[provider] = metrics.provider_usage.get(provider, 0) + count
            metrics.success_rate = metrics.successful_steps / metrics.total_steps if metrics.total_steps else 0.0
            metrics.avg_latency = metrics.latency / metrics.total_steps if metrics.total_steps else 0.0
            return metrics

    async def status_counts(self) -> dict[str, int]:
        async with self._lock:
            rows = self._conn.execute("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status").fetchall()
            return {row["status"]: row["count"] for row in rows}

    async def task_count(self) -> int:
        async with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()
            return int(row["count"])

    async def recover_interrupted_tasks(self) -> int:
        async with self._lock:
            rows = self._conn.execute(
                "SELECT task_id FROM tasks WHERE status = ?",
                (TaskStatus.RUNNING.value,),
            ).fetchall()
            recovered = 0
            for row in rows:
                task = self._load_task(row["task_id"])
                task.status = TaskStatus.FAILED
                task.error = "Task was interrupted by a previous process shutdown."
                task.updated_at = utc_now()
                self._recompute_metrics(task)
                with self._transaction():
                    self._upsert_task(task)
                    self._insert_log(
                        LogEntry(
                            task_id=task.task_id,
                            trace_id=task.trace_id,
                            request_id=task.request_id,
                            level="WARN",
                            event="task_recovered_after_shutdown",
                            message="Recovered interrupted running task",
                            payload={"status": "failed", "reason": task.error},
                        )
                    )
                recovered += 1
            return recovered

    def _connect(self, database_url: str) -> sqlite3.Connection:
        if not database_url.startswith("sqlite:///"):
            raise ValueError("This build supports SQLite database_url values such as sqlite:///data/platform.db")
        path = database_url.removeprefix("sqlite:///")
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                status TEXT NOT NULL,
                task_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_trace_id ON tasks(trace_id);

            CREATE TABLE IF NOT EXISTS steps (
                task_id TEXT NOT NULL,
                step_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                agent TEXT NOT NULL,
                step_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (task_id, step_id)
            );
            CREATE INDEX IF NOT EXISTS idx_steps_task_id ON steps(task_id);

            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                trace_id TEXT,
                request_id TEXT,
                level TEXT NOT NULL,
                event TEXT NOT NULL,
                message TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_logs_task_id ON logs(task_id);
            CREATE INDEX IF NOT EXISTS idx_logs_trace_id ON logs(trace_id);
            """
        )
        self._conn.commit()

    def _load_task(self, task_id: str) -> TaskState:
        row = self._conn.execute("SELECT task_json FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise TaskNotFoundError(task_id)
        task = TaskState.model_validate_json(row["task_json"])
        step_rows = self._conn.execute(
            "SELECT step_json FROM steps WHERE task_id = ? ORDER BY step_id ASC",
            (task_id,),
        ).fetchall()
        task.steps = [StepState.model_validate_json(step_row["step_json"]) for step_row in step_rows]
        self._recompute_metrics(task)
        return task

    def _upsert_task(self, task: TaskState) -> None:
        payload = task.model_dump_json()
        self._conn.execute(
            """
            INSERT INTO tasks (task_id, trace_id, request_id, status, task_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                trace_id = excluded.trace_id,
                request_id = excluded.request_id,
                status = excluded.status,
                task_json = excluded.task_json,
                updated_at = excluded.updated_at
            """,
            (
                task.task_id,
                task.trace_id,
                task.request_id,
                task.status.value,
                payload,
                task.created_at.isoformat(),
                task.updated_at.isoformat(),
            ),
        )

    def _upsert_step(self, task_id: str, step: StepState) -> None:
        now = utc_now().isoformat()
        self._conn.execute(
            """
            INSERT INTO steps (task_id, step_id, status, agent, step_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id, step_id) DO UPDATE SET
                status = excluded.status,
                agent = excluded.agent,
                step_json = excluded.step_json,
                updated_at = excluded.updated_at
            """,
            (
                task_id,
                step.step_id,
                step.status.value,
                step.agent,
                step.model_dump_json(),
                step.created_at.isoformat(),
                now,
            ),
        )

    def _insert_log(self, entry: LogEntry) -> None:
        self._conn.execute(
            """
            INSERT INTO logs (task_id, trace_id, request_id, level, event, message, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.task_id,
                entry.trace_id,
                entry.request_id,
                entry.level,
                entry.event,
                entry.message,
                json.dumps(entry.payload, default=str),
                entry.created_at.isoformat(),
            ),
        )

    def _row_to_log(self, row: sqlite3.Row) -> LogEntry:
        return LogEntry(
            id=row["id"],
            task_id=row["task_id"],
            trace_id=row["trace_id"],
            request_id=row["request_id"],
            level=row["level"],
            event=row["event"],
            message=row["message"] or "",
            payload=json.loads(row["payload_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _next_step_id(self, task_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(step_id), 0) + 1 AS next_id FROM steps WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return int(row["next_id"])

    @staticmethod
    def _require_step(task: TaskState, step_id: int) -> StepState:
        for step in task.steps:
            if step.step_id == step_id:
                return step
        raise KeyError(f"step {step_id}")

    @staticmethod
    def _recompute_metrics(task: TaskState) -> None:
        metrics = TaskMetrics()
        metrics.total_steps = len(task.steps)
        metrics.successful_steps = sum(step.status == StepStatus.SUCCESS for step in task.steps)
        metrics.failed_steps = sum(step.status == StepStatus.FAILED for step in task.steps)
        metrics.retries = sum(step.retries for step in task.steps)
        metrics.latency = sum(step.latency for step in task.steps)
        metrics.llm_calls = sum(len(step.llm_usage) for step in task.steps)
        for step in task.steps:
            for usage in step.llm_usage:
                metrics.total_tokens += usage.total_tokens or usage.prompt_tokens + usage.completion_tokens
                metrics.estimated_cost += usage.estimated_cost
                metrics.provider_usage[usage.provider] = metrics.provider_usage.get(usage.provider, 0) + 1
        metrics.success_rate = metrics.successful_steps / metrics.total_steps if metrics.total_steps else 0.0
        metrics.avg_latency = metrics.latency / metrics.total_steps if metrics.total_steps else 0.0
        task.metrics = metrics
