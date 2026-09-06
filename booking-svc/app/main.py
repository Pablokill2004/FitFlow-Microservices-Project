from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.observability import correlation_middleware, get_correlation_id, logger
from app.database import engine, Base, get_db, SessionLocal
from app import models, schemas, discovery, resilience
from app.auth import get_current_user_id

Base.metadata.create_all(bind=engine)
app = FastAPI(title="booking-svc")
app.middleware("http")(correlation_middleware)

def seed_classes():
    db = SessionLocal()
    try:
        if db.query(models.FitnessClass).count() > 0:
            return
        now = datetime.now(timezone.utc)
        classes = [
            models.FitnessClass(name="Yoga", instructor="Ana Lopez", schedule=now + timedelta(days=1), capacity=10),
            models.FitnessClass(name="Spinning", instructor="Carlos Perez", schedule=now + timedelta(days=1, hours=2), capacity=15),
            models.FitnessClass(name="Crossfit", instructor="Maria Garcia", schedule=now + timedelta(days=2), capacity=8),
            models.FitnessClass(name="Pilates", instructor="Jorge Ramos", schedule=now + timedelta(days=3), capacity=12),
            models.FitnessClass(name="Zumba", instructor="Lucia Mendez", schedule=now + timedelta(days=4), capacity=2),
        ]
        db.add_all(classes)
        db.commit()
        logger.info("classes_seeded", extra={"count": len(classes)})
    finally:
        db.close()

@app.on_event("startup")
def on_startup():
    seed_classes()
    discovery.register_service()          # Task 2A
    resilience.outbox_worker.start()      # Task 3A

@app.on_event("shutdown")
def on_shutdown():
    resilience.outbox_worker.stop()
    discovery.deregister_service()

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=500, detail="Database connection failed")

@app.get("/classes", response_model=list[schemas.ClassResponse])
def list_classes(db: Session = Depends(get_db)):
    return db.query(models.FitnessClass).order_by(models.FitnessClass.schedule).all()

def _queue_and_deliver(db: Session, booking: models.Booking, message: str) -> str:
    """Escribe la notificacion en el outbox y trata de entregarla ahora.

    La fila del outbox se confirma junto con la reserva. Si notif-svc falla o
    el circuito esta abierto, la fila queda "pending" y el hilo del outbox la
    reintenta despues. La reserva nunca falla por culpa de notif-svc.
    """
    entry = models.NotificationOutbox(
        booking_id=booking.id,
        user_id=booking.user_id,
        message=message,
        correlation_id=get_correlation_id(),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return resilience.deliver(db, entry)

def _with_notification(booking: models.Booking, notification_status: str) -> schemas.BookingResponse:
    response = schemas.BookingResponse.model_validate(booking)
    response.notification_status = notification_status
    return response

@app.post("/bookings", response_model=schemas.BookingResponse, status_code=201)
def create_booking(
    booking: schemas.BookingCreate,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    fitness_class = db.query(models.FitnessClass).filter(models.FitnessClass.id == booking.class_id).first()
    if fitness_class is None:
        raise HTTPException(status_code=404, detail="Class not found")
    confirmed_count = (
        db.query(models.Booking)
        .filter(models.Booking.class_id == booking.class_id, models.Booking.status == "confirmed")
        .count()
    )
    if confirmed_count >= fitness_class.capacity:
        raise HTTPException(status_code=400, detail="Class is full")
    duplicate = (
        db.query(models.Booking)
        .filter(
            models.Booking.class_id == booking.class_id,
            models.Booking.user_id == user_id,
            models.Booking.status == "confirmed",
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=400, detail="User already has an active booking for this class")
    new_booking = models.Booking(user_id=user_id, class_id=booking.class_id, status="confirmed")
    db.add(new_booking)
    db.commit()
    db.refresh(new_booking)
    logger.info(
        "booking_created",
        extra={"booking_id": new_booking.id, "user_id": user_id, "class_id": booking.class_id},
    )
    message = f"Tu reserva #{new_booking.id} de {fitness_class.name} fue confirmada"
    notification_status = _queue_and_deliver(db, new_booking, message)
    return _with_notification(new_booking, notification_status)

@app.get("/bookings/{booking_id}", response_model=schemas.BookingResponse)
def get_booking(booking_id: int, db: Session = Depends(get_db)):
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return booking

@app.delete("/bookings/{booking_id}", response_model=schemas.BookingResponse)
def cancel_booking(
    booking_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.user_id != user_id:
        raise HTTPException(status_code=403, detail="Booking belongs to another user")
    if booking.status == "cancelled":
        raise HTTPException(status_code=400, detail="Booking already cancelled")
    booking.status = "cancelled"
    db.commit()
    db.refresh(booking)
    logger.info("booking_cancelled", extra={"booking_id": booking.id, "user_id": user_id})
    fitness_class = db.query(models.FitnessClass).filter(models.FitnessClass.id == booking.class_id).first()
    class_name = fitness_class.name if fitness_class else f"clase {booking.class_id}"
    message = f"Tu reserva #{booking.id} de {class_name} fue cancelada"
    notification_status = _queue_and_deliver(db, booking, message)
    return _with_notification(booking, notification_status)

# --- Observabilidad de la resiliencia (Task 2A / 3A) -------------------------

@app.get("/resilience/status", response_model=schemas.ResilienceStatus)
def resilience_status(db: Session = Depends(get_db)):
    """Estado del circuit breaker, del outbox y de la resolucion via Consul."""
    try:
        url, source = discovery.resolve_notif_svc_url()
        discovery_status = schemas.DiscoveryStatus(service=discovery.NOTIF_SERVICE_NAME, url=url, source=source)
    except discovery.ServiceUnavailable as exc:
        discovery_status = schemas.DiscoveryStatus(service=discovery.NOTIF_SERVICE_NAME, error=str(exc))
    return schemas.ResilienceStatus(
        circuit_breaker=schemas.CircuitBreakerStatus(**resilience.breaker_status()),
        outbox=resilience.outbox_counts(db),
        discovery=discovery_status,
    )

@app.get("/resilience/outbox", response_model=list[schemas.OutboxEntryResponse])
def list_outbox(
    status: Optional[str] = Query(default=None, pattern="^(pending|sent)$"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(models.NotificationOutbox)
    if status is not None:
        query = query.filter(models.NotificationOutbox.status == status)
    return query.order_by(models.NotificationOutbox.id.desc()).limit(limit).all()

@app.post("/resilience/outbox/flush", response_model=schemas.OutboxFlushResult)
def flush_outbox_now():
    """Fuerza una ronda de reintentos sin esperar al hilo en segundo plano."""
    return resilience.flush_outbox()
