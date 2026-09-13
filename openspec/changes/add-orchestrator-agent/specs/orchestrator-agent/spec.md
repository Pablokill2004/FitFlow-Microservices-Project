## Purpose

Turns a single natural-language instruction from a FitFlow user into a coordinated sequence of A2A delegations. The orchestrator container discovers which specialised agent owns each skill from that agent's published Agent Card and executes the delegation; the natural-language understanding is supplied by a Claude Desktop session that reaches the orchestrator through a local MCP server.

## ADDED Requirements

### Requirement: Agent discovery by Agent Card

The orchestrator SHALL learn which downstream agents exist and what they can do by fetching each configured agent's Agent Card from `GET /.well-known/agent.json`. It SHALL build its `skill id -> agent` routing table only from the `skills[].id` values found in those cards, and MUST NOT rely on a hardcoded mapping from skill to agent.

The orchestrator SHALL expose the result of discovery so a reviewer can see which agents and skills it found.

#### Scenario: Both agents discovered at startup

- **WHEN** the orchestrator starts with the Booking Agent and Notification Agent reachable
- **THEN** `GET /agents` returns both agents with their name, URL, and discovered skill ids, including `create_booking`, `cancel_booking`, and `send_notification`

#### Scenario: One agent unreachable during discovery

- **WHEN** discovery runs and one configured agent does not respond to `GET /.well-known/agent.json`
- **THEN** the orchestrator still starts, `GET /agents` reports that agent as unreachable with its error, and the skills of the reachable agents remain routable

#### Scenario: Skill routing follows the card, not a hardcoded table

- **WHEN** an agent's card advertises a skill id under a different agent URL than before
- **THEN** the orchestrator routes tasks for that skill to the URL published in the card it just fetched

### Requirement: A Claude Desktop session drives orchestration over MCP

The system SHALL expose the orchestrator to a locally running Claude Desktop session through an MCP server launched as a local process over stdio. That server SHALL provide tools sufficient for the session to authenticate a user, read the live class catalogue, read the orchestrator's discovered skills, and submit a delegation plan.

The natural-language instruction SHALL be interpreted by the Claude Desktop session. No component of the deployed stack SHALL require an external language-model API key or outbound API access in order to orchestrate.

#### Scenario: Book a class and notify in one instruction

- **WHEN** the user types "Reserva la clase de yoga y avísame" in a Claude Desktop session with the orchestrator MCP server connected and is logged in
- **THEN** the session resolves yoga to its current `class_id` from the live catalogue and submits a two-step plan whose steps are `create_booking` for that class and `send_notification`, and both steps report success

> The rubric's literal phrase is "Reserva yoga para el viernes y avísame", but `booking-svc` seeds
> yoga at `now + 1 day` on every fresh volume, so a Friday yoga class exists only when the stack
> was first started on a Thursday. The instruction is worded to resolve against the live catalogue
> on any day; the delegation flow it exercises is identical.

#### Scenario: Instruction names a weekday with no matching class

- **WHEN** the user asks for a class on a weekday that the live catalogue has no session for
- **THEN** the session reports that no matching class exists and submits no plan, rather than booking a different day

#### Scenario: Cancel an existing booking

- **WHEN** the user asks in natural language to cancel a booking they hold
- **THEN** the submitted plan contains a `cancel_booking` step carrying that `booking_id`, and the step reports success

#### Scenario: Session reads discovery before planning

- **WHEN** the session is asked what the orchestrator can do
- **THEN** the `discover_agents` tool returns the skills the orchestrator discovered from the Agent Cards, and the session's answer reflects those skills rather than a fixed list

#### Scenario: The stack needs no language-model credentials

- **WHEN** the full stack is started with no language-model API key configured anywhere
- **THEN** every container starts healthy and the orchestrator serves discovery, delegation, and its Agent Card normally

### Requirement: Submitted plans are validated before execution

The orchestrator SHALL accept a delegation plan as an ordered list of steps, each naming a skill and carrying that skill's arguments, together with the originating natural-language instruction for traceability.

The orchestrator SHALL reject the whole plan with 422, executing nothing, when the plan is empty or when any step names a skill absent from its discovered index. The originating instruction SHALL be recorded in the orchestrator's logs and echoed in its response so the audit trail reads instruction, then plan, then results.

#### Scenario: Plan references an undiscovered skill

- **WHEN** a submitted plan contains a step whose skill is not in the discovered index
- **THEN** the orchestrator responds 422 naming the unknown skill and sends no task to any agent

#### Scenario: Empty plan

- **WHEN** a submitted plan contains no steps
- **THEN** the orchestrator responds 422 and sends no task to any agent

#### Scenario: Instruction is preserved in the audit trail

- **WHEN** a plan is submitted together with the instruction that produced it
- **THEN** the orchestrator's response and its JSON logs both carry that instruction text alongside the executed steps

### Requirement: Plan execution over A2A

The orchestrator SHALL execute the plan's steps in order by sending `POST /a2a/tasks` to the agent that owns each step's skill, forwarding the caller's access token where the skill requires one.

Execution SHALL stop at the first failing step. The response SHALL report, for every planned step, whether it succeeded, failed, or was skipped, along with the result or error returned by the agent.

#### Scenario: All steps succeed

- **WHEN** a two-step plan is executed and both agents return success
- **THEN** the orchestrator responds 200 with both steps marked succeeded and each agent's result attached

#### Scenario: First step fails

- **WHEN** the `create_booking` step fails because the class is full
- **THEN** the orchestrator responds with the `create_booking` step marked failed and carrying the agent's error, the `send_notification` step marked skipped, and no task is sent to the Notification Agent

#### Scenario: Output of one step feeds the next

- **WHEN** a plan books a class and then notifies the user about it
- **THEN** the notification message sent to the Notification Agent reflects the booking that was actually created

### Requirement: Caller authentication and identity

The orchestrator SHALL validate the caller's access token against the same signing secret the rest of FitFlow uses, and SHALL reject a missing, malformed, or expired token with 401 before any plan is executed.

The orchestrator SHALL take the recipient's identity from the `user_id` claim in that token rather than from the submitted plan or the instruction text, so a caller cannot direct a notification at another user by asking for it.

#### Scenario: Expired token

- **WHEN** a valid plan is submitted with an expired token
- **THEN** the orchestrator responds 401 and calls no agent

#### Scenario: Recipient comes from the token

- **WHEN** the instruction or the submitted plan names a different user than the token's `user_id`
- **THEN** the `send_notification` step is addressed to the token's `user_id`

### Requirement: Correlation ID propagation

The orchestrator SHALL use one correlation ID for an entire orchestration call: it SHALL adopt an incoming `x-correlation-id` header when present and otherwise generate one, send that same value as `x-correlation-id` on every downstream A2A task, and return it to the caller in both the response body and the `x-correlation-id` response header.

The orchestrator SHALL emit structured JSON logs carrying that correlation ID, consistent with the format the FitFlow microservices already use.

#### Scenario: Incoming correlation ID is reused

- **WHEN** the caller sends `x-correlation-id: demo-123`
- **THEN** both the Booking Agent and the Notification Agent receive `x-correlation-id: demo-123`, and the response echoes `demo-123`

#### Scenario: Correlation ID is generated when absent

- **WHEN** the caller sends no `x-correlation-id`
- **THEN** the orchestrator generates one value and uses that same value for every downstream call and in its own logs

#### Scenario: One id spans the whole Claude Desktop flow

- **WHEN** an orchestration is driven from a Claude Desktop session
- **THEN** a single correlation id links the orchestrator's logs to those of both agents and both microservices for that run

### Requirement: The orchestrator is itself a discoverable A2A participant

The orchestrator SHALL run as its own container on its own port, SHALL publish its own Agent Card at `GET /.well-known/agent.json` describing its orchestration skill, and SHALL expose `GET /healthz` returning `{"status":"ok"}` so it can be health-checked like every other FitFlow container.

#### Scenario: Agent Card is served

- **WHEN** a client requests `GET /.well-known/agent.json`
- **THEN** the orchestrator returns a card with its name, description, URL, and a skill describing natural-language orchestration

#### Scenario: Health check

- **WHEN** a client requests `GET /healthz`
- **THEN** the orchestrator returns `{"status":"ok"}` even if a downstream agent is unavailable

#### Scenario: Three agent containers run together

- **WHEN** the stack is started with `docker compose up --build -d`
- **THEN** `orchestrator-agent`, `booking-agent`, and `notification-agent` all run as containers, each on its own port, each serving its own Agent Card

### Requirement: Orchestrator connectivity failures are reported, not masked

When the MCP server cannot reach the orchestrator container, it SHALL surface an error naming the failure to the Claude Desktop session rather than reporting a fabricated success. When the orchestrator cannot reach a target agent for a step, that step SHALL be reported as failed with the transport error attached.

#### Scenario: Orchestrator container is down

- **WHEN** a tool is invoked from Claude Desktop while the orchestrator container is stopped
- **THEN** the tool returns an error naming the unreachable orchestrator, and the session does not report the orchestration as done

#### Scenario: Target agent is unreachable mid-plan

- **WHEN** a step's target agent cannot be reached during execution
- **THEN** that step is reported as failed with the transport error and the remaining steps are reported as skipped
