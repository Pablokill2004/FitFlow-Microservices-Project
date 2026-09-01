# users-svc

Microservicio de usuarios de FitFlow.

## Task 1 - Microservicio y Docker

### Implementacion

- Registro de usuarios con contrasena almacenada como hash.
- Login con JWT; la respuesta incluye `access_token` y el `user_id` forma parte
  del token.
- Consulta del perfil mediante el ID del usuario.
- `/healthz` valida que el proceso este activo.
- `/readyz` valida la conexion con PostgreSQL.
- Auto-registro en Consul al iniciar el servicio.
- Desregistro en Consul al apagar el servicio.
- PostgreSQL dedicado (`users-db`) y Dockerfile propio.

### Ejecucion

Desde la raiz del repositorio, configura las variables definidas en `.env` y
levanta el servicio junto con su base de datos:

```bash
docker compose up --build -d users-db users-svc
```
o
```bash
docker compose up --build -d
```


El servicio queda disponible en `http://localhost:8003`.

## Task 2 - Registro de servicios con Consul

### Registro de users-svc

Al iniciar, `users-svc` registra automaticamente su identidad en Consul con:

- `Name`: nombre logico del servicio.
- `Address` y `Port`: destino para recibir trafico.
- Health check HTTP sobre `/healthz` cada 10 segundos.
- Baja automatica del registro despues de 30 segundos en estado critico.

Al apagar el contenedor, se ejecuta el desregistro para limpiar el catalogo.

Variables usadas por el registro:

- `CONSUL_HOST`
- `CONSUL_PORT`
- `CONSUL_SERVICE_NAME`
- `CONSUL_SERVICE_ID`
- `CONSUL_SERVICE_ADDRESS`
- `CONSUL_SERVICE_PORT`
- `CONSUL_HEALTH_PATH`

### Verificacion del registro

La UI de Consul queda disponible en `http://localhost:8500`. Tambien puede
consultarse el estado del servicio mediante la API:

```bash
curl http://localhost:8500/v1/health/service/users-svc
```

La respuesta debe incluir el servicio `users-svc` con un check en estado
`passing`.

## Task 3 - Resiliencia y observabilidad

La resiliencia de las llamadas entre servicios se implementa en
`booking-svc`. La propagacion de `x-correlation-id` y los logs JSON se
integran de forma transversal en los servicios. En `users-svc`, cada request
usa el valor recibido en `x-correlation-id` o genera un UUID cuando el header
no existe. El mismo valor se devuelve en la respuesta y se agrega a los logs.

### Logs estructurados

`users-svc` escribe cada evento como un objeto JSON en una sola linea. Cada
registro incluye `timestamp`, `level`, `service`, `event` y `correlation_id`.
Los eventos `request_started` y `request_completed` permiten seguir el inicio,
resultado y duracion de cada request. Los eventos de arranque y Consul tambien
usan el mismo formato; en ellos `correlation_id` vale `-` porque no pertenecen
a un request HTTP.

### Verificacion

Levantar `users-svc` y consultar un endpoint sin enviar header:

```powershell
docker compose up --build -d users-db users-svc
curl.exe -i http://localhost:8003/healthz
docker compose logs --no-color --tail=30 users-svc
```

La respuesta debe incluir un header `x-correlation-id` con un UUID. En los
logs, las lineas JSON de `request_started` y `request_completed` deben tener
el mismo `correlation_id`.

Repetir la prueba con un ID conocido:

```powershell
$correlationId = "demo-task3b-001"
curl.exe -i http://localhost:8003/healthz -H "x-correlation-id: $correlationId"
docker compose logs --no-color --tail=30 users-svc | Select-String $correlationId
```

La respuesta y ambos eventos del request deben mostrar
`demo-task3b-001`. Para comprobar que el formato es JSON, copiar una linea
del log y ejecutar:

```powershell
docker compose logs --no-color --tail=1 users-svc | ConvertFrom-Json
```

El objeto resultante debe mostrar los cinco campos requeridos. La propagacion
entre `booking-svc` y `notif-svc` se verificara cuando se complete la
integracion de esos servicios.

### Video de demostración
>
> Video de verificacion de *Task3B*:
>
> [Ver demostracion en YouTube](https://youtu.be/mjTP9gy5nVM)

## Task 4 - Seguridad y configuracion

### JWT

El login genera un JWT firmado. El token incluye el identificador del usuario
en el campo `user_id`, para que otros servicios puedan validar la identidad en
los endpoints protegidos.

### Variables sensibles

La conexion a PostgreSQL y la firma del JWT se configuran mediante variables de
entorno. Los valores locales se guardan en `.env`, que no debe incluirse en el
repositorio.

### Verificacion del Task 1

```bash
curl http://localhost:8003/healthz
curl http://localhost:8003/readyz
```

Ambos endpoints deben responder:

```json
{"status":"ok"}
```

### Flujo funcional

Registrar un usuario:

```powershell
curl.exe -X POST http://localhost:8003/users/register `
  -H "Content-Type: application/json" `
  -d '{"email":"ana@example.com","password":"Secret123!","full_name":"Ana Lopez"}'
```

Iniciar sesion para obtener el JWT:

```powershell
curl.exe -X POST http://localhost:8003/users/login `
  -H "Content-Type: application/json" `
  -d '{"email":"ana@example.com","password":"Secret123!"}'
```

Con el `id` devuelto al registrar el usuario, consultar su perfil:

```powershell
curl.exe http://localhost:8003/users/1
```

El registro duplicado responde `400`, las credenciales invalidas responden
`401` y un usuario inexistente responde `404`.