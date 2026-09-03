import os
import jwt
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET_KEY = os.getenv("JWT_SECRET")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# El servicio no debe iniciar con una clave conocida o definida en el codigo.
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET must be configured in the environment")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(user_id: int, email: str) -> str:
    # user_id identifica al usuario que booking-svc debe autorizar.
    payload = {
        "user_id": user_id,
        "sub": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=8)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)