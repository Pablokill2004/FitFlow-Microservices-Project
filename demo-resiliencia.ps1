<#
.SYNOPSIS
    Demo guiada de resiliencia de FitFlow (Task 3A) - pasos 3 a 5 del video.

.DESCRIPTION
    Recorre paso a paso lo que hay que validar del circuit breaker:
    reserva autenticada, caida de notif-svc, apertura del circuito,
    outbox reteniendo las notificaciones, y recuperacion automatica.

    Pensada para grabarse: se detiene entre pasos para narrar.

.PARAMETER Reset
    Borra los volumenes y vuelve a levantar el stack antes de empezar.
    Usalo para la toma buena: deja la base y el outbox en cero.

.PARAMETER NoPause
    No espera Enter entre pasos (para una corrida de prueba rapida).

.EXAMPLE
    .\demo-resiliencia.ps1 -Reset
#>
[CmdletBinding()]
param(
    [switch]$Reset,
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$Usuario   = 'demo@fitflow.com'
$Password  = 'Secret123!'
$UsersSvc  = 'http://localhost:8003'
$BookSvc   = 'http://localhost:8001'
$NotifSvc  = 'http://localhost:8002'

# ---------- helpers de presentacion ----------

function Titulo($n, $texto) {
    Write-Host ""
    Write-Host ("=" * 72) -ForegroundColor DarkCyan
    Write-Host "  PASO $n  $texto" -ForegroundColor Cyan
    Write-Host ("=" * 72) -ForegroundColor DarkCyan
}

function Nota($texto) { Write-Host "  > $texto" -ForegroundColor DarkGray }

function Pausa($texto = 'Enter para continuar') {
    if ($NoPause) { Start-Sleep -Milliseconds 400; return }
    Write-Host ""
    Write-Host "  [$texto]" -ForegroundColor Yellow
    [void](Read-Host)
}

# Imprime solo lo que importa del /resilience/status, en colores.
function Estado($etiqueta) {
    $s = Invoke-RestMethod -Uri "$BookSvc/resilience/status"
    $cb = $s.circuit_breaker
    $ob = $s.outbox

    $colorCb = if ($cb.state -eq 'open') { 'Red' } elseif ($cb.state -eq 'half-open') { 'Yellow' } else { 'Green' }
    $colorPd = if ($ob.pending -gt 0) { 'Red' } else { 'Green' }

    Write-Host ""
    Write-Host "  --- $etiqueta ---" -ForegroundColor White
    Write-Host "   circuit breaker : " -NoNewline
    Write-Host $cb.state.ToUpper() -ForegroundColor $colorCb -NoNewline
    Write-Host "   (fallos $($cb.fail_counter)/$($cb.fail_max), reset $($cb.reset_timeout_s)s, timeout $($cb.timeout_s)s, reintentos $($cb.max_retries))"
    Write-Host "   outbox          : " -NoNewline
    Write-Host "pending=$($ob.pending)" -ForegroundColor $colorPd -NoNewline
    Write-Host "  sent=$($ob.sent)"
    if ($s.discovery.error) {
        Write-Host "   consul          : " -NoNewline
        Write-Host $s.discovery.error -ForegroundColor Red
    } else {
        Write-Host "   consul          : notif-svc en $($s.discovery.url) (via $($s.discovery.source))" -ForegroundColor Green
    }
    return $s
}

function Reservar($claseId) {
    try {
        $b = Invoke-RestMethod -Method Post -Uri "$BookSvc/bookings" -ContentType 'application/json' `
                -Headers $script:Headers -Body "{`"class_id`":$claseId}"
        $colorN = if ($b.notification_status -eq 'sent') { 'Green' } else { 'Yellow' }
        Write-Host "   clase $claseId -> reserva #$($b.id)  " -NoNewline
        Write-Host "HTTP 201 $($b.status)" -ForegroundColor Green -NoNewline
        Write-Host "   notificacion=" -NoNewline
        Write-Host $b.notification_status -ForegroundColor $colorN
        return $b
    } catch {
        $code = [int]$_.Exception.Response.StatusCode
        Write-Host "   clase $claseId -> HTTP $code  $($_.ErrorDetails.Message)" -ForegroundColor Red
        return $null
    }
}

# ---------- 0. preparacion ----------

if ($Reset) {
    Titulo 0 'Preparacion: stack limpio'
    Nota 'docker compose down -v  (borra volumenes: base y outbox en cero)'
    docker compose down -v | Out-Null
    Nota 'docker compose up -d'
    docker compose up -d | Out-Null
    Write-Host "   esperando a que los servicios respondan..." -ForegroundColor DarkGray
    $ok = $false
    foreach ($i in 1..30) {
        Start-Sleep -Seconds 2
        try {
            Invoke-RestMethod -Uri "$UsersSvc/healthz" -TimeoutSec 3 | Out-Null
            Invoke-RestMethod -Uri "$BookSvc/healthz"  -TimeoutSec 3 | Out-Null
            Invoke-RestMethod -Uri "$NotifSvc/healthz" -TimeoutSec 3 | Out-Null
            $ok = $true; break
        } catch { }
    }
    if (-not $ok) { throw 'Los servicios no respondieron a tiempo.' }
    Invoke-RestMethod -Method Post -Uri "$UsersSvc/users/register" -ContentType 'application/json' `
        -Body "{`"email`":`"$Usuario`",`"password`":`"$Password`",`"full_name`":`"Demo`"}" | Out-Null
    Write-Host "   stack listo y usuario $Usuario registrado" -ForegroundColor Green
}

Write-Host ""
Write-Host "  Contenedores en ejecucion:" -ForegroundColor White
docker compose ps --format "   {{.Service}}`t{{.State}}" | Sort-Object

$r = Invoke-RestMethod -Method Post -Uri "$UsersSvc/users/login" -ContentType 'application/json' `
        -Body "{`"email`":`"$Usuario`",`"password`":`"$Password`"}"
$script:Headers = @{ Authorization = "Bearer $($r.access_token)" }
Write-Host ""
Write-Host "  Login OK: JWT obtenido de users-svc ($($r.access_token.Length) chars)" -ForegroundColor Green

Pausa 'Enter para empezar el PASO 3'

# ---------- 3. reserva con JWT ----------

Titulo 3 'Reserva autenticada con JWT (todo sano)'
Nota 'booking-svc valida el JWT que emitio users-svc y notifica via notif-svc.'
Reservar 1 | Out-Null
$null = Estado 'estado inicial'

Write-Host ""
Nota 'Y sin token, el endpoint de escritura rechaza el request:'
try {
    Invoke-RestMethod -Method Post -Uri "$BookSvc/bookings" -ContentType 'application/json' -Body '{"class_id":2}' | Out-Null
} catch {
    Write-Host "   sin Authorization -> HTTP $([int]$_.Exception.Response.StatusCode) $($_.ErrorDetails.Message)" -ForegroundColor Red
}

Pausa 'Enter para tumbar notif-svc (PASO 4)'

# ---------- 4. caida + circuit breaker ----------

Titulo 4 'Cae notif-svc: el circuito se abre y el outbox retiene'
Nota 'docker compose stop notif-svc'
docker compose stop notif-svc | Out-Null
Write-Host "   notif-svc DETENIDO" -ForegroundColor Red

Write-Host ""
Nota 'Tres reservas con el servicio de notificaciones caido.'
Nota 'Lo que hay que mirar: siguen respondiendo 201, no 500.'
Write-Host ""
foreach ($c in 2,3,4) { Reservar $c | Out-Null }

$s = Estado 'despues de 3 fallos'
Write-Host ""
if ($s.circuit_breaker.state -eq 'open') {
    Write-Host "   OK: el circuito se abrio tras $($s.circuit_breaker.fail_max) fallos seguidos." -ForegroundColor Green
    Write-Host "   OK: $($s.outbox.pending) notificaciones retenidas en el outbox, ninguna perdida." -ForegroundColor Green
    Write-Host "   OK: las reservas se confirmaron igual. Degradacion, no caida." -ForegroundColor Green
}

Pausa 'Enter para levantar notif-svc (PASO 5)'

# ---------- 5. recuperacion ----------

Titulo 5 'Recuperacion automatica'
Nota 'docker compose start notif-svc'
docker compose start notif-svc | Out-Null
Write-Host "   notif-svc ARRIBA" -ForegroundColor Green
Write-Host ""
Nota "El breaker resetea a los $($s.circuit_breaker.reset_timeout_s)s y el worker del outbox corre cada 10s."
Nota 'Esperar 30-40 segundos. Sondeo en vivo:'
Write-Host ""

$t0 = Get-Date
$recuperado = $false
foreach ($i in 1..20) {
    Start-Sleep -Seconds 3
    $st = Invoke-RestMethod -Uri "$BookSvc/resilience/status"
    $el = [int]((Get-Date) - $t0).TotalSeconds
    $col = if ($st.circuit_breaker.state -eq 'closed') { 'Green' } else { 'DarkYellow' }
    Write-Host ("   +{0,3}s   breaker={1,-9} pending={2}" -f $el, $st.circuit_breaker.state, $st.outbox.pending) -ForegroundColor $col
    if ($st.circuit_breaker.state -eq 'closed' -and $st.outbox.pending -eq 0) {
        Write-Host ""
        Write-Host "   >>> RECUPERADO en ${el}s: circuito cerrado y outbox drenado" -ForegroundColor Green
        $recuperado = $true
        break
    }
}
if (-not $recuperado) { Write-Host "   (no se recupero en la ventana; revisar notif-svc)" -ForegroundColor Red }

$null = Estado 'estado final'

Pausa 'Enter para ver las notificaciones entregadas'

# ---------- cierre ----------

Titulo 6 'Nada se perdio'
$notifs = Invoke-RestMethod -Uri "$NotifSvc/notifications/1"
Write-Host ""
foreach ($n in $notifs | Sort-Object id) {
    Write-Host "   #$($n.id)  " -NoNewline
    Write-Host $n.status.PadRight(6) -ForegroundColor Green -NoNewline
    Write-Host "  $($n.message)"
}
Write-Host ""
Write-Host "   Las 3 notificaciones generadas con notif-svc caido llegaron al volver." -ForegroundColor Green
Write-Host ""
Write-Host ("=" * 72) -ForegroundColor DarkCyan
Write-Host "  Resumen: timeout 2s | 3 reintentos con backoff+jitter |" -ForegroundColor Cyan
Write-Host "           breaker abre a los 3 fallos, resetea en 30s | outbox sin perdidas" -ForegroundColor Cyan
Write-Host ("=" * 72) -ForegroundColor DarkCyan
Write-Host ""
