# orchestrator-agent

Contenedor A2A de FitFlow en `:9003`. Descubre a los otros dos agentes
(`booking-agent`, `notification-agent`) leyendo sus Agent Cards, valida el
plan que recibe y lo ejecuta en secuencia sobre `POST /a2a/tasks`, reportando
el resultado de cada paso.

No tiene cliente MCP propio ni base de datos: es un coordinador sin estado
que habla únicamente A2A (HTTP + Agent Cards). El razonamiento en lenguaje
natural — decidir qué reservar, a partir de qué instrucción — no ocurre
aquí, ocurre en la sesión de Claude Desktop que arma el plan y lo somete
mediante [`orchestrator-mcp`](../orchestrator-mcp/README.md). Ver la sección
[Agent-to-Agent: MCP vs A2A](../README.md#agent-to-agent-mcp-vs-a2a) del
README raíz para el panorama completo, y
`openspec/changes/add-orchestrator-agent/design.md` (decisiones 1 a 6) para
el razonamiento de diseño detrás de este contenedor.

## Endpoints

| Endpoint | Método | Qué hace |
| --- | --- | --- |
| `/healthz` | GET | `{"status":"ok"}`. Responde 200 aunque un agente descubierto esté caído. |
| `/.well-known/agent.json` | GET | Agent Card propia: `name`, `description`, `url` (desde `AGENT_PUBLIC_URL`) y un único skill, `orchestrate`. |
| `/agents` | GET | Sondea a `booking-agent` y `notification-agent` en el momento de la llamada y reporta cada uno como `discovered` (con `name`, `routing_url` y `skill_ids`) o `unreachable` (con `error`). No toca el índice de ruteo que usa `/orchestrate` — es solo diagnóstico. |
| `/orchestrate` | POST | Autentica el `access_token`, valida el plan y lo ejecuta en orden sobre A2A. Ver más abajo. |

## Variables de entorno

| Variable | Default | Uso |
| --- | --- | --- |
| `AGENT_URLS` | `http://booking-agent:9001,http://notification-agent:9002` | Lista separada por comas de URLs base donde buscar `/.well-known/agent.json`. Le dice al orchestrator **dónde mirar**, nunca quién tiene cada skill — eso lo decide la card que cada URL devuelve. |
| `AGENT_PUBLIC_URL` | `http://orchestrator-agent:9003` | URL que el orchestrator publica en su propia Agent Card (campo `url`). |
| `JWT_SECRET` | _(sin default; el proceso no arranca sin ella)_ | Clave compartida con `users-svc`/`booking-svc` para validar el `access_token` de cada plan. |
| `ALGORITHM` | `HS256` | Algoritmo de firma del JWT, igual que el resto de los servicios. |

`JWT_SECRET` y `ALGORITHM` deben ser exactamente los mismos valores que usan
`users-svc` y `booking-svc` (comparten `.env`); si no coinciden, todo
`access_token` válido para el resto del sistema será rechazado aquí con 401.

## Cómo funciona el descubrimiento

Al arrancar (evento `startup` de FastAPI, sin abortar el arranque si algún
agente todavía no responde), el orchestrator hace `GET
/.well-known/agent.json` a cada URL de `AGENT_URLS` en paralelo y construye
`skill_id -> AgentRef(name, url, skill_ids)`. Dos reglas importantes:

- **El índice se construye únicamente con lo que trae la card fetcheada.**
  `AGENT_URLS` es solo la lista de direcciones a sondear; la URL de ruteo
  real (`routing_url` en `/agents`) es la que la propia card publica en su
  campo `url`, no la URL de configuración. No existe en este código ninguna
  tabla fija `skill -> agente`.
- **Re-descubrimiento perezoso.** Si un plan nombra un skill que no está en
  el índice, el orchestrator corre el descubrimiento una vez más antes de
  rechazar el plan. Esto cubre la carrera de arranque de `docker compose up`
  (los tres contenedores arrancan a la vez, y `depends_on: service_started`
  no espera a que la app de adentro esté escuchando) y también el caso de un
  agente que se reinicia después de que el orchestrator ya arrancó — sin
  necesidad de reiniciar el orchestrator.

## El plan es entrada no confiable

`POST /orchestrate` recibe `{instruction, steps[], access_token}`. El
`access_token` se valida **antes** de tocar `steps` o llamar a cualquier
agente — un token ausente, malformado, expirado o con un `user_id` que no
sea entero responde 401 sin ejecutar nada. El `Step` de cada paso
(`skill`, `class_id?`, `booking_id?`, `message?`, `reason?`) **no tiene
campo `user_id`**: aunque el JSON entrante traiga uno, Pydantic lo descarta
porque el modelo no lo declara. El `user_id` que de verdad se usa —
incluyendo el destinatario de `send_notification` — sale siempre del JWT
decodificado, nunca del plan.

Esto es deliberado: el plan es generado por un modelo a partir de texto que
escribió un usuario (la instrucción en lenguaje natural, en la sesión de
Claude Desktop), así que tratarlo como entrada confiable dejaría que un
texto de instrucción manipulado redirigiera una notificación hacia otro
`user_id`. El orchestrator es el límite de confianza: valida cada `skill`
contra el índice descubierto (rechaza con 422 el plan completo —sin ejecutar
nada— si viene vacío o si algún step nombra un skill inexistente, incluso
después del re-descubrimiento perezoso) y sobrescribe `user_id` con el que
vino en el token en cada paso `send_notification`.

## Ejecución del plan

Los pasos se ejecutan en el orden del plan, secuencialmente, sobre
`POST /a2a/tasks` del agente que el índice indica para cada `skill`. El
cuerpo de la tarea se arma distinto según el agente destino, porque sus
contratos difieren: Booking Agent espera
`{skill, class_id|booking_id, access_token}`; Notification Agent espera
`{skill, user_id, message}`. En el primer paso que falla (status >= 400,
error de transporte, o respuesta no-JSON), el resto del plan se marca
`skipped` y no se llama a ningún agente más — no hay compensación ni
rollback de lo que ya se ejecutó. Un agente inalcanzable se reporta como
paso `failed` con el error de transporte adjunto, nunca como un 500 del
propio orchestrator.

Cada llamada A2A lleva el `x-correlation-id` de la petición original (lo
adopta si vino en el header, o genera un UUID si no), así que una sola
corrida se puede seguir en los logs de los cinco contenedores
(`orchestrator-agent`, `booking-agent`, `notification-agent`, `booking-svc`,
`notif-svc`).

## Ejemplo: plan de dos pasos con `curl`

Con el stack levantado (`docker compose up --build -d` desde la raíz), esto
reproduce un plan `create_booking` + `send_notification` de punta a punta:

```bash
# 1) Registrar un usuario y obtener su JWT
# La password se toma del entorno: no hay credenciales escritas en el repositorio.
export FITFLOW_DEMO_PASSWORD='elegi-una-password'

curl -s -X POST http://localhost:8003/users/register \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"orch-demo@fitflow.com\",\"password\":\"$FITFLOW_DEMO_PASSWORD\",\"full_name\":\"Orchestrator Demo\"}"

TOKEN=$(curl -s -X POST http://localhost:8003/users/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"orch-demo@fitflow.com\",\"password\":\"$FITFLOW_DEMO_PASSWORD\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# 2) Ver qué clases existen de verdad (los ids cambian en cada
#    docker compose up, porque se siembran relativos a la hora de arranque)
curl -s http://localhost:8001/classes

# 3) Someter el plan (ajustar class_id al que muestre el paso anterior)
curl -s -X POST http://localhost:9003/orchestrate \
  -H "Content-Type: application/json" \
  -H "x-correlation-id: orch-demo-001" \
  -d '{
        "instruction": "Reserva la clase de yoga y avisame",
        "steps": [
          {"skill": "create_booking", "class_id": 2},
          {"skill": "send_notification", "message": "Tu reserva fue confirmada"}
        ],
        "access_token": "'"$TOKEN"'"
      }'

# 4) Confirmar que el mismo id atraviesa los cinco contenedores
docker compose logs --no-color orchestrator-agent booking-agent notification-agent booking-svc notif-svc \
  | grep orch-demo-001
```

Ejecutado contra este stack, el paso 3 devolvió los dos pasos en
`"status":"succeeded"` junto con el `correlation_id`, y el paso 4 mostró
`orch-demo-001` en los cinco contenedores (eventos `request_started` /
`orchestration_started` / `mcp_call_*` / `booking_created` /
`notification_sent`, entre otros).

## Verificación rápida

```bash
curl http://localhost:9003/healthz
curl http://localhost:9003/.well-known/agent.json
curl http://localhost:9003/agents
```

Casos de error demostrables:

- `POST /orchestrate` con un `access_token` expirado o inválido responde 401
  y no llama a ningún agente.
- Un plan con `steps` vacío, o con un `skill` que ningún agente publica
  (por ejemplo `change_password`), responde 422 sin ejecutar nada.
- Un plan cuyo `send_notification` no trae `user_id` en absoluto (el modelo
  ni siquiera lo acepta) siempre notifica al `user_id` del token.
- Con `notification-agent` detenido (`docker compose stop
  notification-agent`), `GET /agents` reporta ese agente como `unreachable`
  mientras `booking-agent` sigue `discovered`, y un plan que use
  `create_booking` sigue funcionando.

## Ver también

- [`orchestrator-mcp/README.md`](../orchestrator-mcp/README.md): el servidor
  MCP que corre en el host y le da a Claude Desktop las cuatro herramientas
  (`login`, `list_classes`, `discover_agents`, `orchestrate`) para armar el
  plan que este contenedor ejecuta.
- [`booking-agent/README.md`](../booking-agent/README.md) y
  [`notification-agent/README.md`](../notification-agent/README.md): los dos
  agentes que este contenedor descubre y delega.
- [README raíz](../README.md#agent-to-agent-mcp-vs-a2a): la sección
  "Agent-to-Agent: MCP vs A2A" con el panorama completo de las tres capas.
