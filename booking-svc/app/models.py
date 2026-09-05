from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from app.database import Base

class FitnessClass(Base):
    __tablename__ = "classes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    instructor = Column(String, nullable=False)
    schedule = Column(DateTime(timezone=True), nullable=False)
    capacity = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    # los usuarios viven en la BD de users-svc; no hay FK entre bases de datos
    user_id = Column(Integer, nullable=False, index=True)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default="confirmed")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class NotificationOutbox(Base):
    """Outbox pattern (Task 3A).

    Cada reserva creada o cancelada deja aqui la notificacion que debe llegar a
    notif-svc. Se escribe en la misma transaccion que la reserva, asi la
    notificacion nunca se pierde aunque notif-svc este caido.
    """
    __tablename__ = "notification_outbox"

    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    message = Column(String, nullable=False)
    # pending: falta entregar a notif-svc; sent: notif-svc la acepto.
    status = Column(String, nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(String, nullable=True)
    # Se conserva para propagar el mismo x-correlation-id en entregas diferidas.
    correlation_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=True)
