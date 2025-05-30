# my-english-corrector-backend/main.py
import os
import uuid
from datetime import datetime, timezone # Importado para created_at/updated_at si no estuviera ya en models
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, SQLModel, create_engine, select
from dotenv import load_dotenv

from supabase import create_client, Client as SupabaseClient

# Importar utilidades de autenticación y modelos de payload
from auth_utils import get_current_user, get_current_user_id, TokenPayload # Asegúrate que la importación sea absoluta
# Importar los modelos
import models # Asegúrate que la importación sea absoluta

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR CRÍTICO: DATABASE_URL no está configurada. La aplicación no funcionará.")
    exit()
engine = create_engine(DATABASE_URL, echo=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY_FROM_ENV = os.getenv("SUPABASE_SERVICE_ROLE_KEY") # Corregido el nombre de la variable

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY_FROM_ENV:
    print("ERROR CRÍTICO: SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY no están configuradas. Funcionalidades como la subida de archivos fallarán.")
    supabase_admin_client: SupabaseClient | None = None
else:
    supabase_admin_client: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY_FROM_ENV)

if not os.getenv("SUPABASE_JWT_SECRET"):
    print("ADVERTENCIA: SUPABASE_JWT_SECRET no parece estar en .env. auth_utils.py podría fallar o la validación de JWT fallará.")

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

app = FastAPI(
    title="English Corrector API",
    description="API for the AI-powered English essay correction assistant.",
    version="0.1.0",
)

origins = [
    "http://localhost:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    print("Ejecutando evento de startup: Creando tablas en la base de datos si no existen...")
    create_db_and_tables()
    print("Evento de startup completado.")

def get_session():
    with Session(engine) as session:
        yield session

@app.get("/")
async def read_root():
    return {"message": "API del Corrector de Inglés lista y funcionando!"}

@app.post("/test_items/", response_model=models.TestItemRead)
async def create_test_item(item_data: models.TestItemCreate, session: Session = Depends(get_session)):
    db_item = models.TestItem.model_validate(item_data)
    session.add(db_item)
    session.commit()
    session.refresh(db_item)
    return db_item

@app.get("/test_items/", response_model=list[models.TestItemRead])
async def read_test_items(skip: int = 0, limit: int = 100, session: Session = Depends(get_session)):
    statement = select(models.TestItem).offset(skip).limit(limit)
    items = session.exec(statement).all()
    return items

@app.get("/test_items/{item_id}", response_model=models.TestItemRead)
async def read_test_item(item_id: int, session: Session = Depends(get_session)):
    item = session.get(models.TestItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"TestItem with id {item_id} not found")
    return item
    
@app.get("/users/me/", response_model=TokenPayload)
async def read_users_me(current_user: TokenPayload = Depends(get_current_user)):
    return current_user

@app.get("/protected-route-example/")
async def protected_route_example(user_id: str = Depends(get_current_user_id)):
    return {"message": "Ruta protegida accedida con éxito!", "user_id_from_token": user_id}

EXAM_IMAGES_BUCKET = "exam-images"

@app.post("/exam_papers/upload_image/", response_model=models.ExamPaperRead)
async def upload_exam_image(
    file: UploadFile = File(...),
    current_auth_user: TokenPayload = Depends(get_current_user), # Usar get_current_user para tener el email
    session: Session = Depends(get_session)
):
    user_id = current_auth_user.sub
    user_email = current_auth_user.email

    if not supabase_admin_client:
        raise HTTPException(status_code=503, detail="El servicio de almacenamiento no está configurado correctamente en el servidor.")

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="El archivo subido no es una imagen válida.")

    try:
        # --- INICIO: Lógica para asegurar que el usuario exista en la tabla 'user' local ---
        db_user = session.get(models.User, user_id)
        if not db_user:
            print(f"Usuario con ID {user_id} no encontrado en la tabla local 'user'. Creándolo...")
            if not user_email: # Aunque TokenPayload lo tiene como opcional, Supabase Auth siempre debería proveerlo
                 print(f"ADVERTENCIA: No se pudo obtener el email del token para el usuario {user_id}. Se creará sin email si el modelo lo permite o con None.")
            
            # Crear el nuevo usuario en nuestra base de datos local
            new_db_user_data = models.UserCreate(id=user_id, email=user_email, credits=0) # Asignar créditos iniciales (ej. 0)
            db_user = models.User.model_validate(new_db_user_data) # Validar y crear la instancia del modelo de tabla
            session.add(db_user)
            # No hacer commit aquí, se hará al final con la creación del ExamPaper
            print(f"Usuario local {user_id} (Email: {user_email}) preparado para ser añadido a la sesión.")
        # --- FIN: Lógica para asegurar que el usuario exista en la tabla 'user' local ---

        original_filename = file.filename if file.filename else "unknown_image"
        file_extension = original_filename.split(".")[-1].lower() if "." in original_filename else "png"
        
        if file_extension not in ["jpg", "jpeg", "png", "gif", "webp"]: # Validación básica de extensión
            raise HTTPException(status_code=400, detail=f"Extensión de archivo no permitida: .{file_extension}")

        unique_filename = f"{uuid.uuid4()}.{file_extension}"
        path_on_storage = f"{user_id}/{unique_filename}" # Organizar por user_id

        contents = await file.read()
        
        print(f"Subiendo archivo a Supabase Storage: bucket='{EXAM_IMAGES_BUCKET}', path='{path_on_storage}'")
        
        # El cliente supabase_admin_client ya está inicializado arriba
        response = supabase_admin_client.storage.from_(EXAM_IMAGES_BUCKET).upload(
            path=path_on_storage,
            file=contents,
            file_options={"content-type": file.content_type, "cache-control": "3600"}
        )
        
        # La API de supabase-py (versión 1.x y 2.x tempranas) puede lanzar una excepción directamente si falla.
        # En versiones más nuevas, el objeto de respuesta podría necesitar ser inspeccionado.
        # Asumimos que si no hay excepción, fue exitoso.

        if SUPABASE_URL is None: # Chequeo por si acaso, aunque ya se verifica arriba
             raise HTTPException(status_code=500, detail="SUPABASE_URL no está configurada para construir la URL de la imagen.")

        image_public_url = f"{SUPABASE_URL}/storage/v1/object/public/{EXAM_IMAGES_BUCKET}/{path_on_storage}"
        print(f"Archivo subido. URL pública: {image_public_url}")

        db_exam_paper_data = models.ExamPaperCreate(
            filename=original_filename,
            image_url=image_public_url,
            status="uploaded",
            user_id=user_id,
        )
        db_exam_paper = models.ExamPaper.model_validate(db_exam_paper_data)
        session.add(db_exam_paper)
        
        session.commit() # Commit de usuario (si es nuevo) y exam_paper juntos
        
        # Refrescar para obtener los valores generados por la BD (como IDs, created_at)
        if not session.get(models.User, user_id): # Si el usuario fue recién añadido y no lo teníamos como db_user
             temp_user = session.get(models.User, user_id)
             if temp_user: session.refresh(temp_user) # Refrescar solo si se encuentra
        elif db_user: # Si ya existía, db_user es el objeto correcto.
             session.refresh(db_user)


        session.refresh(db_exam_paper)
        print(f"Registro de ExamPaper creado en BD con ID: {db_exam_paper.id} para el usuario {user_id}")

        return db_exam_paper

    except HTTPException: # Re-lanzar HTTPExceptions para que FastAPI las maneje
        raise
    except Exception as e:
        if session.is_active: # Solo hacer rollback si la sesión está activa
            session.rollback() 
        print(f"Error detallado durante la subida de imagen o creación de registro: {type(e).__name__} - {e}")
        raise HTTPException(status_code=500, detail=f"Ocurrió un error interno al procesar el archivo: {str(e)}")
    finally:
        if file: # Si el objeto file fue asignado
            # No es necesario verificar file.closed, solo intentar cerrar.
            # El método close() de UploadFile es asíncrono.
            await file.close()
            print("Archivo UploadFile cerrado en el bloque finally.")