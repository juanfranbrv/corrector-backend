
from fastapi import FastAPI, Depends, HTTPException # Añadido HTTPException para el manejo de errores 404
from fastapi.middleware.cors import CORSMiddleware # Import para CORS
from sqlmodel import Field, Session, SQLModel, create_engine, select
from dotenv import load_dotenv
import os

# Cargar variables de entorno desde .env
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL no está configurada en el archivo .env")
    # Considera lanzar una excepción aquí en lugar de solo exit() en producción
    # raise EnvironmentError("DATABASE_URL no está configurada en el archivo .env")
    exit()

# echo=True es útil para debugging, muestra las queries SQL que se ejecutan
engine = create_engine(DATABASE_URL, echo=True)

# Función para crear las tablas en la base de datos si no existen
def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

# --- Modelos SQLModel ---
class TestItemBase(SQLModel): # Modelo base para validación de entrada (sin ID)
    name: str = Field(index=True)
    description: str | None = None

class TestItem(TestItemBase, table=True): # Modelo de tabla con ID (hereda de TestItemBase)
    id: int | None = Field(default=None, primary_key=True)

class TestItemCreate(TestItemBase): # Modelo para crear ítems (usado en el request body)
    pass

class TestItemRead(TestItemBase): # Modelo para leer ítems (usado en la respuesta)
    id: int

# --- FastAPI app instance ---
app = FastAPI(
    title="English Corrector API",
    description="API for the AI-powered English essay correction assistant.",
    version="0.1.0"
)

# --- Configuración de CORS ---
# Orígenes permitidos (tu frontend Next.js en desarrollo)
origins = [
    "http://localhost:3000",  # El puerto por defecto de Next.js dev server
    # "https://your-deployed-frontend.com", # Añadirás esto cuando despliegues
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
    create_db_and_tables() # Crea las tablas al iniciar

# --- Dependencia para la Sesión de Base de Datos ---
def get_session():
    with Session(engine) as session:
        yield session

# --- Endpoints ---
@app.get("/")
async def read_root():
    return {"message": "Hola Mundo desde el Backend FastAPI con conexión a DB y CORS configurado!"}

# Usamos TestItemCreate para la entrada y TestItemRead para la salida para seguir buenas prácticas
@app.post("/test_items/", response_model=TestItemRead)
async def create_test_item(item_data: TestItemCreate, session: Session = Depends(get_session)):
    # Creamos una instancia del modelo de tabla TestItem a partir de los datos de TestItemCreate
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

