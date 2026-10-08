"""JSON logs that pass through the masker (research R14). Every record may carry a turn_id."""

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime

from .masking import mask_text

turn_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("turn_id", default=None)


class MaskingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = mask_text(record.getMessage())
        out = {
            "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": message,
        }
        turn_id = getattr(record, "turn_id", None) or turn_id_var.get()
        if turn_id:
            out["turn_id"] = turn_id
        for key in ("session_id", "metric", "value_ms", "tool", "status"):
            if hasattr(record, key):
                out[key] = getattr(record, key)
        if record.exc_info:
            out["error"] = mask_text(self.formatException(record.exc_info))
        return json.dumps(out, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(MaskingJsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).handlers[:] = [handler]
