import logging
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import engine, Base, get_db, SessionLocal
from app import models, schemas
from app.auth import get_current_user_id

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)
app = FastAPI(title="booking-svc")

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
        logger.info("seeded %d fitness classes", len(classes))
    finally:
        db.close()

@app.on_event("startup")
def on_startup():
    seed_classes()
    # Checkpoint 2 (Task 2A): registrar el servicio en Consul aqui

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
    # Checkpoint 2 (Task 3A): notificar a notif-svc aqui
    logger.info("booking created id=%s user_id=%s class_id=%s", new_booking.id, user_id, booking.class_id)
    return new_booking

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
    # Checkpoint 2 (Task 3A): notificar a notif-svc aqui
    logger.info("booking cancelled id=%s user_id=%s", booking.id, user_id)
    return booking
