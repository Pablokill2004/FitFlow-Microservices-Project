"""Orchestrator Agent: descubre agentes A2A por su Agent Card y coordina delegaciones.

Checkpoint 1 (tasks.md grupos 1-3): scaffolding, observabilidad y descubrimiento
de agentes. La validacion del plan, la autenticacion JWT y la ejecucion sobre
A2A (POST /orchestrate) llegan en checkpoints posteriores.
"""

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI

from observability import correlation_middleware, logger

app = FastAPI(title="FitFlow Orchestrator Agent")
app.middleware("http")(correlation_middleware)

DISCOVERY_TIMEOUT = 5.0


@dataclass
class AgentRef:
    """Un agente descubierto: su nombre, la URL publicada en su card y sus skills."""

    name: str
    url: str
    skill_ids: list[str] = field(default_factory=list)


@dataclass
class AgentFailure:
    """Un agente configurado que no respondio al descubrimiento."""

    url: str
    error: str


# Estado de descubrimiento en memoria: el orchestrator no tiene base de datos.
#
# skill_index mapea skill_id -> AgentRef y se construye UNICAMENTE a partir de
# los skills[].id que traen las Agent Cards obtenidas en tiempo de ejecucion.
# No existe ninguna tabla fija skill -> agente en este archivo: AGENT_URLS le
# dice al orchestrator DONDE buscar, nunca QUIEN tiene cada skill; eso lo
# decide, en cada corrida, la card que el agente publica.
skill_index: dict[str, AgentRef] = {}
_discovered_by_url: dict[str, AgentRef] = {}
_failures_by_url: dict[str, AgentFailure] = {}


def _agent_urls() -> list[str]:
    """URLs base configuradas para el descubrimiento (Task 3.1)."""
    raw = os.getenv("AGENT_URLS", "http://booking-agent:9001,http://notification-agent:9002")
    return [url.strip() for url in raw.split(",") if url.strip()]


async def run_discovery() -> None:
    """Reconstruye el indice de skills consultando /.well-known/agent.json de cada agente.

    Un agente que no responde queda registrado como fallo y no impide que los
    demas se indexen (spec: "One agent unreachable during discovery").
    """
    discovered: dict[str, AgentRef] = {}
    failures: dict[str, AgentFailure] = {}
    new_skill_index: dict[str, AgentRef] = {}

    async def probe(client: httpx.AsyncClient, base_url: str) -> tuple[str, AgentRef | None, str | None]:
        try:
            response = await client.get(f"{base_url}/.well-known/agent.json")
            response.raise_for_status()
            card = response.json()
            skills = card.get("skills") or []
            skill_ids = [skill["id"] for skill in skills if isinstance(skill, dict) and "id" in skill]
            # La URL de ruteo es la que publica la propia card, no la URL de
            # configuracion: eso es lo que exige el escenario "Skill routing
            # follows the card, not a hardcoded table".
            ref = AgentRef(name=card.get("name", base_url), url=card.get("url", base_url), skill_ids=skill_ids)
            return base_url, ref, None
        except Exception as exc:  # noqa: BLE001 - un agente caido no debe tumbar el descubrimiento de los demas
            return base_url, None, str(exc)

    # En paralelo: con el descubrimiento secuencial, N agentes que no responden
    # sumaban N timeouts antes de que uvicorn aceptara conexiones, y un
    # healthcheck de Compose con start_period corto llegaba a fallar.
    async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT) as client:
        results = await asyncio.gather(*(probe(client, url) for url in _agent_urls()))

    for base_url, ref, error in results:
        if ref is not None:
            discovered[base_url] = ref
            for skill_id in ref.skill_ids:
                if skill_id in new_skill_index:
                    logger.warning(
                        "duplicate_skill_id",
                        extra={"skill_id": skill_id, "kept": base_url, "replaced": new_skill_index[skill_id].url},
                    )
                new_skill_index[skill_id] = ref
        else:
            failures[base_url] = AgentFailure(url=base_url, error=error or "unknown error")
            logger.warning("agent_discovery_failed", extra={"agent_url": base_url, "error": error})

    _discovered_by_url.clear()
    _discovered_by_url.update(discovered)
    _failures_by_url.clear()
    _failures_by_url.update(failures)
    skill_index.clear()
    skill_index.update(new_skill_index)

    logger.info(
        "agent_discovery_completed",
        extra={
            "discovered": list(_discovered_by_url.keys()),
            "failed": list(_failures_by_url.keys()),
            "skills": list(skill_index.keys()),
        },
    )


async def ensure_skill_indexed(skill_id: str) -> bool:
    """Vuelve a correr el descubrimiento una sola vez si el skill no esta en el indice.

    Cubre la carrera de arranque de Compose (design decision 2): si un agente
    todavia no estaba listo durante el descubrimiento inicial, esto le da una
    segunda oportunidad antes de que la validacion del plan (checkpoint 2)
    rechace la tarea. Se usara desde POST /orchestrate; aqui solo se expone y
    se deja lista para esa validacion.
    """
    if skill_id in skill_index:
        return True
    await run_discovery()
    return skill_id in skill_index


@app.on_event("startup")
async def on_startup() -> None:
    """Corre el descubrimiento al arrancar sin dejar que un fallo aborte el arranque."""
    try:
        await run_discovery()
    except Exception:
        # run_discovery ya atrapa los fallos por agente; esto es un resguardo
        # extra para que el contenedor jamas falle en /healthz por su culpa.
        logger.exception("startup_discovery_failed")


@app.get("/.well-known/agent.json")
def agent_card() -> dict[str, Any]:
    """Publica la Agent Card del propio orchestrator (spec: "discoverable A2A participant")."""
    return {
        "name": "FitFlow Orchestrator Agent",
        "description": "Coordina una secuencia de delegaciones A2A entre los agentes de FitFlow a partir de una instruccion en lenguaje natural.",
        "url": os.getenv("AGENT_PUBLIC_URL", "http://orchestrator-agent:9003"),
        "skills": [
            {
                "id": "orchestrate",
                "name": "Orquestar instruccion",
                "description": "Recibe una instruccion en lenguaje natural y un plan de pasos, y coordina su ejecucion sobre los agentes descubiertos.",
            },
        ],
    }


@app.get("/healthz")
def healthz() -> dict[str, str]:
    # Debe responder 200 aunque un agente downstream este caido (spec).
    return {"status": "ok"}


@app.get("/agents")
async def list_agents() -> dict[str, Any]:
    """Expone el resultado del descubrimiento para que un revisor vea que se encontro.

    Vuelve a correr el descubrimiento en cada llamada: es un endpoint de
    diagnostico, asi que su valor esta en mostrar el estado real de los
    agentes ahora mismo (por ejemplo tras un `docker compose stop`), no una
    foto tomada solo en el arranque.
    """
    await run_discovery()

    agents_report: list[dict[str, Any]] = []
    for base_url in _agent_urls():
        ref = _discovered_by_url.get(base_url)
        if ref is not None:
            agents_report.append(
                {
                    "url": base_url,
                    "status": "discovered",
                    "name": ref.name,
                    # routing_url es la que publica la card y es la que se usa
                    # para delegar; se reporta aparte de la URL configurada
                    # para que un revisor vea cual es cual.
                    "routing_url": ref.url,
                    "skill_ids": ref.skill_ids,
                }
            )
        else:
            failure = _failures_by_url.get(base_url)
            agents_report.append(
                {
                    "url": base_url,
                    "status": "unreachable",
                    "error": failure.error if failure else "unknown error",
                }
            )
    return {"agents": agents_report}
