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

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR CRÍTICO: DATABASE_URL no está configurada. La aplicación no funcionará.")
    exit()
engine = create_engine(DATABASE_URL, echo=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY_FROM_ENV = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY_FROM_ENV:
    print("ERROR CRÍTICO: SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY no están configuradas. Funcionalidades como la subida de archivos fallarán.")
    supabase_admin_client: SupabaseClient | None = None
else:
    supabase_admin_client: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY_FROM_ENV)

if not os.getenv("SUPABASE_JWT_SECRET"):
    print("ADVERTENCIA: SUPABASE_JWT_SECRET no parece estar en .env. auth_utils.py podría fallar o la validación de JWT fallará.")

MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024
MAX_EXAM_PAPERS_PER_USER = 20 # Límite de redacciones por usuario

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

app = FastAPI(
    title="English Corrector API",
    description="API for the AI-powered English essay correction assistant.",
    version="0.1.0",
)

origins = ["http://localhost:3000"]
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

# --- Modelo de Respuesta para /users/me ---
class UserStatusResponse(TokenPayload): # Hereda de TokenPayload
    current_paper_count: int
    max_paper_quota: int


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
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"TestItem with id {item_id} not found")
    return item
    
# --- ENDPOINT /users/me MODIFICADO ---
@app.get("/users/me/", response_model=UserStatusResponse)
async def read_users_me_with_status(
    current_user_payload: TokenPayload = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    user_id = current_user_payload.sub

    count_statement = select(func.count(models.ExamPaper.id)).where(models.ExamPaper.user_id == user_id)
    current_paper_count = session.exec(count_statement).one()

    print(f"Info de estado para usuario {user_id}: {current_paper_count} redacciones, cuota {MAX_EXAM_PAPERS_PER_USER}")
    return UserStatusResponse(
        **current_user_payload.model_dump(),
        current_paper_count=current_paper_count,
        max_paper_quota=MAX_EXAM_PAPERS_PER_USER
    )

@app.get("/protected-route-example/")
async def protected_route_example(user_id: str = Depends(get_current_user_id)):
    return {"message": "Ruta protegida accedida con éxito!", "user_id_from_token": user_id}

EXAM_IMAGES_BUCKET = "exam-images"

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
    print(f"Usuario {user_id} tiene actualmente {current_paper_count} redacciones. Límite: {MAX_EXAM_PAPERS_PER_USER}")
    if current_paper_count >= MAX_EXAM_PAPERS_PER_USER:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=f"Has alcanzado el límite máximo de {MAX_EXAM_PAPERS_PER_USER} redacciones permitidas."
        )

    if not supabase_admin_client:
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, detail="El servicio de almacenamiento no está configurado correctamente en el servidor.")
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="El archivo subido no es una imagen válida.")

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo es demasiado grande. El tamaño máximo permitido es de {MAX_UPLOAD_SIZE_BYTES / (1024*1024):.0f} MB."
        )
    
    try:
        db_user = session.get(models.User, user_id)
        if not db_user:
            print(f"Usuario con ID {user_id} no encontrado en la tabla local 'user'. Creándolo...")
            if not user_email: print(f"ADVERTENCIA: No se pudo obtener el email del token para el usuario {user_id}.")
            new_db_user_data = models.UserCreate(id=user_id, email=user_email, credits=0)
            db_user = models.User.model_validate(new_db_user_data)
            session.add(db_user)
            print(f"Usuario local {user_id} (Email: {user_email}) preparado para ser añadido a la sesión.")
        
        original_filename = file.filename if file.filename else "unknown_image"
        file_extension = original_filename.split(".")[-1].lower() if "." in original_filename else "png"
        if file_extension not in ["jpg", "jpeg", "png", "gif", "webp"]:
            raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=f"Extensión de archivo no permitida: .{file_extension}")

        unique_filename = f"{uuid.uuid4()}.{file_extension}"
        path_on_storage = f"{user_id}/{unique_filename}"
        
        print(f"Subiendo archivo a Supabase Storage: bucket='{EXAM_IMAGES_BUCKET}', path='{path_on_storage}'")
        supabase_admin_client.storage.from_(EXAM_IMAGES_BUCKET).upload(
            path=path_on_storage, file=contents, file_options={"content-type": file.content_type, "cache-control": "3600"}
        )
        
        if SUPABASE_URL is None: raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="SUPABASE_URL no está configurada.")
        image_public_url = f"{SUPABASE_URL}/storage/v1/object/public/{EXAM_IMAGES_BUCKET}/{path_on_storage}"
        print(f"Archivo subido. URL pública: {image_public_url}")

        db_exam_paper_data = models.ExamPaperCreate(
            filename=original_filename, image_url=image_public_url, status="uploaded", user_id=user_id,
        )
        db_exam_paper = models.ExamPaper.model_validate(db_exam_paper_data)
        session.add(db_exam_paper)
        session.commit()
        
        if db_user and db_user.id is not None: session.refresh(db_user)
        session.refresh(db_exam_paper)
        print(f"Registro de ExamPaper creado en BD con ID: {db_exam_paper.id} para el usuario {user_id}")
        return db_exam_paper

    except HTTPException: raise
    except Exception as e:
        if session.is_active: session.rollback() 
        print(f"Error detallado durante la subida de imagen o creación de registro: {type(e).__name__} - {e}")
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Ocurrió un error interno al procesar el archivo: {str(e)}")
    finally:
        if file: await file.close(); print("Archivo UploadFile cerrado en el bloque finally.")

@app.get("/exam_papers/", response_model=list[models.ExamPaperRead])
async def list_exam_papers_for_current_user(
    user_id: str = Depends(get_current_user_id), session: Session = Depends(get_session),
    skip: int = 0, limit: int = 100
):
    print(f"Listando redacciones para el usuario: {user_id}, skip: {skip}, limit: {limit}")
    statement = (
        select(models.ExamPaper).where(models.ExamPaper.user_id == user_id)
        .order_by(models.ExamPaper.created_at.desc()).offset(skip).limit(limit)
    )
    exam_papers = session.exec(statement).all()
    print(f"Encontradas {len(exam_papers)} redacciones para el usuario {user_id}.")
    return exam_papers

@app.delete("/exam_papers/{paper_id}", status_code=http_status.HTTP_200_OK)
async def delete_exam_paper(
    paper_id: int, current_user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session)
):
    print(f"Intento de eliminación del ExamPaper ID: {paper_id} por el usuario: {current_user_id}")
    db_exam_paper = session.get(models.ExamPaper, paper_id)
    if not db_exam_paper:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"Redacción con ID {paper_id} no encontrada.")
    if db_exam_paper.user_id != current_user_id:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="No tienes permiso para eliminar esta redacción.")

    if db_exam_paper.image_url and supabase_admin_client and EXAM_IMAGES_BUCKET and SUPABASE_URL:
        try:
            parsed_url = urlparse(db_exam_paper.image_url)
            prefix_to_remove = f"/storage/v1/object/public/{EXAM_IMAGES_BUCKET}/"
            if parsed_url.path.startswith(prefix_to_remove):
                path_on_storage_to_delete = parsed_url.path[len(prefix_to_remove):]
                print(f"Intentando eliminar de Supabase Storage: bucket='{EXAM_IMAGES_BUCKET}', path='{path_on_storage_to_delete}'")
                supabase_admin_client.storage.from_(EXAM_IMAGES_BUCKET).remove([path_on_storage_to_delete])
            else: print(f"ADVERTENCIA: No se pudo extraer el path de almacenamiento de la URL: {db_exam_paper.image_url}")
        except Exception as e_storage:
            print(f"Error al intentar eliminar el archivo de Supabase Storage: {e_storage}. Se continuará con la eliminación del registro en BD.")
    try:
        session.delete(db_exam_paper)
        session.commit()
        print(f"Redacción ID: {paper_id} eliminada de la base de datos por el usuario: {current_user_id}")
        return {"message": "Redacción eliminada exitosamente", "paper_id": paper_id}
    except Exception as e_db:
        if session.is_active: session.rollback()
        print(f"Error al eliminar la redacción ID: {paper_id} de la BD: {e_db}")
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error al eliminar la redacción de la base de datos.")