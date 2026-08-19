# users-svc

Microservicio de usuarios de FitFlow, correspondiente al **Checkpoint 1**

## Implementación

- Registro de usuarios con contrasena almacenada como hash.
- Login con JWT; la respuesta incluye `access_token` y el `user_id` forma parte
  del token.
- Consulta del perfil mediante el ID del usuario.
- `/healthz` valida que el proceso este activo.
- `/readyz` valida la conexion con PostgreSQL.
- PostgreSQL dedicado (`users-db`) y Dockerfile propio.

## Ejecutar

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

## Verificacion

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