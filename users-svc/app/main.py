import logging
import os

import requests
from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import engine, Base, get_db
from app import models, schemas, auth

Base.metadata.create_all(bind=engine)
app = FastAPI(title="users-svc")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("users-svc")

def get_env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value


def get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid value for %s=%s. Using default=%s", name, value, default)
        return default


CONSUL_HOST = get_env_str("CONSUL_HOST", "consul")
CONSUL_PORT = get_env_int("CONSUL_PORT", 8500)
CONSUL_SERVICE_NAME = get_env_str("CONSUL_SERVICE_NAME", "users-svc")
CONSUL_SERVICE_ID = get_env_str("CONSUL_SERVICE_ID", "users-svc-8003")
CONSUL_SERVICE_ADDRESS = get_env_str("CONSUL_SERVICE_ADDRESS", "users-svc")
CONSUL_SERVICE_PORT = get_env_int("CONSUL_SERVICE_PORT", 8003)
CONSUL_HEALTH_PATH = get_env_str("CONSUL_HEALTH_PATH", "/healthz")


def register_service_in_consul() -> None:
    consul_url = f"http://{CONSUL_HOST}:{CONSUL_PORT}/v1/agent/service/register"
    health_url = f"http://{CONSUL_SERVICE_ADDRESS}:{CONSUL_SERVICE_PORT}{CONSUL_HEALTH_PATH}"
    payload = {
        "Name": CONSUL_SERVICE_NAME,
        "ID": CONSUL_SERVICE_ID,
        "Address": CONSUL_SERVICE_ADDRESS,
        "Port": CONSUL_SERVICE_PORT,
        "Check": {
            "HTTP": health_url,
            "Interval": "10s",
            "DeregisterCriticalServiceAfter": "30s",
        },
    }

    try:
        response = requests.put(consul_url, json=payload, timeout=3)
        response.raise_for_status()
        logger.info("users-svc registered in Consul with id=%s", CONSUL_SERVICE_ID)
    except requests.RequestException as exc:
        # users-svc must still boot even if Consul is temporarily unavailable.
        logger.warning("Consul registration failed: %s", exc)


def deregister_service_in_consul() -> None:
    consul_url = (
        f"http://{CONSUL_HOST}:{CONSUL_PORT}/v1/agent/service/deregister/{CONSUL_SERVICE_ID}"
    )
    try:
        response = requests.put(consul_url, timeout=3)
        response.raise_for_status()
        logger.info("users-svc deregistered from Consul with id=%s", CONSUL_SERVICE_ID)
    except requests.RequestException as exc:
        logger.warning("Consul deregistration failed: %s", exc)


@app.on_event("startup")
def on_startup() -> None:
    register_service_in_consul()


@app.on_event("shutdown")
def on_shutdown() -> None:
    deregister_service_in_consul()

@app.get("/healthz")
def healthz():
    return {"status": "ok"}  #

@app.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))  # Validar conexión real a BD[cite: 1]
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=500, detail="Database connection failed")

@app.post("/users/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: schemas.UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == user_data.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_pwd = auth.hash_password(user_data.password)
    new_user = models.User(email=user_data.email, hashed_password=hashed_pwd, full_name=user_data.full_name)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@app.post("/users/login")
def login(credentials: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == credentials.email).first()
    if not user or not auth.verify_password(credentials.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = auth.create_access_token(user_id=user.id, email=user.email)
    return {"access_token": token, "token_type": "bearer"}

@app.get("/users/{user_id}", response_model=schemas.UserResponse)
def get_user_profile(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user