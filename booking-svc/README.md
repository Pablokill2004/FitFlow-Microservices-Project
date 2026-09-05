# booking-svc

Microservicio de reservas de clases fitness de FitFlow (puerto 8001).
Es el servicio con la carga de resiliencia del sistema: llama a notif-svc
después de cada reserva y debe seguir respondiendo aunque notif-svc esté caído.

## Task 1 - Microservicio y Docker

### Implementación

- Listar las clases disponibles (`GET /classes`). Al arrancar, el servicio
  siembra 5 clases de ejemplo si la tabla está vacía (seed idempotente).
- Crear una reserva (`POST /bookings`). Valida que la clase exista, que tenga
  cupo (solo cuentan reservas confirmadas) y que el usuario no tenga ya una
  reserva activa de la misma clase.
- Consultar una reserva por ID (`GET /bookings/{id}`).
- Cancelar una reserva (`DELETE /bookings/{id}`). La cancelación es lógica
  (soft-cancel): la reserva pasa a `status="cancelled"` y se conserva el
  historial. Cancelar dos veces responde 400.
- booking-svc nunca lee la base de datos de usuarios (Database per Service).
  El token JWT es la única referencia al usuario.
- `/healthz` valida que el proceso esté activo.
- `/readyz` valida la conexión con PostgreSQL.
- PostgreSQL dedicado (`booking-db`) y Dockerfile propio.

### Ejecución

Desde la raíz del repositorio, configura las variables definidas en `.env`
(ver `.env.example`) y levanta el sistema completo:

```bash
docker compose up --build -d
```

El servicio queda disponible en `http://localhost:8001`.

## Task 2A - Registro en Consul y descubrimiento de notif-svc

### Registro de booking-svc

Al iniciar, `booking-svc` se registra en Consul con:

- `Name`: `booking-svc`.
- `Address` y `Port`: `booking-svc:8001` (nombre lógico de docker-compose).
- Health check HTTP sobre `/healthz` cada 10 segundos.
- Baja automática del registro después de 30 segundos en estado crítico.

Al apagar el contenedor, el servicio se desregistra de Consul.

Variables usadas (prefijo `BOOKING_` para no chocar con las de los otros
servicios en el mismo `.env`):

- `CONSUL_HOST` / `CONSUL_PORT` (compartidas por todos los servicios)
- `BOOKING_CONSUL_SERVICE_NAME`, `BOOKING_CONSUL_SERVICE_ID`
- `BOOKING_CONSUL_SERVICE_ADDRESS`, `BOOKING_CONSUL_SERVICE_PORT`
- `BOOKING_CONSUL_HEALTH_PATH`

### Descubrimiento dinámico de notif-svc

booking-svc **no tiene la URL de notif-svc escrita en el código**. Antes de
cada intento de notificación consulta a Consul:

```
GET http://consul:8500/v1/health/service/notif-svc?passing=true
```

Con la respuesta construye `http://{Address}:{Port}` de una instancia sana.
Si hay varias instancias, elige una al azar. La regla de respaldo es:

| Situación | Comportamiento |
| --- | --- |
| Consul responde con instancias sanas | Usa la dirección que devuelve Consul (`source: consul`). |
| Consul responde sin instancias sanas | La notificación queda pendiente en el outbox. No se llama a una URL fija. |
| Consul no responde | Usa `NOTIF_SVC_FALLBACK_URL` (`http://notif-svc:8002`) y lo registra en el log (`source: fallback`). |

El código está en `booking-svc/app/discovery.py`.

### Verificación

```bash
curl http://localhost:8500/v1/health/service/booking-svc
curl http://localhost:8001/resilience/status
```

El primer comando debe mostrar `booking-svc` con un check en estado
`passing`. El segundo muestra en `discovery` la URL de notif-svc que
booking-svc resolvió vía Consul:

```json
"discovery": {"service": "notif-svc", "url": "http://notif-svc:8002", "source": "consul", "error": null}
```

## Task 3A - Resiliencia (timeout, retries, circuit breaker y outbox)

Después de crear o cancelar una reserva, booking-svc envía una notificación a
`POST /notifications` de notif-svc. La llamada está protegida por los tres
mecanismos del enunciado. El código está en `booking-svc/app/resilience.py`.

### 1. Timeout

Ninguna llamada a notif-svc espera más de 2 segundos (`NOTIF_TIMEOUT_S`).

### 2. Retries con backoff exponencial y jitter

Si la llamada falla, se reintenta hasta 3 veces (`NOTIF_MAX_RETRIES`) con
esperas de 0.5 s, 1 s y 2 s más un jitter aleatorio de 0 a 0.3 s. Se usa la
librería `tenacity`. Cada reintento vuelve a consultar Consul, así aprovecha
una instancia que acabe de volver.

### 3. Circuit breaker + outbox pattern

Se usa la librería `pybreaker` con `fail_max=3` y `reset_timeout=30`:

- **closed**: las notificaciones se envían con normalidad.
- Si 3 entregas seguidas fallan (cada una con sus reintentos), el circuito
  pasa a **open** durante 30 segundos. Mientras está abierto no se llama a
  notif-svc y la reserva responde de inmediato.
- Pasados los 30 segundos, el circuito pasa a **half-open** y permite una
  llamada de prueba. Si funciona, vuelve a **closed**; si falla, vuelve a
  **open** otros 30 segundos.

El **outbox** es la tabla `notification_outbox` de `booking-db`. Cada reserva
creada o cancelada escribe ahí su notificación con `status="pending"` en la
misma base de datos que la reserva. Luego intenta entregarla:

- Si notif-svc la acepta, la fila pasa a `status="sent"`.
- Si falla o el circuito está abierto, la fila queda `pending` con el error
  en `last_error`. La reserva **igual se crea y responde 201**.

Un hilo en segundo plano (`OutboxWorker`) reintenta las pendientes cada 10
segundos (`OUTBOX_FLUSH_INTERVAL_S`). Ese hilo también hace la llamada de
prueba del estado half-open, así el circuito se cierra solo cuando notif-svc
vuelve, sin esperar una reserva nueva.

Orden de composición: `breaker.call(reintentos(post))`. Cada reserva cuenta
como **un** fallo del circuito aunque haga varios intentos HTTP. Así el
circuito se abre en la tercera reserva fallida, como pide la demo.

La respuesta de `POST /bookings` y `DELETE /bookings/{id}` incluye el campo
`notification_status` con valor `sent` o `pending`.

### Endpoints de observación

| Endpoint | Qué muestra |
| --- | --- |
| `GET /resilience/status` | Estado del circuit breaker (`closed`, `open`, `half-open`), contador de fallos, conteo del outbox y la URL de notif-svc resuelta vía Consul. |
| `GET /resilience/outbox?status=pending` | Entradas del outbox. Acepta `status=pending` o `status=sent` y `limit`. |
| `POST /resilience/outbox/flush` | Fuerza una ronda de reintentos sin esperar al hilo en segundo plano. |

### Demo del circuit breaker (pasos 3 a 5 del video)

1. Obtén un token y crea una reserva con notif-svc arriba:

   ```bash
   TOKEN=$(curl -s -X POST http://localhost:8003/users/login \
     -H "Content-Type: application/json" \
     -d '{"email":"demo@fitflow.com","password":"demo1234"}' \
     | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

   curl -X POST http://localhost:8001/bookings \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"class_id":1}'
   ```

   La respuesta trae `"notification_status":"sent"`.

2. Derriba notif-svc:

   ```bash
   docker compose stop notif-svc
   ```

3. Crea 3 reservas más (`class_id` 2, 3 y 4). Cada una tarda unos 4 segundos
   (reintentos) y responde **201** con `"notification_status":"pending"`.
   Después de la tercera:

   ```bash
   curl http://localhost:8001/resilience/status
   ```

   muestra `"state":"open"` y `"fail_counter":3`. Los logs muestran el evento
   `circuit_breaker_state_change` de `closed` a `open`.

4. Crea otra reserva (`class_id` 5). Responde 201 en milisegundos: el
   circuito abierto no llama a notif-svc. `GET /resilience/outbox?status=pending`
   muestra las 4 notificaciones pendientes.

5. Levanta notif-svc y espera hasta 30 segundos:

   ```bash
   docker compose start notif-svc
   sleep 35
   curl http://localhost:8001/resilience/status
   curl http://localhost:8002/notifications/1
   ```

   El circuito vuelve a `closed`, el outbox queda en `"pending":0` y el
   historial de notif-svc muestra las notificaciones que estaban pendientes.

Variables de ajuste (opcionales, con valor por defecto en el código):
`NOTIF_TIMEOUT_S=2`, `NOTIF_MAX_RETRIES=3`, `CB_FAIL_MAX=3`,
`CB_RESET_TIMEOUT_S=30`, `OUTBOX_FLUSH_INTERVAL_S=10`.

## Task 3B - Logs JSON y x-correlation-id

Cada línea de log es un objeto JSON con `timestamp`, `level`, `service`,
`event` y `correlation_id`. Los eventos con JWT válido agregan `user_id`.
Los campos del evento se agregan como claves (`booking_id`, `class_id`,
`status_code`, `duration_ms`, `breaker_state`, etc.).

- Si el request trae `x-correlation-id`, se usa. Si no, se genera un UUID.
- El mismo ID se devuelve en el header `x-correlation-id` de la respuesta.
- La llamada a notif-svc envía el mismo `x-correlation-id`. Esto también
  aplica a las entregas diferidas del outbox: la fila guarda el
  `correlation_id` original y el hilo lo reutiliza al reintentar.

Ejemplo de una reserva con `x-correlation-id: demo-001`:

```json
{"timestamp": "...", "level": "INFO", "service": "booking-svc", "event": "booking_created", "correlation_id": "demo-001", "user_id": 1, "booking_id": 1, "class_id": 1}
{"timestamp": "...", "level": "INFO", "service": "booking-svc", "event": "service_resolved", "correlation_id": "demo-001", "user_id": 1, "target": "notif-svc", "url": "http://notif-svc:8002", "source": "consul"}
{"timestamp": "...", "level": "INFO", "service": "booking-svc", "event": "notification_sent", "correlation_id": "demo-001", "user_id": 1, "outbox_id": 1, "booking_id": 1, "notification_id": 1, "attempts": 1}
{"timestamp": "...", "level": "INFO", "service": "notif-svc", "event": "notification sent to user_id=1: Tu reserva #1 de Yoga fue confirmada", "correlation_id": "demo-001"}
```

Verificación:

```bash
curl -X POST http://localhost:8001/bookings \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "x-correlation-id: demo-001" -d '{"class_id":1}'
docker compose logs --no-color booking-svc notif-svc | grep demo-001
```

El código está en `booking-svc/app/observability.py`.

## Task 4A - Validación de JWT

`POST /bookings` y `DELETE /bookings/{id}` requieren
`Authorization: Bearer <token>` con el JWT que emite users-svc. Ambos servicios
comparten `JWT_SECRET` y `ALGORITHM` desde `.env`.

- Sin header, con firma inválida o con token expirado responde **401** y el
  header `WWW-Authenticate: Bearer`.
- El `user_id` se toma del claim `user_id` del token, nunca del body.
- Cancelar la reserva de otro usuario responde 403.
- El `user_id` del token aparece en los logs junto al `correlation_id`.

```bash
curl -i -X POST http://localhost:8001/bookings -H "Content-Type: application/json" -d '{"class_id":1}'
# HTTP/1.1 401 Unauthorized
```

## Task 4B - Secretos

`JWT_SECRET` y las credenciales de `booking-db` viven en `.env`, que está en
`.gitignore`. El servicio no arranca si falta `JWT_SECRET`.

## Verificación rápida

```bash
curl http://localhost:8001/healthz
curl http://localhost:8001/readyz
curl http://localhost:8001/classes
curl http://localhost:8001/resilience/status
```

Casos de error demostrables:

- `POST /bookings` con `class_id` inexistente responde 404.
- La clase Zumba se siembra con cupo 2; la tercera reserva confirmada
  responde 400 `Class is full`.
- Repetir la reserva de la misma clase con el mismo usuario responde 400.
- Cancelar una reserva ya cancelada responde 400.
