# my-english-corrector-backend/main.py
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, SQLModel, create_engine, select
from dotenv import load_dotenv
import os

# Importar utilidades de autenticación y modelos de payload (USANDO IMPORTACIONES ABSOLUTAS)
from auth_utils import get_current_user, get_current_user_id, TokenPayload
# Importar los modelos desde models.py (USANDO IMPORTACIÓN ABSOLUTA)
import models # Ahora usarás models.User, models.ExamPaper, etc.

# Cargar variables de entorno desde .env
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR CRÍTICO: DATABASE_URL no está configurada en el archivo .env. La aplicación no funcionará.")
    exit() # En producción, considera un manejo de errores más robusto o logging.

SUPABASE_JWT_SECRET_FROM_ENV = os.getenv("SUPABASE_JWT_SECRET")
if not SUPABASE_JWT_SECRET_FROM_ENV:
    # Este chequeo es solo una advertencia aquí, auth_utils.py tiene su propia verificación más crítica.
    print("ADVERTENCIA: SUPABASE_JWT_SECRET no parece estar en .env. auth_utils.py podría fallar al cargar o la validación de JWT fallará.")

engine = create_engine(DATABASE_URL, echo=True)

def create_db_and_tables():
    # Esta línea creará TODAS las tablas definidas
    # en los modelos importados del módulo 'models'
    # que heredan de SQLModel y tienen table=True.
    # Esto incluye User, ExamPaper, y TestItem (si no lo eliminaste de models.py).
    SQLModel.metadata.create_all(engine)

# --- FastAPI app instance ---
app = FastAPI(
    title="English Corrector API",
    description="API for the AI-powered English essay correction assistant.",
    version="0.1.0",
    # openapi_components={ # Opcional: Configuración OpenAPI para BearerAuth si lo deseas
    #     "securitySchemes": {
    #         "BearerAuth": {
    #             "type": "http",
    #             "scheme": "bearer",
    #             "bearerFormat": "JWT"
    #         }
    #     }
    # },
)

# --- Configuración de CORS ---
origins = [
    "http://localhost:3000", # URL de tu frontend Next.js en desarrollo
    # "https://tu-frontend-desplegado.com", # Añade esto cuando despliegues
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], # Permite todos los métodos (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"], # Permite todas las cabeceras
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

# --- Endpoints de TestItem (Considera si aún los necesitas) ---
# Si ya no los necesitas para pruebas, puedes eliminar estas secciones.
# Si los mantienes, asegúrate de que TestItem y sus modelos Pydantic asociados
# (TestItemRead, TestItemCreate) estén definidos en models.py.

@app.post("/test_items/", response_model=models.TestItemRead)
async def create_test_item(item_data: models.TestItemCreate, session: Session = Depends(get_session)):
    # Asegúrate de que models.TestItemCreate y models.TestItem estén definidos en models.py
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
    """
    Endpoint protegido que devuelve la información del usuario actual
    obtenida del token JWT decodificado.
    """
    return current_user

@app.get("/protected-route-example/")
async def protected_route_example(user_id: str = Depends(get_current_user_id)):
    """
    Otro ejemplo de endpoint protegido que solo devuelve el ID del usuario.
    En un caso real, aquí podrías usar el user_id para obtener datos
    específicos de ese usuario de tu base de datos local (tabla 'user').
    """
    # Ejemplo de cómo podrías verificar/crear el usuario en tu BD local:
    # with Session(engine) as session:
    #     db_user = session.get(models.User, user_id)
    #     if not db_user:
    #         # Si el usuario no existe en tu BD local, podrías crearlo aquí
    #         # Esto es útil para la gestión de créditos.
    #         # Necesitarías el email del token para esto, así que quizás usar Depends(get_current_user)
    #         # current_auth_user: TokenPayload = Depends(get_current_user)
    #         # new_db_user = models.User(id=current_auth_user.sub, email=current_auth_user.email, credits=0) # Créditos iniciales
    #         # session.add(new_db_user)
    #         # session.commit()
    #         # session.refresh(new_db_user)
    #         # print(f"Usuario local creado: {new_db_user.id}")
    #         # O simplemente lanzar un error si esperas que ya exista:
    #         # raise HTTPException(status_code=404, detail=f"Usuario local con ID {user_id} no encontrado.")
    #         pass # Por ahora, no hacemos nada si no existe.

    return {"message": "Ruta protegida accedida con éxito!", "user_id_from_token": user_id}

# --- NUEVOS ENDPOINTS PARA EXAMPAPERS (Se desarrollarán en Subfase 2.2 y 2.4) ---
# Estos son placeholders y se implementarán en los siguientes pasos.
# @app.post("/exam_papers/", response_model=models.ExamPaperRead)
# async def create_exam_paper(...):
#     pass

# @app.get("/exam_papers/", response_model=list[models.ExamPaperRead])
# async def list_exam_papers_for_user(...):
#     pass