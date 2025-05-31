# main.py (VERSIÓN DE PRUEBA SIMPLIFICADA)
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text # Usaremos text para una query simple
from sqlalchemy.exc import OperationalError

load_dotenv()
print("Variables de entorno cargadas (o intento realizado).")

DATABASE_URL = os.getenv("DATABASE_URL")
print(f"DATABASE_URL leída: {DATABASE_URL}") # Imprime para verificar

if not DATABASE_URL:
    print("ERROR CRÍTICO: DATABASE_URL no está configurada en .env")
    exit()

try:
    print(f"Intentando crear engine con: {DATABASE_URL}")
    engine = create_engine(DATABASE_URL, echo=True) # echo=True nos dará logs de SQLAlchemy
    
    print("Engine creado. Intentando conectar...")
    with engine.connect() as connection:
        print("¡Conexión exitosa a la base de datos!")
        result = connection.execute(text("SELECT version();")) # Query simple
        version = result.scalar_one()
        print(f"Versión de PostgreSQL: {version}")
        connection.commit() # Aunque sea un select, algunos drivers/pools lo esperan
    print("Conexión cerrada.")

except OperationalError as e:
    print("----------------------------------------------------")
    print("ERROR DE CONEXIÓN OPERACIONAL DE SQLALCHEMY:")
    print(f"Detalles del error: {e}")
    print("Verifica:")
    print("1. Que la DATABASE_URL en .env sea correcta (host, puerto, usuario, contraseña, nombre_db).")
    print("2. Que la contraseña no tenga caracteres especiales problemáticos (o esté bien codificada si los tiene).")
    print("3. Que no haya firewalls locales bloqueando la conexión al puerto 5432.")
    print("4. Que no haya IPs baneadas en Supabase.")
    print("----------------------------------------------------")

except Exception as e:
    print("----------------------------------------------------")
    print(f"OCURRIÓ UN ERROR INESPERADO: {type(e).__name__}")
    print(f"Detalles: {e}")
    print("----------------------------------------------------")

# Para que Uvicorn no se queje, definimos una app FastAPI mínima
from fastapi import FastAPI
app = FastAPI()
@app.get("/")
async def root():
    return {"message": "App de prueba de conexión FastAPI"}