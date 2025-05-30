# my-english-corrector-backend/auth_utils.py
import os
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv()

SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")
ALGORITHM = "HS256" # Supabase usa HS256 con el JWT Secret simple

if not SUPABASE_JWT_SECRET:
    print("ERROR CRÍTICO: SUPABASE_JWT_SECRET no está configurado en el archivo .env. La autenticación fallará.")
    # En un entorno de producción, considera lanzar una excepción para detener la aplicación.
    # raise EnvironmentError("SUPABASE_JWT_SECRET no está configurado en el archivo .env")

# El tokenUrl no es estrictamente necesario aquí ya que Supabase maneja la obtención del token,
# pero FastAPI lo requiere para la documentación OpenAPI.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token") # "auth/token" es un placeholder

class TokenPayload(BaseModel):
    sub: str             # User ID de Supabase (Subject)
    aud: str             # Audiencia, debería ser "authenticated"
    exp: int             # Tiempo de expiración (timestamp Unix)
    email: str | None = None # Email del usuario (Supabase lo incluye por defecto)
    role: str | None = None  # Rol del usuario (ej. "authenticated")
    # Puedes añadir otros campos estándar de JWT o específicos de Supabase si los necesitas:
    # iat: int | None = None # Issued at time
    # app_metadata: dict | None = None
    # user_metadata: dict | None = None

async def get_current_user(token: str = Depends(oauth2_scheme)) -> TokenPayload:
    """
    Decodifica y valida el token JWT.
    Devuelve el payload del token si es válido, o lanza HTTPException si no.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload_dict = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=[ALGORITHM],
            audience="authenticated"
        )
        # Valida la estructura del payload decodificado con el modelo Pydantic
        token_data = TokenPayload(**payload_dict)

    except JWTError as e:
        print(f"Error de JWT al decodificar/validar: {e}")
        raise credentials_exception
    except ValidationError as e:
        print(f"Error de validación del payload del token: {e}")
        raise credentials_exception
    
    if token_data.sub is None:
        # Esta verificación es redundante si 'sub' es un campo obligatorio en TokenPayload,
        # pero no hace daño como una doble comprobación.
        raise credentials_exception
        
    return token_data

async def get_current_user_id(current_user: TokenPayload = Depends(get_current_user)) -> str:
    """
    Dependencia de conveniencia que solo devuelve el ID del usuario (sub) del token.
    """
    return current_user.sub