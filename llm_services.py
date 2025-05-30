# my-english-corrector-backend/llm_services.py
import os
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv() # Asegurarse de que las variables de entorno estén cargadas

# Configuración de Modelos
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
DEFAULT_VISION_MODEL_PROVIDER = os.getenv("DEFAULT_VISION_MODEL_PROVIDER", "GEMINI_FLASH") # Default a Gemini si no está en .env

# Nombres de los modelos específicos que queremos usar
# Asegúrate de que estos nombres de modelo sean válidos y tengas acceso a ellos.
# GPT-4o mini (si OpenAI lo lanza con este nombre específico para la API de visión o si es un alias de gpt-4o)
# o gpt-4-vision-preview o el modelo de visión más reciente de OpenAI.
# Por ahora, usaré "gpt-4o-mini" como placeholder. Reemplaza con el nombre correcto del modelo de OpenAI.
# Para GPT-4o (el modelo completo con visión, no el "mini" si ese no existe aún como API separada): "gpt-4o"
OPENAI_VISION_MODEL_NAME = "gpt-4o-mini" # ¡VERIFICA ESTE NOMBRE DE MODELO! Podría ser "gpt-4o" o "gpt-4-turbo" con capacidad de visión.

# Gemini 1.5 Flash (el nombre puede variar ligeramente, verifica la documentación de Google)
# Gemini 1.5 Flash es "gemini-1.5-flash-latest" o una versión más específica como "models/gemini-1.5-flash-001"
# El nombre que diste "gemini-2.5-flash-preview-04-17" podría ser incorrecto o un modelo futuro.
# Usaré "gemini-1.5-flash-latest" como un nombre común y funcional. ¡VERIFICA ESTE NOMBRE!
GOOGLE_VISION_MODEL_NAME = "gemini-1.5-flash-latest" # O "models/gemini-1.5-flash-001"

def get_vision_model_client():
    """
    Retorna una instancia del cliente LLM de visión configurado
    basado en DEFAULT_VISION_MODEL_PROVIDER.
    """
    if DEFAULT_VISION_MODEL_PROVIDER == "GEMINI_FLASH":
        if not GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY no está configurada en las variables de entorno.")
        print(f"Usando modelo de visión de Google: {GOOGLE_VISION_MODEL_NAME}")
        # Para modelos Gemini que aceptan multimodalidad (imagen y texto)
        # la inicialización puede ser directa si la librería lo soporta bien para imágenes.
        return ChatGoogleGenerativeAI(model=GOOGLE_VISION_MODEL_NAME, google_api_key=GOOGLE_API_KEY)
    
    elif DEFAULT_VISION_MODEL_PROVIDER == "GPT4O_MINI": # O "OPENAI"
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY no está configurada en las variables de entorno.")
        print(f"Usando modelo de visión de OpenAI: {OPENAI_VISION_MODEL_NAME}")
        # Los modelos de OpenAI con capacidad de visión como gpt-4o o gpt-4-turbo
        # pueden manejar contenido multimodal en la lista de mensajes.
        return ChatOpenAI(model=OPENAI_VISION_MODEL_NAME, openai_api_key=OPENAI_API_KEY)
    
    else:
        raise ValueError(f"Proveedor de modelo de visión no soportado: {DEFAULT_VISION_MODEL_PROVIDER}")

async def transcribe_image_url_with_llm(image_url: str, prompt_text: str = "Transcribe el texto manuscrito en esta imagen.") -> str:
    """
    Toma una URL de imagen y un prompt, y usa el LLM de visión configurado
    para obtener una transcripción o descripción.
    """
    llm = get_vision_model_client()

    # Construir el mensaje multimodal. La estructura exacta puede variar ligeramente
    # entre OpenAI y Gemini cuando se usa a través de LangChain.
    # LangChain intenta abstraer esto, pero es bueno revisar la documentación específica
    # de langchain-openai y langchain-google-genai para "multimodal".

    # Ejemplo generalizado para modelos que aceptan una lista de contenidos por mensaje:
    message_content = [
        {"type": "text", "text": prompt_text},
        {
            "type": "image_url",
            "image_url": {"url": image_url},
        },
    ]
    
    human_message = HumanMessage(content=message_content)
    
    print(f"Enviando imagen {image_url} y prompt al LLM...")
    try:
        ai_response = await llm.ainvoke([human_message]) # Usar ainvoke para llamadas asíncronas
        
        # ai_response.content será el texto devuelto por el LLM
        transcription = str(ai_response.content) if ai_response.content else ""
        print("LLM Response Content:", transcription)
        return transcription
    except Exception as e:
        print(f"Error al llamar al LLM de visión: {e}")
        # Podrías querer manejar diferentes tipos de errores aquí (API, red, etc.)
        raise  # Re-lanzar la excepción para que el endpoint la maneje

# --- EJEMPLO DE PRUEBA (Puedes ejecutar este archivo directamente para probar) ---
if __name__ == "__main__":
    import asyncio
    # Reemplaza con una URL de imagen pública para probar
    # test_image_url = "https://www.writingclasses.com/sites/default/files/styles/max_650x650/public/field/image/Handwriting-Photograph-by-Nick-Fewings-for-Unsplash.jpg?itok=M8Fk5GZo"
    test_image_url = "https://images.unsplash.com/photo-1579705379005-1cdcdc76f793?q=80&w=1287&auto=format&fit=crop&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D" # Asegúrate que sea accesible
    
    async def main_test():
        try:
            # Prueba cambiando DEFAULT_VISION_MODEL_PROVIDER en .env
            print(f"Probando con el proveedor por defecto: {DEFAULT_VISION_MODEL_PROVIDER}")
            transcription = await transcribe_image_url_with_llm(test_image_url, "Describe brevemente esta imagen.")
            print("\n--- Transcripción/Descripción Obtenida ---")
            print(transcription)
        except ValueError as ve:
            print(f"Error de configuración: {ve}")
        except Exception as e:
            print(f"Ocurrió un error durante la prueba: {e}")

    asyncio.run(main_test())