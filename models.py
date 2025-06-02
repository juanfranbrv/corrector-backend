# my-english-corrector-backend/models.py
import os # <--- AÑADIR ESTA LÍNEA
from sqlmodel import Field, SQLModel, Relationship
from datetime import datetime, timezone
from typing import Optional, List

# --- Modelo de Usuario ---
class UserBase(SQLModel):
    email: Optional[str] = Field(default=None, index=True)
    credits: int = Field(default=0)

class User(UserBase, table=True):
    id: str = Field(default=None, primary_key=True)
    exam_papers: List["ExamPaper"] = Relationship(back_populates="owner")

class UserCreate(UserBase):
    id: str

class UserRead(UserBase):
    id: str

class UserUpdateCredits(SQLModel):
    credits: int

# --- Modelo ExamImage ---
class ExamImageBase(SQLModel):
    image_url: str = Field(description="URL de la imagen en el almacenamiento")
    page_number: Optional[int] = Field(default=None, description="Número de página para ordenamiento")
    exam_paper_id: Optional[int] = Field(default=None, foreign_key="exampaper.id", index=True)

class ExamImage(ExamImageBase, table=True):
    id: int = Field(default=None, primary_key=True)
    exam_paper: Optional["ExamPaper"] = Relationship(back_populates="images")

class ExamImageCreate(ExamImageBase):
    pass

class ExamImageRead(ExamImageBase):
    id: int

# --- Modelo de Redacción (ExamPaper) ---
class ExamPaperBase(SQLModel):
    filename: Optional[str] = Field(default=None, description="Nombre del archivo original o un título para el ensayo")
    status: str = Field(default="uploaded", description="Estado actual del procesamiento del ensayo")

    # Usar os.getenv requiere 'import os' al principio del archivo
    transcribed_text: Optional[str] = Field(
        default=None, 
        sa_column_kwargs={"longtext": True} if os.getenv("DATABASE_DIALECT") == "mysql" else {}, 
        description="Texto transcrito completo de todas las páginas"
    )
    transcription_credits_consumed: int = Field(default=0)
    
    corrected_feedback: Optional[str] = Field(
        default=None, 
        sa_column_kwargs={"longtext": True} if os.getenv("DATABASE_DIALECT") == "mysql" else {}, 
        description="Feedback de corrección proporcionado por el LLM"
    )
    correction_credits_consumed: int = Field(default=0)
    correction_prompt_version: Optional[str] = Field(default=None, description="Versión del prompt de corrección utilizado")

    user_id: str = Field(foreign_key="user.id", index=True)

class ExamPaper(ExamPaperBase, table=True):
    id: int = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc),
                                 sa_column_kwargs={"onupdate": lambda: datetime.now(timezone.utc)})
    corrected_at: Optional[datetime] = Field(default=None, description="Fecha y hora de cuando se completó la corrección.")

    owner: Optional[User] = Relationship(back_populates="exam_papers")
    images: List["ExamImage"] = Relationship(
        back_populates="exam_paper",
        sa_relationship_kwargs={'lazy': 'selectin'} 
    )

class ExamPaperCreate(ExamPaperBase):
    pass

class ExamPaperRead(ExamPaperBase):
    id: int
    created_at: datetime
    updated_at: datetime
    corrected_at: Optional[datetime]
    images: List[ExamImageRead] = []

class ExamPaperUpdate(SQLModel):
    filename: Optional[str] = None
    status: Optional[str] = None
    transcribed_text: Optional[str] = None

# --- Modelo TestItem ---
class TestItemBase(SQLModel):
    name: str = Field(index=True)
    description: str | None = None

class TestItem(TestItemBase, table=True):
    id: int | None = Field(default=None, primary_key=True)

class TestItemCreate(TestItemBase):
    pass

class TestItemRead(TestItemBase):
    id: int