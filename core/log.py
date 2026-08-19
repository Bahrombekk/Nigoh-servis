"""Nigoh — strukturali jurnal (JSON lines).

Fon xizmatlari (health, reconciler, media) hodisalarni shu yerdan yozadi.
Ikkita chiqish:

  * `nigoh.log` (ildizda, aylanma — 5 MB × 3) — har satr bitta JSON obyekt:
    {"ts": ..., "level": ..., "service": ..., "event": ..., ...maydonlar}.
    Keyinchalik Loki/OpenSearch'ga shu faylni yuborish mumkin.
  * konsol — odam o'qiydigan qisqa satr (ishga-tushirish.bat oynasi uchun).

Ishlatish:
    from core.log import log
    log("reconciler", "mediamtx_restarted", node="Asosiy")
    log("health", "sweep_failed", level="error", error=str(exc))
"""
import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from .db import DATA_DIR

LOG_PATH = DATA_DIR / "nigoh.log"

_logger: logging.Logger | None = None


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "service": getattr(record, "service", record.name),
            "event": record.getMessage(),
        }
        data.update(getattr(record, "fields", {}))
        return json.dumps(data, ensure_ascii=False, default=str)


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", {})
        extra = " ".join(f"{k}={v}" for k, v in fields.items())
        service = getattr(record, "service", record.name)
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"{stamp} [{service}] {record.getMessage()}"
        return f"{line} · {extra}" if extra else line


def _build() -> logging.Logger:
    logger = logging.getLogger("nigoh")
    logger.setLevel(logging.INFO)
    logger.propagate = False           # uvicorn'ning root handlerlariga oqmasin

    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(_JsonFormatter())
    logger.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(_ConsoleFormatter())
    logger.addHandler(console)
    return logger


def log(service: str, event: str, level: str = "info", **fields) -> None:
    """Bitta strukturali hodisa yozadi.

    service — qaysi qatlam (health, reconciler, media, app);
    event — qisqa mashina o'qiydigan nom (mediamtx_restarted, sweep_failed);
    fields — qo'shimcha kontekst (camera_id, node, error...).
    """
    global _logger
    if _logger is None:
        _logger = _build()
    record_level = getattr(logging, level.upper(), logging.INFO)
    _logger.log(record_level, event, extra={"service": service, "fields": fields})
