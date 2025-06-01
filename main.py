# my-english-corrector-backend/main.py
import os
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status as http_status
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, SQLModel, create_engine, select, func # func para count
from dotenv import load_dotenv
from urllib.parse import urlparse
from pydantic import BaseModel # Para el nuevo modelo de request

from supabase import create_client, Client as SupabaseClient

from auth_utils import get_current_user, get_current_user_id, TokenPayload
import models
import llm_services

load_dotenv()

# --- Configuración de Base de Datos ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR CRÍTICO: DATABASE_URL no está configurada. La aplicación no funcionará.")
    exit()

engine = create_engine(DATABASE_URL, echo=True, pool_pre_ping=True)

# --- Configuración del Cliente de Supabase ---
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
    print("ADVERTENCIA: Ninguna clave API de LLM principal (OPENAI_API_KEY o GOOGLE_API_KEY) configurada en .env. Los servicios LLM podrían fallar.")


# --- Constantes de la Aplicación ---
MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024
MAX_EXAM_PAPERS_PER_USER = 20
TRANSCRIPTION_COST = 1
CORRECTION_COST = 5
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
    "http://localhost:3000",
    "https://corrector-frontend.vercel.app",
    "https://corrector-frontend-git-main-juanfranbrvs-projects.vercel.app",
    "https://english-corrector-api.onrender.com"
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

# --- Modelos de Request/Response Específicos para Endpoints ---
class UserStatusResponse(TokenPayload):
    current_paper_count: int
    max_paper_quota: int
    credits: int

class TranscribedTextUpdate(BaseModel): # Usar Pydantic BaseModel es más ligero aquí
    transcribed_text: str

# --- Endpoints Públicos / de Prueba ---
@app.get("/")
async def read_root():
    return {"message": "API del Corrector de Inglés lista y funcionando!"}

# (Endpoints de TestItem omitidos por brevedad si no son relevantes ahora)
# ...

# --- Endpoints de Usuario y Autenticación ---
@app.get("/users/me/", response_model=UserStatusResponse)
async def read_users_me_with_status(
    current_user_payload: TokenPayload = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    user_id = current_user_payload.sub
    count_statement = select(func.count(models.ExamPaper.id)).where(models.ExamPaper.user_id == user_id)
    current_paper_count = session.exec(count_statement).one()
    db_user_local = session.get(models.User, user_id)
    user_credits = db_user_local.credits if db_user_local else 0
    
    if not db_user_local:
         print(f"ADVERTENCIA: Usuario con ID {user_id} no encontrado en la tabla local 'user' durante /users/me. Se devolverán 0 créditos.")

    return UserStatusResponse(
        **current_user_payload.model_dump(),
        current_paper_count=current_paper_count,
        max_paper_quota=MAX_EXAM_PAPERS_PER_USER,
        credits=user_credits
    )

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
            if not user_email:
                print(f"ADVERTENCIA: Email no disponible en el token para el usuario {user_id}. Creando usuario sin email local.")
            new_db_user_data = models.UserCreate(id=user_id, email=user_email, credits=0) 
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
        if db_user: session.refresh(db_user)
        session.refresh(db_exam_paper)
        return db_exam_paper
    except HTTPException: 
        raise
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
    papers = session.exec(statement).all()
    return papers


@app.delete("/exam_papers/{paper_id}", response_model=models.ExamPaperRead)
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
        except Exception as e_parse: 
            print(f"Error al parsear URL de imagen para eliminar: {e_parse}")

    try:
        deleted_paper_data = models.ExamPaperRead.model_validate(db_exam_paper)
        session.delete(db_exam_paper)
        if path_on_storage_to_delete and supabase_admin_client:
            print(f"Intentando eliminar de Supabase Storage: {path_on_storage_to_delete}")
            supabase_admin_client.storage.from_(EXAM_IMAGES_BUCKET).remove([path_on_storage_to_delete])
            print(f"Solicitud de eliminación enviada a Supabase Storage para: {path_on_storage_to_delete}")
        session.commit()
        print(f"Redacción ID: {paper_id} eliminada de la BD.")
        return deleted_paper_data
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
    
    allowed_initial_states_for_transcription = ["uploaded", "error_transcription"]
    if db_exam_paper.status not in allowed_initial_states_for_transcription:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=f"Esta redacción no puede ser transcrita. Estado actual: {db_exam_paper.status}")

    db_user = session.get(models.User, user_id)
    if not db_user:
        print(f"ERROR CRÍTICO: Usuario {user_id} no encontrado en tabla local 'user' durante la transcripción. Creando con 0 créditos.")
        if not current_auth_user.email:
            raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error de datos de usuario, email no encontrado. Contacte soporte.")
        new_db_user_data = models.UserCreate(id=user_id, email=current_auth_user.email, credits=0)
        db_user = models.User.model_validate(new_db_user_data)
        session.add(db_user)

    print(f"Usuario {user_id} tiene {db_user.credits} créditos. Costo de transcripción: {TRANSCRIPTION_COST}.")
    if db_user.credits < TRANSCRIPTION_COST:
        raise HTTPException(status_code=http_status.HTTP_402_PAYMENT_REQUIRED, detail=f"Créditos insuficientes. Necesitas {TRANSCRIPTION_COST}, tienes {db_user.credits}.")

    db_exam_paper.status = "transcribing"
    db_exam_paper.updated_at = datetime.now(timezone.utc)
    session.add(db_exam_paper)
    session.commit()
    session.refresh(db_exam_paper)

    transcribed_text_result: str | None = None
    transcription_successful = False
    try:
        # El prompt ahora se define por defecto en llm_services si no se pasa
        transcribed_text_result = await llm_services.transcribe_image_url_with_llm(
            image_url=db_exam_paper.image_url
        )
        if transcribed_text_result and transcribed_text_result.strip():
            transcription_successful = True
    except Exception as e_llm:
        print(f"Error durante la llamada al LLM para transcripción (paper {paper_id}): {e_llm}")

    try:
        session.refresh(db_user) 
        session.refresh(db_exam_paper)
        if transcription_successful and transcribed_text_result is not None:
            db_exam_paper.transcribed_text = transcribed_text_result
            db_exam_paper.status = "transcribed"
            db_exam_paper.transcription_credits_consumed = TRANSCRIPTION_COST
            db_user.credits -= TRANSCRIPTION_COST
            session.add(db_user)
            print(f"Créditos descontados por transcripción para usuario {user_id}. Nuevo saldo: {db_user.credits}")
        else:
            db_exam_paper.status = "error_transcription"
            print(f"Transcripción falló o resultó vacía para paper {paper_id}. No se descontaron créditos.")
        db_exam_paper.updated_at = datetime.now(timezone.utc)
        session.add(db_exam_paper)
        session.commit()
        session.refresh(db_exam_paper)
        session.refresh(db_user)
        if not transcription_successful:
            raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error durante el proceso de transcripción con IA.")
        return db_exam_paper
    except Exception as e_db_update:
        if session.is_active: session.rollback()
        print(f"Error al actualizar BD después de intento de transcripción para paper {paper_id}: {e_db_update}")
        try:
            paper_to_recover = session.get(models.ExamPaper, paper_id)
            if paper_to_recover and paper_to_recover.status != "error_transcription":
                paper_to_recover.status = "error_transcription"
                paper_to_recover.updated_at = datetime.now(timezone.utc)
                session.add(paper_to_recover)
                session.commit()
        except Exception as e_recovery:
            print(f"Error adicional intentando marcar paper {paper_id} como error_transcription: {e_recovery}")
            if session.is_active: session.rollback()
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error al guardar el resultado de la transcripción.")

# NUEVO ENDPOINT para actualizar el texto transcrito
@app.put("/exam_papers/{paper_id}/transcribed_text", response_model=models.ExamPaperRead)
async def update_exam_paper_transcribed_text(
    paper_id: int,
    update_data: TranscribedTextUpdate,
    current_user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session)
):
    print(f"Usuario {current_user_id} actualizando texto transcrito para ExamPaper ID: {paper_id}")
    db_exam_paper = session.get(models.ExamPaper, paper_id)

    if not db_exam_paper:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"Redacción con ID {paper_id} no encontrada.")
    if db_exam_paper.user_id != current_user_id:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="No tienes permiso para editar esta redacción.")

    db_exam_paper.transcribed_text = update_data.transcribed_text
    db_exam_paper.updated_at = datetime.now(timezone.utc)
    
    # Opcional: Considerar si se debe cambiar el estado.
    # Si estaba en 'error_transcription' y ahora tiene texto, podría volver a 'transcribed'.
    if db_exam_paper.status == "error_transcription" and update_data.transcribed_text and update_data.transcribed_text.strip():
        db_exam_paper.status = "transcribed"
        print(f"Estado de ExamPaper ID: {paper_id} cambiado a 'transcribed' después de edición manual.")
    
    # Si estaba 'uploaded' y se edita un texto (aunque no debería tener texto transcrito aún),
    # se podría cambiar a 'transcribed' también.
    elif db_exam_paper.status == "uploaded" and update_data.transcribed_text and update_data.transcribed_text.strip():
        db_exam_paper.status = "transcribed"
        print(f"Estado de ExamPaper ID: {paper_id} cambiado a 'transcribed' después de edición manual (era 'uploaded').")


    session.add(db_exam_paper)
    session.commit()
    session.refresh(db_exam_paper)

    print(f"Texto transcrito para ExamPaper ID: {paper_id} actualizado exitosamente.")
    return db_exam_paper


@app.post("/exam_papers/{paper_id}/correct", response_model=models.ExamPaperRead)
async def correct_exam_paper_endpoint(
    paper_id: int,
    current_auth_user: TokenPayload = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    user_id = current_auth_user.sub
    print(f"Usuario {user_id} solicitando corrección para ExamPaper ID: {paper_id}")
    db_exam_paper = session.get(models.ExamPaper, paper_id)
    if not db_exam_paper:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"Redacción con ID {paper_id} no encontrada.")
    if db_exam_paper.user_id != user_id:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="No tienes permiso para corregir esta redacción.")
    if db_exam_paper.status != "transcribed":
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=f"Solo se pueden corregir redacciones que han sido transcritas. Estado actual: {db_exam_paper.status}")
    if not db_exam_paper.transcribed_text or not db_exam_paper.transcribed_text.strip():
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="La redacción no tiene texto transcrito para corregir.")

    db_user = session.get(models.User, user_id)
    if not db_user:
        print(f"ERROR CRÍTICO: Usuario {user_id} no encontrado en tabla local 'user' durante la corrección.")
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error de datos de usuario. Contacte soporte.")

    print(f"Usuario {user_id} tiene {db_user.credits} créditos. Costo de corrección: {CORRECTION_COST}.")
    if db_user.credits < CORRECTION_COST:
        raise HTTPException(status_code=http_status.HTTP_402_PAYMENT_REQUIRED, detail=f"Créditos insuficientes. Necesitas {CORRECTION_COST}, tienes {db_user.credits}.")

    db_exam_paper.status = "correcting"
    db_exam_paper.updated_at = datetime.now(timezone.utc)
    session.add(db_exam_paper)
    session.commit()
    session.refresh(db_exam_paper)

    correction_feedback_result: str | None = None
    correction_successful = False
    try:
        correction_feedback_result = await llm_services.correct_text_with_llm(
            text_to_correct=db_exam_paper.transcribed_text
        )
        if correction_feedback_result and correction_feedback_result.strip():
            correction_successful = True
    except Exception as e_llm:
        print(f"Error durante la llamada al LLM para corrección (paper {paper_id}): {e_llm}")

    try:
        session.refresh(db_user)
        session.refresh(db_exam_paper)
        current_time = datetime.now(timezone.utc)
        if correction_successful and correction_feedback_result is not None:
            db_exam_paper.corrected_feedback = correction_feedback_result
            db_exam_paper.status = "corrected"
            db_exam_paper.correction_credits_consumed = CORRECTION_COST
            db_exam_paper.correction_prompt_version = llm_services.CORRECTION_PROMPT_VERSION_CURRENT
            db_exam_paper.corrected_at = current_time
            db_user.credits -= CORRECTION_COST
            session.add(db_user)
            print(f"Créditos descontados por corrección para usuario {user_id}. Nuevo saldo: {db_user.credits}")
        else:
            db_exam_paper.status = "error_correction"
            print(f"Corrección falló o resultó vacía para paper {paper_id}. No se descontaron créditos.")
        db_exam_paper.updated_at = current_time
        session.add(db_exam_paper)
        session.commit()
        session.refresh(db_exam_paper)
        session.refresh(db_user)
        if not correction_successful:
            raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error durante el proceso de corrección con IA.")
        return db_exam_paper
    except Exception as e_db_update:
        if session.is_active: session.rollback()
        print(f"Error al actualizar BD después de intento de corrección para paper {paper_id}: {e_db_update}")
        try:
            paper_to_recover = session.get(models.ExamPaper, paper_id)
            if paper_to_recover and paper_to_recover.status != "error_correction":
                paper_to_recover.status = "error_correction"
                paper_to_recover.updated_at = datetime.now(timezone.utc)
                session.add(paper_to_recover)
                session.commit()
        except Exception as e_recovery:
            print(f"Error adicional intentando marcar paper {paper_id} como error_correction: {e_recovery}")
            if session.is_active: session.rollback()
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error al guardar el resultado de la corrección.")