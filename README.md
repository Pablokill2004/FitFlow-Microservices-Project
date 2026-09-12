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

## Estado actual

Se documentan y validan los microservicios implementados hasta ahora:

| Servicio | Puerto | Responsabilidad | Documentacion |
| --- | ---: | --- | --- |
| users-svc | 8003 | Registro, login con JWT y perfil de usuario | [README](users-svc/README.md) |
| booking-svc | 8001 | Reservas de clases y catalogo de clases disponibles | [README](booking-svc/README.md) |
| notif-svc | 8002 | Envio de notificaciones (log) e historial por usuario | [README](notif-svc/README.md) |
| booking-agent | 9001 | Agente A2A: crear/cancelar reserva | [README](booking-agent/README.md) |
| notification-agent | 9002 | Agente A2A: enviar notificacion | [README](notification-agent/README.md) |

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

Avance en resiliencia (booking-svc -> notif-svc):

- Timeout de 2 segundos, hasta 3 reintentos con backoff exponencial y
  jitter, y circuit breaker (3 fallos abren el circuito por 30 segundos).
- Outbox pattern: la notificacion se guarda en `booking-db` y, si notif-svc
  esta caido, queda pendiente. La reserva responde 201 igual. Un hilo en
  segundo plano reenvia las pendientes cuando notif-svc vuelve.
- `GET http://localhost:8001/resilience/status` muestra el estado del
  circuit breaker y del outbox. Ver [booking-svc/README.md](booking-svc/README.md)
  para la demo paso a paso.

Avance en observabilidad y seguridad:

- Los tres servicios escriben logs JSON con `correlation_id`, `service`,
  `event`, `level` y `timestamp`. `booking-svc` propaga el mismo
  `x-correlation-id` a `notif-svc` y agrega el `user_id` del JWT.
- `booking-svc` valida el JWT que emite `users-svc` y responde 401 si falta,
  es invalido o expiro. `JWT_SECRET` y los passwords de BD viven en `.env`.

El servidor **MCP** (`fitflow-mcp`) ya esta implementado con 5 herramientas
(`login`, `get_available_classes`, `create_booking`, `cancel_booking`,
`send_notification`) y se conecta a Claude Desktop como proceso local; el
mismo servidor tambien corre embebido dentro de `booking-agent` y
`notification-agent` para la capa A2A. Ver
[fitflow-mcp/README.md](fitflow-mcp/README.md) para el detalle.

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
```

La UI de Consul queda disponible en `http://localhost:8500`. El Booking Agent
publica su Agent Card en `http://localhost:9001/.well-known/agent.json` y el
Notification Agent en `http://localhost:9002/.well-known/agent.json`.

### Recursos de Docker Compose

`docker compose up --build -d` construye o actualiza las imagenes de
`users-svc`, `booking-svc`, `notif-svc`, `booking-agent` y
`notification-agent`, y ejecuta 9 contenedores:

- `users-svc`, `booking-svc`, `notif-svc`, `booking-agent` y
  `notification-agent`.
- `users-db`, `booking-db` y `notif-db` (PostgreSQL independiente).
- `consul` para el registro de servicios.

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
         +----------------------+
         | Orchestrator Agent   |   <- PENDIENTE 
         +----------+-----------+
            | A2A / Agent Cards
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
descubrir servicios sanos. MCP conecta un agente con herramientas del sistema;
A2A permite que el Orchestrator descubra y delegue tareas a agentes
especializados mediante Agent Cards.

## Agent-to-Agent

Cada agente publica sus capacidades en `/.well-known/agent.json` y acepta
tareas en `POST /a2a/tasks`, delegando la operación real a su microservicio
vía MCP (nunca accede directamente a una base de datos):

- **Booking Agent** (`create_booking`, `cancel_booking`): recibe `class_id` o
  `booking_id` y el JWT del usuario en `access_token`. Delega a `booking-svc`.
- **Notification Agent** (`send_notification`): recibe `user_id` y `message`.
  No requiere `access_token` porque `notif-svc` no exige JWT. Delega a
  `notif-svc`.

El Orchestrator debe descubrir cada Agent Card y enviar una tarea como estas:

```json
{"skill":"create_booking","class_id":1,"access_token":"<JWT>"}
```

```json
{"skill":"send_notification","user_id":1,"message":"Tu reserva de yoga fue confirmada"}
```

Para el flujo "Reserva yoga para el viernes y avísame", el Orchestrator
delega primero `create_booking` al Booking Agent y luego `send_notification`
al Notification Agent, propagando el mismo `x-correlation-id` a ambas
llamadas.

Para probar el flujo completo de cada servicio, consulta su README:
[users-svc](users-svc/README.md) · [booking-svc](booking-svc/README.md) ·
[notif-svc](notif-svc/README.md) · [fitflow-mcp](fitflow-mcp/README.md) ·
[booking-agent](booking-agent/README.md) ·
[notification-agent](notification-agent/README.md).