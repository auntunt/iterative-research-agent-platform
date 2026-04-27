import pytest

from app.models.schemas import TaskStatus
from app.services.task_manager import TaskManager


@pytest.mark.asyncio
async def test_task_lifecycle_status_updates() -> None:
    manager = TaskManager()
    task = await manager.create_task("Summarize a document")

    assert task.status == TaskStatus.PENDING

    running = await manager.set_status(task.task_id, TaskStatus.RUNNING)
    assert running.status == TaskStatus.RUNNING

    stored = await manager.get_task(task.task_id)
    assert stored.task == "Summarize a document"
    assert stored.status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_delete_task_removes_task_steps_and_logs() -> None:
    manager = TaskManager()
    task = await manager.create_task("Delete me")
    step = await manager.add_step(task.task_id, "planner", "plan")
    await manager.start_step(task.task_id, step.step_id, retries=0)

    deleted = await manager.delete_task(task.task_id)

    assert deleted is True
    with pytest.raises(KeyError):
        await manager.get_task(task.task_id)
    assert await manager.get_logs(task_id=task.task_id) == []
