# notif-svc

Microservicio de notificaciones de FitFlow.

## Task 1 — Microservicio y Docker

### Implementación

- Crear/enviar una notificación (por ahora se registra en el log del servicio;
  la integración con un proveedor real de email/SMS queda fuera del alcance
  de este proyecto).
- Consultar el historial de notificaciones de un usuario, ordenado del más
  reciente al más antiguo.
- `/healthz` valida que el proceso esté activo.
- `/readyz` valida la conexión con PostgreSQL.
- Auto-registro en Consul al iniciar el servicio.
- Desregistro en Consul al apagar el servicio.
- PostgreSQL dedicado (`notif-db`) y Dockerfile propio.

### Ejecución

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

## Task 2 — Registro de servicios con Consul

### Registro de notif-svc

Al iniciar, `notif-svc` registra automáticamente su identidad en Consul con:

- `Name`: nombre lógico del servicio (`notif-svc`).
- `Address` y `Port`: destino para recibir tráfico.
- Health check HTTP sobre `/healthz` cada 10 segundos.
- Baja automática del registro después de 30 segundos en estado crítico.

Al apagar el contenedor, se ejecuta el desregistro para limpiar el catálogo.

Variables usadas por el registro (con prefijo `NOTIF_` para no chocar con las
variables genéricas `CONSUL_SERVICE_*` que usa el registro de `users-svc` en
el mismo `.env` compartido):

- `CONSUL_HOST` / `CONSUL_PORT` (compartidas por todos los servicios)
- `NOTIF_CONSUL_SERVICE_NAME`
- `NOTIF_CONSUL_SERVICE_ID`
- `NOTIF_CONSUL_SERVICE_ADDRESS`
- `NOTIF_CONSUL_SERVICE_PORT`
- `NOTIF_CONSUL_HEALTH_PATH`

### Verificación del registro

La UI de Consul queda disponible en `http://localhost:8500`. También puede
consultarse el estado del servicio mediante la API:

```bash
curl http://localhost:8500/v1/health/service/notif-svc
```

La respuesta debe incluir el servicio `notif-svc` con un check en estado
`passing`.

## Task 2B — Servidor MCP (`fitflow-mcp`)

Ver [fitflow-mcp/README.md](../fitflow-mcp/README.md) para la implementación
completa del servidor MCP, sus 4 herramientas (`login`, `get_available_classes`,
`create_booking`, `cancel_booking`) y cómo conectarlo a Claude Desktop.

## Task 3 — Resiliencia y observabilidad

### Logs estructurados

`notif-svc` escribe cada evento como un objeto JSON en una sola línea. Cada
registro incluye `timestamp`, `level`, `service`, `event` y `correlation_id`.
Los eventos `request_started` y `request_completed` permiten seguir el
inicio, resultado y duración de cada request. Cada request usa el valor
recibido en `x-correlation-id` o genera un UUID cuando el header no existe;
el mismo valor se devuelve en la respuesta y se agrega a los logs.

### Verificación

```bash
docker compose up --build -d notif-db notif-svc
curl -i http://localhost:8002/healthz
docker compose logs --no-color --tail=10 notif-svc
```

La respuesta debe incluir un header `x-correlation-id` con un UUID, y los
logs deben mostrar líneas JSON con ese mismo `correlation_id` en
`request_started` y `request_completed`.

Repetir la prueba con un ID conocido:

```bash
curl -i http://localhost:8002/healthz -H "x-correlation-id: demo-task3b-001"
docker compose logs --no-color --tail=10 notif-svc | grep demo-task3b-001
```

## Task 4 — Seguridad y configuración

### Variables sensibles

La conexión a PostgreSQL de `notif-svc` se configura mediante variables de
entorno. Los valores locales se guardan en `.env`, que no debe incluirse en
el repositorio. `.gitignore` excluye `.env`; `.env.example` solo contiene
nombres de variables y valores de ejemplo que deben reemplazarse localmente.

### Rotación de credenciales

Realizar la rotación en una ventana controlada, conservando temporalmente la
credencial anterior para no interrumpir las conexiones existentes:

1. Generar una nueva contraseña aleatoria para el usuario de `notif-db`. No
   usar valores del README ni del repositorio.
2. Cambiar la contraseña del usuario de PostgreSQL con una cuenta
   administradora, sin detener el contenedor:

   ```bash
   docker compose exec notif-db psql -U "$NOTIF_POSTGRES_USER" -d "$NOTIF_POSTGRES_DB" \
     -c "ALTER ROLE notif_admin WITH PASSWORD 'NUEVA_PASSWORD';"
   ```

   La base de datos permanece disponible mientras se actualiza; las
   conexiones abiertas de `notif-svc` no se cortan, solo las nuevas
   necesitarán la contraseña actualizada.
3. Actualizar en `.env` `NOTIF_POSTGRES_PASSWORD` y `NOTIF_DATABASE_URL` con
   el nuevo valor.
4. Recrear únicamente el contenedor de `notif-svc` para que tome la nueva
   variable de entorno:

   ```bash
   docker compose up -d --force-recreate notif-svc
   ```

5. Verificar que el servicio quedó sano con la nueva credencial y que `.env`
   sigue ignorado por git:

   ```bash
   curl http://localhost:8002/readyz
   git check-ignore .env
   ```

### Verificación del Task 1

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
