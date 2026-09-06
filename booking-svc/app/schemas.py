from datetime import datetime
from typing import Optional
from pydantic import BaseModel

class ClassResponse(BaseModel):
    id: int
    name: str
    instructor: str
    schedule: datetime
    capacity: int
    class Config:
        from_attributes = True

class BookingCreate(BaseModel):
    class_id: int

class BookingResponse(BaseModel):
    id: int
    user_id: int
    class_id: int
    status: str
    created_at: datetime
    # "sent" si notif-svc recibio la notificacion; "pending" si quedo en el outbox.
    notification_status: Optional[str] = None
    class Config:
        from_attributes = True

class OutboxEntryResponse(BaseModel):
    id: int
    booking_id: int
    user_id: int
    message: str
    status: str
    attempts: int
    last_error: Optional[str] = None
    correlation_id: Optional[str] = None
    created_at: datetime
    sent_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class CircuitBreakerStatus(BaseModel):
    name: str
    state: str
    fail_counter: int
    fail_max: int
    reset_timeout_s: int
    timeout_s: float
    max_retries: int

class DiscoveryStatus(BaseModel):
    service: str
    url: Optional[str] = None
    source: Optional[str] = None
    error: Optional[str] = None

class ResilienceStatus(BaseModel):
    circuit_breaker: CircuitBreakerStatus
    outbox: dict
    discovery: DiscoveryStatus

class OutboxFlushResult(BaseModel):
    sent: int
    deferred: int
    pending: int
    breaker_state: str
