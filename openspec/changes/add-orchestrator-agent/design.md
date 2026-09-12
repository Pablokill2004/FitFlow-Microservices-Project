## Context

See `proposal.md` — Why. Design-relevant facts about the code and the requirements as they stand today:

- **The container shape is mandated, not chosen.** `FitFlow-Instrucciones-Generales.md:354-358` says *"Tres contenedores nuevos: orchestrator-agent, booking-agent, notification-agent, cada uno con su propio puerto y publicando su Agent Card en /.well-known/agent.json"* and *"Los tres agentes corren como contenedores"*. An MCP-server-only orchestrator would fail that checkpoint.
- **Claude Desktop is an MCP client, not a server. Nothing can call into it.** This is the single constraint that shapes the whole design: a container cannot "ask Claude Desktop" anything. Control inverts — Claude Desktop launches a local process and calls *it*. So the reasoning layer sits above the stack and reaches down, never the other way around.
- **Both downstream agents already work and are identical in shape.** `booking-agent/app.py` and `notification-agent/app.py` each serve `GET /.well-known/agent.json` (fields: `name`, `description`, `url`, `skills[].id`), `GET /healthz`, and `POST /a2a/tasks`. Each reads an optional `x-correlation-id` header and echoes it back. Their task bodies are *not* uniform:
  - Booking Agent: `{skill: "create_booking"|"cancel_booking", class_id?, booking_id?, access_token}` — `access_token` is required (`min_length=1`).
  - Notification Agent: `{skill: "send_notification", user_id, message, access_token?}` — `access_token` is optional and unused.
  - Both reject unknown skills at the Pydantic layer (`Literal[...]` → 422).
- **Each agent spawns `fitflow-mcp/server.py` over stdio per request** and calls the matching MCP tool. The orchestrator sits above this and never touches MCP downward.
- **`fitflow-mcp` already establishes the host-side MCP pattern.** It runs outside the compose network as a Claude Desktop subprocess, so it asks Consul which port a service landed on but builds the final URL against `FITFLOW_SERVICE_HOST` (`localhost` by default), falling back to the published port when Consul has no healthy instance. `orchestrator-mcp` reuses this exact pattern.
- **The class catalogue is seeded relative to startup time** (`booking-svc/app/main.py:seed_classes` — Yoga at `now + 1 day`, Spinning `+1d2h`, Crossfit `+2d`, Pilates `+3d`, Zumba `+4d`). So "el viernes" maps to a different `class_id` on every fresh `docker compose up`, and `GET /classes` (unauthenticated) is the only trustworthy source for that mapping.
- **JWT**: `users-svc/app/auth.py` signs `{user_id, sub, exp}` with `JWT_SECRET`/`ALGORITHM` (HS256, 8 h). `booking-svc/app/auth.py` validates with the same pair and 401s on missing/expired/invalid. The `user_id` claim is an `int`.
- **Logging**: `booking-svc/app/observability.py` is the house pattern — a `JsonFormatter` emitting `timestamp`/`level`/`service`/`event`/`correlation_id`, a `contextvars`-backed correlation id, and an HTTP middleware that adopts or generates `x-correlation-id` and sets it on the response. The agents do *not* have this; they only echo the header.
- **Compose** publishes 8003/8002/8001/9001/9002 and 8500; every service takes its config from `.env`. `booking-agent`/`notification-agent` build with `context: .` because their Dockerfiles copy `fitflow-mcp/server.py` in.

## Goals / Non-Goals

**Goals:**

- Satisfy the mandated three-container shape while putting the natural-language understanding on the user's Claude Desktop session, so the stack needs no API key and no outbound API access.
- Routing decided by discovered Agent Cards, so the A2A layer is demonstrably *discovery*-based — that is what the rubric grades.
- One correlation id threaded from the orchestrator through both agents into `booking-svc`/`notif-svc` logs, so the demo can `grep` a single id across five containers.
- A resolution path for "yoga el viernes" that survives the catalogue being re-seeded with new timestamps and ids.
- Fits the project's existing idioms: FastAPI + Pydantic for the container, a `fitflow-mcp`-shaped stdio server for the host-side adapter, `.env` for config, a service block in the shared compose file.

**Non-Goals:**

- Registering the orchestrator in Consul. No existing agent does; the agents are discovered by Agent Card, which is the point of the A2A layer. (Consul stays the mechanism for *service* discovery, which is Fase 2 and already done.)
- Any language-model call from inside the stack. The container has no model client and no credential.
- A conversational orchestrator API. The container takes one plan, executes it, reports. Multi-turn conversation lives in Claude Desktop, which is where it belongs.
- Parallel step execution, compensating transactions, or rollback of a partially executed plan.
- Giving the orchestrator its own database. It is stateless.
- Touching `fitflow-mcp`, the two existing agents, or any microservice.

## Decisions

### 1. A separate `orchestrator-agent` container on :9003

Required by the base document (see Context). It also mirrors the two agents that exist — own directory, own Dockerfile, own compose block, own README — and port 9003 continues the 9001/9002 sequence.

Its Dockerfile uses `context: .` like the other two for consistency, but it does **not** copy `fitflow-mcp/server.py` in: the orchestrator speaks A2A only and has no MCP client of its own.

### 2. Discovery: fetch cards from a configured URL list at startup, refresh lazily

`AGENT_URLS` is a comma-separated list of agent base URLs (default `http://booking-agent:9001,http://notification-agent:9002`). At startup the orchestrator `GET`s `/.well-known/agent.json` from each, and builds `{skill_id: AgentRef(name, url, card)}`. Discovery failures are recorded per-agent, not fatal — `GET /agents` shows each entry as discovered-with-skills or unreachable-with-error, satisfying the spec's "one agent unreachable" scenario.

Because compose starts containers concurrently, startup discovery can legitimately race the agents coming up. So: if a plan needs a skill that is not in the index, re-run discovery once before rejecting it. This also covers an agent restarting behind the orchestrator.

*Alternative rejected:* looking the agents up in Consul. The agents do not register there, and adding registration to them would edit other students' deliverables.

*Note on the config list:* seeding discovery from a URL list is not the same as hardcoding routing. The orchestrator is told *where to look*, never *who owns which skill* — that comes only from the fetched cards. Keep that separation in the code, since it is exactly what the spec's third discovery scenario tests.

### 3. Claude Desktop is the reasoning layer, reached through `orchestrator-mcp`

Because nothing can call into Claude Desktop (see Context), the only way to use the user's session as the planner is to give Claude Desktop a tool surface it can drive. `orchestrator-mcp/server.py` is a `FastMCP` stdio server registered in Claude Desktop's config exactly like `fitflow-mcp`, exposing four tools:

| Tool | Reaches | Purpose |
|---|---|---|
| `login(email, password)` | `users-svc` | Authenticate and hold the JWT in process memory, mirroring `fitflow-mcp`'s session pattern |
| `list_classes()` | `booking-svc` `GET /classes` | Give the session real ids and ISO schedules so "el viernes" resolves against live data |
| `discover_agents()` | orchestrator `GET /agents` | Tell the session which skills actually exist, from the discovered cards |
| `orchestrate(instruction, steps)` | orchestrator `POST /orchestrate` | Submit the plan and return the per-step report |

The session does the work the container used to pay an API to do: read the instruction, call `discover_agents` and `list_classes`, resolve "yoga el viernes" to a `class_id`, and submit a plan.

`orchestrator-mcp` runs on the host, so it resolves service addresses the way `fitflow-mcp` does — Consul lookup for the port, `localhost` for the host, published-port fallback. **`list_classes` therefore reads `booking-svc` directly from the host, not through the orchestrator container.** That is deliberate: it keeps the container purely A2A, never talking to a microservice. The MCP server is host-side tooling and is allowed to read the public catalogue, exactly as `fitflow-mcp` already does.

*Alternative rejected:* a single composite `orchestrate(instruction)` tool that also does the planning. There would be nothing left to plan *with* — the whole point is that the session, not the server, does the reasoning.

*Alternative rejected:* granular per-step `delegate(skill, ...)` tools called once each. That would move sequencing and fail-fast into Claude Desktop and reduce the container to a relay, contradicting the base document's *"coordina ambos agentes en secuencia"*. Submitting the whole plan keeps the container the coordinator.

### 4. The container receives both the instruction and the plan

`POST /orchestrate` takes `{instruction, steps[], access_token}`. The `steps` are what it executes; the `instruction` is the original user text, which it logs and echoes in the response.

This is worth being precise about rather than glossing: the base document says the orchestrator *"recibe la instrucción del usuario, decide qué agente(s) necesita"*. With Claude Desktop as the reasoning layer, the container genuinely receives the instruction — it is logged and returned, so the audit trail reads instruction → plan → results — but the *deciding* happens in the session. That is an inherent consequence of dropping the API key, and the README's A2A section should say so plainly rather than imply the container reasons.

### 5. `user_id` comes from the JWT, never from the plan or the instruction

The orchestrator decodes the caller's token with `JWT_SECRET`/`ALGORITHM` exactly as `booking-svc/app/auth.py` does — same library (`pyjwt`), same 401 mapping for missing/expired/invalid — before executing anything. The `user_id` claim is then injected into every `send_notification` step, overriding whatever the plan carried.

This is a security property, not a convenience: the spec requires that a caller cannot redirect a notification at another user by asking for it. The plan now originates from a language model reading user-supplied text, so letting it populate `user_id` would make prompt-injected instruction text an authorization bypass. The container is the trust boundary; treat the submitted plan as untrusted input and validate every field against the discovered index and the token.

The orchestrator therefore needs `JWT_SECRET` and `ALGORITHM` in its environment, shared from `.env` the same way `booking-svc` receives them.

### 6. Execution: sequential, fail-fast, with every step reported

Steps run in plan order over `POST /a2a/tasks`. Each request body is built per target agent, since the two agents' task schemas differ (Context): booking steps carry `class_id`/`booking_id` + `access_token`; the notification step carries `user_id` + `message`. On the first non-2xx, remaining steps are marked `skipped` and the response returns with the failure attached — no compensation, no cancelling the booking that already succeeded. That is a deliberate simplification and is called out in Risks.

HTTP client: `httpx` with an explicit timeout. Note that the agents spawn an MCP subprocess per request, so an A2A call is slower than a plain service call — set the timeout generously (~30 s), well above `booking-svc`'s internal 2 s notif timeout, which is a different concern entirely.

Step results are reported as `{skill, status: "succeeded"|"failed"|"skipped", result|error}` so the demo shows the whole plan even when one hop breaks.

### 7. Correlation id: adopt-or-generate middleware copied from `booking-svc`

Port `booking-svc/app/observability.py` into the orchestrator with `SERVICE_NAME = "orchestrator-agent"`. It already does precisely what the spec requires — adopt an inbound `x-correlation-id` or generate a UUID, expose it via `contextvars`, set it on the response, and emit JSON logs in the house format. The orchestrator then forwards the same value as a header on both A2A calls, and the existing agents already pass it through into their responses.

`orchestrator-mcp` generates a correlation id per `orchestrate` call and sends it as `x-correlation-id`, so the id covers the flow from the Claude Desktop session down. It returns that id in the tool result, giving the demo one value to grep for.

*Note:* this duplicates ~80 lines across services. That is already the project's pattern (`booking-svc` and `notif-svc` each carry their own copy), and introducing a shared package now would mean restructuring three other students' images. Consistency wins here.

### 8. Demo hygiene: connect one MCP server at a time

`fitflow-mcp` exposes `create_booking`, `cancel_booking`, and `send_notification` as *direct* tools. If both servers are registered in Claude Desktop simultaneously, the session sees a one-call direct path and a multi-call A2A path for the same user request, and will often take the direct one — silently bypassing the A2A layer the demo exists to show.

So the README documents connecting one at a time: `fitflow` for the Task 2B (MCP) demo, `fitflow-orchestrator` for the Task 5 (A2A) demo, with a Claude Desktop restart between them. This costs a restart between the two video segments and buys certainty that the A2A segment actually exercises A2A. Verify it by checking that the correlation id appears in `booking-agent`'s logs — if the session took the direct path, it will not.

### 9. README (Task 4C) restructure

Two new top-level sections in `README.md`:

- **Resiliencia** — consolidates what is currently scattered in "Estado actual": the 2 s timeout, 3 retries with exponential backoff + jitter, the circuit breaker (3 failures → open for 30 s), and the outbox pattern; plus the step-by-step `docker compose stop notif-svc` → book → `/resilience/status` → `start` → recovery demo, which is exactly what the video's steps 3–5 record.
- **Agent-to-Agent: MCP vs A2A** — the conceptual distinction the rubric asks for. MCP is the vertical axis (a client reaching *down* to tools over stdio, one process per call); A2A is the horizontal axis (a peer reaching *across* to another agent over HTTP, discovered by Agent Card). This change makes the contrast unusually legible and the section should use it: `orchestrator-mcp` is MCP reaching down from Claude Desktop, the orchestrator→agents hop is A2A reaching across, and the leaf agents are where A2A turns back into MCP. Three layers, two protocols, one request.

The architecture diagram's `Orchestrator Agent <- PENDIENTE` becomes the built container with its port, plus the Claude Desktop / MCP layer above it. The existing `## Agent-to-Agent` section is absorbed rather than left to drift alongside the new one.

## Risks / Trade-offs

- **The demo now depends on Claude Desktop running on the host with the right MCP server connected.** → Documented setup with a verification step (`discover_agents` returns both agents before recording). Unlike the API-key approach this has no cost and no egress, but it is not reproducible from `docker compose up` alone — the README must say so.
- **Tool shadowing between `fitflow-mcp` and `orchestrator-mcp`.** → Decision 8: connect one at a time, and verify via the correlation id that the A2A path was actually taken.
- **The submitted plan is model-generated input crossing a trust boundary.** → Decision 5: the container validates every skill against the discovered index and overrides `user_id` from the JWT. Treat `steps` as untrusted.
- **Non-deterministic planning: the session may pick a different class for an ambiguous instruction.** → The executed plan is echoed in the response, so the demo shows what was chosen. Use an unambiguous instruction when recording. Every skill is validated against the discovered index, so the worst case is a wrong-but-valid class, never an invented skill.
- **Fail-fast leaves a partial effect: booking created, notification never sent.** → Accepted for this scope and stated in the README. Worth noting that `booking-svc`'s outbox already makes the *booking → notification* hop durable on its own path; the A2A path deliberately does not duplicate that machinery.
- **Startup discovery can race the agents' startup under `docker compose up`.** → `depends_on` both agents, plus the lazy re-discovery in decision 2. Neither alone is sufficient: `depends_on: service_started` does not wait for the app inside the container to be listening.
- **Seeded `class_id`s shift on every fresh volume.** → Never hardcode a class id anywhere — `list_classes`, the README examples, and the demo script all read the live catalogue first.
- **`mcp[cli]` version drift.** → Pin the same exact version the existing agents use (`mcp[cli]==1.29.1`) so `orchestrator-mcp` behaves identically to `fitflow-mcp` under the same Claude Desktop.

## Migration Plan

Additive only — no data migration, no contract change. Deploy is `docker compose up --build -d` picking up the new service, plus a one-time Claude Desktop config entry for `orchestrator-mcp`. Rollback is removing the `orchestrator-agent` block from `docker-compose.yml` and the config entry, which returns the system to its exact current behaviour. No other container's configuration changes, so a rollback cannot break the Fase 1/2 deliverables.

## Open Questions

- Whether to also publish the orchestrator's Agent Card into Consul for the extra-credit cloud deployment. Deferred: it changes no requirement here and is only relevant if the team takes the +15 pt option. Note that cloud deployment and a host-local Claude Desktop session do not compose — a deployed orchestrator would have no reasoning layer in front of it, so the +15 pt option would need its own answer to that.
