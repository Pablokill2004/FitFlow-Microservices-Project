## Why

FitFlow's A2A layer is half-built: `booking-agent` (:9001) and `notification-agent` (:9002) each publish an Agent Card at `/.well-known/agent.json` and accept tasks at `POST /a2a/tasks`, but nothing discovers or coordinates them. The architecture diagram in `README.md` still reads `Orchestrator Agent <- PENDIENTE`, and the end-to-end flow the course rubric grades — *"Reserva yoga para el viernes y avísame"* resolved over A2A — cannot be demonstrated. This is Estudiante 2's Fase 3 deliverable (Task 5 + Task 4C), due with the final submission on día 27.

## What Changes

- **New `orchestrator-agent` container** (FastAPI, port 9003, own Dockerfile, added to `docker-compose.yml`) that publishes its own Agent Card and coordinates the two leaf agents. `FitFlow-Instrucciones-Generales.md` requires this shape explicitly: *"Tres contenedores nuevos: orchestrator-agent, booking-agent, notification-agent, cada uno con su propio puerto y publicando su Agent Card"* and *"Los tres agentes corren como contenedores"*.
- **Agent Card discovery**: the orchestrator fetches `/.well-known/agent.json` from each configured agent base URL and builds a `skill id -> agent URL` index at runtime. It never hardcodes which agent owns `create_booking` or `send_notification` — routing comes from the cards it discovered.
- **New `orchestrator-mcp/` local MCP server** (stdio, launched by Claude Desktop on the host, like the existing `fitflow-mcp`) exposing `login`, `list_classes`, `discover_agents`, and `orchestrate`. This is the bridge that lets a Claude Desktop session drive the A2A layer.
- **Natural-language understanding comes from the user's Claude Desktop session**, not from an API call inside a container. Claude Desktop reads *"Reserva yoga para el viernes y avísame"*, calls `discover_agents` and `list_classes` to ground itself in real skills and real class ids, and submits the resulting plan to the orchestrator through `orchestrate`. No `ANTHROPIC_API_KEY`, no paid API usage, no network egress from the stack.
- **Sequential delegation** over `POST /a2a/tasks` to each target agent, propagating one `x-correlation-id` across every hop so the whole flow is traceable in the existing JSON logs.
- **JWT handling**: the orchestrator validates the caller's token with the shared `JWT_SECRET` and extracts `user_id` from it, so `send_notification` gets a real recipient without the caller restating it.
- **README (Task 4C)**: add a dedicated **Resiliencia** section and an **Agent-to-Agent: MCP vs A2A** section, update the architecture diagram to show the orchestrator as built rather than `PENDIENTE`, and document the Claude Desktop connection for the A2A demo.

Not a breaking change: no existing service, endpoint, or agent contract is modified. `booking-agent`, `notification-agent`, and `fitflow-mcp` are consumed exactly as they already behave.

## Capabilities

### New Capabilities
- `orchestrator-agent`: discovering downstream agents by Agent Card, exposing that discovery and a delegation entry point to a Claude Desktop session over MCP, validating and executing a delegation plan over A2A, and reporting the per-step outcome with a shared correlation ID.

### Modified Capabilities
<!-- None. The orchestrator only consumes the existing agent, MCP, and service contracts; no
     existing requirement changes. -->

## Impact

- **New code**: `orchestrator-agent/` (`app.py`, `Dockerfile`, `requirements.txt`, `README.md`) and `orchestrator-mcp/` (`server.py`, `requirements.txt`, `README.md`).
- **Modified**: `docker-compose.yml` (new `orchestrator-agent` service on :9003), `.env.example` (agent URLs, `JWT_SECRET`/`ALGORITHM` passthrough), `README.md` (Resiliencia + MCP vs A2A sections, architecture diagram, Claude Desktop setup).
- **Modified to close the correlation-ID chain** (see design decision 10): `booking-agent/app.py` and `notification-agent/app.py` gain the project's JSON logging and pass the incoming `x-correlation-id` into their MCP subprocess; `fitflow-mcp/server.py` forwards that id as a header on its downstream calls. Without this the id dies at the agents — `booking-svc` and `notif-svc` generate fresh ones — which would leave the "one id across five containers" requirement unmet and remove the only reliable proof that the Claude Desktop demo went through A2A rather than a direct MCP tool. These are Estudiante 1's and Estudiante 3's files; the edits are additive and change no existing contract.
- **Unmodified**: `booking-svc`, `users-svc`, `notif-svc`.
- **New dependencies**: `httpx`/`requests` and `pyjwt` in the orchestrator container; `mcp[cli]` and `requests` in the MCP server, matching the pins the existing agents already use. **No `anthropic` SDK and no API key anywhere.**
- **Ports**: 9003 is newly published on the host.
- **Operational requirement**: the A2A demo needs Claude Desktop running on the host with `orchestrator-mcp` registered. Because `fitflow-mcp` exposes a direct `create_booking` tool that would shadow the A2A path, the README documents connecting one server at a time — `fitflow` for the Task 2B demo, `fitflow-orchestrator` for the Task 5 demo.
- **Grading impact**: closes the "Agent-to-Agent implementation" criterion (20 pts) coordinated by Estudiante 2, satisfies the "los tres agentes corren como contenedores" checkpoint, and completes the README sections Task 4C assigns to Estudiante 2.
