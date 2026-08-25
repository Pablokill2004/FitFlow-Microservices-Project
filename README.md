# FitFlow

Plataforma de reservas de clases fitness construida con microservicios y el
principio **Database per Service**.

## Documentacion del proyecto

- [users-svc](users-svc/README.md): implementacion del servicio de usuarios.
- [booking-svc](booking-svc/README.md): implementacion del servicio de reservas.
- [notif-svc](notif-svc/README.md): implementacion del servicio de notificaciones.

## Estado actual

Se documentan y validan los microservicios implementados hasta ahora:

| Servicio | Puerto | Responsabilidad | Documentacion |
| --- | ---: | --- | --- |
| users-svc | 8003 | Registro, login con JWT y perfil de usuario | [README](users-svc/README.md) |
| booking-svc | 8001 | Reservas de clases y catalogo de clases disponibles | [README](booking-svc/README.md) |
| notif-svc | 8002 | Envio de notificaciones (log) e historial por usuario | [README](notif-svc/README.md) |

Los 3 microservicios del proyecto ya corren con `docker compose up --build`.
Cada servicio usa PostgreSQL dedicado (`users-db`, `booking-db`, `notif-db`)
y se levanta mediante el `docker-compose.yml` de la raiz, que tambien incluye
un contenedor de **Consul** (modo dev) accesible en `http://localhost:8500`.

Avance en service discovery:

- `users-svc` se registra automaticamente en Consul al iniciar.
- `users-svc` publica health check HTTP con intervalo de 10 segundos y
	desregistro automatico tras 30 segundos en estado critico.
- El flujo de desregistro de `users-svc` se ejecuta al apagar el servicio.

El servidor **MCP** corresponde a etapas posteriores y se incorporara al
repositorio conforme avance el proyecto.

## Inicio rapido

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
```

La UI de Consul queda disponible en `http://localhost:8500`.

Para probar el flujo completo de cada servicio, consulta su README:
[users-svc](users-svc/README.md) · [booking-svc](booking-svc/README.md) ·
[notif-svc](notif-svc/README.md).