

Distribución de tareas — Proyecto FitFlow
Universidad Galileo · FISICC · Postgrado en Diseño y Desarrollo de Software
Equipo de 3 estudiantes · Duración total: 27 días (inicio 16 ago 2026)
- Proyecto seleccionado
El equipo usará FitFlow — la plataforma de reservas de clases fitness — tal como está descrita en el documento
base, incluyendo su arquitectura de microservicios (users-svc, booking-svc, notif-svc), el service registry con
Consul, el servidor MCP, la resiliencia con circuit breaker y la capa de Agent-to-Agent (A2A).
Se confirmó con Cecilia que este proyecto de ejemplo puede usarse directamente, sin necesidad de definir uno
propio, ya que el documento no restringe su uso.
- Cronograma general
HitoQué debe estar listoDíaFecha
## Checkpoint 1
Task 1 completo: los 3 microservicios
corren con docker compose up, cada uno
con su propia base de datos.
Día 723 ago 2026
## Checkpoint 2
Task 2, 3 y 4 completos: Consul con los 3
servicios en verde, MCP Server
funcionando desde Claude Desktop,
circuit breaker demostrado, JWT +
secretos fuera del código, README casi
completo.
Día 216 sep 2026
Entrega final
Task 5 (A2A) completo, README final,
video demo 5–8 min, repositorio en
GitHub. Cloud deployment si hay margen
(puntos extra).
Día 2712 sep 2026
- Criterio de división del trabajo
La arquitectura del proyecto ya sugiere una división natural: cada microservicio tiene dueño exclusivo de sus
datos ('database per service'), así que cada estudiante toma un microservicio de punta a punta —
implementación, registro en Consul, logs y seguridad — y en la fase final ese mismo estudiante construye el
agente A2A correspondiente a su dominio. Las tareas transversales (docker-compose, MCP Server, README,
video) se reparten para balancear la carga, ya que booking-svc concentra la parte más pesada del proyecto
## (resiliencia).
- Distribución detallada por estudiante
Estudiante 1 — Responsable de users-svc
Dueño del servicio de usuarios y del Booking Agent en la fase A2A.

Fase 1 · hasta Checkpoint 1 (día 7)
ReferenciaTareaEntregable concreto
## Task 1
Construir users-svc: registro, login (JWT), obtener perfil por ID,
## /healthz, /readyz.
users-svc corriendo con su propio
Dockerfile y PostgreSQL dedicado (usuario
de BD propio).
InfraAportar el bloque de users-svc al docker-compose.yml compartido.
curl a /healthz de users-svc responde
## {"status":"ok"}.
Fase 2 · hasta Checkpoint 2 (día 21)
ReferenciaTareaEntregable concreto
Task 2A
Auto-registro de users-svc en Consul al arrancar (nombre, dirección,
puerto, health check).
users-svc visible en verde en
http://localhost:8500.
Task 3B
Logs JSON estructurados en users-svc (correlation_id, service, event,
level, timestamp).
Logs consultables/filtrables por
correlation_id.
Task 4AEmitir el JWT incluyendo user_id, para que booking-svc lo valide.
Login devuelve un JWT válido con user_id
embebido.
Task 4B
JWT_SECRET y password de BD de users-svc en .env (fuera del código,
en .gitignore).
Sin secretos hardcodeados en el
repositorio.
Fase 3 · hasta la entrega final (día 27)
ReferenciaTareaEntregable concreto
## Task 5
Construir el Booking Agent: Agent Card en /.well-known/agent.json +
delega create_booking/cancel_booking vía MCP.
Contenedor booking-agent corriendo y
descubierto por el Orchestrator.
Task 4C
Redactar sección de Arquitectura del README (diagrama ASCII) y parte
de 'Cómo correr el proyecto'.
Sección del README lista para consolidar.
## Demo
Grabar los pasos 1–2 del video: registro de usuario, login, JWT
recibido.
Clip de 30–45 seg para el video final.
Estudiante 2 — Responsable de booking-svc (carga más pesada)
Dueño del servicio de reservas, la resiliencia del sistema y el Orchestrator Agent.
Fase 1 · hasta Checkpoint 1 (día 7)
ReferenciaTareaEntregable concreto
## Task 1
Construir booking-svc: crear/consultar/cancelar reserva, listar
clases, /healthz, /readyz.
booking-svc corriendo con su propio
Dockerfile y PostgreSQL dedicado.
## Infra
Consolidar y mantener el docker-compose.yml final del equipo (integra
los 3 servicios).
docker compose up --build levanta todo el
sistema sin errores.

Fase 2 · hasta Checkpoint 2 (día 21)
ReferenciaTareaEntregable concreto
Task 2A
Auto-registro de booking-svc en Consul; consultar a Consul la URL de
notif-svc en vez de hardcodearla.
booking-svc localiza notif-svc
dinámicamente vía Consul.
Task 3A
Resiliencia (mínimo 2 de 3): timeout de 2s, retries con backoff
exponencial + jitter, circuit breaker con outbox pattern para
notificaciones pendientes.
Al derribar notif-svc, booking-svc sigue
respondiendo (no 500) y muestra el circuit
breaker abierto/cerrado.
Task 3B
Logs JSON en booking-svc; propagar x-correlation-id en la llamada a
notif-svc.
El mismo correlation_id se ve en los logs de
ambos servicios.
Task 4A
Validar JWT en los endpoints protegidos de booking-svc; responder
401 si es inválido o expiró.
Endpoints de escritura rechazan requests
sin token válido.
Fase 3 · hasta la entrega final (día 27)
ReferenciaTareaEntregable concreto
## Task 5
Construir el Orchestrator Agent: recibe instrucción en lenguaje natural,
descubre Booking Agent y Notification Agent por Agent Card, coordina
la delegación.
Flujo completo: 'Reserva yoga para el
viernes y avísame' se resuelve vía A2A.
Task 4C
Redactar sección de Resiliencia y de Agent-to-Agent (MCP vs A2A) en
el README.
Sección del README lista para consolidar.
## Demo
Liderar la grabación de los pasos 3–5 del video: reserva con JWT, caída
de notif-svc, circuit breaker, recuperación.
Clip de 2–3 min para el video final.
Estudiante 3 — Responsable de notif-svc, MCP Server y README
Dueño del servicio de notificaciones, del servidor MCP y del Notification Agent; consolida el README y el video.
Fase 1 · hasta Checkpoint 1 (día 7)
ReferenciaTareaEntregable concreto
## Task 1
Construir notif-svc: crear/enviar notificación (puede ser solo un log por
ahora), historial por usuario, /healthz, /readyz.
notif-svc corriendo con su propio Dockerfile
y PostgreSQL dedicado.
## Infra
Agregar y configurar el contenedor de Consul (hashicorp/consul:1.17)
en el docker-compose.yml.
http://localhost:8500 accesible con la UI de
## Consul.
Fase 2 · hasta Checkpoint 2 (día 21)
ReferenciaTareaEntregable concreto
Task 2AAuto-registro de notif-svc en Consul al arrancar.
notif-svc visible en verde en
http://localhost:8500.
Task 2B
Construir fitflow-mcp con al menos 3 herramientas:
get_available_classes, create_booking, cancel_booking; conectarlo a
## Claude Desktop.
Desde Claude Desktop, '¿qué clases hay
disponibles?' devuelve datos reales de
booking-svc.
Task 3BLogs JSON estructurados en notif-svc.
Logs de notif-svc filtrables por
correlation_id.

ReferenciaTareaEntregable concreto
Task 4B
Secretos de notif-svc en .env; documentar en el README los pasos
para rotar credenciales sin downtime.
Sección 'Rotación de credenciales'
redactada.
Fase 3 · hasta la entrega final (día 27)
ReferenciaTareaEntregable concreto
## Task 5
Construir el Notification Agent: Agent Card + delega send_notification
vía MCP a notif-svc.
Contenedor notification-agent corriendo y
descubierto por el Orchestrator.
Task 4C
Consolidar el README final (unir los aportes de los 3) y revisar
consistencia de todas las secciones.
README.md completo con las 3 secciones
requeridas + Agent-to-Agent.
## Demo
Grabar los pasos 6–7 (Claude Desktop vía MCP) y editar el video final
(5–8 min).
Video final publicado/enlazado en el
repositorio.
- Responsabilidades compartidas
●docker-compose.yml final: cada quien aporta el bloque de su servicio; Estudiante 2 lo consolida y valida
que docker compose up --build funcione de punta a punta.
●README.md: cada estudiante redacta la sección de su dominio (fase 2–3); Estudiante 3 consolida el
documento final antes de la entrega.
●Video demo (5–8 min): cada estudiante graba el fragmento de su dominio; Estudiante 3 edita y entrega
el video final.
●Punto extra — despliegue en cloud (+15 pts): opcional, solo si el equipo termina el resto antes del día
- Se recomienda Railway o Render por simplicidad.
- Mapeo de criterios de evaluación (110 pts + 15 extra)
CriterioPuntosResponsable principal
Los 3 servicios corren con docker compose up y tienen DB propia20Los 3 (uno por servicio)
Los servicios se registran en Consul y se descubren dinámicamente20Los 3 (registro) + Estudiante 2 (consumo)
MCP Server funciona: Claude puede crear una reserva20Estudiante 3
Circuit breaker demostrado: notif-svc cae, el sistema sigue
respondiendo
20Estudiante 2
JWT implementado + secretos fuera del código10
## Estudiante 1 (emisión) + Estudiante 2
(validación) + los 3 (secretos)
Agent-to-Agent implementation20
Los 3 (un agente c/u), coordinados por
## Estudiante 2
## Total110
Extra: despliegue en cloud (opcional)+15Todo el equipo, si sobra tiempo antes del día 27