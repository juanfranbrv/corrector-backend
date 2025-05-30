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
    # En un entorno de producción, querrías que esto detuviera la aplicación o lanzara un error más severo.
    print("ERROR CRÍTICO: SUPABASE_JWT_SECRET no está configurado en el archivo .env. La autenticación fallará.")
    # raise EnvironmentError("SUPABASE_JWT_SECRET no está configurado en el archivo .env") # Podrías descomentar esto

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token") # "auth/token" es un placeholder

class TokenPayload(BaseModel):
    sub: str             # User ID de Supabase (Subject)
    aud: str             # Audiencia, debería ser "authenticated"
    exp: int             # Tiempo de expiración (timestamp Unix)
    email: str | None = None # Opcional, si Supabase lo incluye y lo necesitas
    role: str | None = None  # Opcional, el rol del usuario (ej. "authenticated")
    # Puedes añadir otros campos que Supabase incluya en el token y necesites,
    # como app_metadata, user_metadata, etc., si están presentes.

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
            audience="authenticated"  # Validar que el token está destinado a la audiencia "authenticated"
            # Opcionalmente, también podrías validar el emisor (issuer) si es consistente:
            # issuer=f"https://{os.getenv('SUPABASE_PROJECT_ID')}.supabase.co/auth/v1"
            # (esto requeriría que añadas SUPABASE_PROJECT_ID a tus variables de entorno)
        )
        # Valida la estructura del payload decodificado con el modelo Pydantic
        token_data = TokenPayload(**payload_dict)

    except JWTError as e:
        # Si python-jose lanza un error (token expirado, firma inválida, audiencia incorrecta, etc.)
        print(f"Error de JWT al decodificar/validar: {e}") # Log para depuración en el servidor
        raise credentials_exception
    except ValidationError as e:
        # Si el payload decodificado no coincide con la estructura de TokenPayload
        print(f"Error de validación del payload del token: {e}") # Log para depuración
        raise credentials_exception
    
    # Una verificación extra, aunque 'sub' es mandatorio en TokenPayload
    if token_data.sub is None:
        raise credentials_exception
        
    return token_data

# Dependencia de conveniencia si solo necesitas el ID del usuario
async def get_current_user_id(current_user: TokenPayload = Depends(get_current_user)) -> str:
    return current_user.sub