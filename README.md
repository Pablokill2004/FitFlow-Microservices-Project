# FitFlow

Plataforma de reservas de clases fitness construida con microservicios y el
principio **Database per Service**.

## Documentacion del proyecto

- [users-svc](users-svc/README.md): implementacion del servicio de usuarios.
- [booking-svc](booking-svc/README.md): implementacion del servicio de reservas.
- [notif-svc](notif-svc/README.md): implementacion del servicio de notificaciones.
- [fitflow-mcp](fitflow-mcp/README.md): servidor MCP conectado a Claude Desktop.
- [booking-agent](booking-agent/README.md): agente A2A para operaciones de reservas.
- [notification-agent](notification-agent/README.md): agente A2A para envío de notificaciones.
- [orchestrator-agent](orchestrator-agent/README.md): contenedor A2A que descubre los dos agentes anteriores por su Agent Card y coordina la delegación.
- [orchestrator-mcp](orchestrator-mcp/README.md): servidor MCP que conecta Claude Desktop con el Orchestrator para el demo de A2A.

## Estado actual

Se documentan y validan los microservicios implementados hasta ahora:

| Servicio | Puerto | Responsabilidad | Documentacion |
| --- | ---: | --- | --- |
| users-svc | 8003 | Registro, login con JWT y perfil de usuario | [README](users-svc/README.md) |
| booking-svc | 8001 | Reservas de clases y catalogo de clases disponibles | [README](booking-svc/README.md) |
| notif-svc | 8002 | Envio de notificaciones (log) e historial por usuario | [README](notif-svc/README.md) |
| booking-agent | 9001 | Agente A2A: crear/cancelar reserva | [README](booking-agent/README.md) |
| notification-agent | 9002 | Agente A2A: enviar notificacion | [README](notification-agent/README.md) |
| orchestrator-agent | 9003 | Descubre Agent Cards y coordina delegaciones A2A | [README](orchestrator-agent/README.md) |

Los 3 microservicios del proyecto ya corren con `docker compose up --build`.
Cada servicio usa PostgreSQL dedicado (`users-db`, `booking-db`, `notif-db`)
y se levanta mediante el `docker-compose.yml` de la raiz, que tambien incluye
un contenedor de **Consul** (modo dev) accesible en `http://localhost:8500`.

Avance en service discovery:

- `users-svc`, `booking-svc` y `notif-svc` se registran automaticamente en
  Consul al iniciar (cada uno con sus propias variables `CONSUL_SERVICE_*`,
  `BOOKING_CONSUL_*` y `NOTIF_CONSUL_*` para no chocar entre si).
- Los tres publican health check HTTP con intervalo de 10 segundos y
  desregistro automatico tras 30 segundos en estado critico, y se
  desregistran al apagarse.
- `booking-svc` consulta a Consul la direccion de `notif-svc` antes de cada
  notificacion. No tiene la URL escrita en el codigo.

Avance en resiliencia (booking-svc -> notif-svc): timeout, reintentos,
circuit breaker y outbox pattern. Ver la sección [Resiliencia](#resiliencia)
más abajo para los mecanismos, los valores reales y la demo paso a paso.

Avance en observabilidad y seguridad:

- Los cinco contenedores de la ruta de orquestación (`orchestrator-agent`,
  `booking-agent`, `notification-agent`, `booking-svc` y `notif-svc`)
  escriben logs JSON con `correlation_id`, `service`, `event`, `level` y
  `timestamp`, y un mismo `x-correlation-id` los atraviesa a los cinco en una
  sola corrida (ver [Agent-to-Agent: MCP vs A2A](#agent-to-agent-mcp-vs-a2a)).
  `booking-svc` agrega además el `user_id` del JWT a sus logs.
- `booking-svc` valida el JWT que emite `users-svc` y responde 401 si falta,
  es invalido o expiro. `JWT_SECRET` y los passwords de BD viven en `.env`.

El servidor **MCP** (`fitflow-mcp`) ya esta implementado con 5 herramientas
(`login`, `get_available_classes`, `create_booking`, `cancel_booking`,
`send_notification`) y se conecta a Claude Desktop como proceso local; el
mismo servidor tambien corre embebido dentro de `booking-agent` y
`notification-agent` para la capa A2A. Ver
[fitflow-mcp/README.md](fitflow-mcp/README.md) para el detalle. El servidor
**`orchestrator-mcp`** sigue el mismo patrón para conectar Claude Desktop con
el contenedor `orchestrator-agent`; ver la sección
[Agent-to-Agent: MCP vs A2A](#agent-to-agent-mcp-vs-a2a).

## Resiliencia

La llamada `booking-svc -> notif-svc` (después de crear o cancelar una
reserva) está protegida por tres mecanismos, implementados en
[`booking-svc/app/resilience.py`](booking-svc/app/resilience.py). Los
nombres de variable y los valores por defecto son los que usa ese archivo:

| Mecanismo | Variable de entorno | Valor por defecto |
| --- | --- | --- |
| Timeout por intento | `NOTIF_TIMEOUT_S` | 2 segundos |
| Reintentos con backoff exponencial + jitter | `NOTIF_MAX_RETRIES` | 3 (4 intentos en total), esperas de 0.5 s, 1 s y 2 s más un jitter aleatorio de 0 a 0.3 s (librería `tenacity`) |
| Circuit breaker | `CB_FAIL_MAX` / `CB_RESET_TIMEOUT_S` | Abre tras 3 fallos seguidos (cada reserva fallida cuenta como **un** fallo, aunque internamente reintente varias veces) y permanece abierto 30 segundos antes de pasar a half-open (librería `pybreaker`) |
| Outbox pattern | `OUTBOX_FLUSH_INTERVAL_S` | Un hilo en segundo plano (`OutboxWorker`) reintenta las entradas `pending` de la tabla `notification_outbox` cada 10 segundos |

Mientras el circuito está abierto no se llama a `notif-svc`: la reserva
igual se crea y responde `201`, con `notification_status: "pending"`, y la
notificación queda en el outbox de `booking-db` hasta que un reintento (de
una nueva reserva o del hilo en segundo plano) tenga éxito. `GET
http://localhost:8001/resilience/status` expone el estado del circuit
breaker (`closed`/`open`/`half-open`) y el conteo del outbox.

### Demo: caída y recuperación de notif-svc

Con el stack levantado (`docker compose up --build -d`), estos comandos son
ejecutables tal cual — reproducen los pasos 3 a 5 del video:

```bash
# 1) Un usuario y una reserva con notif-svc arriba (responde "sent")
# La password se toma del entorno: no hay credenciales escritas en el repositorio.
export FITFLOW_DEMO_PASSWORD='elegi-una-password'

curl -s -X POST http://localhost:8003/users/register \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"resiliencia-demo@fitflow.com\",\"password\":\"$FITFLOW_DEMO_PASSWORD\",\"full_name\":\"Resiliencia Demo\"}"

TOKEN=$(curl -s -X POST http://localhost:8003/users/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"resiliencia-demo@fitflow.com\",\"password\":\"$FITFLOW_DEMO_PASSWORD\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s -X POST http://localhost:8001/bookings \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"class_id":1}'
# -> "notification_status":"sent"

# 2) Derribar notif-svc
docker compose stop notif-svc

# 3) Tres reservas más (class_id 2, 3 y 4). Cada una tarda unos segundos
#    (reintentos) y responde 201 con "notification_status":"pending".
curl -s -X POST http://localhost:8001/bookings \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"class_id":2}'
curl -s -X POST http://localhost:8001/bookings \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"class_id":3}'
curl -s -X POST http://localhost:8001/bookings \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"class_id":4}'

# 4) El circuito ya está abierto: "state":"open", "fail_counter":3, y el
#    outbox muestra las notificaciones pendientes.
curl -s http://localhost:8001/resilience/status
curl -s "http://localhost:8001/resilience/outbox?status=pending"

# 5) Levantar notif-svc y esperar el reset del circuito (hasta 30 s) más el
#    siguiente ciclo del outbox worker (hasta 10 s más).
docker compose start notif-svc
sleep 35
curl -s http://localhost:8001/resilience/status
# -> "state":"closed", "outbox":{"pending":0,...}
```

Verificado en este stack: tras el paso 3 `resilience/status` devolvió
`{"state":"open","fail_counter":3,...}` y el outbox tenía 3 pendientes; tras
el paso 5 volvió a `{"state":"closed",...}` con `"pending":0` y las
notificaciones que estaban pendientes aparecieron con `status:"sent"` en
`GET http://localhost:8002/notifications/<user_id>`. El detalle completo
(incluida una cuarta reserva mientras el circuito sigue abierto, que
responde en milisegundos porque no llega a llamar a `notif-svc`) está en
[booking-svc/README.md](booking-svc/README.md).

## Cómo correr el proyecto

Requisito: Docker Desktop con Docker Compose v2.

```bash
cp .env.example .env   # completar los valores
docker compose up --build -d
curl http://localhost:8003/healthz
curl http://localhost:8003/readyz
curl http://localhost:8002/healthz
curl http://localhost:8002/readyz
curl http://localhost:8001/healthz
curl http://localhost:8001/readyz
curl http://localhost:9001/healthz
curl http://localhost:9002/healthz
curl http://localhost:9003/healthz
```

La UI de Consul queda disponible en `http://localhost:8500`. El Booking Agent
publica su Agent Card en `http://localhost:9001/.well-known/agent.json`, el
Notification Agent en `http://localhost:9002/.well-known/agent.json`, y el
Orchestrator Agent en `http://localhost:9003/.well-known/agent.json` (y su
resultado de descubrimiento en `http://localhost:9003/agents`).

### Recursos de Docker Compose

`docker compose up --build -d` construye o actualiza las imagenes de
`users-svc`, `booking-svc`, `notif-svc`, `booking-agent`,
`notification-agent` y `orchestrator-agent`, y ejecuta 10 contenedores:

- `users-svc`, `booking-svc`, `notif-svc`, `booking-agent`,
  `notification-agent` y `orchestrator-agent`.
- `users-db`, `booking-db` y `notif-db` (PostgreSQL independiente).
- `consul` para el registro de servicios.

`orchestrator-agent` no tiene base de datos propia (es un coordinador sin
estado) ni corre un servidor MCP embebido: es el único de los tres agentes
que habla A2A exclusivamente.

También crea o reutiliza los volúmenes `users_db_data`, `booking_db_data` y
`notif_db_data`. Los volúmenes conservan los datos aunque se detengan los
contenedores.

Al terminar el trabajo, detener los contenedores con:

```bash
docker compose down
```

Este comando elimina los contenedores y la red del proyecto, pero conserva las
imágenes y los datos de las bases. Para volver a trabajar, ejecutar de nuevo
`docker compose up -d`.

Solo si se desea borrar también los datos persistentes:

```bash
docker compose down -v
```

`down -v` elimina los tres volúmenes y toda la información almacenada en las
bases de datos. Para revisar el estado actual, usar `docker compose ps`.

## Arquitectura

```text
+--------------------------+
| Claude Desktop (host)    |   razona en lenguaje natural, aqui vive el
| sesion del usuario       |   unico paso "de razonamiento" del sistema
+------------+-------------+
             | MCP (stdio) -- un subproceso por llamada, hacia ABAJO
             v
+--------------------------+
| orchestrator-mcp (host)  |   login / list_classes / discover_agents /
| (proceso local)          |   orchestrate
+------------+-------------+
             | HTTP (login, list_classes -> booking-svc directo;
             | discover_agents/orchestrate -> orchestrator-agent)
             v
       +----------------------+
       | Orchestrator Agent   |   solo A2A: sin cliente MCP propio
       | :9003                |
       +----------+-----------+
            | A2A / Agent Cards (HTTP, hacia LOS LADOS)
       +-----------+-----------+
       |                       |
+------v---------+   +---------v----------+
| Booking Agent   |   | Notification Agent |
| :9001           |   | :9002               |
+------+---------+   +---------+----------+
       | MCP (stdio)           | MCP (stdio)
+------v---------+   +---------v----------+
| booking-svc     |   | notif-svc          |
| :8001           |   | :8002              |
+------+---------+   +--------------------+
       | HTTP + outbox         ^
       +-----------------------+

 users-svc :8003 <----- JWT ---- booking-svc
   ^                    ^                ^
   |                    |                |
   +------------- Consul :8500 ----------+
```

Los microservicios mantienen una base de datos independiente. Consul permite
descubrir servicios sanos. MCP conecta un cliente con herramientas locales
(dos puntos distintos: Claude Desktop -> `orchestrator-mcp`/`fitflow-mcp`, y
cada agente -> su `fitflow-mcp` embebido); A2A permite que el Orchestrator
descubra y delegue tareas a los agentes especializados mediante Agent Cards.
Ver [Agent-to-Agent: MCP vs A2A](#agent-to-agent-mcp-vs-a2a) para el detalle
de esta doble capa.

## Agent-to-Agent: MCP vs A2A

FitFlow usa dos protocolos de agentes distintos, y este proyecto termina
teniendo la forma exacta para que el contraste se vea con claridad porque
aparecen los dos, en tres capas, en la misma corrida:

- **MCP es el eje vertical**: un cliente que baja a herramientas locales por
  stdio, lanzando un subproceso por llamada. Pasa en dos lugares distintos:
  Claude Desktop -> `orchestrator-mcp` (o -> `fitflow-mcp`), y cada agente
  hoja -> su propio `fitflow-mcp` embebido.
- **A2A es el eje horizontal**: un par que llega a otro agente por HTTP,
  descubierto por su Agent Card (`/.well-known/agent.json`), nunca por una
  tabla de ruteo fija.

| Capa | Protocolo | Dirección | Quién habla con quién |
| --- | --- | --- | --- |
| Claude Desktop -> `orchestrator-mcp` (host) | MCP (stdio) | vertical, hacia abajo | la sesión de Claude Desktop llama a las 4 herramientas del servidor local |
| `orchestrator-agent` -> Booking/Notification Agent | A2A (HTTP) | horizontal, hacia el costado | el contenedor descubre cada Agent Card y delega `POST /a2a/tasks` |
| Booking/Notification Agent -> su `fitflow-mcp` embebido | MCP (stdio) | vertical, hacia abajo | cada agente hoja lanza el mismo `fitflow-mcp` como subproceso para ejecutar la operación real |

El contenedor `orchestrator-agent` **no tiene cliente MCP propio** — habla
únicamente A2A (design decision 1: su Dockerfile ni siquiera copia
`fitflow-mcp/server.py`). Son los agentes hoja (Booking, Notification) donde
A2A vuelve a convertirse en MCP.

**Quién razona y quién coordina.** Vale decirlo sin rodeos (design decision
4): el razonamiento en lenguaje natural — leer "Reserva la clase de yoga y
avísame", resolver a qué `class_id` corresponde, decidir qué skills usar —
ocurre en la sesión de Claude Desktop, no dentro de ningún contenedor. El
contenedor `orchestrator-agent` recibe la instrucción original (la registra
y la devuelve en la respuesta, así el audit trail queda instrucción -> plan
-> resultados) y el plan ya armado (`steps[]`), valida ambos, y coordina la
delegación en secuencia sobre A2A. No hay ninguna llamada a un modelo ni una
API key dentro del stack: el "razonamiento" completo vive del lado del host,
en la sesión del usuario.

### La capa A2A: Agent Cards y tareas

Cada agente publica sus capacidades en `/.well-known/agent.json` y acepta
tareas en `POST /a2a/tasks`, delegando la operación real a su microservicio
vía MCP (nunca accede directamente a una base de datos):

- **Booking Agent** (`create_booking`, `cancel_booking`): recibe `class_id` o
  `booking_id` y el JWT del usuario en `access_token`. Delega a `booking-svc`.
- **Notification Agent** (`send_notification`): recibe `user_id` y `message`.
  No requiere `access_token` porque `notif-svc` no exige JWT. Delega a
  `notif-svc`.

El Orchestrator descubre cada Agent Card al arrancar (con una segunda
oportunidad de descubrimiento si un plan nombra un skill que todavía no
apareció, para cubrir la carrera de arranque de `docker compose up`) y
construye el índice `skill -> agente` únicamente a partir de esas cards —
`AGENT_URLS` le dice dónde mirar, nunca quién tiene cada skill. Envía tareas
como estas:

```json
{"skill":"create_booking","class_id":1,"access_token":"<JWT>"}
```

```json
{"skill":"send_notification","user_id":1,"message":"Tu reserva de yoga fue confirmada"}
```

Para el flujo "Reserva yoga y avísame", el Orchestrator delega primero
`create_booking` al Booking Agent y luego `send_notification` al
Notification Agent, propagando el mismo `x-correlation-id` a ambas llamadas
— y ese mismo id llega hasta `booking-svc` y `notif-svc`, así que una sola
corrida se puede seguir en los cinco logs
(`docker compose logs orchestrator-agent booking-agent notification-agent
booking-svc notif-svc | grep <id>`).

El caller se autentica ante el Orchestrator con un JWT en el body de
`POST /orchestrate` (`access_token`, mismo `JWT_SECRET`/`ALGORITHM` que
`users-svc`/`booking-svc`); el `user_id` sale siempre de ese token, nunca del
plan, así un plan generado por un modelo no puede redirigir una notificación
hacia otro usuario. Ver [orchestrator-agent/README.md](orchestrator-agent/README.md)
para los endpoints, la validación y un ejemplo completo con `curl`.

### Conectar Claude Desktop para la demo A2A

El demo de A2A ("Reserva la clase de yoga y avísame" resuelto por la sesión
de Claude Desktop) necesita `orchestrator-mcp` registrado en Claude Desktop.
El bloque de configuración completo (rutas absolutas del venv, JSON exacto
para Windows/macOS/Linux) está en
[orchestrator-mcp/README.md](orchestrator-mcp/README.md#conectar-a-claude-desktop)
— no se repite aquí para no tener dos copias divergentes.

**Regla crítica: conectar un solo servidor MCP a la vez.** `fitflow-mcp`
expone `create_booking`, `cancel_booking` y `send_notification` como
herramientas **directas** de una sola llamada. Si `fitflow` y
`fitflow-orchestrator` están conectados al mismo tiempo, la sesión ve un
camino directo (una llamada) y un camino A2A (descubrir, planificar, delegar
en varios pasos) para el mismo pedido, y con frecuencia toma el directo —
vaciando en silencio la demo que existe para mostrar A2A. Por eso:

- Task 2B (MCP directo): conectar solo `fitflow`.
- Task 5 (A2A): conectar solo `fitflow-orchestrator`, con `fitflow`
  desconectado, y reiniciar Claude Desktop por completo entre una demo y la
  otra.

**Verificación de que la demo realmente pasó por A2A:** el `correlation_id`
que devuelve la herramienta `orchestrate` debe aparecer en
`docker compose logs booking-agent` (y `notification-agent`). Si no
aparece, la sesión tomó el camino directo de `fitflow-mcp` en vez de
delegar — la comprobación de arriba ("un id atraviesa los cinco
contenedores") es la misma prueba, aplicada al flujo iniciado desde Claude
Desktop en vez de por `curl`.

Para probar el flujo completo de cada servicio, consulta su README:
[users-svc](users-svc/README.md) · [booking-svc](booking-svc/README.md) ·
[notif-svc](notif-svc/README.md) · [fitflow-mcp](fitflow-mcp/README.md) ·
[booking-agent](booking-agent/README.md) ·
[notification-agent](notification-agent/README.md) ·
[orchestrator-agent](orchestrator-agent/README.md) ·
[orchestrator-mcp](orchestrator-mcp/README.md).