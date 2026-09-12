# fitflow-mcp

Servidor MCP de FitFlow. Expone las operaciones de `users-svc`, `booking-svc`
y `notif-svc` como herramientas MCP. Se usa de dos formas distintas en el
proyecto:

1. **Task 2B** — Claude Desktop lo lanza como proceso local (stdio) para que
   un agente humano-en-el-loop pueda listar clases y reservar en lenguaje
   natural.
2. **Task 5 (A2A)** — `booking-agent` y `notification-agent` lo lanzan como
   subproceso MCP embebido dentro de su propio contenedor, para delegarle la
   ejecución real de la herramienta que anuncian en su Agent Card.

Es el mismo `server.py` en ambos casos; lo que cambia es quién lo lanza y con
qué variables de entorno.

## Por qué resuelve direcciones distinto según quién lo lanza

Cuando Claude Desktop lo lanza, corre en el **host**, fuera de la red de
docker-compose, así que las direcciones internas de Docker
(`booking-svc`, `users-svc`, `notif-svc`) no son alcanzables — por eso
consulta a Consul para saber si un servicio está sano y en qué puerto quedó,
pero construye la URL final contra `FITFLOW_SERVICE_HOST` (`localhost` por
defecto, usando los puertos publicados).

Cuando un agente A2A lo lanza (dentro de su propio contenedor), sí está en la
red de Docker, así que el agente le pasa `FITFLOW_SERVICE_HOST=booking-svc` o
`FITFLOW_SERVICE_HOST=notif-svc` para que resuelva contra el nombre lógico
del servicio en vez de `localhost`.

Si Consul no tiene una instancia sana registrada para un servicio, el
servidor usa como respaldo el puerto publicado por defecto en
`docker-compose.yml`, sobre el mismo `FITFLOW_SERVICE_HOST`.

## Herramientas

| Herramienta | Descripción |
|---|---|
| `login(email, password)` | Inicia sesión contra `users-svc` y guarda el JWT en memoria del proceso, para que las siguientes reservas no necesiten que el usuario maneje el token. |
| `get_available_classes()` | Lista las clases disponibles en `booking-svc`. No requiere sesión. |
| `create_booking(class_id)` | Reserva una clase para el usuario que inició sesión (o para el `FITFLOW_ACCESS_TOKEN` inyectado por el Booking Agent). |
| `cancel_booking(booking_id)` | Cancela una reserva del usuario que inició sesión (o del `FITFLOW_ACCESS_TOKEN` inyectado). |
| `send_notification(user_id, message)` | Envía una notificación a un usuario a través de `notif-svc`. No requiere sesión ni token — usada por el Notification Agent (Task 5). |

La sesión de `login` vive solo mientras el proceso del servidor MCP sigue
corriendo. Cuando corre embebido en un agente A2A, la tarea trae el JWT en
`access_token` y el agente lo inyecta como `FITFLOW_ACCESS_TOKEN`, así que no
hace falta llamar a `login` en ese modo.

## Instalación

Requiere Python 3.10+. Desde esta carpeta:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## Ejecutar el sistema completo primero

`fitflow-mcp` necesita `users-svc`, `booking-svc` y Consul corriendo:

```bash
cd ..
docker compose up --build -d
```

## Conectar a Claude Desktop

1. En Claude Desktop: **Settings → Developer → Edit Config** (o busca
   "Connectors"/"MCP" en Settings si tu versión no tiene esa opción).
2. Agrega la clave `mcpServers` al JSON existente (sin borrar lo que ya haya):

   ```json
   "mcpServers": {
     "fitflow": {
       "command": "/ruta/absoluta/a/fitflow-mcp/.venv/bin/python",
       "args": ["/ruta/absoluta/a/fitflow-mcp/server.py"]
     }
   }
   ```

3. Reinicia Claude Desktop por completo (Cmd+Q y volver a abrir).
4. Pregunta "¿qué clases hay disponibles?" — debe llamar a
   `get_available_classes` y devolver datos reales de `booking-svc`.

## Verificación manual (sin Claude Desktop)

Con el sistema levantado, registrar un usuario de prueba en `users-svc`:

```bash
curl -X POST http://localhost:8003/users/register \
  -H "Content-Type: application/json" \
  -d '{"email":"mcp-test@fitflow.com","password":"Secret123!","full_name":"MCP Test"}'
```

Y probar la lógica de las 5 herramientas directamente en Python:

```bash
./.venv/bin/python -c "
import server
print(server.login('mcp-test@fitflow.com', 'Secret123!'))
classes = server.get_available_classes()
print(classes)
booking = server.create_booking(classes[0]['id'])
print(booking)
print(server.cancel_booking(booking['id']))
print(server.send_notification(1, 'Prueba de fitflow-mcp'))
"
```

También se puede listar el esquema de las 5 herramientas registradas:

```bash
./.venv/bin/python -c "
import asyncio, server
async def main():
    for t in await server.mcp.list_tools():
        print(t.name, list(t.inputSchema.get('properties', {}).keys()))
asyncio.run(main())
"
```

## Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `CONSUL_HOST` | `localhost` | Host de Consul. Los agentes A2A lo sobrescriben con `consul` (nombre logico dentro de Docker). |
| `CONSUL_PORT` | `8500` | Puerto de Consul. |
| `FITFLOW_SERVICE_HOST` | `localhost` | Host contra el que se arma la URL final de cada servicio descubierto. Los agentes A2A lo sobrescriben con el nombre logico del servicio (`booking-svc`, `notif-svc`). |
| `FITFLOW_ACCESS_TOKEN` | _(vacío)_ | JWT a usar en `create_booking`/`cancel_booking` cuando no se llamó a `login` en este proceso — lo inyecta el Booking Agent con el token que trae la tarea A2A. |

## Usado por los agentes A2A

`booking-agent` y `notification-agent` copian este `server.py` a
`/opt/fitflow-mcp/server.py` dentro de su propia imagen y lo lanzan como
subproceso MCP por cada tarea A2A que reciben. Ver
[booking-agent/README.md](../booking-agent/README.md) y
[notification-agent/README.md](../notification-agent/README.md).
