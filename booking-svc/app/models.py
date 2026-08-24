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
