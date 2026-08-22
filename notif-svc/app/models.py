from sqlalchemy import Column, Integer, String, DateTime, func
from app.database import Base

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    message = Column(String, nullable=False)
    status = Column(String, nullable=False, default="sent")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
