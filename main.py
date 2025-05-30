# my-english-corrector-backend/main.py
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Field, Session, SQLModel, create_engine, select
from dotenv import load_dotenv
import os

# Importar utilidades de autenticación y modelos de payload
from auth_utils import get_current_user, get_current_user_id, TokenPayload
# Cargar variables de entorno desde .env
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL no está configurada en el archivo .env")
    exit()

SUPABASE_JWT_SECRET_CHECK = os.getenv("SUPABASE_JWT_SECRET") # Solo para verificar que auth_utils lo cargará
if not SUPABASE_JWT_SECRET_CHECK:
    print("ADVERTENCIA: SUPABASE_JWT_SECRET no parece estar en .env. auth_utils.py podría fallar al cargar.")


engine = create_engine(DATABASE_URL, echo=True)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

# --- Modelos SQLModel ---
class TestItemBase(SQLModel):
    name: str = Field(index=True)
    description: str | None = None

class TestItem(TestItemBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    # user_id: str | None = Field(default=None, foreign_key="user.id", index=True) # Ejemplo para asociar a usuario

class TestItemCreate(TestItemBase):
    pass

class TestItemRead(TestItemBase):
    id: int

# Podrías tener un modelo User en el futuro si quieres almacenar info de usuarios en tu BD además de Supabase
# class User(SQLModel, table=True):
#     id: str = Field(default=None, primary_key=True) # ID de Supabase Auth
#     email: str = Field(unique=True, index=True)
#     # ... otros campos ...

# --- FastAPI app instance ---
app = FastAPI(
    title="English Corrector API",
    description="API for the AI-powered English essay correction assistant.",
    version="0.1.0",
    # Puedes añadir aquí la configuración de OpenAPI para que tokenUrl de oauth2_scheme se use:
    # openapi_components={
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
    create_db_and_tables()

# --- Dependencia para la Sesión de Base de Datos ---
def get_session():
    with Session(engine) as session:
        yield session

# --- Endpoints Públicos ---
@app.get("/")
async def read_root():
    return {"message": "Hola Mundo desde el Backend FastAPI con conexión a DB y CORS configurado!"}

@app.post("/test_items/", response_model=TestItemRead)
async def create_test_item(item_data: TestItemCreate, session: Session = Depends(get_session)):
    db_item = TestItem.model_validate(item_data)
    session.add(db_item)
    session.commit()
    session.refresh(db_item)
    return db_item

@app.get("/test_items/", response_model=list[TestItemRead])
async def read_test_items(skip: int = 0, limit: int = 100, session: Session = Depends(get_session)):
    statement = select(TestItem).offset(skip).limit(limit)
    items = session.exec(statement).all()
    return items

@app.get("/test_items/{item_id}", response_model=TestItemRead)
async def read_test_item(item_id: int, session: Session = Depends(get_session)):
    item = session.get(TestItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item with id {item_id} not found")
    return item

# --- Endpoints Protegidos ---
@app.get("/users/me/", response_model=TokenPayload)
async def read_users_me(current_user: TokenPayload = Depends(get_current_user)):
    """
    Endpoint protegido que devuelve la información del usuario actual
    obtenida del token JWT.
    """
    return current_user

@app.get("/protected-route-example/")
async def protected_route_example(user_id: str = Depends(get_current_user_id)):
    """
    Otro ejemplo de endpoint protegido que solo devuelve el ID del usuario.
    """
    # En un caso real, aquí harías algo con el user_id,
    # como obtener datos específicos de ese usuario de la base de datos.
    return {"message": "Si ves esto, estás autenticado y tu ID de usuario ha sido validado!", "user_id": user_id}