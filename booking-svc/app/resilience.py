"""Resiliencia de la llamada booking-svc -> notif-svc (Task 3A).

Se implementan los tres mecanismos del enunciado:

1. Timeout: ninguna llamada a notif-svc espera mas de NOTIF_TIMEOUT_S (2 s).
2. Retries con backoff exponencial + jitter: hasta NOTIF_MAX_RETRIES (3)
   reintentos esperando 0.5 s, 1 s y 2 s mas un jitter aleatorio.
3. Circuit breaker + outbox pattern: si notif-svc falla CB_FAIL_MAX (3) veces
   seguidas el circuito se abre por CB_RESET_TIMEOUT_S (30 s). Mientras esta
   abierto no se llama a notif-svc; la notificacion queda en la tabla
   notification_outbox con status="pending". Un hilo en segundo plano reintenta
   las pendientes cada OUTBOX_FLUSH_INTERVAL_S y, al pasar el tiempo de reset,
   sirve como la llamada de prueba (half-open) que vuelve a cerrar el circuito.

Orden de composicion: breaker.call(reintentos(post)). Asi cada reserva cuenta
como UN fallo del circuito aunque internamente haga varios intentos HTTP; el
circuito se abre en la tercera reserva fallida, tal como pide la demo.
"""

import os
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

import pybreaker
import requests
from sqlalchemy.orm import Session
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

from app import models
from app.database import SessionLocal
from app.discovery import NOTIF_SERVICE_NAME, ServiceUnavailable, resolve_notif_svc_url
from app.observability import correlation_id_context, logger


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


NOTIF_TIMEOUT_S = _env_float("NOTIF_TIMEOUT_S", 2.0)
NOTIF_MAX_RETRIES = int(_env_float("NOTIF_MAX_RETRIES", 3))
CB_FAIL_MAX = int(_env_float("CB_FAIL_MAX", 3))
CB_RESET_TIMEOUT_S = int(_env_float("CB_RESET_TIMEOUT_S", 30))
OUTBOX_FLUSH_INTERVAL_S = _env_float("OUTBOX_FLUSH_INTERVAL_S", 10.0)
OUTBOX_FLUSH_BATCH = 20

RETRYABLE_ERRORS = (requests.RequestException, ServiceUnavailable)

# Serializa las entregas entre los requests y el hilo del outbox para que una
# misma notificacion no se envie dos veces.
_delivery_lock = threading.RLock()


class BreakerLogListener(pybreaker.CircuitBreakerListener):
    def state_change(self, cb: pybreaker.CircuitBreaker, old_state, new_state) -> None:
        logger.warning(
            "circuit_breaker_state_change",
            extra={
                "breaker": cb.name,
                "old_state": old_state.name if old_state else None,
                "new_state": new_state.name,
                "fail_counter": cb.fail_counter,
            },
        )

    def failure(self, cb: pybreaker.CircuitBreaker, exc: BaseException) -> None:
        logger.warning(
            "circuit_breaker_failure",
            extra={"breaker": cb.name, "fail_counter": cb.fail_counter + 1, "fail_max": cb.fail_max},
        )


breaker = pybreaker.CircuitBreaker(
    fail_max=CB_FAIL_MAX,
    reset_timeout=CB_RESET_TIMEOUT_S,
    name=NOTIF_SERVICE_NAME,
    listeners=[BreakerLogListener()],
)


def _log_retry(retry_state) -> None:
    logger.warning(
        "notification_retry",
        extra={
            "attempt": retry_state.attempt_number,
            "max_attempts": 1 + NOTIF_MAX_RETRIES,
            "sleep_s": round(retry_state.next_action.sleep, 3),
            "error": str(retry_state.outcome.exception()),
        },
    )


@retry(
    stop=stop_after_attempt(1 + NOTIF_MAX_RETRIES),
    # multiplier=0.5 -> 0.5 s, 1 s, 2 s entre intentos, mas jitter de 0-0.3 s.
    wait=wait_exponential(multiplier=0.5, min=0.5, max=2) + wait_random(0, 0.3),
    retry=retry_if_exception_type(RETRYABLE_ERRORS),
    before_sleep=_log_retry,
    reraise=True,
)
def _post_notification_with_retries(user_id: int, message: str, correlation_id: str) -> dict:
    # Se resuelve la URL en cada intento: si notif-svc cambio de direccion o
    # volvio a estar sano en Consul, el reintento lo aprovecha.
    base_url, _ = resolve_notif_svc_url()
    response = requests.post(
        f"{base_url}/notifications",
        json={"user_id": user_id, "message": message},
        headers={"x-correlation-id": correlation_id},
        timeout=NOTIF_TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()


def deliver(db: Session, entry: models.NotificationOutbox) -> str:
    """Intenta entregar una entrada del outbox. Devuelve el status final.

    Nunca lanza excepciones de red: si la entrega falla la entrada queda
    "pending" y el llamador sigue respondiendo con normalidad.
    """
    with _delivery_lock:
        db.refresh(entry)
        if entry.status != "pending":
            return entry.status

        def _attempt() -> dict:
            entry.attempts += 1
            return _post_notification_with_retries(entry.user_id, entry.message, entry.correlation_id)

        try:
            result = breaker.call(_attempt)
            entry.status = "sent"
            entry.sent_at = datetime.now(timezone.utc)
            entry.last_error = None
            logger.info(
                "notification_sent",
                extra={
                    "outbox_id": entry.id,
                    "booking_id": entry.booking_id,
                    "user_id": entry.user_id,
                    "notification_id": result.get("id"),
                    "attempts": entry.attempts,
                },
            )
        except pybreaker.CircuitBreakerError as exc:
            entry.last_error = str(exc)
            logger.warning(
                "notification_deferred",
                extra={
                    "outbox_id": entry.id,
                    "booking_id": entry.booking_id,
                    "user_id": entry.user_id,
                    "reason": "circuit_open",
                    "breaker_state": breaker.current_state,
                },
            )
        except RETRYABLE_ERRORS as exc:
            entry.last_error = str(exc)[:500]
            logger.warning(
                "notification_deferred",
                extra={
                    "outbox_id": entry.id,
                    "booking_id": entry.booking_id,
                    "user_id": entry.user_id,
                    "reason": "delivery_failed",
                    "attempts": entry.attempts,
                    "breaker_state": breaker.current_state,
                    "error": str(exc)[:200],
                },
            )
        db.commit()
        return entry.status


def flush_outbox(limit: int = OUTBOX_FLUSH_BATCH) -> dict:
    """Reintenta las notificaciones pendientes en orden de llegada.

    Se detiene en la primera que falla: si notif-svc sigue caido no tiene
    sentido insistir con el resto en esta ronda.
    """
    # Si un request esta entregando en este momento, esta ronda se salta para
    # no hacerlo esperar; la siguiente ronda recoge lo que quede pendiente.
    if not _delivery_lock.acquire(blocking=False):
        logger.info("outbox_flush_skipped", extra={"reason": "delivery_in_progress"})
        db = SessionLocal()
        try:
            remaining = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.status == "pending").count()
        finally:
            db.close()
        return {"sent": 0, "deferred": 0, "pending": remaining, "breaker_state": breaker.current_state}
    db = SessionLocal()
    sent = 0
    deferred = 0
    try:
        pending = (
            db.query(models.NotificationOutbox)
            .filter(models.NotificationOutbox.status == "pending")
            .order_by(models.NotificationOutbox.created_at, models.NotificationOutbox.id)
            .limit(limit)
            .all()
        )
        for entry in pending:
            # El log y el header hacia notif-svc conservan el correlation_id
            # original de la reserva, aunque la entrega ocurra mas tarde.
            token = correlation_id_context.set(entry.correlation_id or "-")
            try:
                status = deliver(db, entry)
            finally:
                correlation_id_context.reset(token)
            if status == "sent":
                sent += 1
            else:
                deferred += 1
                break
        remaining = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.status == "pending").count()
    finally:
        db.close()
        _delivery_lock.release()

    if sent or deferred:
        logger.info(
            "outbox_flush",
            extra={"sent": sent, "deferred": deferred, "pending": remaining, "breaker_state": breaker.current_state},
        )
    return {"sent": sent, "deferred": deferred, "pending": remaining, "breaker_state": breaker.current_state}


def outbox_counts(db: Session) -> dict:
    rows = (
        db.query(models.NotificationOutbox.status, models.NotificationOutbox.id)
        .all()
    )
    counts = {"pending": 0, "sent": 0}
    for status, _ in rows:
        counts[status] = counts.get(status, 0) + 1
    return counts


def breaker_status() -> dict:
    return {
        "name": breaker.name,
        "state": breaker.current_state,
        "fail_counter": breaker.fail_counter,
        "fail_max": CB_FAIL_MAX,
        "reset_timeout_s": CB_RESET_TIMEOUT_S,
        "timeout_s": NOTIF_TIMEOUT_S,
        "max_retries": NOTIF_MAX_RETRIES,
    }


class OutboxWorker:
    """Hilo en segundo plano que vacia el outbox periodicamente."""

    def __init__(self, interval_s: float = OUTBOX_FLUSH_INTERVAL_S, flush: Callable[[], dict] = flush_outbox):
        self._interval_s = interval_s
        self._flush = flush
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="outbox-worker", daemon=True)
        self._thread.start()
        logger.info("outbox_worker_started", extra={"interval_s": self._interval_s})

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._interval_s):
            try:
                self._flush()
            except Exception:
                logger.exception("outbox_flush_failed")


outbox_worker = OutboxWorker()
