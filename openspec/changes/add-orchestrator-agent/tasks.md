> Branch: `phase3/orchestrator-agent/estudiante2` (already created off `main`).
> Implementation agents for this change should run on Sonnet.
> Prerequisite for groups 6 and 9: Claude Desktop installed on the host. No API key is needed anywhere.

## 1. Scaffold the orchestrator container

- [x] 1.1 Create `orchestrator-agent/` with `app.py`, `requirements.txt`, `Dockerfile`, `README.md`; verify `ls orchestrator-agent` shows all four files
- [x] 1.2 Pin `requirements.txt` to exact versions matching the other agents' style — `fastapi==0.110.0`, `uvicorn[standard]>=0.31.1,<1.0.0`, `httpx==<pinned>`, `pyjwt==<pinned>`. **No `anthropic` dependency.** Verify `pip install -r orchestrator-agent/requirements.txt` resolves in a scratch venv
- [x] 1.3 Write the Dockerfile modelled on `booking-agent/Dockerfile` but **without** copying `fitflow-mcp/server.py` (the container speaks A2A only — design decision 1), exposing 9003 and running uvicorn on that port; verify `docker build -f orchestrator-agent/Dockerfile .` succeeds
- [x] 1.4 Add a FastAPI app with `GET /healthz` returning `{"status":"ok"}` and `GET /.well-known/agent.json` returning the orchestrator's own card (name, description, url from `AGENT_PUBLIC_URL`, one orchestration skill); verify both endpoints respond when run locally with `uvicorn app:app --port 9003`

## 2. Observability and correlation ID

- [x] 2.1 Port `booking-svc/app/observability.py` into the orchestrator with `SERVICE_NAME = "orchestrator-agent"` (JSON formatter, `correlation_id` contextvar, `correlation_middleware`); verify a request to `/healthz` prints a JSON log line containing `"service":"orchestrator-agent"` and a `correlation_id`
- [x] 2.2 Register the middleware on the app and confirm it adopts an inbound `x-correlation-id` and generates one otherwise; verify `curl -H "x-correlation-id: demo-123" localhost:9003/healthz -i` echoes `x-correlation-id: demo-123` in the response headers, and a call without the header returns a generated UUID

## 3. Agent Card discovery

- [x] 3.1 Read `AGENT_URLS` (comma-separated, default `http://booking-agent:9001,http://notification-agent:9002`) and implement discovery that fetches `/.well-known/agent.json` from each URL and builds a `skill_id -> agent` index **only** from `skills[].id` in the fetched cards, recording per-agent errors instead of raising; verify against both running agents that the index contains `create_booking`, `cancel_booking`, and `send_notification`
- [x] 3.2 Run discovery on FastAPI startup without letting a failure abort startup; verify the orchestrator still starts and serves `/healthz` when started with one agent stopped
- [x] 3.3 Add `GET /agents` returning each configured agent as discovered (name, url, skill ids) or unreachable (url, error); verify with both agents up, then with `docker compose stop notification-agent`, that the output reflects each state
- [x] 3.4 Add a single lazy re-discovery triggered when a submitted step names a skill missing from the index (design decision 2, guards the compose startup race); verify by starting the orchestrator with an agent down, bringing that agent up, and confirming a subsequent plan routes to it without a restart

## 4. Caller authentication and plan validation

- [ ] 4.1 Validate the caller's `access_token` with `JWT_SECRET`/`ALGORITHM` using the same `pyjwt` decode and 401 mapping as `booking-svc/app/auth.py` (missing / malformed / expired / non-int `user_id` → 401), running **before** anything is executed; verify a plan submitted with an expired token returns 401 and produces no outbound call
- [ ] 4.2 Define the `OrchestrateRequest` / `Step` Pydantic models — `{instruction, steps[], access_token}` with `Step{skill, class_id?, booking_id?, message?, reason?}` and **no `user_id` field on Step**; verify the models reject a malformed body with 422
- [ ] 4.3 Reject the whole plan with 422 — executing nothing — when `steps` is empty or any step names a skill absent from the discovered index (after the lazy re-discovery from 3.4); verify a plan naming `change_password` returns 422 and sends no task, and that an empty `steps` list does the same
- [ ] 4.4 Extract `user_id` from the token and override it on every `send_notification` step, ignoring any recipient the plan carried (design decision 5 — the plan is untrusted, model-generated input); verify a plan whose notification step names another user still notifies the token's `user_id`

## 5. Plan execution over A2A

- [ ] 5.1 Implement `POST /orchestrate` wiring together auth (group 4), validation (4.3), and execution; verify it returns the echoed instruction, the executed plan, the correlation id, and a per-step report
- [ ] 5.2 Build each A2A task body per target agent — booking steps send `{skill, class_id|booking_id, access_token}`, the notification step sends `{skill, user_id, message}` — since the two agents' schemas differ; verify both agents accept the bodies rather than returning 422
- [ ] 5.3 Execute steps sequentially with `httpx` at a ~30 s timeout (the agents spawn an MCP subprocess per request), sending `x-correlation-id` on every call; verify the same id appears in `docker compose logs booking-agent notification-agent booking-svc notif-svc`
- [ ] 5.4 Report every step as `{skill, status: "succeeded"|"failed"|"skipped", result|error}`, stopping at the first failure and marking the rest skipped; verify that booking a full class (Zumba, capacity 2) marks `create_booking` failed and `send_notification` skipped with no task sent to the Notification Agent
- [ ] 5.5 Report a step whose target agent is unreachable as failed with the transport error attached, rather than raising a 500; verify by stopping `notification-agent` mid-plan that the response reports the failed step and the orchestrator stays healthy
- [ ] 5.6 Log the originating `instruction` alongside the executed steps under the run's correlation id; verify the instruction text appears in `docker compose logs orchestrator-agent` for a run

## 6. The `orchestrator-mcp` server

- [ ] 6.1 Create `orchestrator-mcp/` with `server.py`, `requirements.txt` (`mcp[cli]==1.29.1`, `requests==2.32.3` — matching the existing agents' pins), and `README.md`; verify `pip install -r orchestrator-mcp/requirements.txt` resolves
- [ ] 6.2 Port the host-side address resolution from `fitflow-mcp/server.py` (Consul lookup for the port, `FITFLOW_SERVICE_HOST`/`localhost` for the host, published-port fallback) and add an `ORCHESTRATOR_URL` default of `http://localhost:9003`; verify resolution returns working URLs with the stack up
- [ ] 6.3 Implement `login(email, password)` against `users-svc`, holding the JWT in process memory exactly as `fitflow-mcp` does; verify it returns a success message and that a later tool call reuses the token
- [ ] 6.4 Implement `list_classes()` reading `booking-svc GET /classes` directly from the host (design decision 3 — keeps the container purely A2A); verify it returns the same ids as `curl localhost:8001/classes`
- [ ] 6.5 Implement `discover_agents()` proxying the orchestrator's `GET /agents`; verify it returns both agents with their discovered skills
- [ ] 6.6 Implement `orchestrate(instruction, steps)` posting to the orchestrator's `POST /orchestrate` with the stored JWT and a freshly generated `x-correlation-id`, returning the per-step report and that correlation id; verify a hand-written two-step plan executes end to end
- [ ] 6.7 Surface an unreachable orchestrator as a tool error naming the failure rather than a fabricated success; verify that with the container stopped, `discover_agents` returns an explicit error
- [ ] 6.8 Verify the tool schemas load: `python -c "import asyncio, server; asyncio.run(...)"` listing `server.mcp.list_tools()` shows all four tools with their parameters, following the pattern in `fitflow-mcp/README.md`

## 7. Compose and configuration

- [ ] 7.1 Add the `orchestrator-agent` service to `docker-compose.yml` (build `context: .` with `dockerfile: orchestrator-agent/Dockerfile`, port `9003:9003`, `depends_on` both agents) passing `AGENT_URLS`, `JWT_SECRET`, `ALGORITHM`, `AGENT_PUBLIC_URL`; verify `docker compose config` renders the service without error
- [ ] 7.2 Add the new keys to `.env.example` with placeholder values; confirm **no API key of any kind** is introduced and `git diff` shows no secret staged
- [ ] 7.3 Bring the whole stack up and confirm the three agent containers run together as the base document's checkpoint requires; verify `docker compose ps` shows `orchestrator-agent`, `booking-agent`, and `notification-agent` running, and `curl localhost:9003/healthz`, `curl localhost:9003/agents`, and `curl localhost:9003/.well-known/agent.json` all respond

## 8. Container-level verification (no Claude Desktop)

- [ ] 8.1 Register and log in a test user via `users-svc`, then `POST localhost:9003/orchestrate` with a hand-written two-step plan (`create_booking` + `send_notification`) and the JWT; verify both steps report `succeeded`, the booking exists in `booking-svc`, and the notification exists in `notif-svc`
- [ ] 8.2 Verify correlation propagation for that run by grepping the one id across `orchestrator-agent`, `booking-agent`, `notification-agent`, `booking-svc`, and `notif-svc` logs
- [ ] 8.3 Verify the cancel path with a hand-written `cancel_booking` plan; confirm the step succeeds and the booking's status changes
- [ ] 8.4 Verify the degraded paths in one pass — expired token → 401, unknown skill → 422, empty steps → 422, notification step naming another user → delivered to the token's `user_id`, one agent stopped → `/agents` reports it unreachable while the other stays routable

## 9. End-to-end verification through Claude Desktop

- [ ] 9.1 Register `orchestrator-mcp` in Claude Desktop's config following the `fitflow-mcp/README.md` pattern (absolute venv python + absolute `server.py` path), with `fitflow` disconnected per design decision 8; verify after a full restart that Claude Desktop lists all four orchestrator tools
- [ ] 9.2 Run the rubric flow: log in through the session, then type "Reserva yoga para el viernes y avísame"; verify the session calls `discover_agents` and `list_classes`, submits a two-step plan, and both steps report `succeeded`
- [ ] 9.3 Confirm the A2A path was actually taken and not shadowed by a direct tool — verify the run's correlation id appears in `docker compose logs booking-agent` and `notification-agent`, which only happens if the delegation went through A2A
- [ ] 9.4 Verify a natural-language cancel instruction in the same session produces a `cancel_booking` step that succeeds
- [ ] 9.5 Verify an out-of-scope instruction ("cámbiame la contraseña") does not fabricate a delegation — the session reports that no discovered skill covers it, or the orchestrator returns 422

## 10. README (Task 4C)

- [ ] 10.1 Add a **Resiliencia** section consolidating the 2 s timeout, 3 retries with exponential backoff + jitter, circuit breaker (3 failures → open 30 s), and outbox pattern, with the `docker compose stop notif-svc` → book → `/resilience/status` → `start` → recovery demo written as runnable steps; verify each command in the section actually runs as written
- [ ] 10.2 Add an **Agent-to-Agent: MCP vs A2A** section using this change's three-layer shape — `orchestrator-mcp` is MCP reaching *down* from Claude Desktop, the orchestrator→agents hop is A2A reaching *across*, and the leaf agents are where A2A turns back into MCP. State plainly that the reasoning happens in the Claude Desktop session and the container coordinates the delegation (design decision 4). Absorb the existing `## Agent-to-Agent` section rather than leaving both; verify the README has exactly one A2A section
- [ ] 10.3 Document the Claude Desktop setup for the A2A demo, including the one-server-at-a-time rule and why it exists (`fitflow-mcp`'s direct tools would shadow the A2A path); verify a reader following the section reaches a working `discover_agents` call
- [ ] 10.4 Update the architecture diagram to show `Orchestrator Agent :9003` as built (remove `<- PENDIENTE`) with the Claude Desktop / MCP layer above it, and add the orchestrator to the service table, the "Documentacion del proyecto" list, the healthcheck command list, and the container count in "Recursos de Docker Compose" (9 → 10); verify no `PENDIENTE` remains and every count and list mentions the orchestrator
- [ ] 10.5 Write `orchestrator-agent/README.md` (endpoints, env vars, discovery, a `curl` plan example) and `orchestrator-mcp/README.md` (tools, Claude Desktop config, the shadowing caveat); verify each README's example reproduces the 8.1 and 9.2 results respectively
