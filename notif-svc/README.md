# notif-svc

Microservicio de notificaciones de FitFlow, correspondiente al **Checkpoint 1**.

## Implementación

- Crear/enviar una notificación (por ahora se registra en el log del servicio;
  la integración con un proveedor real de email/SMS queda fuera del alcance de
  este checkpoint).
- Consultar el historial de notificaciones de un usuario, ordenado del más
  reciente al más antiguo.
- `/healthz` valida que el proceso esté activo.
- `/readyz` valida la conexión con PostgreSQL.
- PostgreSQL dedicado (`notif-db`) y Dockerfile propio.

## Ejecutar

Desde la raíz del repositorio, configura las variables definidas en `.env`
(ver `.env.example`) y levanta el servicio junto con su base de datos:

```bash
docker compose up --build -d notif-db notif-svc
```
o
```bash
docker compose up --build -d
```

El servicio queda disponible en `http://localhost:8002`.

## Verificación

```bash
curl http://localhost:8002/healthz
curl http://localhost:8002/readyz
```

Ambos endpoints deben responder:

```json
{"status":"ok"}
```

### Flujo funcional

Enviar una notificación:

```bash
curl -X POST http://localhost:8002/notifications \
  -H "Content-Type: application/json" \
  -d '{"user_id":1,"message":"Tu reserva de yoga fue confirmada"}'
```

Consultar el historial de un usuario:

```bash
curl http://localhost:8002/notifications/1
```

Un usuario sin notificaciones responde con una lista vacía `[]`.

El servicio registra cada notificación enviada en su log (`logger.info`),
visible con:

```bash
docker compose logs notif-svc
```
