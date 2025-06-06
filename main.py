# my-english-corrector-backend/main.py
import os
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, status as http_status 
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, SQLModel, create_engine, select, func
from dotenv import load_dotenv
from urllib.parse import urlparse
from typing import List, Optional
from pydantic import BaseModel

from supabase import create_client, Client as SupabaseClient

from auth_utils import get_current_user, get_current_user_id, TokenPayload
import models # models.py ahora tiene ExamPaper y ExamImage
import llm_services

load_dotenv()

# --- Configuración de Base de Datos ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR CRÍTICO: DATABASE_URL no está configurada.")
    exit()
engine = create_engine(DATABASE_URL, echo=True, pool_pre_ping=True)

# --- Configuración del Cliente de Supabase ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("ERROR CRÍTICO: SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY no configuradas.")
    supabase_admin_client: SupabaseClient | None = None
else:
    supabase_admin_client: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# --- Constantes de la Aplicación ---
MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024
MAX_EXAM_PAPERS_PER_USER = 20 
TRANSCRIPTION_COST = 1
CORRECTION_COST = 5
EXAM_IMAGES_BUCKET = "exam-images"

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

app = FastAPI(title="English Corrector API", version="0.1.0")

origins = [
    "http://localhost:3000",
    "https://corrector-frontend.vercel.app",
    "https://corrector-frontend-git-main-juanfranbrvs-projects.vercel.app",
    "https://english-corrector-api.onrender.com"
]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def on_startup():
    print("Creando tablas en BD si no existen...")
    create_db_and_tables()
    print("Evento de startup completado.")

def get_session():
    with Session(engine) as session:
        yield session

class UserStatusResponse(TokenPayload):
    current_paper_count: int
    max_paper_quota: int
    credits: int

class TranscribedTextUpdate(BaseModel):
    transcribed_text: str

@app.get("/")
async def read_root():
    return {"message": "API del Corrector de Inglés lista!"}

@app.get("/users/me/", response_model=UserStatusResponse)
async def read_users_me_with_status(
    current_user_payload: TokenPayload = Depends(get_current_user), session: Session = Depends(get_session)
):
    user_id = current_user_payload.sub
    count_statement = select(func.count(models.ExamPaper.id)).where(models.ExamPaper.user_id == user_id)
    current_paper_count = session.exec(count_statement).one()
    db_user_local = session.get(models.User, user_id)
    user_credits = db_user_local.credits if db_user_local else 0
    if not db_user_local:
         print(f"ADVERTENCIA: Usuario {user_id} no encontrado en tabla local 'user' para /users/me.")
    return UserStatusResponse(**current_user_payload.model_dump(), current_paper_count=current_paper_count, max_paper_quota=MAX_EXAM_PAPERS_PER_USER, credits=user_credits)

# --- Endpoints para ExamPapers ---

@app.post("/exam_papers/upload_multiple_images/", response_model=models.ExamPaperRead)
async def upload_multiple_exam_images(
    files: List[UploadFile] = File(..., description="Lista de archivos de imagen del ensayo (páginas)"),
    essay_title: Optional[str] = Form(None, description="Título opcional para el ensayo proporcionado por el usuario"), # <--- NUEVO PARÁMETRO
    current_auth_user: TokenPayload = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    user_id = current_auth_user.sub
    user_email = current_auth_user.email

    count_statement = select(func.count(models.ExamPaper.id)).where(models.ExamPaper.user_id == user_id)
    current_paper_count = session.exec(count_statement).one()
    if current_paper_count >= MAX_EXAM_PAPERS_PER_USER:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail=f"Límite de {MAX_EXAM_PAPERS_PER_USER} ensayos alcanzado.")

    if not supabase_admin_client:
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, detail="Servicio de almacenamiento no configurado.")
    if not files or len(files) == 0:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="No se proporcionaron archivos de imagen.")

    # Crear/Obtener usuario local
    db_user = session.get(models.User, user_id)
    if not db_user:
        # Esta lógica debería ser manejada por el trigger de base de datos ahora.
        # Si el trigger está funcionando, db_user no debería ser None aquí para un usuario autenticado.
        # Podríamos lanzar un error si no se encuentra, asumiendo que el trigger DEBE haberlo creado.
        print(f"ADVERTENCIA/ERROR: Usuario {user_id} no encontrado en tabla local 'user' durante la subida. El trigger debería haberlo creado.")
        # Por robustez, podríamos crearlo aquí como fallback, pero idealmente el trigger lo maneja.
        new_db_user_data = models.UserCreate(id=user_id, email=user_email, credits=0) # O los créditos iniciales por defecto
        db_user = models.User.model_validate(new_db_user_data)
        session.add(db_user)
        # No hacer commit aún, se hará al final o con el paper

    # 1. Crear el ExamPaper
    # Determinar el nombre del archivo para el ExamPaper
    paper_filename: str
    if essay_title and essay_title.strip(): # Si el usuario proporcionó un título
        paper_filename = essay_title.strip()
    elif files[0].filename: # Usar el nombre del primer archivo como fallback
        paper_filename = files[0].filename
    else: # Generar un nombre por defecto si todo lo demás falla
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        paper_filename = f"Ensayo subido el {current_date} - {uuid.uuid4().hex[:6]}"
    
    # Truncar si es demasiado largo (opcional, depende de la longitud máxima de tu campo 'filename')
    MAX_FILENAME_LENGTH = 255 # Asume un límite razonable
    if len(paper_filename) > MAX_FILENAME_LENGTH:
        paper_filename = paper_filename[:MAX_FILENAME_LENGTH]

    db_exam_paper_data = models.ExamPaperCreate(filename=paper_filename, status="uploaded", user_id=user_id)
    db_exam_paper = models.ExamPaper.model_validate(db_exam_paper_data)
    session.add(db_exam_paper)
    session.commit() 
    session.refresh(db_exam_paper)

    uploaded_image_models: List[models.ExamImage] = []
    try:
        for index, file_item in enumerate(files):
            if not file_item.content_type or not file_item.content_type.startswith("image/"):
                raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=f"Archivo '{file_item.filename}' no es una imagen válida.")
            
            contents = await file_item.read()
            if len(contents) > MAX_UPLOAD_SIZE_BYTES:
                raise HTTPException(status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"Archivo '{file_item.filename}' demasiado grande (Máx {MAX_UPLOAD_SIZE_BYTES/(1024*1024)}MB).")

            original_image_filename = file_item.filename if file_item.filename else f"page_{index + 1}"
            file_extension = original_image_filename.split(".")[-1].lower() if "." in original_image_filename else "png"
            
            unique_storage_filename = f"page_{index + 1}_{uuid.uuid4().hex[:12]}.{file_extension}"
            path_on_storage = f"{user_id}/{db_exam_paper.id}/{unique_storage_filename}"
            
            print(f"Subiendo imagen a Supabase Storage: {path_on_storage}")
            supabase_admin_client.storage.from_(EXAM_IMAGES_BUCKET).upload(
                path=path_on_storage, file=contents, file_options={"content-type": file_item.content_type, "cache-control": "3600"}
            )
            image_public_url = f"{SUPABASE_URL}/storage/v1/object/public/{EXAM_IMAGES_BUCKET}/{path_on_storage}"

            db_exam_image_data = models.ExamImageCreate(
                image_url=image_public_url,
                page_number=index + 1, 
                exam_paper_id=db_exam_paper.id
            )
            db_exam_image = models.ExamImage.model_validate(db_exam_image_data)
            session.add(db_exam_image)
            uploaded_image_models.append(db_exam_image)
        
        session.commit() 
        for img_model in uploaded_image_models: 
            session.refresh(img_model)
        
        session.refresh(db_exam_paper) 
        if db_user:
            session.refresh(db_user)

        return db_exam_paper

    except Exception as e:
        if session.is_active:
            session.rollback()
        print(f"Error durante la subida de múltiples imágenes: {type(e).__name__} - {e}")
        paper_to_delete_on_error = session.get(models.ExamPaper, db_exam_paper.id)
        if paper_to_delete_on_error:
            # También deberíamos intentar eliminar las imágenes de Supabase Storage aquí si algunas se subieron
            paths_to_delete_on_storage_error = []
            # (Esta lógica de encontrar paths puede ser compleja si no todos los ExamImage se crearon)
            # Por simplicidad en este ejemplo, nos enfocaremos en la BD
            
            # Eliminar ExamImages asociadas si existen en la BD
            images_in_db_on_error = session.exec(select(models.ExamImage).where(models.ExamImage.exam_paper_id == db_exam_paper.id)).all()
            for img_db in images_in_db_on_error:
                session.delete(img_db)

            session.delete(paper_to_delete_on_error)
            session.commit()
            print(f"ExamPaper ID {db_exam_paper.id} y sus imágenes asociadas eliminados de la BD debido a error en subida de imágenes.")
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error interno al procesar archivos: {str(e)}")
    finally:
        for file_item in files:
             if hasattr(file_item, 'close') and callable(file_item.close):
                await file_item.close() # type: ignore


@app.get("/exam_papers/", response_model=List[models.ExamPaperRead])
async def list_exam_papers_for_current_user(
    user_id: str = Depends(get_current_user_id), session: Session = Depends(get_session),
    skip: int = 0, limit: int = 100
):
    statement = (
        select(models.ExamPaper)
        .where(models.ExamPaper.user_id == user_id)
        .order_by(getattr(models.ExamPaper, 'created_at'))
        .offset(skip)
        .limit(limit)
    )
    papers = session.exec(statement).all()
    return papers


@app.get("/exam_papers/{paper_id}", response_model=models.ExamPaperRead)
async def get_exam_paper(
    paper_id: int,
    current_user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session)
):
    db_exam_paper = session.get(models.ExamPaper, paper_id)
    if not db_exam_paper:
        raise HTTPException(status_code=404, detail="Redacción no encontrada.")
    if db_exam_paper.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="No tienes permiso para ver esta redacción.")
    return db_exam_paper


@app.delete("/exam_papers/{paper_id}", response_model=models.ExamPaperRead)
async def delete_exam_paper(
    paper_id: int, current_user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session)
):
    db_exam_paper = session.get(models.ExamPaper, paper_id)
    if not db_exam_paper:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Redacción no encontrada.")
    if db_exam_paper.user_id != current_user_id:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="No tienes permiso para eliminar.")
    
    try:
        images_to_delete = session.exec(select(models.ExamImage).where(models.ExamImage.exam_paper_id == paper_id)).all()
        temp_paper_dict = db_exam_paper.model_dump()
        temp_paper_dict["images"] = [img.model_dump() for img in images_to_delete]
        deleted_paper_data_for_response = models.ExamPaperRead.model_validate(temp_paper_dict)
    except Exception as e_val:
        print(f"Error validando ExamPaperRead para delete response: {e_val}")
        deleted_paper_data_for_response = models.ExamPaperRead.model_validate(db_exam_paper)

    paths_on_storage_to_delete = []
    if supabase_admin_client and EXAM_IMAGES_BUCKET and SUPABASE_URL:
        for image_obj in images_to_delete: 
            if image_obj.image_url:
                try:
                    parsed_url = urlparse(image_obj.image_url)
                    prefix_to_remove = f"/storage/v1/object/public/{EXAM_IMAGES_BUCKET}/"
                    if parsed_url.path.startswith(prefix_to_remove):
                        paths_on_storage_to_delete.append(parsed_url.path[len(prefix_to_remove):])
                except Exception as e_parse:
                    print(f"Error parseando URL de imagen para eliminar: {e_parse}")
    
    try:
        for image_obj in images_to_delete:
            session.delete(image_obj)
        session.delete(db_exam_paper)

        if paths_on_storage_to_delete and supabase_admin_client:
            print(f"Intentando eliminar de Supabase Storage: {paths_on_storage_to_delete}")
            if paths_on_storage_to_delete:
                 supabase_admin_client.storage.from_(EXAM_IMAGES_BUCKET).remove(paths_on_storage_to_delete)
                 print("Solicitud de eliminación enviada a Supabase Storage.")
        
        session.commit()
        print(f"Redacción ID: {paper_id} y sus imágenes eliminadas de la BD.")
        return deleted_paper_data_for_response
    except Exception as e_db:
        if session.is_active:
            session.rollback()
        print(f"Error al eliminar la redacción ID: {paper_id} de la BD: {e_db}")
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error al eliminar la redacción.")


@app.post("/exam_papers/{paper_id}/transcribe", response_model=models.ExamPaperRead)
async def transcribe_exam_paper_endpoint(
    paper_id: int,
    current_auth_user: TokenPayload = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    user_id = current_auth_user.sub
    db_exam_paper = session.get(models.ExamPaper, paper_id)
    
    if not db_exam_paper:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"Redacción ID {paper_id} no encontrada.")
    if db_exam_paper.user_id != user_id:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="No tienes permiso.")
    
    if not db_exam_paper.images: # type: ignore
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="La redacción no tiene imágenes asociadas para transcribir.")

    allowed_initial_states = ["uploaded", "error_transcription"]
    if db_exam_paper.status not in allowed_initial_states:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=f"No se puede transcribir. Estado: {db_exam_paper.status}")

    db_user = session.get(models.User, user_id)
    if not db_user: # Esto no debería suceder si el trigger está funcionando
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")
    if db_user.credits < TRANSCRIPTION_COST:
        raise HTTPException(status_code=http_status.HTTP_402_PAYMENT_REQUIRED, detail=f"Créditos insuficientes ({db_user.credits}/{TRANSCRIPTION_COST}).")

    db_exam_paper.status = "transcribing"
    db_exam_paper.updated_at = datetime.now(timezone.utc)
    session.add(db_exam_paper)
    session.commit()
    session.refresh(db_exam_paper)

    full_transcribed_text_parts = []
    any_page_transcription_failed = False
    
    sorted_images = sorted(db_exam_paper.images, key=lambda img: img.page_number if img.page_number is not None else float('inf')) # type: ignore

    for index, image_obj in enumerate(sorted_images):
        page_prefix = f"--- Página {image_obj.page_number or index + 1} ---\n"
        page_suffix = f"\n--- Fin de Página {image_obj.page_number or index + 1} ---\n\n"
        
        try:
            print(f"Transcribiendo página {image_obj.page_number or index + 1} (URL: {image_obj.image_url})")
            page_transcription = await llm_services.transcribe_image_url_with_llm(image_url=image_obj.image_url)
            
            if page_transcription and page_transcription.strip():
                full_transcribed_text_parts.append(page_prefix + page_transcription.strip() + page_suffix)
            else:
                full_transcribed_text_parts.append(page_prefix + "[Transcripción vacía para esta página]" + page_suffix)
        except Exception as e_llm_page:
            print(f"Error al transcribir página {image_obj.page_number or index + 1}: {e_llm_page}")
            full_transcribed_text_parts.append(page_prefix + "[ERROR EN TRANSCRIPCIÓN DE ESTA PÁGINA]" + page_suffix)
            any_page_transcription_failed = True

    final_transcribed_text = "".join(full_transcribed_text_parts).strip()

    try:
        session.refresh(db_user) 
        session.refresh(db_exam_paper)

        if final_transcribed_text:
            db_exam_paper.transcribed_text = final_transcribed_text
            if any_page_transcription_failed:
                db_exam_paper.status = "error_transcription" 
                print(f"Transcripción para paper {paper_id} completada con errores en algunas páginas.")
            else:
                db_exam_paper.status = "transcribed"
                db_exam_paper.transcription_credits_consumed = TRANSCRIPTION_COST
                db_user.credits -= TRANSCRIPTION_COST
                session.add(db_user)
                print(f"Créditos descontados (transcripción) para {user_id}. Saldo: {db_user.credits}")
        else: 
            db_exam_paper.status = "error_transcription"
            print(f"Transcripción falló completamente para paper {paper_id}. No se obtuvo texto.")

        db_exam_paper.updated_at = datetime.now(timezone.utc)
        session.add(db_exam_paper)
        session.commit()
        session.refresh(db_exam_paper)
        if db_user:
            session.refresh(db_user)

        if not final_transcribed_text and any_page_transcription_failed:
            raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error durante la transcripción IA. No se obtuvo texto.")
        
        return db_exam_paper
        
    except Exception as e_db_update:
        if session.is_active:
            session.rollback()
        print(f"Error DB post-transcripción paper {paper_id}: {e_db_update}")
        try:
            paper_to_recover = session.get(models.ExamPaper, paper_id)
            if paper_to_recover and paper_to_recover.status != "error_transcription":
                paper_to_recover.status = "error_transcription"
                paper_to_recover.updated_at = datetime.now(timezone.utc)
                session.add(paper_to_recover)
                session.commit()
        except Exception as e_recovery:
            print(f"Error adicional marcando paper {paper_id} como error: {e_recovery}")
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error guardando resultado de transcripción.")


@app.put("/exam_papers/{paper_id}/transcribed_text", response_model=models.ExamPaperRead)
async def update_exam_paper_transcribed_text(
    paper_id: int, update_data: TranscribedTextUpdate,
    current_user_id: str = Depends(get_current_user_id), session: Session = Depends(get_session)
):
    db_exam_paper = session.get(models.ExamPaper, paper_id)
    if not db_exam_paper:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"Redacción ID {paper_id} no encontrada.")
    if db_exam_paper.user_id != current_user_id:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="No tienes permiso.")

    db_exam_paper.transcribed_text = update_data.transcribed_text
    db_exam_paper.updated_at = datetime.now(timezone.utc)
    
    if db_exam_paper.status in ["error_transcription", "uploaded"] and update_data.transcribed_text and update_data.transcribed_text.strip():
        db_exam_paper.status = "transcribed"
        print(f"Estado de ExamPaper ID: {paper_id} cambiado a 'transcribed' tras edición manual.")
    
    session.add(db_exam_paper)
    session.commit()
    session.refresh(db_exam_paper)
    return db_exam_paper


@app.post("/exam_papers/{paper_id}/correct", response_model=models.ExamPaperRead)
async def correct_exam_paper_endpoint(
    paper_id: int, current_auth_user: TokenPayload = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    user_id = current_auth_user.sub
    db_exam_paper = session.get(models.ExamPaper, paper_id)
    if not db_exam_paper:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"Redacción ID {paper_id} no encontrada.")
    if db_exam_paper.user_id != user_id:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="No tienes permiso.")
    if db_exam_paper.status != "transcribed":
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=f"Solo se pueden corregir redacciones transcritas. Estado: {db_exam_paper.status}")
    if not db_exam_paper.transcribed_text or not db_exam_paper.transcribed_text.strip():
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Redacción sin texto transcrito para corregir.")

    db_user = session.get(models.User, user_id)
    if not db_user: # No debería pasar con el trigger
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error de datos de usuario.")
    if db_user.credits < CORRECTION_COST:
        raise HTTPException(status_code=http_status.HTTP_402_PAYMENT_REQUIRED, detail=f"Créditos insuficientes ({db_user.credits}/{CORRECTION_COST}).")

    db_exam_paper.status = "correcting"
    db_exam_paper.updated_at = datetime.now(timezone.utc)
    session.add(db_exam_paper)
    session.commit()
    session.refresh(db_exam_paper)

    correction_feedback_result: str | None = None
    correction_successful = False
    try:
        correction_feedback_result = await llm_services.correct_text_with_llm(text_to_correct=db_exam_paper.transcribed_text)
        if correction_feedback_result and correction_feedback_result.strip():
            correction_successful = True
    except Exception as e_llm:
        print(f"Error LLM corrección paper {paper_id}: {e_llm}")

    try:
        session.refresh(db_user)
        session.refresh(db_exam_paper)
        current_time = datetime.now(timezone.utc)
        if correction_successful and correction_feedback_result:
            db_exam_paper.corrected_feedback = correction_feedback_result
            db_exam_paper.status = "corrected"
            db_exam_paper.correction_credits_consumed = CORRECTION_COST
            db_exam_paper.correction_prompt_version = llm_services.CORRECTION_PROMPT_VERSION_CURRENT
            db_exam_paper.corrected_at = current_time
            db_user.credits -= CORRECTION_COST
            session.add(db_user)
            print(f"Créditos descontados (corrección) para {user_id}. Saldo: {db_user.credits}")
        else:
            db_exam_paper.status = "error_correction"
            print(f"Corrección falló o vacía para paper {paper_id}. No se descontaron créditos.")
        db_exam_paper.updated_at = current_time
        session.add(db_exam_paper)
        session.commit()
        session.refresh(db_exam_paper)
        if db_user:
            session.refresh(db_user)
        if not correction_successful:
            raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error durante corrección IA.")
        return db_exam_paper
    except Exception as e_db_update:
        if session.is_active:
            session.rollback()
        print(f"Error DB post-corrección paper {paper_id}: {e_db_update}")
        try: 
            paper_to_recover = session.get(models.ExamPaper, paper_id)
            if paper_to_recover and paper_to_recover.status != "error_correction":
                paper_to_recover.status = "error_correction"
                paper_to_recover.updated_at = datetime.now(timezone.utc)
                session.add(paper_to_recover)
                session.commit()
        except Exception as e_recovery:
            print(f"Error adicional marcando paper {paper_id} como error_correction: {e_recovery}")
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error guardando resultado de corrección.")