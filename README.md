# FitFlow

Plataforma de reservas de clases fitness construida con microservicios y el
principio **Database per Service**.

## Documentacion del proyecto

- [users-svc](users-svc/README.md): implementacion del servicio de usuarios.

## Estado: Checkpoint 1

En este checkpoint se documenta y valida el primer microservicio implementado:

| Servicio | Puerto | Responsabilidad | Documentacion |
| --- | ---: | --- | --- |
| users-svc | 8003 | Registro, login con JWT y perfil de usuario | [README](users-svc/README.md) |

El servicio usa PostgreSQL dedicado (`users-db`) y se levanta mediante el
`docker-compose.yml` de la raiz. Los servicios `booking-svc`, `notif-svc`,
Consul y MCP corresponden a etapas posteriores y se incorporaran al repositorio
conforme avance el proyecto.

## Inicio rapido

Requisito: Docker Desktop con Docker Compose v2.

```bash
docker compose up --build -d
curl http://localhost:8003/healthz
curl http://localhost:8003/readyz
```

Para probar el flujo completo de usuarios, consulta el
[README de users-svc](users-svc/README.md).