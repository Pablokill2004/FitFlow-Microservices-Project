"""Booking Agent: adaptador A2A que delega operaciones al servidor MCP."""

import os
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field

from observability import correlation_middleware, get_correlation_id, logger

app = FastAPI(title="FitFlow Booking Agent")
app.middleware("http")(correlation_middleware)


class A2ATask(BaseModel):
    """Solicitud mínima que el Orchestrator envía al agente."""

    skill: Literal["create_booking", "cancel_booking"]
    class_id: int | None = Field(default=None, ge=1)
    booking_id: int | None = Field(default=None, ge=1)
    access_token: str = Field(min_length=1)


def _mcp_parameters(access_token: str, correlation_id: str) -> StdioServerParameters:
    """Configura el MCP dentro de Docker usando los nombres de Compose."""
    environment = os.environ.copy()
    environment.update(
        {
            "CONSUL_HOST": os.getenv("CONSUL_HOST", "consul"),
            "FITFLOW_SERVICE_HOST": os.getenv("FITFLOW_SERVICE_HOST", "booking-svc"),
            "FITFLOW_ACCESS_TOKEN": access_token,
            # Mismo mecanismo que FITFLOW_ACCESS_TOKEN: es la unica forma de pasar
            # estado por-request al subproceso MCP (task 11.2).
            "FITFLOW_CORRELATION_ID": correlation_id,
        }
    )
    return StdioServerParameters(
        command="python",
        args=["/opt/fitflow-mcp/server.py"],
        env=environment,
    )


async def _call_mcp(task: A2ATask) -> Any:
    """Abre una sesion MCP y ejecuta solo la herramienta anunciada.

    El HTTPException se lanza FUERA del bloque `async with` (task 11.6): si se
    lanza dentro del TaskGroup de anyio que usan stdio_client/ClientSession,
    anyio lo envuelve en un ExceptionGroup que FastAPI no reconoce y el
    llamador recibe un 500 generico en vez del mensaje real (ej. "Class is
    full"). Por eso el error se guarda y se relanza despues de cerrar el
    contexto async.
    """
    tool_arguments = {"class_id": task.class_id} if task.skill == "create_booking" else {"booking_id": task.booking_id}
    if any(value is None for value in tool_arguments.values()):
        raise HTTPException(status_code=422, detail=f"Falta un identificador para {task.skill}")

    correlation_id = get_correlation_id()
    error_status: int | None = None
    error_detail: str | None = None
    tool_result: Any = None

    logger.info("mcp_call_started", extra={"skill": task.skill})
    async with stdio_client(_mcp_parameters(task.access_token, correlation_id)) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(task.skill, arguments=tool_arguments)
            if result.isError:
                error_status, error_detail = 502, str(result.content)
            else:
                tool_result = [item.model_dump() if hasattr(item, "model_dump") else item for item in result.content]

    if error_status is not None:
        logger.warning("mcp_call_failed", extra={"skill": task.skill, "detail": error_detail})
        raise HTTPException(status_code=error_status, detail=error_detail)
    logger.info("mcp_call_succeeded", extra={"skill": task.skill})
    return tool_result


@app.get("/.well-known/agent.json")
def agent_card() -> dict[str, Any]:
    """Publica las capacidades que usa el Orchestrator para descubrir el agente."""
    return {
        "name": "FitFlow Booking Agent",
        "description": "Gestiona reservas de clases fitness en FitFlow.",
        "url": os.getenv("AGENT_PUBLIC_URL", "http://booking-agent:9001"),
        "skills": [
            {"id": "create_booking", "name": "Crear reserva", "description": "Reserva una clase para un usuario."},
            {"id": "cancel_booking", "name": "Cancelar reserva", "description": "Cancela una reserva de un usuario."},
        ],
    }


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/a2a/tasks")
async def execute_task(task: A2ATask, x_correlation_id: str | None = Header(default=None)) -> dict[str, Any]:
    """Recibe una tarea A2A y conserva el correlation ID en la respuesta."""
    result = await _call_mcp(task)
    # El correlation id real: el del header si vino, o el que genero el
    # middleware. Devolver un literal fijo cuando no viene header mentia,
    # porque la respuesta si lleva el id generado en su propio header.
    return {"skill": task.skill, "result": result, "correlation_id": get_correlation_id()}