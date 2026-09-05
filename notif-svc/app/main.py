import contextvars
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import engine, Base, get_db
from app import models, schemas

Base.metadata.create_all(bind=engine)
app = FastAPI(title="notif-svc")

correlation_id_context = contextvars.ContextVar("correlation_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": "notif-svc",
            "event": record.getMessage(),
            "correlation_id": correlation_id_context.get(),
        }
        return json.dumps(log_entry, ensure_ascii=True)


logger = logging.getLogger("notif-svc")
logger.setLevel(logging.INFO)
logger.propagate = False
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.handlers = [handler]


# El contexto se mantiene aislado para que requests concurrentes no mezclen sus IDs.
@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
    token = correlation_id_context.set(correlation_id)
    started_at = time.perf_counter()
    try:
        logger.info("request_started method=%s path=%s", request.method, request.url.path)
        response = await call_next(request)
        logger.info(
            "request_completed method=%s path=%s status_code=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started_at) * 1000,
        )
        response.headers["x-correlation-id"] = correlation_id
        return response
    except Exception:
        logger.exception("request_failed method=%s path=%s", request.method, request.url.path)
        raise
    finally:
        correlation_id_context.reset(token)


def get_env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value


def get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid value for %s=%s. Using default=%s", name, value, default)
        return default


# Variables con prefijo NOTIF_ para no chocar con el registro de users-svc,
CONSUL_HOST = get_env_str("CONSUL_HOST", "consul")
CONSUL_PORT = get_env_int("CONSUL_PORT", 8500)
CONSUL_SERVICE_NAME = get_env_str("NOTIF_CONSUL_SERVICE_NAME", "notif-svc")
CONSUL_SERVICE_ID = get_env_str("NOTIF_CONSUL_SERVICE_ID", "notif-svc-8002")
CONSUL_SERVICE_ADDRESS = get_env_str("NOTIF_CONSUL_SERVICE_ADDRESS", "notif-svc")
CONSUL_SERVICE_PORT = get_env_int("NOTIF_CONSUL_SERVICE_PORT", 8002)
CONSUL_HEALTH_PATH = get_env_str("NOTIF_CONSUL_HEALTH_PATH", "/healthz")


def register_service_in_consul() -> None:
    consul_url = f"http://{CONSUL_HOST}:{CONSUL_PORT}/v1/agent/service/register"
    health_url = f"http://{CONSUL_SERVICE_ADDRESS}:{CONSUL_SERVICE_PORT}{CONSUL_HEALTH_PATH}"
    payload = {
        "Name": CONSUL_SERVICE_NAME,
        "ID": CONSUL_SERVICE_ID,
        "Address": CONSUL_SERVICE_ADDRESS,
        "Port": CONSUL_SERVICE_PORT,
        "Check": {
            "HTTP": health_url,
            "Interval": "10s",
            "DeregisterCriticalServiceAfter": "30s",
        },
    }

    try:
        response = requests.put(consul_url, json=payload, timeout=3)
        response.raise_for_status()
        logger.info("notif-svc registered in Consul with id=%s", CONSUL_SERVICE_ID)
    except requests.RequestException as exc:
        # notif-svc debe arrancar igual aunque Consul no este disponible.
        logger.warning("Consul registration failed: %s", exc)


def deregister_service_in_consul() -> None:
    consul_url = (
        f"http://{CONSUL_HOST}:{CONSUL_PORT}/v1/agent/service/deregister/{CONSUL_SERVICE_ID}"
    )
    try:
        response = requests.put(consul_url, timeout=3)
        response.raise_for_status()
        logger.info("notif-svc deregistered from Consul with id=%s", CONSUL_SERVICE_ID)
    except requests.RequestException as exc:
        logger.warning("Consul deregistration failed: %s", exc)


@app.on_event("startup")
def on_startup() -> None:
    register_service_in_consul()


@app.on_event("shutdown")
def on_shutdown() -> None:
    deregister_service_in_consul()

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=500, detail="Database connection failed")

@app.post("/notifications", response_model=schemas.NotificationResponse, status_code=201)
def send_notification(notification: schemas.NotificationCreate, db: Session = Depends(get_db)):
    new_notification = models.Notification(
        user_id=notification.user_id,
        message=notification.message,
        status="sent",
    )
    db.add(new_notification)
    db.commit()
    db.refresh(new_notification)
    logger.info("notification sent to user_id=%s: %s", new_notification.user_id, new_notification.message)
    return new_notification

@app.get("/notifications/{user_id}", response_model=list[schemas.NotificationResponse])
def get_notification_history(user_id: int, db: Session = Depends(get_db)):
    return db.query(models.Notification).filter(models.Notification.user_id == user_id).order_by(models.Notification.created_at.desc()).all()
