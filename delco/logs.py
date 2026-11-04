from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

_STANDARD = set(vars(logging.makeLogRecord({})))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            "process": record.process,
            "thread": record.threadName,
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                data[key] = value
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)
