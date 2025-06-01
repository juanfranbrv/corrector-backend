# my-english-corrector-backend/main.py
import os
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status as http_status
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, SQLModel, create_engine, select, func # func para count
from dotenv import load_dotenv
from urllib.parse import urlparse

from supabase import create_client, Client as SupabaseClient

from auth_utils import get_current_user, get_current_user_id, TokenPayload
import models
import llm_services # Importar nuestro nuevo módulo de servicios LLM




load_dotenv()

# --- Configuración de Base de Datos ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR CRÍTICO: DATABASE_URL no está configurada. La aplicación no funcionará.")
    exit()

engine = create_engine(DATABASE_URL, echo=True, pool_pre_ping=True) # Añadido pool_pre_ping

# --- Configuración del Cliente de Supabase (para Storage y otras interacciones de backend) ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY_FROM_ENV = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY_FROM_ENV:
    print("ERROR CRÍTICO: SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY no están configuradas. Funcionalidades como la subida de archivos fallarán.")
    supabase_admin_client: SupabaseClient | None = None
else:
    supabase_admin_client: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY_FROM_ENV)

# --- Verificación de Claves API Esenciales ---
if not os.getenv("SUPABASE_JWT_SECRET"):
    print("ADVERTENCIA: SUPABASE_JWT_SECRET no parece estar en .env. auth_utils.py podría fallar o la validación de JWT fallará.")
if not os.getenv("OPENAI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
    print("ADVERTENCIA: Ninguna clave API de LLM (OPENAI_API_KEY o GOOGLE_API_KEY) configurada en .env. Los servicios LLM fallarán.")


# --- Constantes de la Aplicación ---
MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_EXAM_PAPERS_PER_USER = 20
TRANSCRIPTION_COST = 1 # 1 crédito por transcripción
EXAM_IMAGES_BUCKET = "exam-images"


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
    "http://localhost:3000",  # Para desarrollo local
    "https://tu-proyecto-frontend.vercel.app", 
    "https://corrector-frontend-git-main-juanfranbrvs-projects.vercel.app/" # TU URL DE VERCEL AQUÍ
    # Si Vercel te da otros dominios de vista previa que quieras probar, añádelos también.
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

# --- Modelo de Respuesta para /users/me ---
class UserStatusResponse(TokenPayload):
    current_paper_count: int
    max_paper_quota: int
    credits: int # Añadir créditos

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
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"TestItem with id {item_id} not found")
    return item
    
# --- Endpoints de Usuario y Autenticación ---
@app.get("/users/me/", response_model=UserStatusResponse)
async def read_users_me_with_status(
    current_user_payload: TokenPayload = Depends(get_current_user), # El payload base del token
    session: Session = Depends(get_session)                 # Sesión de base de datos
):
    user_id = current_user_payload.sub

    # 1. Contar las redacciones del usuario actual
    count_statement = select(func.count(models.ExamPaper.id)).where(models.ExamPaper.user_id == user_id)
    current_paper_count = session.exec(count_statement).one() # func.count() siempre devuelve una fila

    # 2. Obtener los créditos del usuario de la tabla 'user' local
    db_user_local = session.get(models.User, user_id)
    user_credits = 0 # Valor por defecto si el usuario no se encuentra en la tabla local
    
    if db_user_local:
        user_credits = db_user_local.credits
        print(f"Usuario local {user_id} encontrado. Créditos: {user_credits}")
    else:
        # Esto podría ocurrir si es la primera vez que el usuario interactúa después del login
        # y aún no ha realizado una acción que cree su registro local (como subir un ExamPaper).
        # Podrías decidir crear el usuario aquí también si lo deseas, similar a como se hace en 'upload_exam_image'.
        # Por ahora, si no existe, se asume que tiene 0 créditos para mostrar.
        print(f"ADVERTENCIA: Usuario con ID {user_id} no encontrado en la tabla local 'user'. Se devolverán 0 créditos.")
        # Opcional: Crear el usuario local aquí si no existe, para asegurar que siempre tenga una entrada
        # if current_user_payload.email: # Solo si tenemos el email
        #     print(f"Creando usuario local para {user_id} ya que no existe...")
        #     new_db_user_data = models.UserCreate(id=user_id, email=current_user_payload.email, credits=0) # Créditos iniciales
        #     db_user_to_create = models.User.model_validate(new_db_user_data)
        #     session.add(db_user_to_create)
        #     session.commit() # Commit separado para la creación del usuario
        #     session.refresh(db_user_to_create)
        #     user_credits = db_user_to_create.credits # Ahora es 0
        # else:
        #     print(f"No se puede crear usuario local para {user_id} sin email del token.")
        pass


    print(f"Info de estado para usuario {user_id}: {current_paper_count} redacciones, cuota {MAX_EXAM_PAPERS_PER_USER}, créditos {user_credits}")
    
    # Construir la respuesta combinando datos del token y datos locales
    return UserStatusResponse(
        **current_user_payload.model_dump(), # Desempaquetar todos los campos de TokenPayload
        current_paper_count=current_paper_count,
        max_paper_quota=MAX_EXAM_PAPERS_PER_USER, # Usar la constante global definida
        credits=user_credits
    )

@app.get("/protected-route-example/") # Ejemplo de ruta protegida
async def protected_route_example(user_id: str = Depends(get_current_user_id)):
    return {"message": "Ruta protegida accedida con éxito!", "user_id_from_token": user_id}

# --- Endpoints para ExamPapers ---
@app.post("/exam_papers/upload_image/", response_model=models.ExamPaperRead)
async def upload_exam_image(
    file: UploadFile = File(..., description=f"Archivo de imagen a subir (máx {MAX_UPLOAD_SIZE_BYTES / (1024*1024):.0f}MB)"),
    current_auth_user: TokenPayload = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    user_id = current_auth_user.sub
    user_email = current_auth_user.email

    count_statement = select(func.count(models.ExamPaper.id)).where(models.ExamPaper.user_id == user_id)
    current_paper_count = session.exec(count_statement).one()
    if current_paper_count >= MAX_EXAM_PAPERS_PER_USER:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=f"Has alcanzado el límite máximo de {MAX_EXAM_PAPERS_PER_USER} redacciones permitidas."
        )

    if not supabase_admin_client:
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, detail="El servicio de almacenamiento no está configurado.")
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="El archivo subido no es una imagen válida.")

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"El archivo es demasiado grande (Máx {MAX_UPLOAD_SIZE_BYTES / (1024*1024):.0f}MB).")
    
    try:
        db_user = session.get(models.User, user_id)
        if not db_user:
            new_db_user_data = models.UserCreate(id=user_id, email=user_email, credits=0) # Créditos iniciales
            db_user = models.User.model_validate(new_db_user_data)
            session.add(db_user)
        
        original_filename = file.filename if file.filename else "unknown_image"
        file_extension = original_filename.split(".")[-1].lower() if "." in original_filename else "png"
        if file_extension not in ["jpg", "jpeg", "png", "gif", "webp"]:
            raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=f"Extensión de archivo no permitida: .{file_extension}")

        unique_filename = f"{uuid.uuid4()}.{file_extension}"
        path_on_storage = f"{user_id}/{unique_filename}"
        
        supabase_admin_client.storage.from_(EXAM_IMAGES_BUCKET).upload(
            path=path_on_storage, file=contents, file_options={"content-type": file.content_type, "cache-control": "3600"}
        )
        if SUPABASE_URL is None: raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="SUPABASE_URL no está configurada.")
        image_public_url = f"{SUPABASE_URL}/storage/v1/object/public/{EXAM_IMAGES_BUCKET}/{path_on_storage}"

        db_exam_paper_data = models.ExamPaperCreate(filename=original_filename, image_url=image_public_url, status="uploaded", user_id=user_id)
        db_exam_paper = models.ExamPaper.model_validate(db_exam_paper_data)
        session.add(db_exam_paper)
        session.commit()
        
        if db_user: session.refresh(db_user) # Refrescar usuario si fue creado o modificado (no en este flujo)
        session.refresh(db_exam_paper)
        return db_exam_paper
    except HTTPException: raise
    except Exception as e:
        if session.is_active: session.rollback() 
        print(f"Error en upload_exam_image: {type(e).__name__} - {e}")
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error interno al procesar archivo: {str(e)}")
    finally:
        if file: await file.close()

@app.get("/exam_papers/", response_model=list[models.ExamPaperRead])
async def list_exam_papers_for_current_user(
    user_id: str = Depends(get_current_user_id), session: Session = Depends(get_session),
    skip: int = 0, limit: int = 100
):
    statement = (select(models.ExamPaper).where(models.ExamPaper.user_id == user_id)
                 .order_by(models.ExamPaper.created_at.desc()).offset(skip).limit(limit))
    return session.exec(statement).all()

@app.delete("/exam_papers/{paper_id}", response_model=models.ExamPaperRead) # Devolver el paper eliminado puede ser útil
async def delete_exam_paper(
    paper_id: int, current_user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session)
):
    db_exam_paper = session.get(models.ExamPaper, paper_id)
    if not db_exam_paper:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Redacción no encontrada.")
    if db_exam_paper.user_id != current_user_id:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="No tienes permiso para eliminar esta redacción.")

    path_on_storage_to_delete = None
    if db_exam_paper.image_url and supabase_admin_client and EXAM_IMAGES_BUCKET and SUPABASE_URL:
        try:
            parsed_url = urlparse(db_exam_paper.image_url)
            prefix_to_remove = f"/storage/v1/object/public/{EXAM_IMAGES_BUCKET}/"
            if parsed_url.path.startswith(prefix_to_remove):
                path_on_storage_to_delete = parsed_url.path[len(prefix_to_remove):]
        except Exception as e_parse: print(f"Error al parsear URL de imagen para eliminar: {e_parse}")

    try:
        # Guardar una copia de los datos antes de eliminar, para la respuesta
        deleted_paper_data = models.ExamPaperRead.model_validate(db_exam_paper)
        session.delete(db_exam_paper) # Marcar para eliminar de la BD

        if path_on_storage_to_delete and supabase_admin_client: # Intentar eliminar del storage después de marcar para delete en BD
            print(f"Intentando eliminar de Supabase Storage: {path_on_storage_to_delete}")
            supabase_admin_client.storage.from_(EXAM_IMAGES_BUCKET).remove([path_on_storage_to_delete])
            print(f"Solicitud de eliminación enviada a Supabase Storage para: {path_on_storage_to_delete}")
        
        session.commit() # Aplicar eliminación de la BD
        print(f"Redacción ID: {paper_id} eliminada de la BD.")
        return deleted_paper_data # Devolver los datos del paper eliminado
    except Exception as e_db:
        if session.is_active: session.rollback()
        print(f"Error al eliminar la redacción ID: {paper_id} de la BD: {e_db}")
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error al eliminar la redacción.")


@app.post("/exam_papers/{paper_id}/transcribe", response_model=models.ExamPaperRead)
async def transcribe_exam_paper_endpoint(
    paper_id: int,
    current_auth_user: TokenPayload = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    user_id = current_auth_user.sub
    print(f"Usuario {user_id} solicitando transcripción para ExamPaper ID: {paper_id}")

    db_exam_paper = session.get(models.ExamPaper, paper_id)
    if not db_exam_paper:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"Redacción con ID {paper_id} no encontrada.")
    if db_exam_paper.user_id != user_id:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="No tienes permiso para transcribir esta redacción.")
    if not db_exam_paper.image_url:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="La redacción no tiene imagen asociada.")
    if db_exam_paper.status in ["transcribed", "transcribing", "corrected", "correcting"]: # Estados donde no se debería re-transcribir
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=f"Esta redacción ya procesada o en proceso. Estado: {db_exam_paper.status}")

    db_user = session.get(models.User, user_id)
    if not db_user:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado. Contacta soporte.")

    print(f"Usuario {user_id} tiene {db_user.credits} créditos. Costo de transcripción: {TRANSCRIPTION_COST}.")
    if db_user.credits < TRANSCRIPTION_COST:
        raise HTTPException(status_code=http_status.HTTP_402_PAYMENT_REQUIRED, detail=f"Créditos insuficientes. Necesitas {TRANSCRIPTION_COST}, tienes {db_user.credits}.")

    original_status = db_exam_paper.status
    db_exam_paper.status = "transcribing"
    db_exam_paper.updated_at = datetime.now(timezone.utc)
    session.add(db_exam_paper)
    session.commit()
    session.refresh(db_exam_paper)

    transcribed_text_result: str | None = None
    transcription_successful = False
    try:
        prompt_for_transcription = "Transcribe el texto manuscrito visible en esta imagen de la forma más precisa y completa posible."
        transcribed_text_result = await llm_services.transcribe_image_url_with_llm(
            image_url=db_exam_paper.image_url, prompt_text=prompt_for_transcription
        )
        if transcribed_text_result and transcribed_text_result.strip():
            transcription_successful = True
    except Exception as e_llm:
        print(f"Error durante la llamada al LLM para paper {paper_id}: {e_llm}")

    try: # Nuevo bloque try para la actualización final de la BD
        if transcription_successful and transcribed_text_result is not None:
            db_exam_paper.transcribed_text = transcribed_text_result
            db_exam_paper.status = "transcribed"
            db_exam_paper.transcription_credits_consumed = TRANSCRIPTION_COST
            db_user.credits -= TRANSCRIPTION_COST
            session.add(db_user)
            print(f"Créditos descontados para usuario {user_id}. Nuevo saldo: {db_user.credits}")
        else:
            db_exam_paper.status = "error_transcription" # O volver al estado original_status
            print(f"Transcripción falló o resultó vacía para paper {paper_id}. No se descontaron créditos.")

        db_exam_paper.updated_at = datetime.now(timezone.utc)
        session.add(db_exam_paper)
        session.commit()
        session.refresh(db_exam_paper)
        if db_user: session.refresh(db_user)

        if not transcription_successful:
            raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error durante el proceso de transcripción con IA.")
        
        return db_exam_paper
    except Exception as e_db_update: # Capturar errores al actualizar la BD después del LLM
        if session.is_active: session.rollback()
        print(f"Error al actualizar BD después de intento de transcripción para paper {paper_id}: {e_db_update}")
        # Decidir si el estado debe ser 'error_transcription' o el original
        # En este punto, la llamada al LLM ya ocurrió.
        # Podríamos intentar guardar el texto transcrito si se obtuvo, incluso si falla el débito de créditos.
        # Pero por ahora, un error genérico.
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error al guardar el resultado de la transcripción.")