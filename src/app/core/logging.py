import json
import logging
import sys
from collections.abc import MutableMapping
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog


class PrettyLogFormatter(logging.Formatter):
    """Human-readable formatter for structlog JSON records."""

    _SKIP_KEYS = {
        "message",
        "event",
        "level",
        "timestamp",
        "logger",
        "exception",
        "exc_info",
        "stack",
    }

    def format(self, record: logging.LogRecord) -> str:
        raw = record.getMessage()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return super().format(record)

        ts = str(payload.get("timestamp") or "").strip()
        if ts:
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        level = str(payload.get("level") or record.levelname or "INFO").upper()
        logger_name = str(payload.get("logger") or record.name or "app")
        message = str(payload.get("message") or payload.get("event") or "")
        base = f"{ts} | {level:<7} | {logger_name} | {message}".strip()

        extras: list[str] = []
        for key, value in payload.items():
            if key in self._SKIP_KEYS:
                continue
            extras.append(f"{key}={json.dumps(value, ensure_ascii=True)}")
        if extras:
            base = f"{base} | " + " ".join(extras)

        exception_text = payload.get("exception")
        if exception_text:
            base = f"{base}\n{exception_text}"
        return base


def setup_logging(
    log_level: str,
    *,
    log_file_enabled: bool = True,
    log_file_path: str = "logs/backend.log",
    log_file_max_bytes: int = 10485760,
    log_file_backup_count: int = 10,
    log_pretty_file_enabled: bool = True,
    log_pretty_file_path: str = "logs/backend.pretty.log",
    log_pretty_file_max_bytes: int = 10485760,
    log_pretty_file_backup_count: int = 10,
) -> None:
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.EventRenamer("message"),
        structlog.processors.JSONRenderer(serializer=json.dumps),
    ]

    structlog.configure(
        processors=processors,  # type: ignore[arg-type]
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    level = getattr(logging, str(log_level).upper(), logging.INFO)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console_handler)

    if log_file_enabled:
        path = Path(log_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=path,
            maxBytes=max(1024, int(log_file_max_bytes)),
            backupCount=max(1, int(log_file_backup_count)),
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(file_handler)

    if log_pretty_file_enabled:
        pretty_path = Path(log_pretty_file_path)
        pretty_path.parent.mkdir(parents=True, exist_ok=True)
        pretty_handler = RotatingFileHandler(
            filename=pretty_path,
            maxBytes=max(1024, int(log_pretty_file_max_bytes)),
            backupCount=max(1, int(log_pretty_file_backup_count)),
            encoding="utf-8",
        )
        pretty_handler.setFormatter(PrettyLogFormatter("%(message)s"))
        root.addHandler(pretty_handler)


def bind_request_context(*, request_id: str, path: str, method: str) -> None:
    structlog.contextvars.bind_contextvars(request_id=request_id, path=path, method=method)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()


def normalize_log_data(extra: MutableMapping[str, object] | None = None) -> dict[str, object]:
    return dict(extra or {})

