# main.py
from fastapi import FastAPI

app = FastAPI(
    title="English Corrector API",
    description="API for the AI-powered English essay correction assistant.",
    version="0.1.0"
)

@app.get("/")
async def read_root():
    return {"message": "Hola Mundo desde el Backend FastAPI!"}

# Para probar, puedes añadir otro endpoint
@app.get("/api/v1/hello")
async def سے_hello():
    return {"greeting": "Hello from API v1"}