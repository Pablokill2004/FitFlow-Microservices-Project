# booking-svc

Microservicio de reservas de clases fitness de FitFlow, correspondiente al
**Checkpoint 1**.

## Implementación

- Listar las clases disponibles (`GET /classes`). Al arrancar, el servicio
  siembra 5 clases de ejemplo si la tabla está vacía (seed idempotente).
- Crear una reserva (`POST /bookings`). Valida que la clase exista, que tenga
  cupo (solo cuentan reservas confirmadas) y que el usuario no tenga ya una
  reserva activa de la misma clase.
- Consultar una reserva por ID (`GET /bookings/{id}`).
- Cancelar una reserva (`DELETE /bookings/{id}`). La cancelación es lógica
  (soft-cancel): la reserva pasa a `status="cancelled"` y se conserva el
  historial. Cancelar dos veces responde 400.
- `POST /bookings` y `DELETE /bookings/{id}` requieren un JWT válido emitido
  por users-svc (`Authorization: Bearer <token>`). El `user_id` se toma del
  token, nunca del body. Sin token o con token inválido/expirado responde 401.
  Cancelar la reserva de otro usuario responde 403.
- booking-svc nunca lee la base de datos de usuarios (Database per Service);
  el token es la única referencia al usuario.
- `/healthz` valida que el proceso esté activo.
- `/readyz` valida la conexión con PostgreSQL.
- PostgreSQL dedicado (`booking-db`) y Dockerfile propio.

## Ejecutar

Desde la raíz del repositorio, configura las variables definidas en `.env`
(ver `.env.example`) y levanta el servicio junto con su base de datos:

```bash
docker compose up --build -d booking-db booking-svc
```
o
```bash
docker compose up --build -d
```

El servicio queda disponible en `http://localhost:8001`.

## Verificación

```bash
curl http://localhost:8001/healthz
curl http://localhost:8001/readyz
```

Ambos endpoints deben responder:

```json
{"status":"ok"}
```

### Flujo funcional

Obtener un token desde users-svc (requiere users-svc levantado):

```bash
curl -X POST http://localhost:8003/users/register \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@fitflow.com","password":"demo1234","full_name":"Demo"}'

TOKEN=$(curl -s -X POST http://localhost:8003/users/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@fitflow.com","password":"demo1234"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
```

Listar las clases disponibles:

```bash
curl http://localhost:8001/classes
```

Crear una reserva (sin token responde 401):

```bash
curl -X POST http://localhost:8001/bookings \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"class_id":1}'
```

Consultar y cancelar la reserva:

```bash
curl http://localhost:8001/bookings/1
curl -X DELETE http://localhost:8001/bookings/1 -H "Authorization: Bearer $TOKEN"
```

Casos de error demostrables:

- `POST /bookings` con `class_id` inexistente responde 404.
- La clase Zumba se siembra con cupo 2; la tercera reserva confirmada
  responde 400 `Class is full`.
- Repetir la reserva de la misma clase con el mismo usuario responde 400.
- Cancelar una reserva ya cancelada responde 400.

> **Nota (Task 4A adelantada):** el documento de distribución de tareas ubica
> la validación de JWT en booking-svc en el Checkpoint 2. Este servicio la
> implementa desde el Checkpoint 1 porque las instrucciones generales exigen
> token válido para crear reservas.
