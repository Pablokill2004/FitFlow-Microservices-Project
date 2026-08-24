from datetime import datetime
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
    class Config:
        from_attributes = True
