"""Notification Agent: adaptador A2A que delega envio de notificaciones al servidor MCP."""

import os
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field

from observability import correlation_middleware, get_correlation_id, logger

app = FastAPI(title="FitFlow Notification Agent")
app.middleware("http")(correlation_middleware)


class A2ATask(BaseModel):
    """Solicitud minima que el Orchestrator envia al agente."""

    skill: Literal["send_notification"]
    user_id: int = Field(ge=1)
    message: str = Field(min_length=1)
    # No lo usa send_notification (notif-svc no exige JWT), pero se acepta
    # para mantener el mismo esquema de tarea que el Booking Agent.
    access_token: str | None = None


def _mcp_parameters(correlation_id: str) -> StdioServerParameters:
    """Configura el MCP dentro de Docker usando los nombres de Compose."""
    environment = os.environ.copy()
    environment.update(
        {
            "CONSUL_HOST": os.getenv("CONSUL_HOST", "consul"),
            "FITFLOW_SERVICE_HOST": os.getenv("FITFLOW_SERVICE_HOST", "notif-svc"),
            # Mismo mecanismo que FITFLOW_ACCESS_TOKEN en el Booking Agent: es la
            # unica forma de pasar estado por-request al subproceso MCP (task 11.2).
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
    llamador recibe un 500 generico en vez del mensaje real. Por eso el error
    se guarda y se relanza despues de cerrar el contexto async.
    """
    correlation_id = get_correlation_id()
    error_status: int | None = None
    error_detail: str | None = None
    tool_result: Any = None

    logger.info("mcp_call_started", extra={"skill": task.skill})
    async with stdio_client(_mcp_parameters(correlation_id)) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                "send_notification",
                arguments={"user_id": task.user_id, "message": task.message},
            )
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
        "name": "FitFlow Notification Agent",
        "description": "Envia notificaciones a los usuarios de FitFlow.",
        "url": os.getenv("AGENT_PUBLIC_URL", "http://notification-agent:9002"),
        "skills": [
            {
                "id": "send_notification",
                "name": "Enviar notificacion",
                "description": "Envia una notificacion a un usuario de FitFlow.",
            },
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
