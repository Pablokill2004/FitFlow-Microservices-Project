# booking-agent

Agente A2A de FitFlow para operaciones de reservas. Publica sus capacidades
mediante un Agent Card y delega las operaciones a `booking-svc` usando MCP.

## Task 5 - Booking Agent

### Implementación

- `GET /.well-known/agent.json` publica el nombre, URL y habilidades del agente.
- `POST /a2a/tasks` acepta `create_booking` y `cancel_booking`.
- El agente recibe el JWT en `access_token` y lo pasa al servidor MCP.
- El servidor MCP consulta Consul y llama a `booking-svc` sin acceso directo a
  la base de datos.
- `GET /healthz` permite comprobar que el contenedor está activo.

### Ejecución

Desde la raíz del repositorio:

```bash
docker compose up --build -d booking-agent booking-svc users-svc consul
```

El agente queda disponible en `http://localhost:9001`.

### Verificación

Comprobar el Agent Card:

```bash
curl http://localhost:9001/.well-known/agent.json
```

La respuesta debe listar `create_booking` y `cancel_booking`. Con un JWT
obtenido desde `users-svc`, probar una reserva:

```bash
curl -X POST http://localhost:9001/a2a/tasks \
  -H "Content-Type: application/json" \
  -H "x-correlation-id: a2a-demo-001" \
  -d '{"skill":"create_booking","class_id":1,"access_token":"<JWT>"}'
```

La respuesta debe incluir `skill`, `result` y `correlation_id`. En los logs de
`booking-agent` se puede observar la llamada MCP; en los logs de `booking-svc`
debe aparecer la reserva creada. Para cancelar, cambiar la tarea a
`cancel_booking` y enviar `booking_id`.

### Integración con el Orchestrator

El Orchestrator descubre este agente leyendo el Agent Card y usa su `url` para
enviar tareas A2A. La coordinación con el Notification Agent ocurre fuera de
este contenedor: el Orchestrator puede ejecutar primero una reserva y luego
delegar el envío de la notificación al agente correspondiente.