# orchestrator-mcp

Servidor MCP que corre en el **host** (fuera de la red de docker-compose),
lanzado por Claude Desktop como proceso local via stdio -- exactamente el
mismo patron que [`fitflow-mcp`](../fitflow-mcp/README.md). Es el brazo de
razonamiento en lenguaje natural del contenedor `orchestrator-agent`: como
nada puede llamar *hacia* Claude Desktop (no es un servidor), la sesion es
quien lee la instruccion del usuario, decide el plan y se lo somete al
contenedor a traves de estas cuatro herramientas.

Ver `openspec/changes/add-orchestrator-agent/design.md`, decision 3, para el
razonamiento completo.

## Herramientas

| Herramienta | Llega a | Proposito |
|---|---|---|
| `login(email, password)` | `users-svc` | Autentica y guarda el JWT en memoria del proceso, igual que `fitflow-mcp`. |
| `list_classes()` | `booking-svc` `GET /classes` **directo desde el host** | Da los `id`, `name`, `instructor` y `schedule` (ISO) reales de ahora mismo, para que la sesion resuelva "yoga el viernes" contra un `class_id` que de verdad existe. |
| `discover_agents()` | orchestrator `GET /agents` | Muestra que agentes y skills descubrio el contenedor -- para que la sesion planifique contra la capacidad real, no una lista fija. |
| `orchestrate(instruction, steps)` | orchestrator `POST /orchestrate` | Somete el plan armado por la sesion y devuelve el reporte por paso mas el correlation id de la corrida. |

### Por que `list_classes` no pasa por el contenedor

Es deliberado (design decision 3): `orchestrator-mcp` corre en el host, igual
que `fitflow-mcp`, y leer el catalogo publico de clases directo de
`booking-svc` mantiene al contenedor `orchestrator-agent` hablando
**unicamente A2A** -- nunca a un microservicio. El servidor MCP es tooling
del lado del host y esta autorizado a leer ese catalogo publico, tal como ya
hace `fitflow-mcp`.

### `login`

Autentica contra `users-svc` y guarda el JWT en un dict en memoria del
proceso. Devuelve un mensaje de confirmacion legible ("Sesion iniciada como
..."), nunca el token.

### `orchestrate(instruction, steps)`

- Requiere haber llamado `login` antes: si no hay token guardado, lanza
  `RuntimeError("Debes iniciar sesion primero usando la herramienta
  'login'.")` -- el mismo mensaje que usa `fitflow-mcp` para el mismo caso.
- Envia el JWT en el **body** de `POST /orchestrate` como `access_token`
  (el orchestrator lo espera ahi, no en un header `Authorization`).
- Genera un `correlation_id` nuevo (`uuid4`) en cada llamada y lo manda como
  header `x-correlation-id`.
- `steps` es una lista de pasos con esta forma (mismo shape que el `Step`
  del contenedor, sin `user_id` -- el destinatario de una notificacion
  siempre sale del JWT dentro del orchestrator, nunca del plan):

  ```json
  [
    {"skill": "create_booking", "class_id": 4},
    {"skill": "send_notification", "message": "Tu reserva fue confirmada"}
  ]
  ```

- Devuelve el reporte por paso (`succeeded` / `failed` / `skipped`) **y** el
  `correlation_id` de la corrida, para que la sesion le diga al usuario que
  id grepear en los logs de los cinco contenedores
  (`orchestrator-agent`, `booking-agent`, `notification-agent`,
  `booking-svc`, `notif-svc`).

## Fallas de conectividad: se reportan, no se disimulan

Requisito de la spec ("Orchestrator connectivity failures are reported, not
masked"): si el contenedor `orchestrator-agent` esta caido, `discover_agents`
y `orchestrate` lanzan un `RuntimeError` que nombra la falla explicitamente,
por ejemplo:

```
No se pudo contactar al orchestrator en http://localhost:9003: HTTPConnectionPool(host='localhost', port=9003): Max retries exceeded...
```

Nunca devuelven un JSON vacio ni un "exito" fabricado. `login` y
`list_classes` no dependen del contenedor del orchestrator -- hablan directo
con `users-svc` y `booking-svc` -- asi que siguen funcionando aunque
`orchestrator-agent` este caido; si esos servicios tambien estan caidos,
fallan de la misma forma explicita (`RuntimeError` nombrando el host y el
error de conexion).

## Instalacion

Requiere Python 3.10+. Desde esta carpeta:

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
```

macOS / Linux:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## Ejecutar el sistema completo primero

`orchestrator-mcp` necesita `users-svc`, `booking-svc`, Consul y el
contenedor `orchestrator-agent` corriendo:

```bash
cd ..
docker compose up --build -d
```

## Conectar a Claude Desktop

**CRITICO -- conectar un solo servidor MCP a la vez.** `fitflow-mcp` expone
`create_booking`, `cancel_booking` y `send_notification` como herramientas
**directas**. Si `fitflow` y `fitflow-orchestrator` estan conectados al
mismo tiempo, la sesion ve un camino directo de una sola llamada y un camino
A2A de varias, y con frecuencia toma el directo -- silenciosamente evita la
capa A2A que esta demo existe para mostrar (design decision 8). Por eso:

- Para la demo de MCP directo (Task 2B): conectar solo `fitflow`.
- Para la demo de A2A (Task 5): conectar solo `fitflow-orchestrator`,
  con `fitflow` **desconectado**.
- Reiniciar Claude Desktop por completo entre una demo y la otra.
- Verificacion de que la demo A2A de verdad paso por A2A: el
  `correlation_id` que devuelve `orchestrate` debe aparecer en
  `docker compose logs booking-agent notification-agent`. Si no aparece, la
  sesion tomo el camino directo de `fitflow-mcp` en vez de delegar.

Pasos:

1. En Claude Desktop: **Settings -> Developer -> Edit Config** (o busca
   "Connectors"/"MCP" en Settings si tu version no tiene esa opcion).
2. Agrega la clave `mcpServers` al JSON existente (sin borrar lo que ya
   haya), dejando **solo uno** de `fitflow` / `fitflow-orchestrator` activo
   segun que demo vayas a grabar:

   En Windows -- ojo con las barras invertidas dobles, el JSON las exige, y
   con `Scripts\\python.exe`, que es donde el venv pone el interprete (no
   `bin/python`):

   ```json
   "mcpServers": {
     "fitflow-orchestrator": {
       "command": "C:\\ruta\\absoluta\\a\\orchestrator-mcp\\.venv\\Scripts\\python.exe",
       "args": ["C:\\ruta\\absoluta\\a\\orchestrator-mcp\\server.py"]
     }
   }
   ```

   En macOS / Linux:

   ```json
   "mcpServers": {
     "fitflow-orchestrator": {
       "command": "/ruta/absoluta/a/orchestrator-mcp/.venv/bin/python",
       "args": ["/ruta/absoluta/a/orchestrator-mcp/server.py"]
     }
   }
   ```

3. Reinicia Claude Desktop por completo (en Windows, cerrarlo tambien desde
   el area de notificacion; en macOS, Cmd+Q) y volver a abrir.
4. Pregunta "¿que agentes y habilidades tenes disponibles?" -- debe llamar a
   `discover_agents` y devolver los agentes descubiertos por el contenedor.
5. Iniciar sesion y probar el flujo completo: "Reserva la clase de yoga y
   avisame" -- la sesion deberia llamar `list_classes`, resolver el
   `class_id` de Yoga, y someter un plan de dos pasos con `orchestrate`.

## Verificacion manual (sin Claude Desktop)

Con el sistema levantado (incluyendo `orchestrator-agent`, ver mas abajo
como levantarlo si todavia no esta en `docker-compose.yml`):

```bash
./.venv/bin/python -c "
import os, server
# La password se toma del entorno: no hay credenciales en el repositorio.
print(server.login('e2e@fitflow.com', os.environ['FITFLOW_DEMO_PASSWORD']))
classes = server.list_classes()
print(classes)
plan = [
    {'skill': 'create_booking', 'class_id': classes[0]['id']},
    {'skill': 'send_notification', 'message': 'Prueba de orchestrator-mcp'},
]
result = server.orchestrate('prueba manual', plan)
print(result)
print(server.discover_agents())
"
```

Tambien se puede listar el esquema de las 4 herramientas registradas (mismo
patron que documenta `fitflow-mcp/README.md`):

```bash
./.venv/bin/python -c "
import asyncio, server
async def main():
    for t in await server.mcp.list_tools():
        print(t.name, list(t.inputSchema.get('properties', {}).keys()))
asyncio.run(main())
"
```

Debe imprimir las 4 herramientas: `login`, `list_classes`,
`discover_agents`, `orchestrate` -- esta ultima con `instruction` y `steps`
(un arreglo cuyos items tienen `skill`, `class_id`, `booking_id`, `message`,
`reason`).

## Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `CONSUL_HOST` | `localhost` | Host de Consul, para resolver `users-svc`/`booking-svc`. |
| `CONSUL_PORT` | `8500` | Puerto de Consul. |
| `FITFLOW_SERVICE_HOST` | `localhost` | Host contra el que se arma la URL final de cada servicio descubierto via Consul. |
| `ORCHESTRATOR_URL` | `http://localhost:9003` | URL base del contenedor `orchestrator-agent`. No pasa por Consul: el orchestrator no se registra ahi (design decision 2), asi que es un env var directo. |

## Nota sobre el contenedor `orchestrator-agent`

Al momento de este checkpoint (grupo 6), `orchestrator-agent` todavia no
tiene un servicio en `docker-compose.yml` (eso es el grupo 7). Para probar
este servidor MCP contra un contenedor real mientras tanto:

```bash
cd ..
docker build -f orchestrator-agent/Dockerfile -t orchestrator-agent .
docker run -d --rm --name orchestrator-agent \
  --network fitflow-microservices-project_default \
  -p 9003:9003 \
  -e JWT_SECRET=<mismo valor que .env> \
  -e ALGORITHM=HS256 \
  orchestrator-agent
```

Publicar en el puerto 9003 del host es lo que hace que el default de
`ORCHESTRATOR_URL` (`http://localhost:9003`) funcione sin configuracion
adicional.
