import os
import jwt
from fastapi import Header, HTTPException, Request

from app.observability import user_id_context

SECRET_KEY = os.getenv("JWT_SECRET")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# booking-svc debe compartir la clave configurada con users-svc.
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET must be configured in the environment")

def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})

# Es async a proposito: asi corre en el contexto del request y el user_id que
# deja en el contextvar llega a los logs del endpoint (Task 4A).
async def get_current_user_id(request: Request, authorization: str = Header(None)) -> int:
    if authorization is None or not authorization.startswith("Bearer "):
        raise _unauthorized("Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token expired")
    except jwt.InvalidTokenError:
        raise _unauthorized("Invalid token")
    user_id = payload.get("user_id")
    if not isinstance(user_id, int):
        raise _unauthorized("Invalid token")
    user_id_context.set(user_id)
    request.state.user_id = user_id
    return user_id
