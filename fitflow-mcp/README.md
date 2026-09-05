# fitflow-mcp

Servidor MCP de FitFlow. Expone las operaciones de `users-svc` y `booking-svc`
como herramientas que Claude puede usar desde Claude Desktop.

## Por qué corre fuera de docker-compose

Claude Desktop lanza los servidores MCP como **procesos locales** (transporte
stdio), no dentro de una red Docker. Por eso `fitflow-mcp` se instala y
ejecuta directamente en el host, no como contenedor. Esto tiene una
consecuencia importante para el *discovery*: aunque el servidor consulta a
Consul para saber si un servicio está sano y en qué puerto quedó, siempre
construye la URL final contra `localhost` (los puertos publicados por
`docker-compose`), porque los nombres de host internos de Docker
(`booking-svc`, `users-svc`) no son alcanzables desde el proceso del host.

Si Consul no tiene una instancia sana registrada para un servicio, el
servidor usa como respaldo el puerto publicado por defecto en
`docker-compose.yml`.

## Herramientas

| Herramienta | Descripción |
|---|---|
| `login(email, password)` | Inicia sesión contra `users-svc` y guarda el JWT en memoria del proceso, para que las siguientes reservas no necesiten que el usuario maneje el token. |
| `get_available_classes()` | Lista las clases disponibles en `booking-svc`. No requiere sesión. |
| `create_booking(class_id)` | Reserva una clase para el usuario que inició sesión. Requiere haber llamado antes a `login`. |
| `cancel_booking(booking_id)` | Cancela una reserva del usuario que inició sesión. Requiere haber llamado antes a `login`. |

La sesión (`login`) vive solo mientras el proceso del servidor MCP sigue
corriendo — si Claude Desktop lo reinicia, hay que volver a iniciar sesión.

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

Y probar la lógica de las 4 herramientas directamente en Python:

```bash
./.venv/bin/python -c "
import server
print(server.login('mcp-test@fitflow.com', 'Secret123!'))
classes = server.get_available_classes()
print(classes)
booking = server.create_booking(classes[0]['id'])
print(booking)
print(server.cancel_booking(booking['id']))
"
```

También se puede listar el esquema de las 4 herramientas registradas:

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
| `CONSUL_HOST` | `localhost` | Host de Consul visto desde el proceso MCP (fuera de Docker). |
| `CONSUL_PORT` | `8500` | Puerto publicado de Consul. |
