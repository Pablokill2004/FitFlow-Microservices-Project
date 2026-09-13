"""Autenticacion del caller (Task 4.1).

El JWT no llega en un header Authorization como en booking-svc, sino en el
campo access_token del propio plan (OrchestrateRequest), asi que este modulo
replica el mismo decode y el mismo mapeo a 401 de booking-svc/app/auth.py
pero leyendo el token desde ahi. Debe correr ANTES de validar el plan o de
llamar a cualquier agente (spec: "Caller authentication and identity").
"""

import os

import jwt
from fastapi import HTTPException

from observability import user_id_context

SECRET_KEY = os.getenv("JWT_SECRET")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# El orchestrator comparte la clave de firma con el resto de FitFlow.
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET must be configured in the environment")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def authenticate(access_token: str) -> int:
    """Decodifica access_token y devuelve el user_id, o levanta 401.

    Cubre token ausente/vacio, malformado y expirado, y un user_id que no sea
    int -- exactamente los casos que booking-svc rechaza. Deja el user_id en
    el contextvar de observabilidad para que los logs del request lo lleven.
    """
    if not access_token:
        raise _unauthorized("Missing access token")
    try:
        payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token expired")
    except jwt.InvalidTokenError:
        raise _unauthorized("Invalid token")
    user_id = payload.get("user_id")
    if not isinstance(user_id, int):
        raise _unauthorized("Invalid token")
    user_id_context.set(user_id)
    return user_id
