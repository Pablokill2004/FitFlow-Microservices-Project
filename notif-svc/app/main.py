import logging
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import engine, Base, get_db
from app import models, schemas

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)
app = FastAPI(title="notif-svc")

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

@app.post("/notifications", response_model=schemas.NotificationResponse, status_code=201)
def send_notification(notification: schemas.NotificationCreate, db: Session = Depends(get_db)):
    new_notification = models.Notification(
        user_id=notification.user_id,
        message=notification.message,
        status="sent",
    )
    db.add(new_notification)
    db.commit()
    db.refresh(new_notification)
    logger.info("notification sent to user_id=%s: %s", new_notification.user_id, new_notification.message)
    return new_notification

@app.get("/notifications/{user_id}", response_model=list[schemas.NotificationResponse])
def get_notification_history(user_id: int, db: Session = Depends(get_db)):
    return db.query(models.Notification).filter(models.Notification.user_id == user_id).order_by(models.Notification.created_at.desc()).all()
