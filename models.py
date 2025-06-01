# my-english-corrector-backend/models.py
from sqlmodel import Field, SQLModel, Relationship # Relationship para claves foráneas
from datetime import datetime, timezone # Para created_at y updated_at
from typing import Optional, List # Para tipos opcionales y listas en relaciones

# --- Modelo de Usuario ---
# Este modelo almacenará el ID del usuario de Supabase y sus créditos.
class UserBase(SQLModel):
    email: Optional[str] = Field(default=None, index=True) # Opcional, para referencia
    credits: int = Field(default=0)

class User(UserBase, table=True):
    id: str = Field(default=None, primary_key=True) # ID de Supabase Auth (sub)

    # Relación uno-a-muchos: Un usuario puede tener muchas redacciones (ExamPaper)
    exam_papers: List["ExamPaper"] = Relationship(back_populates="owner")

class UserCreate(UserBase): # Para crear un usuario si es necesario (ej. primera compra de créditos)
    id: str # El ID vendrá de Supabase Auth

class UserRead(UserBase): # Para leer la info del usuario
    id: str

class UserUpdateCredits(SQLModel): # Para actualizar solo los créditos
    credits: int


# --- Modelo de Redacción (ExamPaper) ---
class ExamPaperBase(SQLModel):
    filename: Optional[str] = None
    image_url: Optional[str] = None
    status: str = Field(default="uploaded")

    transcribed_text: Optional[str] = Field(default=None)
    transcription_credits_consumed: int = Field(default=0)
    
    # Nuevos campos para la corrección
    corrected_feedback: Optional[str] = Field(default=None, description="Feedback de corrección proporcionado por el LLM.")
    correction_credits_consumed: int = Field(default=0)
    correction_prompt_version: Optional[str] = Field(default=None, description="Versión del prompt de corrección utilizado.")

    user_id: str = Field(foreign_key="user.id", index=True)

class ExamPaper(ExamPaperBase, table=True):
    id: int = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc),
                                 sa_column_kwargs={"onupdate": lambda: datetime.now(timezone.utc)})
    
    # Nuevo campo para la fecha de corrección
    corrected_at: Optional[datetime] = Field(default=None, description="Fecha y hora de cuando se completó la corrección.")

    # Relación muchos-a-uno: Esta redacción pertenece a un "owner" (User)
    owner: Optional[User] = Relationship(back_populates="exam_papers")


# Modelos Pydantic para diferentes operaciones CRUD de ExamPaper
class ExamPaperCreate(ExamPaperBase):
    pass

class ExamPaperRead(ExamPaperBase):
    id: int
    created_at: datetime
    updated_at: datetime
    corrected_at: Optional[datetime] # Incluir en la lectura

class ExamPaperUpdate(SQLModel): # Para actualizaciones parciales
    filename: Optional[str] = None
    image_url: Optional[str] = None
    status: Optional[str] = None
    transcribed_text: Optional[str] = None
    transcription_credits_consumed: Optional[int] = None
    
    # Nuevos campos para actualización
    corrected_feedback: Optional[str] = None
    correction_credits_consumed: Optional[int] = None
    correction_prompt_version: Optional[str] = None
    corrected_at: Optional[datetime] = None


# --- Modelo TestItem (Considera si aún lo necesitas) ---
class TestItemBase(SQLModel):
    name: str = Field(index=True)
    description: str | None = None

class TestItem(TestItemBase, table=True):
    id: int | None = Field(default=None, primary_key=True)

class TestItemCreate(TestItemBase):
    pass

class TestItemRead(TestItemBase):
    id: int