"""Booking Agent: adaptador A2A que delega operaciones al servidor MCP."""

import os
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field

app = FastAPI(title="FitFlow Booking Agent")


class A2ATask(BaseModel):
    """Solicitud mínima que el Orchestrator envía al agente."""

    skill: Literal["create_booking", "cancel_booking"]
    class_id: int | None = Field(default=None, ge=1)
    booking_id: int | None = Field(default=None, ge=1)
    access_token: str = Field(min_length=1)


def _mcp_parameters(access_token: str) -> StdioServerParameters:
    """Configura el MCP dentro de Docker usando los nombres de Compose."""
    environment = os.environ.copy()
    environment.update(
        {
            "CONSUL_HOST": os.getenv("CONSUL_HOST", "consul"),
            "FITFLOW_SERVICE_HOST": os.getenv("FITFLOW_SERVICE_HOST", "booking-svc"),
            "FITFLOW_ACCESS_TOKEN": access_token,
        }
    )
    return StdioServerParameters(
        command="python",
        args=["/opt/fitflow-mcp/server.py"],
        env=environment,
    )


async def _call_mcp(task: A2ATask) -> Any:
    """Abre una sesión MCP y ejecuta solo la herramienta anunciada."""
    tool_arguments = {"class_id": task.class_id} if task.skill == "create_booking" else {"booking_id": task.booking_id}
    if any(value is None for value in tool_arguments.values()):
        raise HTTPException(status_code=422, detail=f"Falta un identificador para {task.skill}")

    async with stdio_client(_mcp_parameters(task.access_token)) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(task.skill, arguments=tool_arguments)
            if result.isError:
                raise HTTPException(status_code=502, detail=str(result.content))
            return [item.model_dump() if hasattr(item, "model_dump") else item for item in result.content]


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
    return {"skill": task.skill, "result": result, "correlation_id": x_correlation_id or "generated-by-orchestrator"}