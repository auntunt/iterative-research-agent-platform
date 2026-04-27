from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import Settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
            "trace_id": getattr(record, "trace_id", None) or trace_id_var.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key.startswith("_ctx_"):
                payload[key.removeprefix("_ctx_")] = value
        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    formatter = JsonFormatter()
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [console_handler]

    if settings.log_file:
        log_path = Path(settings.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    logging.basicConfig(
        level=settings.log_level.upper(),
        handlers=handlers,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_context(**kwargs: Any) -> dict[str, Any]:
    return {f"_ctx_{key}": value for key, value in kwargs.items()}


def bind_trace(request_id: str | None = None, trace_id: str | None = None) -> None:
    if request_id:
        request_id_var.set(request_id)
    if trace_id:
        trace_id_var.set(trace_id)
