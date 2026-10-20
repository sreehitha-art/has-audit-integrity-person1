from fastapi import FastAPI

from app.routers.patients import router as patients_router
from app.routers.appointments import router as appointments_router
app = FastAPI(
    title="Hospital Management System API",
    description="HMS backend for patient and appointment management",
    version="1.0.0",
)


@app.get("/")
def home():
    return {
        "message": "Hospital Management System API is running",
        "status": "success",
    }


@app.get("/health")
def health_check():
    return {"status": "healthy"}


app.include_router(patients_router)
app.include_router(appointments_router)