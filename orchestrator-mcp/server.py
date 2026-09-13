"""Servidor MCP del Orchestrator de FitFlow.

Corre en el host, lanzado por Claude Desktop via stdio (igual patron que
fitflow-mcp/server.py), y es el brazo de razonamiento que la sesion usa para
iniciar sesion, leer el catalogo de clases, ver que agentes descubrio el
contenedor `orchestrator-agent` y someterle un plan de ejecucion sobre A2A.

Por diseno (design decision 3), `list_classes` lee `booking-svc` DIRECTO
desde el host -- igual que `fitflow-mcp` -- para que el contenedor del
orchestrator siga hablando unicamente A2A y nunca a un microservicio.
`discover_agents` y `orchestrate` si dependen del contenedor
`orchestrator-agent`: si esta caido, deben fallar de forma explicita (spec:
"Orchestrator connectivity failures are reported, not masked"), nunca simular
un exito.
"""

import logging
import os
import uuid
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orchestrator-mcp")

mcp = FastMCP("orchestrator-mcp")

CONSUL_HOST = os.getenv("CONSUL_HOST", "localhost")
CONSUL_PORT = int(os.getenv("CONSUL_PORT", "8500"))
FITFLOW_SERVICE_HOST = os.getenv("FITFLOW_SERVICE_HOST", "localhost")
# El orchestrator NO se registra en Consul (design decision 2: los agentes se
# descubren por Agent Card, no por Consul -- eso es justamente lo que este
# cambio demuestra), asi que su URL es un env var directo, sin Consul lookup
# ni fallback de puerto publicado.
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:9003")

# Sesion en memoria del proceso MCP, igual que fitflow-mcp: se llena con la
# herramienta `login` y la reutiliza `orchestrate`. Vive solo mientras corre
# este proceso.
_session: dict[str, Any] = {"token": None, "email": None}


def resolve_service_base_url(service_name: str, fallback_port: int) -> str:
    """Portado de fitflow-mcp/server.py (task 6.2): consulta a Consul el
    puerto de una instancia sana; si no hay ninguna registrada, usa el puerto
    publicado por docker-compose como respaldo. Este proceso corre en el
    host, por eso arma la URL final contra FITFLOW_SERVICE_HOST (`localhost`
    por defecto) y no contra el nombre logico del servicio en Docker.
    """
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


def _stored_token() -> str:
    """Devuelve el JWT guardado por `login`, o lanza si no hay sesion.

    Mirror de `_auth_headers()` en fitflow-mcp, salvo que aqui el llamador
    decide donde va el token: el orchestrator lo espera en el BODY de
    /orchestrate como `access_token`, nunca en un header Authorization
    (task 6.6).
    """
    token = _session["token"]
    if not token:
        raise RuntimeError("Debes iniciar sesion primero usando la herramienta 'login'.")
    return token


@mcp.tool()
def login(email: str, password: str) -> str:
    """Inicia sesion en FitFlow (users-svc) y guarda el JWT en memoria del
    proceso para que `orchestrate` lo reutilice sin que el usuario maneje el
    token."""
    url = f"{_users_svc_url()}/users/login"
    try:
        resp = requests.post(url, json={"email": email, "password": password}, timeout=5)
    except requests.RequestException as exc:
        raise RuntimeError(f"No se pudo contactar a users-svc en {url}: {exc}") from exc

    if resp.status_code == 401:
        return "Credenciales invalidas."
    resp.raise_for_status()
    token = resp.json()["access_token"]
    _session["token"] = token
    _session["email"] = email
    logger.info("login ok for %s", email)
    return f"Sesion iniciada como {email}."


@mcp.tool()
def list_classes() -> list[dict]:
    """Lista las clases disponibles en booking-svc, leidas DIRECTO desde el
    host (design decision 3, task 6.4): da los ids, instructor y horario ISO
    reales de ahora mismo, para que la sesion resuelva una frase como 'yoga
    el viernes' contra un class_id que de verdad existe -- sin pasar por el
    contenedor del orchestrator, que solo habla A2A."""
    url = f"{_booking_svc_url()}/classes"
    try:
        resp = requests.get(url, timeout=5)
    except requests.RequestException as exc:
        raise RuntimeError(f"No se pudo contactar a booking-svc en {url}: {exc}") from exc
    resp.raise_for_status()
    return [
        {"id": c["id"], "name": c["name"], "instructor": c["instructor"], "schedule": c["schedule"]}
        for c in resp.json()
    ]


@mcp.tool()
def discover_agents() -> dict:
    """Proxea GET /agents del contenedor orchestrator: que agentes descubrio
    y que skills tiene cada uno, para que la sesion sepa que puede delegar
    antes de armar un plan (no una lista fija)."""
    url = f"{ORCHESTRATOR_URL}/agents"
    try:
        resp = requests.get(url, timeout=5)
    except requests.RequestException as exc:
        # Task 6.7 / spec "Orchestrator connectivity failures are reported,
        # not masked": nunca se devuelve un exito fabricado si el
        # contenedor esta caido -- se nombra el fallo explicitamente.
        raise RuntimeError(f"No se pudo contactar al orchestrator en {url}: {exc}") from exc
    resp.raise_for_status()
    return resp.json()


class Step(BaseModel):
    """Un paso del plan que la sesion arma. Mismo shape que `Step` en
    `orchestrator-agent/app.py`: sin `user_id` -- el destinatario de una
    notificacion sale del JWT dentro del contenedor, nunca del plan (design
    decision 5), asi que ni se declara aqui. Tipado como modelo pydantic (no
    `dict` opaco) para que FastMCP genere un JSON Schema real -- un arreglo
    de objetos con estas propiedades -- que Claude Desktop pueda usar (task
    6.6)."""

    skill: str
    class_id: int | None = None
    booking_id: int | None = None
    message: str | None = None
    reason: str | None = None


@mcp.tool()
def orchestrate(instruction: str, steps: list[Step]) -> dict:
    """Somete un plan de ejecucion al orchestrator (POST /orchestrate).

    Envia el JWT guardado por `login` en el BODY como `access_token` -- el
    orchestrator lo espera ahi, no en un header Authorization (design
    decision 3). Genera un correlation id nuevo por llamada (uuid4) y lo
    manda como header `x-correlation-id`; la respuesta incluye ese mismo id
    para que la sesion le diga al usuario que grepear en los logs de los
    cinco contenedores.
    """
    token = _stored_token()
    correlation_id = str(uuid.uuid4())
    url = f"{ORCHESTRATOR_URL}/orchestrate"
    body = {
        "instruction": instruction,
        "steps": [step.model_dump(exclude_none=True) for step in steps],
        "access_token": token,
    }
    try:
        # El orchestrator ejecuta los steps en serie con hasta ~30s por hop
        # A2A (cada agente levanta un subproceso MCP por request), asi que
        # este timeout debe cubrir varios hops, no uno solo.
        resp = requests.post(url, json=body, headers={"x-correlation-id": correlation_id}, timeout=90)
    except requests.RequestException as exc:
        raise RuntimeError(f"No se pudo contactar al orchestrator en {url}: {exc}") from exc

    if resp.status_code == 401:
        raise RuntimeError("Sesion invalida o expirada en el orchestrator. Vuelve a usar la herramienta 'login'.")
    if resp.status_code == 422:
        raise RuntimeError(f"El orchestrator rechazo el plan: {resp.text}")
    resp.raise_for_status()

    result = resp.json()
    # El body del orchestrator ya trae su propio correlation_id (el que
    # adopto o genero); esto solo asegura que el id quede presente aunque
    # cambiara esa forma de respuesta.
    result.setdefault("correlation_id", correlation_id)
    return result


if __name__ == "__main__":
    mcp.run()
