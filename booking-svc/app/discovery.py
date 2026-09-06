"""Registro en Consul y descubrimiento dinamico de notif-svc (Task 2A).

booking-svc se registra al arrancar con nombre, direccion, puerto y health
check. Para llamar a notif-svc no usa una URL fija: pregunta a Consul por una
instancia sana. Solo si Consul no responde usa el nombre logico de
docker-compose como respaldo.
"""

import os
import random
from typing import Tuple

import requests

from app.observability import logger


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("invalid_env_value", extra={"name": name, "value": value, "default": default})
        return default


CONSUL_HOST = _env_str("CONSUL_HOST", "consul")
CONSUL_PORT = _env_int("CONSUL_PORT", 8500)
CONSUL_BASE_URL = f"http://{CONSUL_HOST}:{CONSUL_PORT}"

# Prefijo BOOKING_ para no chocar con las variables de users-svc y notif-svc.
SERVICE_NAME = _env_str("BOOKING_CONSUL_SERVICE_NAME", "booking-svc")
SERVICE_ID = _env_str("BOOKING_CONSUL_SERVICE_ID", "booking-svc-8001")
SERVICE_ADDRESS = _env_str("BOOKING_CONSUL_SERVICE_ADDRESS", "booking-svc")
SERVICE_PORT = _env_int("BOOKING_CONSUL_SERVICE_PORT", 8001)
HEALTH_PATH = _env_str("BOOKING_CONSUL_HEALTH_PATH", "/healthz")

NOTIF_SERVICE_NAME = _env_str("NOTIF_SERVICE_NAME", "notif-svc")
NOTIF_SVC_FALLBACK_URL = _env_str("NOTIF_SVC_FALLBACK_URL", "http://notif-svc:8002").rstrip("/")

CONSUL_TIMEOUT_S = 2.0


class ServiceUnavailable(Exception):
    """Consul respondio, pero no tiene ninguna instancia sana del servicio."""


def register_service() -> None:
    payload = {
        "Name": SERVICE_NAME,
        "ID": SERVICE_ID,
        "Address": SERVICE_ADDRESS,
        "Port": SERVICE_PORT,
        "Check": {
            "HTTP": f"http://{SERVICE_ADDRESS}:{SERVICE_PORT}{HEALTH_PATH}",
            "Interval": "10s",
            "DeregisterCriticalServiceAfter": "30s",
        },
    }
    try:
        response = requests.put(f"{CONSUL_BASE_URL}/v1/agent/service/register", json=payload, timeout=3)
        response.raise_for_status()
        logger.info("consul_registered", extra={"service_id": SERVICE_ID, "consul": CONSUL_BASE_URL})
    except requests.RequestException as exc:
        # booking-svc debe arrancar igual aunque Consul no este disponible.
        logger.warning("consul_registration_failed", extra={"error": str(exc)})


def deregister_service() -> None:
    try:
        response = requests.put(f"{CONSUL_BASE_URL}/v1/agent/service/deregister/{SERVICE_ID}", timeout=3)
        response.raise_for_status()
        logger.info("consul_deregistered", extra={"service_id": SERVICE_ID})
    except requests.RequestException as exc:
        logger.warning("consul_deregistration_failed", extra={"error": str(exc)})


def resolve_service_url(service_name: str) -> Tuple[str, str]:
    """Devuelve (url_base, origen). origen es "consul" o "fallback".

    Lanza ServiceUnavailable si Consul responde sin instancias sanas.
    """
    try:
        response = requests.get(
            f"{CONSUL_BASE_URL}/v1/health/service/{service_name}",
            params={"passing": "true"},
            timeout=CONSUL_TIMEOUT_S,
        )
        response.raise_for_status()
        instances = response.json()
    except requests.RequestException as exc:
        if service_name != NOTIF_SERVICE_NAME:
            raise ServiceUnavailable(f"Consul unreachable while resolving {service_name}: {exc}") from exc
        logger.warning(
            "consul_lookup_failed",
            extra={"target": service_name, "error": str(exc), "fallback_url": NOTIF_SVC_FALLBACK_URL},
        )
        return NOTIF_SVC_FALLBACK_URL, "fallback"

    if not instances:
        raise ServiceUnavailable(f"no healthy instance of {service_name} registered in Consul")

    # Con varias instancias sanas se reparte la carga al azar.
    instance = random.choice(instances)
    address = instance["Service"].get("Address") or instance["Node"]["Address"]
    port = instance["Service"]["Port"]
    url = f"http://{address}:{port}"
    logger.info("service_resolved", extra={"target": service_name, "url": url, "source": "consul"})
    return url, "consul"


def resolve_notif_svc_url() -> Tuple[str, str]:
    return resolve_service_url(NOTIF_SERVICE_NAME)
