# my-english-corrector-backend/main.py
import os
import uuid # Para generar nombres de archivo únicos si es necesario
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, SQLModel, create_engine, select
from dotenv import load_dotenv

# Importar el cliente de Supabase para Storage
from supabase import create_client, Client as SupabaseClient # Renombrar Client a SupabaseClient para evitar colisión con otros "Client"

# Importar utilidades de autenticación y modelos de payload (USANDO IMPORTACIONES ABSOLUTAS)
from auth_utils import get_current_user, get_current_user_id, TokenPayload
# Importar los modelos desde models.py (USANDO IMPORTACIÓN ABSOLUTA)
import models

# Cargar variables de entorno desde .env
load_dotenv()

# --- Configuración de Base de Datos ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR CRÍTICO: DATABASE_URL no está configurada. La aplicación no funcionará.")
    exit()
engine = create_engine(DATABASE_URL, echo=True)

# --- Configuración del Cliente de Supabase (para Storage y otras interacciones de backend) ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("ERROR CRÍTICO: SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY no están configuradas. Funcionalidades como la subida de archivos fallarán.")
    # Considerar no iniciar la app si estas son críticas desde el inicio.
    # Por ahora, se permite iniciar pero las funciones dependientes fallarán.
    supabase_admin_client: SupabaseClient | None = None # Establecer como None si no se puede inicializar
else:
    supabase_admin_client: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Verificar que SUPABASE_JWT_SECRET está en .env para auth_utils.py
if not os.getenv("SUPABASE_JWT_SECRET"):
    print("ADVERTENCIA: SUPABASE_JWT_SECRET no parece estar en .env. auth_utils.py podría fallar al cargar o la validación de JWT fallará.")


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

# --- FastAPI app instance ---
app = FastAPI(
    title="English Corrector API",
    description="API for the AI-powered English essay correction assistant.",
    version="0.1.0",
)

# --- Configuración de CORS ---
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

# --- Evento de Startup ---
@app.on_event("startup")
def on_startup():
    print("Ejecutando evento de startup: Creando tablas en la base de datos si no existen...")
    create_db_and_tables()
    print("Evento de startup completado.")

# --- Dependencia para la Sesión de Base de Datos ---
def get_session():
    with Session(engine) as session:
        yield session

# --- Endpoints Públicos / de Prueba ---
@app.get("/")
async def read_root():
    return {"message": "API del Corrector de Inglés lista y funcionando!"}

# --- Endpoints de TestItem (Mantener o eliminar según necesidad) ---
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
    
# --- Endpoints Protegidos de Autenticación (Ejemplos) ---
@app.get("/users/me/", response_model=TokenPayload)
async def read_users_me(current_user: TokenPayload = Depends(get_current_user)):
    return current_user

@app.get("/protected-route-example/")
async def protected_route_example(user_id: str = Depends(get_current_user_id)):
    return {"message": "Ruta protegida accedida con éxito!", "user_id_from_token": user_id}

# --- Endpoints para ExamPapers ---

# Nombre del bucket de Supabase Storage para las imágenes de los exámenes
EXAM_IMAGES_BUCKET = "exam-images" # Asegúrate de que este nombre coincida con el bucket que creaste en Supabase

@app.post("/exam_papers/upload_image/", response_model=models.ExamPaperRead)
async def upload_exam_image(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session)
):
    if not supabase_admin_client: # Verificar que el cliente de Supabase se inicializó
        raise HTTPException(status_code=500, detail="El cliente de Supabase no está configurado en el servidor.")

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="El archivo subido no es una imagen válida.")

    try:
        original_filename = file.filename if file.filename else "unknown_image"
        file_extension = original_filename.split(".")[-1] if "." in original_filename else "png"
        
        # Crear un nombre de archivo único para evitar colisiones y organizar por usuario
        # Ejemplo: "user_abc123/random_uuid_string.png"
        unique_filename = f"{uuid.uuid4()}.{file_extension}"
        path_on_storage = f"{user_id}/{unique_filename}"

        contents = await file.read()

        print(f"Subiendo archivo a Supabase Storage: bucket='{EXAM_IMAGES_BUCKET}', path='{path_on_storage}'")
        
        upload_response = supabase_admin_client.storage.from_(EXAM_IMAGES_BUCKET).upload(
            path=path_on_storage,
            file=contents,
            file_options={"content-type": file.content_type, "cache-control": "3600"} # Cache por 1 hora
        )
        
        # La API de supabase-py para upload devuelve None si es exitoso o lanza una excepción si falla.
        # Necesitamos construir la URL pública.
        
        if SUPABASE_URL is None: # Doble chequeo por si acaso
             raise HTTPException(status_code=500, detail="SUPABASE_URL no está configurada.")

        image_public_url = f"{SUPABASE_URL}/storage/v1/object/public/{EXAM_IMAGES_BUCKET}/{path_on_storage}"
        print(f"Archivo subido. URL pública: {image_public_url}")

        # Crear el registro en la base de datos
        db_exam_paper_data = models.ExamPaperCreate(
            filename=original_filename, # Guardar el nombre original del archivo
            image_url=image_public_url,
            status="uploaded",
            user_id=user_id,
            # Los campos de créditos consumidos tendrán su valor por defecto (0)
        )
        db_exam_paper = models.ExamPaper.model_validate(db_exam_paper_data)
        
        session.add(db_exam_paper)
        session.commit()
        session.refresh(db_exam_paper)
        print(f"Registro de ExamPaper creado en BD con ID: {db_exam_paper.id}")

        return db_exam_paper

    except Exception as e:
        print(f"Error detallado durante la subida de imagen o creación de registro: {type(e).__name__} - {e}")
        # Para StorageApiError de Supabase, puedes acceder a e.args[0]['message'] o similar.
        # if hasattr(e, 'args') and e.args and isinstance(e.args[0], dict):
        #     error_message = e.args[0].get('message', str(e))
        # else:
        #     error_message = str(e)
        raise HTTPException(status_code=500, detail=f"Ocurrió un error al procesar el archivo: {str(e)}")
    finally:
        if file: # Asegurarse de que file no es None
            await file.close()

# @app.get("/exam_papers/", response_model=list[models.ExamPaperRead])
# async def list_exam_papers_for_user(...): # Se desarrollará en Subfase 2.4
#     pass