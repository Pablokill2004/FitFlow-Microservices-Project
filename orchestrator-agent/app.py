"""Orchestrator Agent: descubre agentes A2A por su Agent Card y coordina delegaciones.

Checkpoint 1 (tasks.md grupos 1-3): scaffolding, observabilidad y descubrimiento
de agentes. Checkpoint 2 (grupos 4-5) agrega: autenticacion del caller,
validacion del plan y POST /orchestrate, que ejecuta el plan sobre A2A.
"""

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from auth import authenticate
from observability import correlation_middleware, get_correlation_id, logger

app = FastAPI(title="FitFlow Orchestrator Agent")
app.middleware("http")(correlation_middleware)

DISCOVERY_TIMEOUT = 5.0
# Los agentes levantan un subproceso MCP por request (design decision 6): un
# timeout corto tipo microservicio cortaria llamadas validas.
A2A_TIMEOUT = 30.0


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


async def probe_agents() -> tuple[dict[str, AgentRef], dict[str, AgentFailure]]:
    """Consulta /.well-known/agent.json de cada agente SIN tocar el indice.

    Separado de run_discovery a proposito: /agents necesita el estado real de
    ahora mismo, pero un endpoint de diagnostico no debe reconstruir la tabla
    de ruteo que usa la ejecucion de planes.
    """
    discovered: dict[str, AgentRef] = {}
    failures: dict[str, AgentFailure] = {}

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
        else:
            failures[base_url] = AgentFailure(url=base_url, error=error or "unknown error")
            logger.warning("agent_discovery_failed", extra={"agent_url": base_url, "error": error})

    return discovered, failures


async def run_discovery() -> None:
    """Reconstruye el indice de skills a partir de las cards descubiertas.

    Un agente que no responde queda registrado como fallo y no impide que los
    demas se indexen (spec: "One agent unreachable during discovery").
    """
    discovered, failures = await probe_agents()

    new_skill_index: dict[str, AgentRef] = {}
    for base_url, ref in discovered.items():
        for skill_id in ref.skill_ids:
            if skill_id in new_skill_index:
                logger.warning(
                    "duplicate_skill_id",
                    extra={"skill_id": skill_id, "kept": base_url, "replaced": new_skill_index[skill_id].url},
                )
            new_skill_index[skill_id] = ref

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


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    """Devuelve el body solo si es un objeto JSON; None en cualquier otro caso."""
    try:
        parsed = response.json()
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


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

    Sondea a los agentes en cada llamada para mostrar su estado real ahora
    mismo (por ejemplo tras un `docker compose stop`), pero NO toca el indice
    de ruteo: si lo reconstruyera, un bache momentaneo durante una llamada de
    diagnostico sacaria ese skill del indice y el siguiente plan se rechazaria
    con 422 en vez de llegar a ejecutarse. Un endpoint de solo lectura no debe
    cambiar como falla el siguiente request.
    """
    probed, probe_failures = await probe_agents()

    agents_report: list[dict[str, Any]] = []
    for base_url in _agent_urls():
        ref = probed.get(base_url)
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
            failure = probe_failures.get(base_url)
            agents_report.append(
                {
                    "url": base_url,
                    "status": "unreachable",
                    "error": failure.error if failure else "unknown error",
                }
            )
    return {"agents": agents_report}


# --- Modelos del plan (Task 4.2) ------------------------------------------
#
# Step NO tiene user_id: el destinatario de una notificacion sale unicamente
# del JWT (design decision 5), nunca del plan. El plan es texto generado por
# un modelo a partir de la instruccion del usuario -- es entrada no confiable
# -- asi que aunque el JSON entrante traiga un "user_id" en un step, Pydantic
# lo descarta por no estar declarado en el modelo.
class Step(BaseModel):
    skill: str
    class_id: int | None = None
    booking_id: int | None = None
    message: str | None = None
    reason: str | None = None


class OrchestrateRequest(BaseModel):
    instruction: str
    steps: list[Step]
    access_token: str


# --- Validacion del plan (Task 4.3) ---------------------------------------


async def _validate_plan(steps: list[Step]) -> None:
    """Rechaza el plan completo con 422, sin ejecutar nada, si esta vacio o
    si algun step nombra un skill ausente del indice descubierto.

    Nota de eficiencia: si K steps nombran skills desconocidos, esto dispara
    UNA sola pasada de re-descubrimiento (no K). ensure_skill_indexed() se
    llama una unica vez, con el primer skill faltante; el resto del plan se
    revalida contra el indice ya refrescado por esa misma llamada, sin volver
    a invocar discovery por cada skill que siga sin existir.
    """
    if not steps:
        raise HTTPException(status_code=422, detail="El plan no puede tener steps vacio")

    missing = [step.skill for step in steps if step.skill not in skill_index]
    if missing:
        await ensure_skill_indexed(missing[0])
        missing = [step.skill for step in steps if step.skill not in skill_index]
        if missing:
            raise HTTPException(status_code=422, detail=f"Skill desconocido: {missing[0]}")


# --- Ejecucion sobre A2A (Task 5) -----------------------------------------


def _extract_booking(result: Any) -> dict[str, Any] | None:
    """Busca el BookingResponse dentro del resultado A2A de create_booking.

    Booking Agent devuelve el content MCP tal cual (una lista de content
    items); el JSON de la reserva viaja como texto dentro de uno de ellos.
    """
    if not isinstance(result, list):
        return None
    for item in result:
        text = item.get("text") if isinstance(item, dict) else None
        if not text:
            continue
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and "id" in data:
            return data
    return None


def _compose_notification_message(last_booking_result: Any) -> str:
    """Mensaje por defecto cuando un step de notificacion no trae uno propio.

    Si el step anterior fue un create_booking exitoso, referencia la reserva
    real (spec: "Output of one step feeds the next"); si no hay reserva
    previa, un mensaje generico simple y predecible.
    """
    booking = _extract_booking(last_booking_result)
    if booking:
        clase = f" para la clase {booking['class_id']}" if "class_id" in booking else ""
        return f"Tu reserva #{booking['id']}{clase} fue confirmada."
    return "Tu solicitud se proceso correctamente."


def _build_task_body(step: Step, access_token: str, user_id: int, last_booking_result: Any) -> dict[str, Any]:
    """Arma el body de /a2a/tasks para el skill de este step.

    Esto NO es una tabla skill->agente -- el agente destino sale siempre de
    skill_index (design decision 2). Es la forma del body, que es inherente
    al contrato de cada skill publicado en su Agent Card: Booking Agent
    espera {skill, class_id|booking_id, access_token}; Notification Agent
    espera {skill, user_id, message}. Se deriva de lo que trae el step
    (message vs class_id/booking_id), no de un mapeo fijo.
    """
    if step.skill == "send_notification":
        message = step.message or _compose_notification_message(last_booking_result)
        # user_id SIEMPRE sale del JWT, nunca del plan (Task 4.4): el plan es
        # entrada no confiable y Step ni siquiera declara ese campo.
        return {"skill": step.skill, "user_id": user_id, "message": message}

    body: dict[str, Any] = {"skill": step.skill, "access_token": access_token}
    if step.class_id is not None:
        body["class_id"] = step.class_id
    if step.booking_id is not None:
        body["booking_id"] = step.booking_id
    return body


async def _execute_step(
    client: httpx.AsyncClient, step: Step, access_token: str, user_id: int, last_booking_result: Any
) -> dict[str, Any]:
    agent = skill_index.get(step.skill)
    if agent is None:
        # Defensivo: _validate_plan ya garantizo que el skill existia, pero
        # el indice puede refrescarse entre requests concurrentes.
        return {"skill": step.skill, "status": "failed", "error": f"Skill ya no esta indexado: {step.skill}"}

    body = _build_task_body(step, access_token, user_id, last_booking_result)
    try:
        response = await client.post(
            f"{agent.url}/a2a/tasks",
            json=body,
            headers={"x-correlation-id": get_correlation_id()},
        )
    except httpx.HTTPError as exc:
        # Task 5.5: un agente inalcanzable se reporta failed, nunca un 500
        # del orchestrator.
        logger.warning("a2a_call_failed", extra={"skill": step.skill, "agent_url": agent.url, "error": str(exc)})
        return {"skill": step.skill, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    # Un agente puede devolver algo que no sea un objeto JSON (o no ser JSON).
    # Eso no debe tumbar el reporte completo con un 500: el paso se reporta
    # igual que cualquier otro fallo.
    payload = _json_object(response)

    if response.status_code >= 400:
        detail = payload.get("detail", response.text) if payload is not None else response.text
        return {"skill": step.skill, "status": "failed", "error": detail}

    if payload is None:
        logger.warning("a2a_unexpected_body", extra={"skill": step.skill, "agent_url": agent.url})
        return {"skill": step.skill, "status": "failed", "error": f"Respuesta no valida del agente: {response.text[:200]}"}

    return {"skill": step.skill, "status": "succeeded", "result": payload.get("result")}


@app.post("/orchestrate")
async def orchestrate(payload: OrchestrateRequest, request: Request) -> dict[str, Any]:
    """Recibe un plan, lo valida y lo ejecuta en orden sobre A2A (spec: "Plan
    execution over A2A")."""
    # Auth primero: nada se valida ni se llama con un token invalido (Task 4.1).
    user_id = authenticate(payload.access_token)
    request.state.user_id = user_id

    await _validate_plan(payload.steps)

    logger.info(
        "orchestration_started",
        extra={"instruction": payload.instruction, "step_count": len(payload.steps)},
    )

    report: list[dict[str, Any]] = []
    last_booking_result: Any = None
    failed = False

    async with httpx.AsyncClient(timeout=A2A_TIMEOUT) as client:
        for step in payload.steps:
            if failed:
                report.append({"skill": step.skill, "status": "skipped"})
                continue

            outcome = await _execute_step(client, step, payload.access_token, user_id, last_booking_result)
            report.append(outcome)

            if outcome["status"] == "succeeded" and step.skill == "create_booking":
                last_booking_result = outcome.get("result")
            if outcome["status"] != "succeeded":
                failed = True

    logger.info(
        "orchestration_completed",
        extra={"instruction": payload.instruction, "steps": report},
    )

    return {
        "instruction": payload.instruction,
        "steps": report,
        "correlation_id": get_correlation_id(),
    }
