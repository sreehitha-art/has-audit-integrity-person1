from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PatientCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    date_of_birth: datetime | None = None
    gender: str | None = Field(default=None, max_length=30)
    phone: str | None = Field(default=None, max_length=20)


class PatientUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=150)
    date_of_birth: datetime | None = None
    gender: str | None = Field(default=None, max_length=30)
    phone: str | None = Field(default=None, max_length=20)


class PatientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    date_of_birth: datetime | None
    gender: str | None
    phone: str | None
    created_at: datetime


class AppointmentCreate(BaseModel):
    patient_id: int = Field(gt=0)
    scheduled_at: datetime
    status: str = Field(default="scheduled", max_length=30)
    notes: str | None = None


class AppointmentUpdate(BaseModel):
    scheduled_at: datetime | None = None
    status: str | None = Field(default=None, max_length=30)
    notes: str | None = None


class AppointmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    scheduled_at: datetime
    status: str
    notes: str | None
    created_at: datetime