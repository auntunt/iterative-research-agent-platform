from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from app.core.config import Settings
from app.core.logger import get_logger, log_context
from app.services.orchestrator import Orchestrator

try:
    import redis.asyncio as redis
except ImportError:  # pragma: no cover - optional dependency fallback
    redis = None

logger = get_logger(__name__)


class QueueFullError(RuntimeError):
    pass


class TaskQueue:
    def __init__(self, settings: Settings, orchestrator: Orchestrator) -> None:
        self.settings = settings
        self.orchestrator = orchestrator
        self._queue: asyncio.Queue[str] | None = None
        self._workers: list[asyncio.Task[None]] = []
        self._running = False
        self._redis_client: Any | None = None
        self._redis_available = False
        # 重连状态：下次允许重连的时间点，当前退避间隔
        self._redis_reconnect_at: float = 0.0
        self._redis_backoff: float = 0.0

    async def start(self) -> None:
        if self._workers:
            return
        self._running = True
        self._queue = asyncio.Queue(maxsize=self.settings.max_queue_size)
        await self._connect_redis()
        for index in range(self.settings.max_concurrent_tasks):
            self._workers.append(
                asyncio.create_task(self._work(index), name=f"task-queue-worker-{index}")
            )
        logger.info(
            "task_queue_started",
            extra=log_context(
                backend=self.backend,
                max_concurrent_tasks=self.settings.max_concurrent_tasks,
                max_queue_size=self.settings.max_queue_size,
            ),
        )

    async def stop(self) -> None:
        self._running = False
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            with suppress(asyncio.CancelledError, asyncio.TimeoutError, TimeoutError):
                await worker
        self._workers.clear()
        if self._redis_client:
            await self._redis_client.aclose()
        logger.info("task_queue_stopped")

    @property
    def backend(self) -> str:
        if self.settings.queue_backend == "redis" and self._redis_available:
            return "redis"
        return "memory"

    async def enqueue(self, task_id: str) -> None:
        # 若 Redis 离线，尝试重连后再决定 backend
        if self.settings.queue_backend == "redis" and not self._redis_available:
            await self._try_reconnect_redis()

        if self.backend == "redis":
            if self._redis_client is None:
                raise RuntimeError("Redis client is not initialized")
            try:
                length = await self._redis_client.llen(self.settings.redis_queue_name)
                if length >= self.settings.max_queue_size:
                    raise QueueFullError("Task queue is full.")
                await self._redis_client.rpush(self.settings.redis_queue_name, task_id)
                queue_size = length + 1
            except QueueFullError:
                raise
            except Exception as exc:
                # Redis 操作失败：标记离线，回退到内存队列
                logger.warning("redis_operation_failed_falling_back", extra=log_context(error=str(exc)))
                self._redis_available = False
                self._schedule_reconnect()
                return await self._enqueue_memory(task_id)
        else:
            await self._enqueue_memory(task_id)
            queue_size = self._queue.qsize() if self._queue else 0

        logger.info(
            "task_enqueued",
            extra=log_context(task_id=task_id, backend=self.backend, queue_size=queue_size),
        )

    async def _enqueue_memory(self, task_id: str) -> None:
        if self._queue is None:
            raise RuntimeError("Task queue is not initialized; call start() first")
        if self._queue.full():
            raise QueueFullError("Task queue is full.")
        await self._queue.put(task_id)

    async def stats(self) -> dict[str, Any]:
        if self.backend == "redis":
            if self._redis_client is None:
                raise RuntimeError("Redis client is not initialized")
            size = await self._redis_client.llen(self.settings.redis_queue_name)
        else:
            if self._queue is None:
                raise RuntimeError("Task queue is not initialized; call start() first")
            size = self._queue.qsize()
        return {
            "backend": self.backend,
            "queue_size": size,
            "max_queue_size": self.settings.max_queue_size,
            "worker_count": len(self._workers),
            "max_concurrent_tasks": self.settings.max_concurrent_tasks,
        }

    # ------------------------------------------------------------------
    # Redis 连接 & 重连
    # ------------------------------------------------------------------

    async def _connect_redis(self) -> None:
        if self.settings.queue_backend != "redis" or redis is None:
            return
        try:
            self._redis_client = redis.from_url(self.settings.redis_url, decode_responses=True)
            await self._redis_client.ping()
            self._redis_available = True
            self._redis_backoff = 0.0
            logger.info("redis_connected", extra=log_context(redis_url=self.settings.redis_url))
        except Exception as exc:
            self._redis_available = False
            self._schedule_reconnect()
            logger.warning(
                "redis_queue_unavailable_falling_back_to_memory",
                extra=log_context(error=str(exc), redis_url=self.settings.redis_url),
            )

    async def _try_reconnect_redis(self) -> bool:
        """指数退避重连 Redis，返回是否成功。"""
        if redis is None or self.settings.queue_backend != "redis":
            return False
        loop = asyncio.get_event_loop()
        if loop.time() < self._redis_reconnect_at:
            return False  # 还在退避窗口内

        try:
            if self._redis_client:
                with suppress(Exception):
                    await self._redis_client.aclose()
            self._redis_client = redis.from_url(self.settings.redis_url, decode_responses=True)
            await self._redis_client.ping()
            self._redis_available = True
            self._redis_backoff = 0.0
            logger.info("redis_reconnected", extra=log_context(redis_url=self.settings.redis_url))
            return True
        except Exception as exc:
            self._redis_available = False
            self._schedule_reconnect()
            logger.warning(
                "redis_reconnect_failed",
                extra=log_context(
                    error=str(exc),
                    retry_in=self._redis_backoff,
                ),
            )
            return False

    def _schedule_reconnect(self) -> None:
        """计算下次重连时间点（指数退避）。"""
        base = self.settings.redis_reconnect_interval_seconds
        maximum = self.settings.redis_max_reconnect_interval_seconds
        self._redis_backoff = min(maximum, max(base, self._redis_backoff * 2 or base))
        loop = asyncio.get_event_loop()
        self._redis_reconnect_at = loop.time() + self._redis_backoff

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _work(self, worker_index: int) -> None:
        try:
            while self._running:
                try:
                    task_id = await self._dequeue()
                except (asyncio.TimeoutError, TimeoutError):
                    continue
                if task_id is None:
                    continue
                try:
                    logger.info("queued_task_started", extra=log_context(task_id=task_id, worker=worker_index))
                    await self.orchestrator.execute_task(task_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("queued_task_failed", extra=log_context(task_id=task_id, worker=worker_index))
                finally:
                    if self.backend == "memory" and self._queue is not None:
                        self._queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("task_queue_worker_crashed", extra=log_context(worker=worker_index))

    async def _dequeue(self) -> str | None:
        if self.backend == "redis":
            if self._redis_client is None:
                # Redis 已标记不可用，等待退避后重试
                await asyncio.sleep(self.settings.worker_poll_interval_seconds)
                await self._try_reconnect_redis()
                return None
            try:
                item = await self._redis_client.blpop(
                    self.settings.redis_queue_name,
                    timeout=max(1, int(self.settings.worker_poll_interval_seconds)),
                )
            except Exception as exc:
                logger.warning("redis_dequeue_failed", extra=log_context(error=str(exc)))
                self._redis_available = False
                self._schedule_reconnect()
                await asyncio.sleep(self.settings.worker_poll_interval_seconds)
                return None
            if not item:
                # blpop 超时，趁机尝试重连（如果之前离线过）
                if not self._redis_available:
                    await self._try_reconnect_redis()
                return None
            _, task_id = item
            return str(task_id)

        if self._queue is None:
            raise RuntimeError("Task queue is not initialized; call start() first")
        try:
            return await asyncio.wait_for(
                self._queue.get(),
                timeout=self.settings.worker_poll_interval_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError):
            return None
