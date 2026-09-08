"""Servidor MCP de FitFlow.

Corre como proceso local que Claude Desktop lanza via stdio (no dentro de la
red de docker-compose). Por eso resuelve las direcciones de los servicios via
`localhost` usando los puertos publicados por docker-compose, aunque consulta
a Consul para saber si el servicio esta sano y en que puerto quedo. Si Consul
no tiene una instancia sana registrada, usa el puerto publicado por defecto como
respaldo.
"""

import logging
import os
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fitflow-mcp")

mcp = FastMCP("fitflow-mcp")

CONSUL_HOST = os.getenv("CONSUL_HOST", "localhost")
CONSUL_PORT = int(os.getenv("CONSUL_PORT", "8500"))
FITFLOW_SERVICE_HOST = os.getenv("FITFLOW_SERVICE_HOST", "localhost")

# Sesion en memoria del proceso MCP: se llena con la herramienta `login` y la
# usan create_booking/cancel_booking. Vive solo mientras corre este proceso.
_session: dict[str, Any] = {"token": None, "email": None}


def resolve_service_base_url(service_name: str, fallback_port: int) -> str:
    try:
        resp = requests.get(
            f"http://{CONSUL_HOST}:{CONSUL_PORT}/v1/health/service/{service_name}",
            params={"passing": "true"},
            timeout=2,
        )
        resp.raise_for_status()
        instances = resp.json()
        if instances:
            port = instances[0]["Service"]["Port"]
            logger.info("resolved %s via Consul on port %s", service_name, port)
            return f"http://{FITFLOW_SERVICE_HOST}:{port}"
    except requests.RequestException as exc:
        logger.warning("Consul lookup failed for %s: %s", service_name, exc)

    logger.info("using fallback port %s for %s (not registered/healthy in Consul)", fallback_port, service_name)
    return f"http://{FITFLOW_SERVICE_HOST}:{fallback_port}"


def _users_svc_url() -> str:
    return resolve_service_base_url("users-svc", 8003)


def _booking_svc_url() -> str:
    return resolve_service_base_url("booking-svc", 8001)


def _auth_headers() -> dict:
    token = _session["token"] or os.getenv("FITFLOW_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("Debes iniciar sesion primero usando la herramienta 'login'.")
    return {"Authorization": f"Bearer {token}"}


@mcp.tool()
def login(email: str, password: str) -> str:
    """Inicia sesion en FitFlow (users-svc) y guarda el token para reservar clases."""
    url = f"{_users_svc_url()}/users/login"
    try:
        resp = requests.post(url, json={"email": email, "password": password}, timeout=5)
    except requests.RequestException as exc:
        raise RuntimeError(f"No se pudo contactar a users-svc: {exc}") from exc

    if resp.status_code == 401:
        return "Credenciales invalidas."
    resp.raise_for_status()
    token = resp.json()["access_token"]
    _session["token"] = token
    _session["email"] = email
    logger.info("login ok for %s", email)
    return f"Sesion iniciada como {email}."


@mcp.tool()
def get_available_classes() -> list[dict]:
    """Lista las clases fitness disponibles en FitFlow (booking-svc)."""
    url = f"{_booking_svc_url()}/classes"
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def create_booking(class_id: int) -> dict:
    """Reserva una clase (por id) para el usuario que inicio sesion."""
    url = f"{_booking_svc_url()}/bookings"
    resp = requests.post(url, json={"class_id": class_id}, headers=_auth_headers(), timeout=5)
    if resp.status_code == 401:
        raise RuntimeError("Sesion invalida o expirada. Vuelve a usar la herramienta 'login'.")
    resp.raise_for_status()
    logger.info("booking created via MCP class_id=%s", class_id)
    return resp.json()


@mcp.tool()
def cancel_booking(booking_id: int) -> dict:
    """Cancela una reserva existente (por id) del usuario que inicio sesion."""
    url = f"{_booking_svc_url()}/bookings/{booking_id}"
    resp = requests.delete(url, headers=_auth_headers(), timeout=5)
    if resp.status_code == 401:
        raise RuntimeError("Sesion invalida o expirada. Vuelve a usar la herramienta 'login'.")
    resp.raise_for_status()
    logger.info("booking cancelled via MCP booking_id=%s", booking_id)
    return resp.json()


if __name__ == "__main__":
    mcp.run()
