"""Logs JSON estructurados y propagacion de x-correlation-id (Task 3B / 4A).

Cada linea de log es un objeto JSON con al menos: timestamp, level, service,
event y correlation_id. Cuando el request trae un JWT valido tambien se agrega
user_id. Los campos extra que se pasan con `logger.info("evento", extra={...})`
se incorporan como claves del JSON.
"""

import contextvars
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Request

SERVICE_NAME = "booking-svc"

# Los contextvars mantienen el ID aislado entre requests concurrentes.
correlation_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")
user_id_context: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("user_id", default=None)

# Atributos estandar de LogRecord; todo lo demas se considera un campo extra.
_RESERVED_ATTRS = set(logging.LogRecord("x", logging.INFO, "x", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "event": record.getMessage(),
            "correlation_id": correlation_id_context.get(),
        }
        user_id = user_id_context.get()
        if user_id is not None:
            entry["user_id"] = user_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=True, default=str)


def configure_logging() -> logging.Logger:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    # Toda la salida del proceso (incluida la de uvicorn) sale como JSON.
    for name in ("", "uvicorn", "uvicorn.error", SERVICE_NAME):
        target = logging.getLogger(name)
        target.handlers = [handler]
        target.setLevel(logging.INFO)
        target.propagate = False

    # El middleware ya registra request_completed; el access log plano sobra.
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False

    return logging.getLogger(SERVICE_NAME)


logger = configure_logging()


def get_correlation_id() -> str:
    return correlation_id_context.get()


async def correlation_middleware(request: Request, call_next):
    correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
    token = correlation_id_context.set(correlation_id)
    started_at = time.perf_counter()
    try:
        logger.info("request_started", extra={"method": request.method, "path": request.url.path})
        response = await call_next(request)
        # El user_id lo deja la dependencia de auth en request.state porque
        # el endpoint corre en otro contexto y su contextvar no llega aqui.
        user_id = getattr(request.state, "user_id", None)
        logger.info(
            "request_completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                **({"user_id": user_id} if user_id is not None else {}),
            },
        )
        response.headers["x-correlation-id"] = correlation_id
        return response
    except Exception:
        logger.exception("request_failed", extra={"method": request.method, "path": request.url.path})
        raise
    finally:
        correlation_id_context.reset(token)
