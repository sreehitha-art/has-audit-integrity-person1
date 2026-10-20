from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Appointment, Patient
from app.schemas import (
    AppointmentCreate,
    AppointmentResponse,
    AppointmentUpdate,
)

router = APIRouter(prefix="/appointments", tags=["Appointments"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post(
    "/",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_appointment(
    data: AppointmentCreate,
    db: Session = Depends(get_db),
):
    if db.get(Patient, data.patient_id) is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    appointment = Appointment(**data.model_dump())
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    return appointment


@router.get("/", response_model=list[AppointmentResponse])
def list_appointments(db: Session = Depends(get_db)):
    return db.scalars(
        select(Appointment).order_by(Appointment.id)
    ).all()


@router.get("/{appointment_id}", response_model=AppointmentResponse)
def get_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
):
    appointment = db.get(Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return appointment


@router.patch("/{appointment_id}", response_model=AppointmentResponse)
def update_appointment(
    appointment_id: int,
    data: AppointmentUpdate,
    db: Session = Depends(get_db),
):
    appointment = db.get(Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(appointment, field, value)

    db.commit()
    db.refresh(appointment)
    return appointment


@router.delete(
    "/{appointment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
):
    appointment = db.get(Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found")

    db.delete(appointment)
    db.commit()
    return None