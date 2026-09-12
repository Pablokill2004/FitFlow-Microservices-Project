# notification-agent

Agente A2A de FitFlow para el envío de notificaciones. Publica sus
capacidades mediante un Agent Card y delega la operación a `notif-svc`
usando MCP.

## Task 5 - Notification Agent

### Implementación

- `GET /.well-known/agent.json` publica el nombre, URL y habilidad del agente
  (`send_notification`).
- `POST /a2a/tasks` acepta `send_notification` con `user_id` y `message`.
- El agente abre una sesión MCP contra `fitflow-mcp/server.py` (el mismo
  servidor de Task 2B, reutilizado dentro del contenedor) y delega la
  herramienta `send_notification`, que a su vez llama a `notif-svc`. El
  agente nunca accede directamente a la base de datos de notificaciones.
- `notif-svc` no exige JWT para recibir notificaciones, así que a diferencia
  del Booking Agent este agente no necesita un `access_token` para operar
  (se acepta el campo por compatibilidad con el esquema de tarea del
  Orchestrator, pero no se usa).
- `GET /healthz` permite comprobar que el contenedor está activo.

### Ejecución

Desde la raíz del repositorio:

```bash
docker compose up --build -d notification-agent notif-svc consul
```

El agente queda disponible en `http://localhost:9002`.

### Verificación

Comprobar el Agent Card:

```bash
curl http://localhost:9002/.well-known/agent.json
```

La respuesta debe listar la habilidad `send_notification`. Probar el envío:

```bash
curl -X POST http://localhost:9002/a2a/tasks \
  -H "Content-Type: application/json" \
  -H "x-correlation-id: a2a-notif-demo-001" \
  -d '{"skill":"send_notification","user_id":1,"message":"Tu reserva de yoga fue confirmada"}'
```

La respuesta debe incluir `skill`, `result` y `correlation_id`. Confirmar que
quedó guardada en el historial de `notif-svc`:

```bash
curl http://localhost:8002/notifications/1
```

### Integración con el Orchestrator

El Orchestrator descubre este agente leyendo el Agent Card y usa su `url`
para enviar tareas A2A después de (o en paralelo a) delegar una reserva al
Booking Agent — por ejemplo, para el flujo "Reserva yoga para el viernes y
avísame".
